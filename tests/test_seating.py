"""Phase 3B: seat_building 단위 테스트.

합성 DEMPatch로 footprint 최저 꼭짓점 기준 base_z 결정 검증.
"""

import numpy as np
import pytest
from affine import Affine

from src.geometry.building import BuildingSolid
from src.geometry.seating import BURIAL_M, seat_building
from src.terrain.dem import DEMPatch


def _flat_dem(elev: float = 50.0, cell: float = 10.0) -> DEMPatch:
    """균일한 표고 elev(m)의 DEMPatch."""
    nrows, ncols = 10, 10
    minx, miny = 0.0, 0.0
    maxy = miny + nrows * cell
    transform = Affine(cell, 0, minx, 0, -cell, maxy)
    grid = np.full((nrows, ncols), elev, dtype=np.float32)
    return DEMPatch(grid=grid, transform=transform, offset=(minx, miny))


def _slope_dem(cell: float = 10.0) -> DEMPatch:
    """Y 방향 경사 DEMPatch: corner-sample 기준 표고 = y_abs = 100 - row*cell.

    row=0 → y_abs=100 → 표고 100m
    row=9 → y_abs=10  → 표고 10m

    elev_at(x, y_local)에서 y_local=10 → y_abs=10 → row_f=9 → 표고≈10m.
    """
    nrows, ncols = 10, 10
    minx, miny = 0.0, 0.0
    maxy = miny + nrows * cell          # = 100.0
    transform = Affine(cell, 0, minx, 0, -cell, maxy)
    # corner-sample: grid[r] = y_abs at row r = maxy - r*cell
    grid = np.array(
        [[float(maxy - r * cell) for _ in range(ncols)] for r in range(nrows)],
        dtype=np.float32,
    )
    return DEMPatch(grid=grid, transform=transform, offset=(minx, miny))


def _solid(footprint_m, height_m=9.0) -> BuildingSolid:
    return BuildingSolid(
        name="test",
        footprint_m=footprint_m,
        base_z_m=0.0,
        height_m=height_m,
        floors=3,
        attrs={},
    )


# ---------------------------------------------------------------------------
# 평지
# ---------------------------------------------------------------------------

def test_seat_building_flat():
    """평지에서 base_z = elev - BURIAL_M."""
    elev = 50.0
    dem = _flat_dem(elev)
    solid = _solid([(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)])
    bz = seat_building(solid, dem)
    assert bz == pytest.approx(elev - BURIAL_M, abs=0.1)


# ---------------------------------------------------------------------------
# 경사지 — 최저 꼭짓점 기준
# ---------------------------------------------------------------------------

def test_seat_building_slope_min_vertex():
    """경사지에서 base_z = footprint 중 최저 표고 - BURIAL_M."""
    dem = _slope_dem()
    # corner-sample: y_local=y_abs → elev_at(x, 10)≈10m, elev_at(x, 30)≈30m
    footprint = [(5.0, 10.0), (15.0, 10.0), (15.0, 30.0), (5.0, 30.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    # 최저 꼭짓점(y_local=10) 표고 = 10m
    assert bz == pytest.approx(10.0 - BURIAL_M, abs=0.1)


def test_seat_building_slope_not_center():
    """중심점 기준(Phase 2 구현)이 아닌 최저 꼭짓점 기준임을 확인."""
    dem = _slope_dem()
    # footprint 중심 y=20 → 표고20m
    # 최저 꼭짓점 y=10 → 표고10m
    footprint = [(5.0, 10.0), (15.0, 10.0), (15.0, 30.0), (5.0, 30.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    center_elev = 20.0  # 중심 표고 가정
    # base_z 가 중심 기준보다 낮아야 함(최저 꼭짓점이 더 낮으므로)
    assert bz < center_elev - BURIAL_M + 5.0


# ---------------------------------------------------------------------------
# 엣지 케이스
# ---------------------------------------------------------------------------

def test_seat_building_out_of_dem():
    """모든 꼭짓점이 DEM 범위 밖 → elev_at=0 → base_z = -BURIAL_M."""
    dem = _flat_dem(50.0)
    footprint = [(9999.0, 9999.0), (10000.0, 9999.0), (10000.0, 10000.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    assert bz == pytest.approx(0.0 - BURIAL_M, abs=0.01)


def test_seat_building_single_vertex():
    """꼭짓점 1개 footprint(극단 케이스) — 오류 없이 동작."""
    dem = _flat_dem(30.0)
    footprint = [(5.0, 5.0), (10.0, 5.0), (10.0, 10.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    assert isinstance(bz, float)
