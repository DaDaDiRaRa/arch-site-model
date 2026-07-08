"""도로 노면 런타임 지오메트리 (Phase R).

road_manifest 지역 GeoJSON(EPSG:5186)을 질의 bbox로 클립해 로컬 미터 링으로 변환한다.
런타임은 json+shapely만 사용(geopandas 없이 — DEM과 동일 원칙). z는 파이프라인에서 DEM 드레이프.

R1a는 도로 '외곽선'(링)만 낸다. R1b에서 폴리곤 내부를 삼각화한 노면 메시로 확장한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import box, shape


@dataclass
class RoadFeature:
    """도로 폴리곤 하나. rings[0]=외곽, 이후=구멍(중앙분리대 등). 로컬 미터(offset 적용)."""

    rings: list[list[tuple[float, float]]]


@dataclass
class RoadMesh:
    """DEM 드레이프된 병합 노면 메시 + 외곽선. 좌표=로컬 미터. F2/.3dm/.skp 공용."""

    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    outlines: list[list[tuple[float, float, float]]]

    def to_geometry(self) -> dict:
        """F2 뷰어용 JSON(cm 반올림)."""
        return {
            "vertices": [[round(x, 2), round(y, 2), round(z, 2)] for x, y, z in self.vertices],
            "triangles": [[int(a), int(b), int(c)] for a, b, c in self.triangles],
            "outlines": [
                [[round(x, 2), round(y, 2), round(z, 2)] for x, y, z in ring]
                for ring in self.outlines
            ],
        }


# 노면을 지형 바로 위로 살짝 띄우는 리프트(m) — .3dm/.skp z-fighting 방지(F2는 뷰어에서 별도).
ROAD_LIFT_M = 0.1


def _ring_local(coords, offset) -> list[tuple[float, float]]:
    ox, oy = offset
    pts = [(float(x) - ox, float(y) - oy) for x, y in coords]
    if len(pts) >= 2 and pts[0] == pts[-1]:  # 닫힘점 제거(LineLoop 자동 폐합)
        pts = pts[:-1]
    return pts


def _iter_polys(geom):
    t = geom.geom_type
    if t == "Polygon":
        yield geom
    elif t == "MultiPolygon":
        yield from geom.geoms
    elif t == "GeometryCollection":
        for g in geom.geoms:
            yield from _iter_polys(g)


def clip_roads(geojson_path: str | Path, bbox_5186, offset) -> list[RoadFeature]:
    """지역 도로 GeoJSON을 bbox_5186으로 클립 → 로컬 미터 RoadFeature 목록.

    bbox_5186: (minx, miny, maxx, maxy) EPSG:5186. offset: origin_offset(건물·지형과 공통 기준).
    bbox와 겹치는 폴리곤만, bbox로 잘라서 반환. 파일 없으면 빈 목록.
    """
    path = Path(geojson_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data.get("features", []) if isinstance(data, dict) else []
    clip = box(*bbox_5186)
    out: list[RoadFeature] = []
    for f in feats:
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            g = shape(geom)
        except Exception:  # noqa: BLE001 — 깨진 지오메트리는 건너뜀
            continue
        if g.is_empty or not g.intersects(clip):
            continue
        for poly in _iter_polys(g.intersection(clip)):
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            ext = _ring_local(poly.exterior.coords, offset)
            if len(ext) < 3:
                continue
            holes = [
                _ring_local(r.coords, offset)
                for r in poly.interiors
                if len(r.coords) >= 4
            ]
            out.append(RoadFeature(rings=[ext] + holes))
    return out


# --- R1b: 노면 드레이프 메시 ------------------------------------------------

def _z(dem, x: float, y: float) -> float:
    """로컬 (x,y) → DEM 표고. dem 없으면 0.0(평면)."""
    return float(dem.elev_at(x, y)) if dem is not None else 0.0


def _densify_ring(ring, cell: float):
    """링 각 변을 cell 간격 이하로 잘게 나눈 점 목록(닫힘점 없이 순회)."""
    import math

    out = []
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]  # 마지막→처음으로 폐합
        out.append((x0, y0))
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg > cell:
            steps = int(seg // cell)
            for k in range(1, steps + 1):
                t = k / (steps + 1)
                out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


def _drape_polygon(rings, dem, cell: float):
    """폴리곤(외곽+구멍) 하나 → DEM 드레이프 삼각 메시 (정점[(x,y,z)], 삼각형[(i,j,k)]).

    경계(densify) + 내부 격자점을 모아 Delaunay 후, 삼각형 중심이 폴리곤 밖(또는 구멍
    안)이면 컬링한다(오목·구멍 지원). 정점 z = DEM 표고. scipy/shapely 실패 시 빈 메시.
    """
    import numpy as np
    from scipy.spatial import Delaunay
    from shapely.geometry import Point, Polygon
    from shapely.prepared import prep

    ext = rings[0]
    holes = [r for r in rings[1:] if len(r) >= 3]
    try:
        poly = Polygon(ext, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
    except Exception:  # noqa: BLE001
        return [], []
    if poly.is_empty or poly.area <= 0.0 or poly.geom_type != "Polygon":
        return [], []

    pae = prep(poly)
    pts: set[tuple[float, float]] = set()
    for ring in rings:  # 경계점(잘게)
        for px, py in _densify_ring(ring, cell):
            pts.add((round(px, 3), round(py, 3)))
    minx, miny, maxx, maxy = poly.bounds  # 내부 격자점
    for x in np.arange(minx, maxx + cell, cell):
        for y in np.arange(miny, maxy + cell, cell):
            if pae.contains(Point(x, y)):
                pts.add((round(float(x), 3), round(float(y), 3)))

    pt_list = list(pts)
    if len(pt_list) < 3:
        return [], []
    arr = np.array(pt_list, dtype=float)
    try:
        d = Delaunay(arr)
    except Exception:  # noqa: BLE001 — 공선점 등
        return [], []

    tris = []
    for s in d.simplices:
        a, b, c = int(s[0]), int(s[1]), int(s[2])
        cx = (arr[a, 0] + arr[b, 0] + arr[c, 0]) / 3.0
        cy = (arr[a, 1] + arr[b, 1] + arr[c, 1]) / 3.0
        if pae.contains(Point(cx, cy)):  # 폴리곤 밖/구멍 삼각형 컬링
            tris.append((a, b, c))
    verts = [(px, py, _z(dem, px, py)) for px, py in pt_list]
    return verts, tris


def build_road_mesh(features, dem, cell: float = 2.5) -> RoadMesh | None:
    """RoadFeature 목록 → 병합 노면 메시(RoadMesh, 로컬 미터). F2/.3dm/.skp 공용.

    각 폴리곤을 _drape_polygon으로 삼각화·드레이프해 하나의 정점/삼각형 버퍼로 병합하고,
    외곽선(링)도 드레이프해 담는다. 삼각화 실패 폴리곤도 외곽선은 남는다(조용한 열화).
    유효 지오메트리가 전혀 없으면 None.
    """
    verts: list = []
    tris: list = []
    outlines: list = []
    for f in features:
        v, t = _drape_polygon(f.rings, dem, cell)
        base = len(verts)
        verts.extend(v)
        tris.extend((a + base, b + base, c + base) for a, b, c in t)
        for ring in f.rings:
            if len(ring) >= 3:
                outlines.append([(x, y, _z(dem, x, y)) for x, y in ring])

    if not verts and not outlines:
        return None
    return RoadMesh(vertices=verts, triangles=tris, outlines=outlines)


# --- R2a: 중심선 종단 평활 + KD-트리 단면 평탄 --------------------------------

def _iter_lines(geom):
    t = geom.geom_type
    if t == "LineString":
        yield geom
    elif t == "MultiLineString":
        yield from geom.geoms
    elif t == "GeometryCollection":
        for g in geom.geoms:
            yield from _iter_lines(g)


def clip_centerlines(geojson_path: str | Path, bbox_5186, offset) -> list[list[tuple[float, float]]]:
    """지역 GeoJSON의 도로중심선(LineString)만 bbox 클립 → 로컬 미터 폴리라인 목록."""
    path = Path(geojson_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data.get("features", []) if isinstance(data, dict) else []
    clip = box(*bbox_5186)
    ox, oy = offset
    out: list[list[tuple[float, float]]] = []
    for f in feats:
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("LineString", "MultiLineString"):
            continue
        try:
            g = shape(geom)
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty or not g.intersects(clip):
            continue
        for ls in _iter_lines(g.intersection(clip)):
            coords = [(float(x) - ox, float(y) - oy) for x, y in ls.coords]
            if len(coords) >= 2:
                out.append(coords)
    return out


def _densify_line(coords, step: float):
    """폴리라인을 호길이 step 간격 이하로 조밀화(개곡선 — 끝점 폐합 안 함)."""
    import math

    if len(coords) < 2:
        return list(coords)
    out = [tuple(coords[0])]
    for i in range(len(coords) - 1):
        x0, y0 = coords[i]
        x1, y1 = coords[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 0:
            continue
        n = max(1, int(seg // step))
        for k in range(1, n + 1):
            t = k / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


def _smooth_1d(z, win_samples: int):
    """1D 이동평균(창 양끝은 축소 — 반사 인공물 없음)."""
    import numpy as np

    n = len(z)
    if n == 0:
        return z
    h = max(1, win_samples // 2)
    out = np.empty(n, dtype=float)
    for i in range(n):
        lo = max(0, i - h)
        hi = min(n, i + h + 1)
        out[i] = z[lo:hi].mean()
    return out


def _build_centerline_tree(centerlines, dem, win_m, sample_m):
    """중심선 → 조밀화·DEM샘플·종단 이동평균한 (x,y,z_smooth) 점들의 KD-트리.

    반환 (cKDTree, z_array) 또는 (None, None). flatten_road_mesh·burn_roads 공용.
    """
    if not centerlines or dem is None:
        return None, None
    import numpy as np
    from scipy.spatial import cKDTree

    win_samples = max(1, int(round(win_m / max(sample_m, 0.1))))
    pts = []
    zs = []
    for line in centerlines:
        dl = _densify_line(line, sample_m)
        if not dl:
            continue
        zc = _smooth_1d(np.array([dem.elev_at(x, y) for x, y in dl], dtype=float), win_samples)
        pts.extend(dl)
        zs.extend(float(z) for z in zc)
    if not pts:
        return None, None
    return cKDTree(np.asarray(pts)), np.asarray(zs)


def flatten_road_mesh(mesh, centerlines, dem, win_m=40.0, sample_m=5.0, max_dist_m=30.0, max_dev=None):
    """RoadMesh를 중심선 종단 프로파일로 평탄화(R2a).

    노면 정점/외곽선 정점마다 최근접 중심선 점의 z_smooth로 교체한다(같은 단면 폭은 같은 중심선
    지점에 매핑→평평, 종단은 평활곡선→리플 제거). 중심선이 max_dist_m 밖이면 원래 드레이프 z 유지
    (광장·주차장 등). max_dev 지정 시 각 정점을 자기 지면(원래 z) ±max_dev로 클램프(급경사서 도로가
    크게 뜨거나 파이는 것 방지). centerlines 없거나 dem 없으면 원본 그대로.
    """
    import numpy as np

    tree, zarr = _build_centerline_tree(centerlines, dem, win_m, sample_m)
    if tree is None:
        return mesh

    def _apply(points):
        if not points:
            return points
        xy = np.asarray([(x, y) for x, y, _ in points])
        d, idx = tree.query(xy)
        zt = zarr[idx]
        out = []
        for i, (x, y, z) in enumerate(points):
            if d[i] > max_dist_m:
                out.append((x, y, z))  # 중심선 멀면 드레이프 유지
                continue
            zn = zt[i]
            if max_dev is not None:  # 자기 지면 ±max_dev로 클램프
                zn = min(max(zn, z - max_dev), z + max_dev)
            out.append((x, y, float(zn)))
        return out

    return RoadMesh(
        vertices=_apply(mesh.vertices),
        triangles=mesh.triangles,
        outlines=[_apply(r) for r in mesh.outlines],
    )


def _centerline_points_tree(centerlines, sample_m):
    """중심선 조밀화 점들의 KD-트리(z 없이 거리용). apply_crown 전용."""
    import numpy as np
    from scipy.spatial import cKDTree

    pts = []
    for line in centerlines:
        pts.extend(_densify_line(line, sample_m))
    if not pts:
        return None
    return cKDTree(np.asarray(pts))


def apply_crown(mesh, centerlines, crown_pct=2.0, sample_m=5.0, cap_m=15.0):
    """노면에 크라운(횡단구배) 부여 — 중심선에서 멀수록 z를 낮춰 볼록한 배수형상(R2b 정제).

    각 정점의 중심선까지 수직거리 d(최근접) × crown_pct 만큼 z를 낮춘다(d는 cap_m로 상한 —
    광장 등 과도 하강 방지). 중심선 위(d=0)는 그대로, 가장자리는 crown_pct×half_width 만큼 하강.
    centerlines 없거나 crown_pct<=0 이면 원본 그대로.
    """
    if not centerlines or crown_pct <= 0:
        return mesh
    import numpy as np

    tree = _centerline_points_tree(centerlines, sample_m)
    if tree is None:
        return mesh
    slope = crown_pct / 100.0

    def _apply(points):
        if not points:
            return points
        xy = np.asarray([(x, y) for x, y, _ in points])
        d, _ = tree.query(xy)
        drop = np.minimum(d, cap_m) * slope
        return [(x, y, z - float(drop[i])) for i, (x, y, z) in enumerate(points)]

    return RoadMesh(
        vertices=_apply(mesh.vertices),
        triangles=mesh.triangles,
        outlines=[_apply(r) for r in mesh.outlines],
    )


def carve_terrain(terrain_mesh, road_features, m2i):
    """도로 footprint 안의 지형 삼각형을 제거한다(적응형 TIN이 도로 위를 덮는 초록 겹침 제거).

    지형 정점은 인치(×m2i) — /m2i로 미터 환산해 도로 폴리곤(로컬 미터) 포함 여부를 삼각형
    중심점으로 판정, 안쪽이면 버린다. 남은 정점만 재색인(compact). 도로 없으면 원본 그대로.
    """
    if not road_features or terrain_mesh is None or not terrain_mesh.triangles:
        return terrain_mesh
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    from shapely.prepared import prep

    from src.geometry.terrain_mesh import TerrainMesh

    polys = []
    for f in road_features:
        try:
            p = Polygon(f.rings[0], [r for r in f.rings[1:] if len(r) >= 3])
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                polys.append(p)
        except Exception:  # noqa: BLE001
            continue
    if not polys:
        return terrain_mesh
    pae = prep(unary_union(polys))

    V = terrain_mesh.vertices
    keep = []
    for a, b, c in terrain_mesh.triangles:
        cx = (V[a][0] + V[b][0] + V[c][0]) / 3.0 / m2i
        cy = (V[a][1] + V[b][1] + V[c][1]) / 3.0 / m2i
        if not pae.contains(Point(cx, cy)):
            keep.append((a, b, c))
    if len(keep) == len(terrain_mesh.triangles):
        return terrain_mesh  # 잘린 것 없음

    used = sorted({i for tri in keep for i in tri})
    remap = {old: new for new, old in enumerate(used)}
    return TerrainMesh(
        vertices=[terrain_mesh.vertices[i] for i in used],
        triangles=[(remap[a], remap[b], remap[c]) for a, b, c in keep],
    )


def burn_roads(
    dem, road_features, centerlines,
    win_m=40.0, sample_m=5.0, max_dist_m=30.0, skirt_m=12.0, max_dev=None,
):
    """도로를 DEM에 구워 지형이 도로에 맞게 절토/성토되게 한다(R2b).

    ① 도로 폴리곤(A0010000)을 DEM 격자에 래스터화 → footprint 셀 = 중심선 종단 평활 표고로 세팅
       (지형이 도로보다 높으면 깎임=절토, 낮으면 채움=성토 → '지형이 도로 덮음/뜸' 소멸).
    ② footprint 밖 skirt_m 밴드는 도로 표고↔자연 표고로 선형 블렌딩(비탈) → 수직 절벽 방지.
    새 DEMPatch 반환(원본 불변). 중심선 없거나 도로 없으면 원본 그대로.

    터널/지하차도(A0110020·A0090000)는 애초에 베이크(A0010000)에 없어 여기 도달 안 함 → 자동 제외
    (연속 지형을 개착하지 않음).
    """
    import numpy as np

    grid = getattr(dem, "grid", None)
    if grid is None or grid.size == 0 or not road_features:
        return dem
    tree, zarr = _build_centerline_tree(centerlines, dem, win_m, sample_m)
    if tree is None:
        return dem

    from rasterio.features import rasterize
    from scipy.ndimage import distance_transform_edt
    from shapely.geometry import Polygon

    from src.terrain.dem import DEMPatch

    rows, cols = grid.shape
    tf = dem.transform
    ox, oy = dem.offset

    # ① 도로 폴리곤(절대좌표) 래스터화
    shapes = []
    for f in road_features:
        ext = [(x + ox, y + oy) for x, y in f.rings[0]]
        holes = [[(x + ox, y + oy) for x, y in r] for r in f.rings[1:] if len(r) >= 3]
        try:
            poly = Polygon(ext, holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                shapes.append((poly, 1))
        except Exception:  # noqa: BLE001
            continue
    if not shapes:
        return dem
    road_mask = rasterize(
        shapes, out_shape=(rows, cols), transform=tf, fill=0, all_touched=True
    ).astype(bool)
    if not road_mask.any():
        return dem

    orig = grid.astype(float)
    new = orig.copy()

    # 셀 중심 로컬 좌표
    xs = (tf.c + tf.a * (np.arange(cols) + 0.5)) - ox   # (cols,)
    ys = (tf.f + tf.e * (np.arange(rows) + 0.5)) - oy   # (rows,)

    # ② footprint 셀 = 중심선 평활 표고(멀면 원본 유지)
    rr, cc = np.where(road_mask)
    cell_xy = np.column_stack([xs[cc], ys[rr]])
    # 교차부 블렌딩: 최근접 하나가 아니라 k-최근접 중심선의 거리가중 평균(IDW)으로 z를 정한다 —
    # 서로 다른 높이의 도로가 만나는 접합부에서 z 계단(튐)을 매끄럽게 잇는다. 단일 도로에선
    # 1/d² 가중이 수직 최근접점을 크게 실어 사실상 그 도로 z(영향 미미).
    kk = min(8, len(zarr))
    dists, idxs = tree.query(cell_xy, k=kk)
    if kk == 1:
        dists = dists[:, None]
        idxs = idxs[:, None]
    nearest = dists[:, 0]
    w = np.where(dists <= max_dist_m, 1.0 / (dists ** 2 + 1e-6), 0.0)  # 블렌드 반경 밖 제외
    wsum = w.sum(axis=1)
    wsum[wsum == 0.0] = 1.0
    z_blend = (w * zarr[idxs]).sum(axis=1) / wsum
    oc = orig[rr, cc]
    burned = np.where(nearest <= max_dist_m, z_blend, oc)  # 중심선 먼 도로셀은 원본 유지
    if max_dev is not None:                                 # 자기 지면 ±dev로 클램프(산 방지)
        burned = np.clip(burned, oc - max_dev, oc + max_dev)
    new[rr, cc] = burned

    # ③ 스커트 밴드: 도로 밖 skirt_m 내 → 최근접 도로셀 표고↔자연 선형 블렌딩(비탈)
    if skirt_m > 0:
        cell = abs(tf.a) or 1.0
        dist_px, (iy, ix) = distance_transform_edt(~road_mask, return_indices=True)
        dist_m = dist_px * cell
        near_z = new[iy, ix]                      # 최근접 도로셀의 (버닝된) 표고
        t = np.clip(dist_m / skirt_m, 0.0, 1.0)
        blended = near_z * (1.0 - t) + orig * t
        band = (~road_mask) & (dist_m > 0.0) & (dist_m <= skirt_m) & np.isfinite(orig)
        new[band] = blended[band]

    return DEMPatch(grid=new.astype(np.float32), transform=tf, offset=dem.offset)
