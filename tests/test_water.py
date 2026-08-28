"""수계 런타임 (water.py) — 합성 GeoJSON + 합성 DEM으로 클립·수면z·평면 메시·버닝 검증."""

import json

import numpy as np
import pytest
from rasterio.transform import from_bounds

from src.geometry.water import (
    WaterFeature,
    build_water_mesh,
    burn_water,
    clip_water,
    surface_zs,
    water_surface_z,
)
from src.terrain.dem import DEMPatch


def _flat_dem(z=50.0, offset=(0.0, 0.0), span=200.0, n=40):
    """어디서나 표고 z인 합성 DEMPatch (로컬 offset 원점)."""
    minx, miny = offset
    tf = from_bounds(minx, miny, minx + span, miny + span, n, n)
    grid = np.full((n, n), z, dtype=np.float32)
    return DEMPatch(grid=grid, transform=tf, offset=offset)


def _square_water_geojson(path):
    """절대 5186 좌표 사각형 수면(offset 0이면 로컬과 동일)."""
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[50, 50], [150, 50], [150, 150], [50, 150], [50, 50]]]}},
    ]}
    path.write_text(json.dumps(fc), encoding="utf-8")
    return path


def test_clip_water_returns_local_ring(tmp_path):
    p = _square_water_geojson(tmp_path / "water.geojson")
    feats = clip_water(p, (0, 0, 200, 200), (0.0, 0.0))
    assert len(feats) == 1 and isinstance(feats[0], WaterFeature)
    ring = feats[0].rings[0]
    assert len(ring) == 4  # 닫힘점 제거
    assert {(round(x), round(y)) for x, y in ring} == {(50, 50), (150, 50), (150, 150), (50, 150)}


def test_water_surface_z_low_percentile():
    """평평 DEM(50)이면 수면 z = 50."""
    dem = _flat_dem(z=50.0)
    feats = [WaterFeature(rings=[[(50, 50), (150, 50), (150, 150), (50, 150)]])]
    assert abs(water_surface_z(feats[0].rings, dem) - 50.0) < 1e-6
    assert surface_zs(feats, dem) == [water_surface_z(feats[0].rings, dem)]


def test_build_water_mesh_is_flat():
    """수면 메시는 폴리곤마다 자기 수면 z로 완전 평면(정점 z 모두 wz+lift)."""
    from src.geometry.water import WATER_LIFT_M

    dem = _flat_dem(z=50.0)
    feats = [WaterFeature(rings=[[(50, 50), (150, 50), (150, 150), (50, 150)]])]
    mesh = build_water_mesh(feats, [50.0], dem, cell=10.0)
    assert mesh is not None and mesh.vertices and mesh.triangles
    zs = [v[2] for v in mesh.vertices]
    assert max(zs) - min(zs) < 1e-6                 # 완전 평면
    assert abs(zs[0] - (50.0 + WATER_LIFT_M)) < 1e-6


def test_burn_water_flattens_interior():
    """수계 폴리곤 내부 DEM 셀이 수면 z로 세팅(지형이 물 위로 안 삐져나옴)."""
    dem = _flat_dem(z=50.0)
    feats = [WaterFeature(rings=[[(50, 50), (150, 50), (150, 150), (50, 150)]])]
    burned = burn_water(dem, feats, [45.0])
    # 중심(100,100) 근처 셀 = 45, 폴리곤 밖(모서리) = 50 유지
    assert burned is not dem
    assert abs(burned.elev_at(100.0, 100.0) - 45.0) < 1e-6
    assert abs(burned.elev_at(10.0, 10.0) - 50.0) < 1e-6


def test_clip_water_missing_file(tmp_path):
    assert clip_water(tmp_path / "nope.geojson", (0, 0, 1, 1), (0.0, 0.0)) == []


def _water_tile(path, x0, x1):
    """[x0,x1] x [50,150] 사각 수면 타일 하나 (하드클립된 조각을 흉내)."""
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[x0, 50], [x1, 50], [x1, 150], [x0, 150], [x0, 50]]]}},
    ]}
    path.write_text(json.dumps(fc), encoding="utf-8")
    return path


def test_clip_water_accepts_multiple_tiles(tmp_path):
    """넓은 지역은 수계도 타일로 쪼개므로 경로 리스트를 받아 합쳐 읽는다.

    경기도 수계가 단일 파일 200MB라 요청마다 전량 파싱하던 문제 → 도로처럼 타일 병합.
    """
    a = _water_tile(tmp_path / "w_r0c0.geojson", 50, 100)
    b = _water_tile(tmp_path / "w_r0c1.geojson", 100, 150)

    one = clip_water(a, (0, 0, 200, 200), (0.0, 0.0))
    both = clip_water([a, b], (0, 0, 200, 200), (0.0, 0.0))

    assert len(one) == 1
    assert len(both) == 2                      # 두 타일이 합쳐져 들어온다
    assert sum(len(f.rings[0]) for f in both) == 8


def test_clip_water_multi_skips_missing(tmp_path):
    """리스트 안에 없는 파일이 섞여도 나머지는 읽는다(조용한 건너뜀)."""
    a = _water_tile(tmp_path / "w_ok.geojson", 50, 100)
    feats = clip_water([a, tmp_path / "없는파일.geojson"], (0, 0, 200, 200), (0.0, 0.0))
    assert len(feats) == 1


def test_bake_water_tiled_splits_and_registers(tmp_path, monkeypatch):
    """넓은 수계를 타일로 하드클립하고 manifest에 타일별로 등록한다."""
    import geopandas as gpd
    from shapely.geometry import Polygon, box

    from src import config
    from src.terrain.water_bake import bake_water_tiled

    monkeypatch.setattr(config, "GEO_STORE", tmp_path / "store")
    (tmp_path / "store").mkdir()
    src = tmp_path / "src"; src.mkdir()
    # 가로로 긴 하천(5km) → 1km 타일이면 여러 장으로 쪼개진다
    river = Polygon([(0, 0), (5000, 0), (5000, 200), (0, 200)])
    gpd.GeoDataFrame({"geometry": [river]}, crs="EPSG:5186").to_file(
        src / "N3A_E0010001.shp")

    res = bake_water_tiled(src, tmp_path / "out" / "water_t.geojson", region="t", tile_km=1.0)

    assert res["tiles"] >= 5
    entries = json.loads((tmp_path / "store" / "water_manifest.json").read_text(encoding="utf-8"))
    assert len(entries) == res["tiles"]
    # 타일은 서로 겹치지 않고(하드클립) 합치면 원본 면적이 된다
    total = 0.0
    for e in entries:
        fc = json.loads((tmp_path / "out" / e["file"]).read_text(encoding="utf-8"))
        for f in fc["features"]:
            from shapely.geometry import shape
            total += shape(f["geometry"]).area
    assert total == pytest.approx(river.area, rel=1e-6)
