"""수계 런타임 (water.py) — 합성 GeoJSON + 합성 DEM으로 클립·수면z·평면 메시·버닝 검증."""

import json

import numpy as np
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
