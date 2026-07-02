"""Phase 3A: contour_bake 단위 테스트.

합성 등고선(동심원 언덕 + 봉우리 표고점)으로 read_contours/bake_dem/write_dem_tif 검증.
실제 SHP 파일 없이 동작.
"""

import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from shapely.geometry import LineString, Point

from src.terrain.contour_bake import _find_elev_field, bake_dem, read_contours, write_dem_tif


# ---------------------------------------------------------------------------
# 합성 SHP 픽스처
# ---------------------------------------------------------------------------

def _make_synthetic_shp(tmp_dir: Path) -> tuple[Path, Path]:
    """동심원 등고선 + 봉우리 표고점 SHP를 임시 폴더에 생성."""
    # EPSG:5186 가상 원점 근처
    cx, cy = 200_000.0, 400_000.0

    # 등고선: 반경 50, 100, 150m 원을 각각 고도 10, 20, 30m 로
    contours = []
    elevs = []
    for r, h in [(150, 10), (100, 20), (50, 30)]:
        angles = np.linspace(0, 2 * np.pi, 72, endpoint=False)
        ring = [(cx + r * np.cos(a), cy + r * np.sin(a)) for a in angles]
        ring.append(ring[0])  # 닫기
        contours.append(LineString(ring))
        elevs.append(h)

    gdf_c = gpd.GeoDataFrame(
        {"ELEV": elevs, "geometry": contours},
        crs="EPSG:5186",
    )
    contour_path = tmp_dir / "F0010000_test.shp"
    gdf_c.to_file(contour_path)

    # 표고점: 봉우리 (원점) 고도 40m
    gdf_s = gpd.GeoDataFrame(
        {"ELEV": [40.0], "geometry": [Point(cx, cy)]},
        crs="EPSG:5186",
    )
    spot_path = tmp_dir / "F0020000_test.shp"
    gdf_s.to_file(spot_path)

    return contour_path, spot_path


# ---------------------------------------------------------------------------
# _find_elev_field
# ---------------------------------------------------------------------------

def test_find_elev_field_standard():
    gdf = gpd.GeoDataFrame({"ELEV": [1.0], "geometry": [Point(0, 0)]})
    assert _find_elev_field(gdf) == "ELEV"


def test_find_elev_field_lowercase():
    gdf = gpd.GeoDataFrame({"elev": [1.0], "geometry": [Point(0, 0)]})
    assert _find_elev_field(gdf) == "elev"


def test_find_elev_field_missing():
    gdf = gpd.GeoDataFrame({"NAME": ["x"], "geometry": [Point(0, 0)]})
    with pytest.raises(ValueError, match="표고 필드"):
        _find_elev_field(gdf)


# ---------------------------------------------------------------------------
# read_contours
# ---------------------------------------------------------------------------

def test_read_contours_loads_points(tmp_path):
    _make_synthetic_shp(tmp_path)
    xs, ys, zs = read_contours(tmp_path)
    assert len(xs) > 0
    assert len(xs) == len(ys) == len(zs)
    # 봉우리 표고점(40m)이 포함돼 있어야 함
    assert 40.0 in zs


def test_read_contours_no_contour_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="F0010000"):
        read_contours(tmp_path)


# ---------------------------------------------------------------------------
# bake_dem
# ---------------------------------------------------------------------------

def test_bake_dem_shape_and_range(tmp_path):
    _make_synthetic_shp(tmp_path)
    xs, ys, zs = read_contours(tmp_path)
    grid, transform = bake_dem(xs, ys, zs, cell_m=10.0)

    # nan 없어야 함 (최근방 채움)
    assert not np.isnan(grid).any()

    # 표고 범위: 입력 최소~최대 사이
    assert grid.min() >= zs.min() - 1.0   # 보간 오차 여유
    assert grid.max() <= zs.max() + 1.0

    # 중심(봉우리)이 외곽(저지대)보다 높아야 함
    cx_idx = grid.shape[1] // 2
    cy_idx = grid.shape[0] // 2
    center_z = grid[cy_idx, cx_idx]
    edge_z = grid[0, 0]
    assert center_z > edge_z


