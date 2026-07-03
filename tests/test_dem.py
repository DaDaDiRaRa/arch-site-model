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

from src.terrain.dem import DEMPatch, clip_dem, clip_dem_mosaic, elev_at


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


# ---------------------------------------------------------------------------
# clip_dem_mosaic (다중 타일 병합)
# ---------------------------------------------------------------------------

def _write_flat_tile(path, minx, maxy, nrows, ncols, cell, value):
    """상수 표고 EPSG:5186 GeoTIFF 생성. bounds(minx,miny,maxx,maxy) 반환."""
    transform = Affine(cell, 0, minx, 0, -cell, maxy)
    grid = np.full((nrows, ncols), float(value), dtype=np.float32)
    with rasterio.open(
        path, "w", driver="GTiff", height=nrows, width=ncols, count=1,
        dtype="float32", crs=CRS.from_epsg(5186), transform=transform, nodata=np.nan,
    ) as dst:
        dst.write(grid, 1)
    return (minx, maxy - nrows * cell, minx + ncols * cell, maxy)


def test_clip_dem_mosaic_single_delegates(synthetic_dem):
    """타일 1개면 clip_dem과 동일 결과(위임)."""
    path, _, bounds = synthetic_dem
    offset = (bounds[0], bounds[1])
    a = clip_dem(path, bounds, offset)
    b = clip_dem_mosaic([path], bounds, offset)
    assert np.array_equal(np.nan_to_num(a.grid), np.nan_to_num(b.grid))


def test_clip_dem_mosaic_two_adjacent(tmp_path):
    """가로로 인접한 두 타일을 걸친 bbox → 양쪽 값이 각자 자리에서 나옴."""
    cell = 10.0
    nrows, ncols = 20, 30  # 200m x 300m
    maxy = 400_200.0
    left = tmp_path / "left.tif"
    right = tmp_path / "right.tif"
    _write_flat_tile(left, 200_000.0, maxy, nrows, ncols, cell, 10.0)
    _write_flat_tile(right, 200_300.0, maxy, nrows, ncols, cell, 20.0)

    bbox = (200_100.0, 400_000.0, 200_500.0, 400_200.0)  # 두 타일에 걸침
    dem = clip_dem_mosaic([left, right], bbox, (0.0, 0.0))

    # offset (0,0) → local == abs. seam(200300)에서 충분히 떨어진 지점 샘플.
    assert abs(dem.elev_at(200_150.0, 400_100.0) - 10.0) < 1e-3   # 왼쪽 타일
    assert abs(dem.elev_at(200_450.0, 400_100.0) - 20.0) < 1e-3   # 오른쪽 타일
    # 두 타일 합집합이 bbox를 전부 덮으므로 NaN 없음.
    assert not np.isnan(dem.grid).any()


def test_clip_dem_mosaic_fills_uncovered_with_nan(tmp_path):
    """bbox가 타일 합집합을 벗어난 영역은 NaN으로 채움."""
    cell = 10.0
    nrows, ncols = 20, 30
    maxy = 400_200.0
    left = tmp_path / "left.tif"
    right = tmp_path / "right.tif"
    _write_flat_tile(left, 200_000.0, maxy, nrows, ncols, cell, 10.0)
    _write_flat_tile(right, 200_300.0, maxy, nrows, ncols, cell, 20.0)

    # bbox가 타일 좌/우/상/하로 넉넉히 더 넓다 → 가장자리는 미커버.
    bbox = (199_800.0, 399_800.0, 200_800.0, 400_400.0)
    dem = clip_dem_mosaic([left, right], bbox, (0.0, 0.0))
    assert np.isnan(dem.grid).any()      # 미커버 영역 존재
    assert np.isfinite(dem.grid).any()   # 커버 영역도 존재


def test_clip_dem_mosaic_skips_missing_tile(tmp_path):
    """존재하는 타일 + 없는 타일 → 없는 건 건너뛰고 있는 것으로 병합(로컬 누락·GCS 미도달)."""
    cell = 10.0
    nrows, ncols = 20, 30
    maxy = 400_200.0
    left = tmp_path / "left.tif"
    _write_flat_tile(left, 200_000.0, maxy, nrows, ncols, cell, 10.0)
    missing = tmp_path / "does_not_exist.tif"

    bbox = (200_050.0, 400_000.0, 200_250.0, 400_200.0)  # left 타일 내부
    dem = clip_dem_mosaic([str(missing), str(left)], bbox, (0.0, 0.0))
    assert abs(dem.elev_at(200_150.0, 400_100.0) - 10.0) < 1e-3


def test_clip_dem_mosaic_all_missing_raises(tmp_path):
    """타일을 하나도 못 열면 FileNotFoundError(상위에서 지형 생략 처리)."""
    with pytest.raises(FileNotFoundError):
        clip_dem_mosaic(
            [str(tmp_path / "a.tif"), str(tmp_path / "b.tif")],
            (0.0, 0.0, 100.0, 100.0), (0.0, 0.0),
        )
