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
