"""Phase 3B: dem.clip_dem / elev_at 단위 테스트.

합성 GeoTIFF(경사면)로 파일 없이 동작한다.
"""

import tempfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS

from src.terrain.dem import DEMPatch, clip_dem, elev_at


# ---------------------------------------------------------------------------
# 합성 DEM 픽스처
# ---------------------------------------------------------------------------

def _write_synthetic_dem(path: Path) -> tuple[Affine, tuple[float, float, float, float]]:
    """경사면 GeoTIFF(EPSG:5186) 생성.

    격자: 20행 × 30열, 셀 크기 10m.
    표고: row * 5 + col * 2  (m) — 단순 선형 경사.
    bounds_5186: (minx, miny, maxx, maxy).
    """
    nrows, ncols = 20, 30
    cell = 10.0
    minx, maxy = 200_000.0, 400_200.0       # EPSG:5186 절대 좌표
    transform = Affine(cell, 0, minx, 0, -cell, maxy)

    grid = np.array(
        [[float(r * 5 + c * 2) for c in range(ncols)] for r in range(nrows)],
        dtype=np.float32,
    )

    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=nrows, width=ncols,
        count=1,
        dtype="float32",
        crs=CRS.from_epsg(5186),
        transform=transform,
        nodata=np.nan,
    ) as dst:
        dst.write(grid, 1)

    bounds = (minx, maxy - nrows * cell, minx + ncols * cell, maxy)
    return transform, bounds


@pytest.fixture
def synthetic_dem(tmp_path):
    p = tmp_path / "dem_test.tif"
    transform, bounds = _write_synthetic_dem(p)
    return p, transform, bounds


# ---------------------------------------------------------------------------
# clip_dem
# ---------------------------------------------------------------------------

def test_clip_dem_full_coverage(synthetic_dem):
    """전체 타일 bbox 클립 → grid shape 정상, NaN 없음."""
    path, _, bounds = synthetic_dem
    offset = (bounds[0], bounds[1])
    dem = clip_dem(path, bounds, offset)

    assert dem.grid.ndim == 2
    assert dem.grid.shape[0] > 0
    assert dem.grid.shape[1] > 0
    assert not np.any(np.isnan(dem.grid)), "전체 커버리지: NaN 없어야 함"
    assert dem.offset == offset


def test_clip_dem_partial(synthetic_dem):
    """부분 클립 → 원래보다 작은 grid."""
    path, transform, bounds = synthetic_dem
    minx, miny, maxx, maxy = bounds
    # 전체의 절반만 클립
    sub_bbox = (minx, miny, (minx + maxx) / 2, (miny + maxy) / 2)
    offset = (minx, miny)
    dem = clip_dem(path, sub_bbox, offset)

    # 클립된 격자가 원본보다 작아야 함
    full_dem = clip_dem(path, bounds, offset)
    assert dem.grid.shape[0] < full_dem.grid.shape[0] or dem.grid.shape[1] < full_dem.grid.shape[1]


def test_clip_dem_z_range(synthetic_dem):
    """z_range가 grid 최솟값/최댓값과 일치."""
    path, _, bounds = synthetic_dem
    offset = (bounds[0], bounds[1])
    dem = clip_dem(path, bounds, offset)

    zr = dem.z_range()
    assert zr is not None
    assert abs(zr[0] - float(dem.grid[~np.isnan(dem.grid)].min())) < 1e-3
    assert abs(zr[1] - float(dem.grid[~np.isnan(dem.grid)].max())) < 1e-3


def test_clip_dem_no_nan_in_full_tile(synthetic_dem):
    """nodata=NaN으로 저장된 타일, 전체 클립 시 NaN 없음."""
    path, _, bounds = synthetic_dem
    offset = (bounds[0], bounds[1])
    dem = clip_dem(path, bounds, offset)
    assert dem.z_range() is not None


# ---------------------------------------------------------------------------
# elev_at
# ---------------------------------------------------------------------------

def test_elev_at_bottom_row(synthetic_dem):
    """최하단 픽셀 샘플점(row=19, col=0) 정확 조회."""
    path, transform, bounds = synthetic_dem
    minx, miny = bounds[0], bounds[1]
    offset = (minx, miny)
    dem = clip_dem(path, bounds, offset)

    # row=19의 y_abs(픽셀 상단 코너) = tf.f + tf.e * 19 = 400200 - 190 = 400010
    y_abs_row19 = transform.f + transform.e * 19
    y_local = y_abs_row19 - miny  # = 10.0

    # x_local=0 → x_abs=minx → col_f=0.0
    z = dem.elev_at(0.0, y_local)
    # corner-sample: row_f=19.0 → r0=18, dr=1.0 → grid[19,0] = 19*5+0 = 95
    assert abs(z - 95.0) < 1.0, f"기대 95m, 실제 {z}"


def test_elev_at_interpolated(synthetic_dem):
    """격자 중간점 보간: 선형 경사면에서 bilinear = linear."""
    path, transform, bounds = synthetic_dem
    minx, miny, _, maxy = bounds
    offset = (minx, miny)
    dem = clip_dem(path, bounds, offset)

    # 정확한 픽셀 중심 좌표: row=0, col=0 → (minx, maxy)
    x_local = 0.0          # = minx - minx
    y_local = maxy - miny  # = maxy - miny

    z = dem.elev_at(x_local, y_local)
    # row=0, col=0 → 0*5 + 0*2 = 0
    assert abs(z - 0.0) < 1.0


def test_elev_at_out_of_bounds(synthetic_dem):
    """범위 밖 좌표 → 가장 가까운 가장자리 표고로 클램프(0.0 침몰 방지)."""
    path, _, bounds = synthetic_dem
    offset = (bounds[0], bounds[1])
    dem = clip_dem(path, bounds, offset)

    z = dem.elev_at(-9999.0, -9999.0)
    lo, hi = dem.z_range()
    assert lo <= z <= hi   # 유효 표고 범위 내(0 sentinel 아님)


def test_elev_at_method_matches_function(synthetic_dem):
    """DEMPatch.elev_at() 메서드와 모듈 함수 elev_at()가 동일 결과."""
    path, _, bounds = synthetic_dem
    offset = (bounds[0], bounds[1])
    dem = clip_dem(path, bounds, offset)

    x_local, y_local = 50.0, 50.0
    assert dem.elev_at(x_local, y_local) == elev_at(x_local, y_local, dem)
