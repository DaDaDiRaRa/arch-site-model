"""Phase 3B: grid_to_tin 단위 테스트.

합성 DEMPatch(3×3 격자)로 삼각망 구조 검증.
"""

import numpy as np
import pytest
from affine import Affine

from src.config import M2I
from src.geometry.terrain_mesh import TerrainMesh, grid_to_tin
from src.terrain.dem import DEMPatch


def _make_dem(nrows: int = 3, ncols: int = 3, cell: float = 10.0) -> DEMPatch:
    """단순 경사면 DEMPatch 생성 (row*10 + col 표고 m)."""
    minx, miny = 200_000.0, 400_000.0
    maxy = miny + nrows * cell
    transform = Affine(cell, 0, minx, 0, -cell, maxy)

    grid = np.array(
        [[float(r * 10 + c) for c in range(ncols)] for r in range(nrows)],
        dtype=np.float32,
    )
    offset = (minx, miny)
    return DEMPatch(grid=grid, transform=transform, offset=offset)


# ---------------------------------------------------------------------------
# 기본 구조
# ---------------------------------------------------------------------------

def test_grid_to_tin_vertex_count():
    """NaN 없는 3×3 격자 → 9개 정점."""
    dem = _make_dem(3, 3)
    mesh = grid_to_tin(dem)
    assert len(mesh.vertices) == 9


def test_grid_to_tin_triangle_count():
    """3×3 격자 → 셀 2×2 × 2삼각형/셀 = 8삼각형."""
    dem = _make_dem(3, 3)
    mesh = grid_to_tin(dem)
    assert len(mesh.triangles) == 8


def test_grid_to_tin_triangle_indices_valid():
    """모든 삼각형 인덱스가 정점 범위 안에 있어야 함."""
    dem = _make_dem(4, 5)
    mesh = grid_to_tin(dem)
    n = len(mesh.vertices)
    for tri in mesh.triangles:
        assert all(0 <= idx < n for idx in tri), f"범위 밖 인덱스: {tri}"


def test_grid_to_tin_units_inch():
    """Z 좌표가 미터 × M2I(인치)로 변환됨."""
    dem = _make_dem(2, 2)
    mesh = grid_to_tin(dem)
    # grid[0, 0] = 0.0m → z_inch = 0.0 * M2I = 0.0
    # 정점은 (x, y, z) — z=0인 정점이 있어야 함
    zs = [v[2] for v in mesh.vertices]
    assert min(zs) == pytest.approx(0.0 * M2I, abs=0.01)


def test_grid_to_tin_z_range_matches():
    """메시 z 범위 = grid 표고 범위 × M2I."""
    dem = _make_dem(3, 3, cell=5.0)
    mesh = grid_to_tin(dem)

    zr = dem.z_range()
    assert zr is not None
    zmin_inch = zr[0] * M2I
    zmax_inch = zr[1] * M2I

    mesh_zs = [v[2] for v in mesh.vertices]
    assert min(mesh_zs) == pytest.approx(zmin_inch, rel=1e-4)
    assert max(mesh_zs) == pytest.approx(zmax_inch, rel=1e-4)


# ---------------------------------------------------------------------------
# NaN 처리
# ---------------------------------------------------------------------------

def test_grid_to_tin_nan_skipped():
    """NaN 픽셀은 정점/삼각형에서 제외된다."""
    dem = _make_dem(3, 3)
    # 중앙 픽셀(1,1) NaN
    dem.grid[1, 1] = np.nan
    mesh = grid_to_tin(dem)

    assert len(mesh.vertices) == 8  # 9 - 1
    # NaN을 포함하는 셀(4개)의 일부 삼각형 제외 → 8보다 적어야 함
    assert len(mesh.triangles) < 8


def test_grid_to_tin_all_nan():
    """모든 픽셀 NaN → 빈 메시."""
    dem = _make_dem(2, 2)
    dem.grid[:] = np.nan
    mesh = grid_to_tin(dem)
    assert len(mesh.vertices) == 0
    assert len(mesh.triangles) == 0


# ---------------------------------------------------------------------------
# 큰 격자 성능 스모크
# ---------------------------------------------------------------------------

def test_grid_to_tin_large_grid():
    """100×100 격자 처리 완료 (성능 스모크)."""
    dem = _make_dem(100, 100, cell=5.0)
    mesh = grid_to_tin(dem)
    assert len(mesh.vertices) == 10_000
    assert len(mesh.triangles) == 99 * 99 * 2


# ---------------------------------------------------------------------------
# 적응형 TIN (adaptive_tin / build_tin)
# ---------------------------------------------------------------------------

def _surface_dem(n: int, heights: np.ndarray, cell: float = 5.0) -> DEMPatch:
    """n×n 격자 + 지정 표고 배열로 DEMPatch 생성."""
    minx, miny = 200_000.0, 400_000.0
    maxy = miny + n * cell
    transform = Affine(cell, 0, minx, 0, -cell, maxy)
    return DEMPatch(grid=heights.astype(np.float32), transform=transform, offset=(minx, miny))


