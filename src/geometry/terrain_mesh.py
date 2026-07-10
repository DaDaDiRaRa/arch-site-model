"""DEM 격자 → TIN 삼각망 (Phase 3B, 사양서 §6.5).

grid_to_tin  : 각 셀을 대각 교차 2삼각형으로 분할(균일 — 어디든 5m마다 삼각형).
adaptive_tin : 오차 한계 적응형 TIN(greedy insertion) — 평지는 큰 삼각형, 복잡한 곳만
               촘촘. 지정한 수직오차(max_error_m) 이내를 보장하는 최소 삼각형을 지향.
               → 정확도 유지하며 삼각형 대폭 감소(넓은 반경도 가벼워짐). pydelatin이
               C 컴파일러를 요구해 설치가 취약하므로 scipy만으로 순수 파이썬 구현.
TerrainMesh  : SketchUp 인치 단위 (x,y,z) 정점 + 삼각형 인덱스.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from src.config import M2I
from src.terrain.dem import DEMPatch

log = logging.getLogger(__name__)


@dataclass
class TerrainMesh:
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)


def grid_to_tin(dem: DEMPatch) -> TerrainMesh:
    """DEMPatch 격자 → TIN.

    각 셀을 SW–NE 대각선으로 2삼각형 분할(사양서 §6.5).
    NaN 픽셀을 포함하는 셀은 건너뜀.
    출력 좌표 = 로컬 미터 × M2I (인치, SketchUp 단위).
    """
    grid = dem.grid
    rows, cols = grid.shape
    tf = dem.transform
    ox, oy = dem.offset

    # 픽셀 (row, col) → EPSG:5186 절대 좌표 → 로컬 미터
    # Affine: x_abs = tf.c + tf.a * col, y_abs = tf.f + tf.e * row
    col_idx = np.arange(cols, dtype=np.float64)
    row_idx = np.arange(rows, dtype=np.float64)
    x_local = (tf.c + tf.a * col_idx) - ox   # shape (cols,)
    y_local = (tf.f + tf.e * row_idx) - oy   # shape (rows,), tf.e < 0

    # (row, col) → vertex index 맵. -1 = NaN(제외)
    vid = np.full((rows, cols), -1, dtype=np.int32)
    vertices: list[tuple[float, float, float]] = []

    for r in range(rows):
        for c in range(cols):
            z = grid[r, c]
            if np.isnan(z):
                continue
            vid[r, c] = len(vertices)
            vertices.append((
                float(x_local[c]) * M2I,
                float(y_local[r]) * M2I,
                float(z) * M2I,
            ))

    triangles: list[tuple[int, int, int]] = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            # 셀 4 꼭짓점: (r,c)=NW (r,c+1)=NE (r+1,c)=SW (r+1,c+1)=SE
            vnw = vid[r,     c]
            vne = vid[r,     c + 1]
            vsw = vid[r + 1, c]
            vse = vid[r + 1, c + 1]
            # SW–NE 대각 교차
            if vnw >= 0 and vne >= 0 and vse >= 0:
                triangles.append((vnw, vne, vse))
            if vnw >= 0 and vse >= 0 and vsw >= 0:
                triangles.append((vnw, vse, vsw))

    return TerrainMesh(vertices=vertices, triangles=triangles)


def build_tin(dem: DEMPatch, max_error_m: float = 0.0) -> TerrainMesh:
    """max_error_m>0 이면 적응형 TIN, 아니면 균일 격자. 적응형 실패 시 격자로 폴백."""
    if max_error_m and max_error_m > 0:
        try:
            return adaptive_tin(dem, max_error_m)
        except Exception as e:  # noqa: BLE001 — 어떤 이유든 균일 격자로 안전 폴백
            log.warning("adaptive_tin 실패, 균일 격자 폴백: %s", e)
    return grid_to_tin(dem)


def _fill_nan_nearest(h: np.ndarray) -> np.ndarray:
    """NaN을 최근접 유효값으로 채운다(적응형 보간용 완전 격자 확보)."""
    mask = np.isnan(h)
    if not mask.any():
        return h
    from scipy.ndimage import distance_transform_edt

    idx = distance_transform_edt(mask, return_distances=False, return_indices=True)
    return h[tuple(idx)]


def adaptive_select(dem: DEMPatch, max_error_m: float, max_iters: int = 25):
    """오차 한계 적응형으로 고른 DEM 점 집합. 반환 (pts_pixel[N,2 col,row], z[N]) 또는 None.

    격자 네 모서리에서 시작해, 삼각망이 실제 표고를 max_error_m 넘게 벗어나는 셀을 반복
    삽입한다(삼각형마다 최악 오차 점 1개). 격자가 너무 작으면 None(호출측이 격자 폴백).
    """
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import Delaunay

    grid = dem.grid
    rows, cols = grid.shape
    if rows < 3 or cols < 3:
        return None

    h = _fill_nan_nearest(grid).astype(np.float64)
    cc, rr = np.meshgrid(np.arange(cols), np.arange(rows))
    pts_all = np.column_stack([cc.ravel(), rr.ravel()]).astype(np.float64)  # (N,2) col,row
    z_all = h.ravel()
    n = pts_all.shape[0]

    selected = list(dict.fromkeys([0, cols - 1, (rows - 1) * cols, n - 1]))
    cap = max(4, int(n * 0.9))  # 급경사서 오차한계 충족 위해 높게(정점↑ 대신 ±오차 보장 우선)

    for _ in range(max_iters):
        sel = np.array(selected)
        tri = Delaunay(pts_all[sel])
        zi = LinearNDInterpolator(tri, z_all[sel])(pts_all)
        err = np.abs(z_all - zi)
        err = np.where(np.isnan(err), 0.0, err)  # hull 밖(없음이 정상) → 0
        if float(err.max()) <= max_error_m or len(selected) >= cap:
            break
        simp = tri.find_simplex(pts_all)
        over = (simp >= 0) & (err > max_error_m)
        cand = np.where(over)[0]
        if cand.size == 0:
            break
        s = simp[cand]
        order = np.lexsort((-err[cand], s))
        s_sorted, cand_sorted = s[order], cand[order]
        first = np.empty(s_sorted.shape, dtype=bool)
        first[0] = True
        first[1:] = s_sorted[1:] != s_sorted[:-1]
        selected.extend(cand_sorted[first].tolist())
        selected = list(dict.fromkeys(selected))

    sel = np.array(selected)
    return pts_all[sel], z_all[sel]


def pixel_to_local_m(pts_pixel: np.ndarray, dem: DEMPatch) -> np.ndarray:
    """(col,row) 픽셀 좌표 → 로컬 미터 (x,y). dem.transform·offset 사용."""
    tf = dem.transform
    ox, oy = dem.offset
    x = (tf.c + tf.a * pts_pixel[:, 0]) - ox
    y = (tf.f + tf.e * pts_pixel[:, 1]) - oy
    return np.column_stack([x, y])


def adaptive_tin(
    dem: DEMPatch, max_error_m: float, max_iters: int = 25
) -> TerrainMesh:
    """오차 한계 적응형 TIN (greedy insertion, Garland–Heckbert 계열).

    평지·완경사는 큰 삼각형 몇 개로 오차 0에 수렴하고, 능선·급경사에만 삼각형이 촘촘해진다.
    출력 좌표 = 로컬 미터 × M2I (grid_to_tin과 동일 계약).
    """
    from scipy.spatial import Delaunay

    sel = adaptive_select(dem, max_error_m, max_iters)
    if sel is None:
        return grid_to_tin(dem)
    pts, zsel = sel
    tri = Delaunay(pts)

    tf = dem.transform
    ox, oy = dem.offset
    vertices: list[tuple[float, float, float]] = []
    for k in range(pts.shape[0]):
        col, row = pts[k]
        x_abs = tf.c + tf.a * col
        y_abs = tf.f + tf.e * row
        vertices.append((
            float(x_abs - ox) * M2I,
            float(y_abs - oy) * M2I,
            float(zsel[k]) * M2I,
        ))
    triangles = [(int(a), int(b), int(c)) for a, b, c in tri.simplices]
    return TerrainMesh(vertices=vertices, triangles=triangles)
