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


def test_seating_not_sunk_over_nan_hole():
    """footprint 정점이 in-range NaN 구멍에 걸려도 건물이 -0.5로 침몰하지 않는다(회귀)."""
    dem = _flat_dem(elev=50.0)
    dem.grid[3:7, 3:7] = np.nan   # 내부 NaN 구멍(등고선 채움 한계 모사)
    # (50,50)은 NaN 구멍 위, 나머지 3정점은 유효
    fp = [(50, 50), (90, 10), (90, 90), (10, 90)]
    b = BuildingSolid(name="A", footprint_m=fp, base_z_m=0.0, height_m=10.0, floors=3, attrs={})
    base_z = seat_building(b, dem)
    assert abs(base_z - (50.0 - BURIAL_M)) < 1e-3   # 50m 지형에 앉음(침몰 없음)


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
# 완경사 — 기복 ≤ 커튼가드폭 → 기존 '최저 - BURIAL' 유지(무회귀)
# ---------------------------------------------------------------------------

def test_seat_gentle_slope_no_regression():
    """완경사(기복 ≤ MAX_SKIRT)에선 base_z = 최저지반 - BURIAL_M (기존과 동일)."""
    dem = _slope_dem()
    # y_local 10..12 → 표고 10..12m, 기복 2m < MAX_SKIRT(3) → 가드 미발동
    footprint = [(5.0, 10.0), (15.0, 10.0), (15.0, 12.0), (5.0, 12.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    assert bz == pytest.approx(10.0 - BURIAL_M, abs=0.3)


# ---------------------------------------------------------------------------
# 급경사/절벽/이상치 — 커튼 가드 발동(최저점에 안 박고 대표지반 근처로)
# ---------------------------------------------------------------------------

def test_seat_steep_slope_curtain_guard():
    """급경사(기복 20m)에선 최저점(10m)에 박지 않고 대표지반-MAX_SKIRT로 상한."""
    dem = _slope_dem()
    # y_local 10..30 → 표고 10..30m, 중앙값≈20, 기복 20m ≫ MAX_SKIRT
    footprint = [(5.0, 10.0), (15.0, 10.0), (15.0, 30.0), (5.0, 30.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    # 옛 동작(min-0.5=9.5)이 아니라 grade(≈20-3)-0.5≈16.5 로 올라와야 함
    assert bz > 12.0                         # 최저점(9.5)에 안 박힘 — 가드 발동
    assert bz == pytest.approx(20.0 - 3.0 - BURIAL_M, abs=2.0)


def _cliff_dem(cell: float = 5.0) -> DEMPatch:
    """왼쪽 x<30 구역만 60m 낮은 절벽, 나머지 플랫폼 100m."""
    nrows, ncols = 20, 20
    minx, miny = 0.0, 0.0
    maxy = miny + nrows * cell
    transform = Affine(cell, 0, minx, 0, -cell, maxy)
    grid = np.full((nrows, ncols), 100.0, dtype=np.float32)
    grid[:, :6] = 60.0                        # 좌측 6열(x_abs<30) = 절벽 바닥
    return DEMPatch(grid=grid, transform=transform, offset=(minx, miny))


def test_seat_cliff_platform_not_bottom():
    """footprint가 절벽을 걸쳐도 다수 플랫폼(100m)에 앉고 절벽 바닥(60m)에 안 박힌다."""
    dem = _cliff_dem()
    # x 10..90: x<30(절벽 60m) 25% + x≥30(플랫폼 100m) 75% → 중앙값=플랫폼
    footprint = [(10.0, 20.0), (90.0, 20.0), (90.0, 80.0), (10.0, 80.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    assert bz > 90.0                          # 플랫폼 근처(가드가 절벽 바닥으로 못 내려가게)
    assert bz < 100.0                          # 매몰만큼은 아래


def test_seat_single_low_cell_outlier_bounded():
    """DEM 단일 저셀(노이즈)이 있어도 건물이 그 셀 바닥까지 침몰하지 않는다(가드 상한)."""
    dem = _flat_dem(elev=50.0)                 # 10×10 격자, cell 10 → 100m×100m
    dem.grid[5, 5] = 10.0                      # 한 셀만 40m 뚝 떨어진 노이즈
    footprint = [(10.0, 10.0), (80.0, 10.0), (80.0, 80.0), (10.0, 80.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    # 옛 동작이면 ~10-0.5로 침몰. 가드로 대표지반(50) - MAX_SKIRT - BURIAL ≈ 46.5 이상 유지.
    assert bz > 45.0


# ---------------------------------------------------------------------------
# 엣지 케이스
# ---------------------------------------------------------------------------

def test_seat_building_out_of_dem():
    """모든 꼭짓점이 DEM 범위 밖 → 가장자리 표고로 클램프 → 지형 위에 앉음(침몰 방지)."""
    dem = _flat_dem(50.0)
    footprint = [(9999.0, 9999.0), (10000.0, 9999.0), (10000.0, 10000.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    # 평탄 DEM(50m) 가장자리로 클램프 → base_z = 50 - BURIAL_M (예전엔 -0.5로 침몰)
    assert bz == pytest.approx(50.0 - BURIAL_M, abs=0.01)


def test_seat_building_single_vertex():
    """꼭짓점 1개 footprint(극단 케이스) — 오류 없이 동작."""
    dem = _flat_dem(30.0)
    footprint = [(5.0, 5.0), (10.0, 5.0), (10.0, 10.0)]
    solid = _solid(footprint)
    bz = seat_building(solid, dem)
    assert isinstance(bz, float)