def _max_vertical_error(dem: DEMPatch, mesh: TerrainMesh) -> float:
    """메시가 실제 DEM 표고를 얼마나 벗어나는가(m). 각 격자셀에서 최대 절대 오차."""
    from scipy.interpolate import LinearNDInterpolator

    tf = dem.transform
    ox, oy = dem.offset
    # 정점(인치·로컬) → (col, row, z[m]) 복원
    cr = []
    zz = []
    for vx, vy, vz in mesh.vertices:
        x_abs = vx / M2I + ox
        y_abs = vy / M2I + oy
        col = (x_abs - tf.c) / tf.a
        row = (y_abs - tf.f) / tf.e
        cr.append((col, row))
        zz.append(vz / M2I)
    interp = LinearNDInterpolator(np.array(cr), np.array(zz))
    rows, cols = dem.grid.shape
    cc, rr = np.meshgrid(np.arange(cols), np.arange(rows))
    zi = interp(np.column_stack([cc.ravel(), rr.ravel()]))
    err = np.abs(dem.grid.ravel().astype(np.float64) - zi)
    err = err[~np.isnan(err)]
    return float(err.max()) if err.size else 0.0


def test_adaptive_flat_is_two_triangles():
    """완전 평지 → 삼각형 극소(평면이므로 2개)."""
    from src.geometry.terrain_mesh import adaptive_tin

    dem = _surface_dem(40, np.full((40, 40), 50.0))
    mesh = adaptive_tin(dem, max_error_m=0.25)
    assert len(mesh.triangles) <= 8  # 균일격자라면 39*39*2=3042


def test_adaptive_plane_is_few_triangles():
    """기울어진 평면 → 소수 삼각형(평면은 2삼각으로 정확 근사)."""
    from src.geometry.terrain_mesh import adaptive_tin

    n = 40
    yy, xx = np.mgrid[0:n, 0:n]
    dem = _surface_dem(n, 50.0 + 0.4 * xx + 0.2 * yy)
    mesh = adaptive_tin(dem, max_error_m=0.25)
    assert len(mesh.triangles) <= 8


def test_adaptive_respects_error_bound():
    """봉우리 지형: 삼각형은 균일격자보다 훨씬 적고, 오차는 한계 이내."""
    from src.geometry.terrain_mesh import adaptive_tin

    n = 50
    yy, xx = np.mgrid[0:n, 0:n]
    heights = 50.0 + 30.0 * np.exp(-((xx - n / 2) ** 2 + (yy - n / 2) ** 2) / (2 * 7.0 ** 2))
    dem = _surface_dem(n, heights)
    tol = 0.5

    adaptive = adaptive_tin(dem, max_error_m=tol)
    uniform = grid_to_tin(dem)

    assert len(adaptive.triangles) < len(uniform.triangles)          # 삼각형 감소
    assert _max_vertical_error(dem, adaptive) <= tol + 1e-6          # 오차 한계 준수


def test_build_tin_dispatch():
    """build_tin: max_error=0 → 균일격자, >0 → 적응형(평지에서 삼각형 적음)."""
    from src.geometry.terrain_mesh import build_tin

    dem = _surface_dem(30, np.full((30, 30), 50.0))
    uniform = build_tin(dem, max_error_m=0.0)
    adaptive = build_tin(dem, max_error_m=0.25)
    assert len(uniform.triangles) == 29 * 29 * 2
    assert len(adaptive.triangles) <= 8


# ---------------------------------------------------------------------------
# add_skirt — 지형 바깥 둘레 벽 (TopoShaper 스타일)
# ---------------------------------------------------------------------------

def test_add_skirt_grid_wraps_outer_perimeter():
    """RxC 격자 → 스커트는 외곽 둘레 정점(2R+2C-4)만큼 바닥 정점 + 그 2배 삼각형 추가."""
    from src.geometry.terrain_mesh import add_skirt

    dem = _make_dem(4, 4, cell=10.0)
    mesh = grid_to_tin(dem)
    perim = 2 * 4 + 2 * 4 - 4  # = 12
    base = min(v[2] for v in mesh.vertices)

    skirted = add_skirt(mesh, depth_m=5.0)
    assert len(skirted.vertices) == len(mesh.vertices) + perim
    assert len(skirted.triangles) == len(mesh.triangles) + 2 * perim
    # 바닥 링 정점은 전부 min표고 - depth*M2I 에 평평하게.
    bottom_z = base - 5.0 * M2I
    bottoms = [v for v in skirted.vertices if abs(v[2] - bottom_z) < 1e-6]
    assert len(bottoms) == perim


def test_add_skirt_ignores_interior_hole():
    """도로 구멍(내부 루프)엔 스커트를 세우지 않고 외곽 둘레에만 세운다."""
    from src.geometry.terrain_mesh import TerrainMesh, add_skirt

    dem = _make_dem(5, 5, cell=10.0)
    mesh = grid_to_tin(dem)
    center = 2 * 5 + 2  # vid[2,2] = 12 (구멍 낼 중심 정점)
    holed = TerrainMesh(
        vertices=mesh.vertices,
        triangles=[t for t in mesh.triangles if center not in t],
    )
    perim = 2 * 5 + 2 * 5 - 4  # 외곽 둘레 = 16 (구멍 루프는 제외돼야)

    skirted = add_skirt(holed, depth_m=8.0)
    assert len(skirted.vertices) == len(holed.vertices) + perim


def test_add_skirt_noop_when_zero_depth():
    """depth<=0 이면 원본 그대로."""
    from src.geometry.terrain_mesh import add_skirt

    mesh = grid_to_tin(_make_dem(3, 3))
    assert add_skirt(mesh, 0.0) is mesh
