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

from src.terrain.contour_bake import (
    _CONTOUR_PAT,
    _SHEET_PAT,
    _find_elev_field,
    _find_shp,
    bake_dem,
    bake_tiled,
    read_contours,
    write_dem_tif,
)


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


def test_bake_dem_limits_fill_distance(tmp_path):
    """실데이터에서 fill_dist_m 넘게 떨어진 셀은 nan 유지(무제한 외삽 금지).

    지역 경계에서 한 타일의 외삽값이 이웃 지역을 오염시키지 않도록 하는 핵심.
    """
    _make_synthetic_shp(tmp_path)   # 반경 150m 언덕, 중심 (200000, 400000)
    xs, ys, zs = read_contours(tmp_path)
    # bounds를 데이터(중앙 ~300m)보다 훨씬 크게 → 먼 구석은 실데이터에서 멀어 nan
    bounds = (199_000, 399_000, 201_000, 401_000)   # 2×2km
    grid, _ = bake_dem(xs, ys, zs, cell_m=10.0, bounds=bounds, fill_dist_m=200.0)
    assert np.isnan(grid).any()          # 먼 구석은 nan
    assert np.isfinite(grid).any()       # 데이터 근처는 값
    assert np.isnan(grid[0, 0])          # 좌상단(199000,401000) ~1.4km → nan


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
# 라플라스 조화 격자 솔버 (method="solver") — 계단현상 제거
# ---------------------------------------------------------------------------

