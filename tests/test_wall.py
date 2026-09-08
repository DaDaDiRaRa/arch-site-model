"""옹벽 런타임/굽기 — 합성 GeoJSON·SHP·DEM으로 클립·단차 버닝·필드 별칭 검증."""

import json

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import from_bounds
from shapely.geometry import LineString

from src.geometry.wall import WallFeature, burn_walls, clip_walls, walls_to_geometry
from src.terrain.dem import DEMPatch
from src.terrain.wall_bake import read_walls


def _ramp_dem(n=60, cell=1.0):
    """z = x 인 균일 경사 DEM (로컬 원점 0,0, 셀 1m)."""
    tf = from_bounds(0, 0, n * cell, n * cell, n, n)
    xs = np.arange(n, dtype=np.float32) * cell + cell / 2
    grid = np.tile(xs, (n, 1))              # 행 무관, 열(x)에 비례
    return DEMPatch(grid=grid.astype(np.float32), transform=tf, offset=(0.0, 0.0))


def _wall_geojson(path, x=30.0, h=3.0):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"h": h},
         "geometry": {"type": "LineString", "coordinates": [[x, 5.0], [x, 55.0]]}},
    ]}
    path.write_text(json.dumps(fc), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# clip_walls
# ---------------------------------------------------------------------------

def test_clip_walls_returns_local_line_with_height(tmp_path):
    p = _wall_geojson(tmp_path / "walls.geojson")
    walls = clip_walls(p, (0, 0, 60, 60), (0.0, 0.0))
    assert len(walls) == 1
    assert walls[0].height_m == pytest.approx(3.0)
    assert walls[0].points[0] == pytest.approx((30.0, 5.0))


def test_clip_walls_accepts_multiple_tiles(tmp_path):
    a = _wall_geojson(tmp_path / "w_r0c0.geojson", x=20.0)
    b = _wall_geojson(tmp_path / "w_r0c1.geojson", x=40.0)
    assert len(clip_walls([a, b], (0, 0, 60, 60), (0.0, 0.0))) == 2


def test_clip_walls_drops_zero_height(tmp_path):
    """높이 없는 선(하단선)은 단차를 만들 수 없으므로 버린다."""
    p = _wall_geojson(tmp_path / "w.geojson", h=0.0)
    assert clip_walls(p, (0, 0, 60, 60), (0.0, 0.0)) == []


# ---------------------------------------------------------------------------
# burn_walls
# ---------------------------------------------------------------------------

def test_burn_walls_creates_step(tmp_path):
    """균일 경사에 옹벽을 심으면 그 선에서 높이만큼 단차가 생긴다."""
    dem = _ramp_dem()
    wall = WallFeature(points=[(30.0, 5.0), (30.0, 55.0)], height_m=3.0)

    out = burn_walls(dem, [wall])

    row = out.grid[30]                       # y=30 행을 가로질러 본다
    lo = float(row[27])                      # 벽 아래쪽(x=27.5, 낮은 편)
    hi = float(row[32])                      # 벽 위쪽(x=32.5, 높은 편)
    assert hi - lo >= 3.0, f"단차가 안 생김: {lo:.2f} → {hi:.2f}"
    # 복도(3m) 밖은 원본 그대로
    assert float(out.grid[30][5]) == pytest.approx(float(dem.grid[30][5]))
    assert float(out.grid[30][55]) == pytest.approx(float(dem.grid[30][55]))


def test_burn_walls_noop_without_walls():
    dem = _ramp_dem()
    assert burn_walls(dem, []) is dem


def test_burn_walls_does_not_mutate_input():
    dem = _ramp_dem()
    before = dem.grid.copy()
    burn_walls(dem, [WallFeature(points=[(30.0, 5.0), (30.0, 55.0)], height_m=3.0)])
    assert np.array_equal(dem.grid, before)


def test_walls_to_geometry_drapes_z():
    dem = _ramp_dem()
    g = walls_to_geometry([WallFeature(points=[(10.0, 10.0), (10.0, 40.0)], height_m=2.0)], dem)
    assert len(g) == 1 and g[0]["h"] == pytest.approx(2.0)
    assert all(len(p) == 3 for p in g[0]["points"])


# ---------------------------------------------------------------------------
# wall_bake.read_walls — 필드 별칭(도엽별 한글 ↔ 연속수치지형도 영문)
# ---------------------------------------------------------------------------

def _wall_shp(tmp_dir, col: str):
    gpd.GeoDataFrame(
        {col: [2.0, 0.2, 4.0],
         "geometry": [LineString([(0, 0), (50, 0)]),
                      LineString([(0, 10), (50, 10)]),     # 0.2m — 연석 수준이라 제외돼야
                      LineString([(0, 20), (50, 20)])]},
        crs="EPSG:5186",
    ).to_file(tmp_dir / "N3L_F0040000.shp")


def test_read_walls_english_field_and_min_height(tmp_path):
    """연속수치지형도는 높이가 HEIG. 0.5m 미만은 5m 격자에서 의미 없어 버린다."""
    _wall_shp(tmp_path, "HEIG")
    walls = read_walls(tmp_path)
    assert sorted(h for _g, h in walls) == [2.0, 4.0]


def test_read_walls_korean_field(tmp_path):
    """도엽별 수치지도는 높이가 한글 '높이'."""
    _wall_shp(tmp_path, "높이")
    assert sorted(h for _g, h in read_walls(tmp_path)) == [2.0, 4.0]


def test_burn_walls_protects_building_footprints():
    """건물 발자국 아래 지면은 그대로 둔다.

    파이프라인은 건물을 버닝 전 지면에 앉히므로, 옹벽이 건물 아래를 내리면 건물이 뜬다
    (QA building_float). 옹벽은 대개 건물 경계선을 따라가 실제로 이 일이 생긴다.
    """
    dem = _ramp_dem()
    wall = WallFeature(points=[(30.0, 5.0), (30.0, 55.0)], height_m=3.0)
    foot = [(26.0, 20.0), (34.0, 20.0), (34.0, 40.0), (26.0, 40.0)]   # 벽을 가로지르는 건물

    free = burn_walls(dem, [wall])
    kept = burn_walls(dem, [wall], protect_footprints=[foot])

    # 벽 바로 아래쪽 셀(x=29.5)은 상단-높이 아래로 눌린다. 건물 안이면(y≈29.5, 행 30) 보호돼야.
    assert float(free.grid[30][29]) != pytest.approx(float(dem.grid[30][29])), "보호 없으면 바뀌어야"
    assert float(kept.grid[30][29]) == pytest.approx(float(dem.grid[30][29])), "건물 아래는 보존"
    # 건물 밖(행 10 ≈ y 49.5)은 보호와 무관하게 단차가 선다
    assert float(kept.grid[10][29]) != pytest.approx(float(dem.grid[10][29]))