def test_bake_dem_no_nan(tmp_path):
    _make_synthetic_shp(tmp_path)
    xs, ys, zs = read_contours(tmp_path)
    grid, _ = bake_dem(xs, ys, zs, cell_m=5.0)
    assert np.isfinite(grid).all()


def test_bake_dem_spot_raises_peak(tmp_path):
    """표고점 빼면 봉우리가 낮아지는지 — 표고점 포함 시 더 높아야 함."""
    _, spot_path = _make_synthetic_shp(tmp_path)
    xs_all, ys_all, zs_all = read_contours(tmp_path)

    # 표고점 제외 버전: 40m 제외
    mask = zs_all != 40.0
    xs_no, ys_no, zs_no = xs_all[mask], ys_all[mask], zs_all[mask]

    grid_with, _ = bake_dem(xs_all, ys_all, zs_all, cell_m=10.0)
    grid_without, _ = bake_dem(xs_no, ys_no, zs_no, cell_m=10.0)

    # 표고점 포함 시 최고점이 더 높아야 함
    assert grid_with.max() > grid_without.max()


# ---------------------------------------------------------------------------
# 보간법(method) — 계단현상 재굽기 (clough 가드 vs linear)
# ---------------------------------------------------------------------------

def test_bake_dem_linear_method_in_range(tmp_path):
    """method='linear'도 여전히 동작하고 표고 범위 안(오버슈트 없음)."""
    _make_synthetic_shp(tmp_path)
    xs, ys, zs = read_contours(tmp_path)
    grid, _ = bake_dem(xs, ys, zs, cell_m=10.0, method="linear")
    assert np.isfinite(grid).all()
    assert grid.min() >= zs.min() - 1e-3
    assert grid.max() <= zs.max() + 1e-3


def test_bake_dem_invalid_method_raises(tmp_path):
    _make_synthetic_shp(tmp_path)
    xs, ys, zs = read_contours(tmp_path)
    with pytest.raises(ValueError, match="보간법"):
        bake_dem(xs, ys, zs, cell_m=10.0, method="bogus")


def test_bake_dem_clough_guarded_within_tube(tmp_path):
    """clough(기본)는 linear에서 guard_m 이상 벗어나지 않아야 함(오버슈트 스파이크 차단)."""
    _make_synthetic_shp(tmp_path)
    xs, ys, zs = read_contours(tmp_path)
    guard = 2.0
    grid_lin, _ = bake_dem(xs, ys, zs, cell_m=5.0, method="linear")
    grid_ct, _ = bake_dem(xs, ys, zs, cell_m=5.0, method="clough", guard_m=guard)

    # 볼록껍질 밖은 두 방법 모두 동일 최근방 채움 → 차이는 껍질 안에서만.
    # 어떤 셀도 linear에서 guard보다 더 벗어나지 않음(수치오차 여유 포함).
    assert np.all(np.abs(grid_ct - grid_lin) <= guard + 1e-3)
    # 표고 범위 클램프 유지
    assert grid_ct.min() >= zs.min() - 1e-3
    assert grid_ct.max() <= zs.max() + 1e-3


# ---------------------------------------------------------------------------
# write_dem_tif
# ---------------------------------------------------------------------------

def test_write_dem_tif_readable(tmp_path):
    _make_synthetic_shp(tmp_path)
    xs, ys, zs = read_contours(tmp_path)
    grid, transform = bake_dem(xs, ys, zs, cell_m=10.0)

    out = tmp_path / "dem_test.tif"
    write_dem_tif(out, grid, transform)

    assert out.exists()
    with rasterio.open(out) as src:
        assert src.crs.to_epsg() == 5186
        data = src.read(1)
        assert data.shape == grid.shape
        # 표고 범위 일치
        assert np.nanmin(data) >= zs.min() - 1.0
        assert np.nanmax(data) <= zs.max() + 1.0