def test_grid_relax_removes_step_between_levels():
    """초기값에 계단이 있어도, 양끝만 고정하면 완화가 계단을 없애고 매끈한 선형 램프로.

    조화함수(∇²z=0)는 1D에서 정확히 선형 → 2차차분≈0(계단 아님). 제약 셀은 고정.
    """
    from src.terrain.contour_bake import _grid_relax

    n = 21
    z0 = np.zeros((n, n))
    z0[:, n // 2:] = 10.0            # 초기 계단(왼쪽 0 / 오른쪽 10)
    constrained = np.zeros((n, n), bool)
    valid = np.ones((n, n), bool)
    constrained[:, 0] = True
    z0[:, 0] = 0.0
    constrained[:, -1] = True
    z0[:, -1] = 10.0

    zr = _grid_relax(z0, constrained, valid, iters=1500, omega=1.9)
    mid = zr[n // 2]
    assert np.all(np.diff(mid) > -1e-6)              # 단조 증가(계단 사라짐)
    assert np.all(np.abs(np.diff(mid, 2)) < 0.1)     # 2차차분≈0 → 직선 램프(매끈)
    assert abs(mid[n // 2] - 5.0) < 0.3              # 중앙 ≈ 5 (조화=선형)
    assert np.allclose(zr[:, 0], 0.0) and np.allclose(zr[:, -1], 10.0)  # 제약 고정


def test_bake_dem_solver_runs_in_range_no_overshoot(tmp_path):
    """method='solver'가 동작 + 조화라 전역 표고범위 안(오버슈트 없음) + 봉우리 유지."""
    _make_synthetic_shp(tmp_path)
    xs, ys, zs = read_contours(tmp_path)
    grid, _ = bake_dem(xs, ys, zs, cell_m=10.0, method="solver", solver_iters=200)

    assert np.isfinite(grid).all()                   # 최근방 채움 후 nan 없음
    assert grid.min() >= zs.min() - 1e-3             # 조화함수 최대원리 → 오버슈트 없음
    assert grid.max() <= zs.max() + 1e-3
    # 봉우리(중심 표고점 40m)가 외곽 저지대보다 높음
    assert grid[grid.shape[0] // 2, grid.shape[1] // 2] > grid[0, 0]


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


# ---------------------------------------------------------------------------
# bake_tiled (대용량 지역 타일 분할 배치 베이크)
# ---------------------------------------------------------------------------

def test_bake_tiled_creates_multiple_aligned_tiles(tmp_path):
    """합성 언덕(≈300m)을 작은 타일로 나누면 여러 타일이 생성되고, 인접 타일이
    픽셀 정합(공통 minx/maxy 격자)한다. manifest는 건드리지 않는다."""
    _make_synthetic_shp(tmp_path)
    out = tmp_path / "dem_syn.tif"
    made = bake_tiled(
        tmp_path, out, cell_m=5.0, tile_km=0.1, margin_m=20.0,
        update_manifest_flag=False,
    )

    assert len(made) >= 2
    assert all(p.exists() for p in made)
    assert all(("_r" in p.stem and "c" in p.stem) for p in made)

    with rasterio.open(made[0]) as src:
        assert src.crs.to_epsg() == 5186
        assert abs(src.res[0] - 5.0) < 1e-6
        ox, oy = src.transform.c, src.transform.f  # 원점(좌상단)

    # 다른 타일의 원점도 같은 5m 격자에 정렬 → (원점 차)가 5m의 정수배.
    with rasterio.open(made[-1]) as src2:
        dx = abs(src2.transform.c - ox)
        dy = abs(src2.transform.f - oy)
    assert abs((dx / 5.0) - round(dx / 5.0)) < 1e-6
    assert abs((dy / 5.0) - round(dy / 5.0)) < 1e-6


# ---------------------------------------------------------------------------
# _find_shp — 도엽 중복 제거 (시·도 단위 다운로드)
# ---------------------------------------------------------------------------

def test_find_shp_dedups_sheet_across_folders(tmp_path):
    """같은 도엽이 여러 구 폴더에 중복 복사돼 있으면 하나만 취한다(바이트 동일)."""
    for gu in ("강북구", "종로구"):
        d = tmp_path / gu / "(B010)수치지도_37608048_2025_x"
        d.mkdir(parents=True)
        (d / "N3L_F0010000.shp").write_bytes(b"")
    d2 = tmp_path / "강북구" / "(B010)수치지도_37608049_2025_y"  # 다른 도엽
    d2.mkdir(parents=True)
    (d2 / "N3L_F0010000.shp").write_bytes(b"")

    found = _find_shp(tmp_path, _CONTOUR_PAT)
    sheets = sorted(_SHEET_PAT.search(p.parent.name).group(1) for p in found)
    assert len(found) == 2                    # 37608048(중복→1) + 37608049
    assert sheets == ["37608048", "37608049"]


def test_find_shp_flat_dump_no_dedup(tmp_path):
    """평면 덤프(도엽 폴더 아님)는 중복제거 없이 파일마다 유지."""
    for suf in ("(1)", "(2)"):
        (tmp_path / f"N3L_F0010000 {suf}.shp").write_bytes(b"")
    found = _find_shp(tmp_path, _CONTOUR_PAT)
    assert len(found) == 2


def test_find_shp_dedups_bare_sheet_number_folders(tmp_path):
    """2MAP5000 포맷(도엽번호가 곧 폴더명, 예: 37611088)도 중복 도엽 제거."""
    for si in ("화성시", "수원시"):   # 같은 도엽 37611088이 두 시에 중복
        d = tmp_path / si / "37611088"
        d.mkdir(parents=True)
        (d / "N3L_F0010000.shp").write_bytes(b"")
    d2 = tmp_path / "화성시" / "37611089"   # 다른 도엽
    d2.mkdir(parents=True)
    (d2 / "N3L_F0010000.shp").write_bytes(b"")

    found = _find_shp(tmp_path, _CONTOUR_PAT)
    assert len(found) == 2   # 37611088(중복→1) + 37611089


def test_read_contours_reprojects_5187_to_5186(tmp_path):
    """동부원점(5187) SHP는 읽을 때 5186으로 재투영된다(부산·대구 대응)."""
    from pyproj import Transformer

    lon, lat = 129.05, 35.15   # 부산 근처
    x87, y87 = Transformer.from_crs("EPSG:4326", "EPSG:5187", always_xy=True).transform(lon, lat)
    x86, y86 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True).transform(lon, lat)
    assert abs(x86 - x87) > 1000   # 두 좌표대는 수십 km 차이

    gpd.GeoDataFrame(
        {"ELEV": [10.0], "geometry": [LineString([(x87, y87), (x87 + 10, y87 + 10)])]},
        crs="EPSG:5187",
    ).to_file(tmp_path / "F0010000_test.shp")
    gpd.GeoDataFrame(
        {"ELEV": [20.0], "geometry": [Point(x87, y87)]}, crs="EPSG:5187",
    ).to_file(tmp_path / "F0020000_test.shp")

    xs, ys, zs = read_contours(tmp_path)
    assert abs(xs.min() - x86) < 5.0    # 5186으로 재투영됨(5187 원본 아님)
    assert abs(ys.min() - y86) < 5.0
