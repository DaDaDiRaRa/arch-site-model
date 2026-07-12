"""타일 순차조립 백엔드 — tile_plan(격자 계획) + generate_tile(타일별 geometry)."""

import numpy as np

import src.tiles_stream as ts
from src.pipeline import _bbox_4326_to_5186
from src.tiles_stream import generate_tile, tile_plan


class FakeClient:
    def __init__(self, features):
        self._features = features

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        return self._features

    def count(self, dataset, bbox):
        return len(self._features)


def _square(lon0, lat0, side_deg=0.0002):
    return [[
        [lon0, lat0],
        [lon0 + side_deg, lat0],
        [lon0 + side_deg, lat0 + side_deg],
        [lon0, lat0 + side_deg],
        [lon0, lat0],
    ]]


def _feature(idx, lon0, lat0, floors=3):
    return {
        "type": "Feature",
        "properties": {"gro_flo_co": str(floors), "buld_nm": f"BLDG{idx}"},
        "geometry": {"type": "MultiPolygon", "coordinates": [_square(lon0, lat0)]},
    }


# --------------------------------------------------------------------------
# tile_plan
# --------------------------------------------------------------------------

def test_tile_plan_grid_and_offset(monkeypatch):
    monkeypatch.setattr(
        ts, "geocode", lambda a: {"lon": 127.3705, "lat": 36.3400, "crs": "EPSG:4326"}
    )
    plan = tile_plan("대전 어딘가", radius_m=500, tile_size_m=250.0)

    assert plan["ok"] is True
    # 반경 500m → bbox 한 변 ~1000m → 250m 타일이면 4×4 = 16개(경계 반올림 포함).
    assert len(plan["tiles"]) >= 9
    # offset = 전체 bbox의 5186 좌하단.
    full5186 = _bbox_4326_to_5186(tuple(plan["full_bbox_4326"]))
    assert plan["origin_offset"][0] == full5186[0]
    assert plan["origin_offset"][1] == full5186[1]
    # 각 타일은 4326/5186 bbox를 온전히 갖는다.
    for t in plan["tiles"]:
        assert len(t["bbox_4326"]) == 4
        assert len(t["bbox_5186"]) == 4
        assert "tile_id" in t


def test_tile_plan_tiles_cover_full_bbox(monkeypatch):
    monkeypatch.setattr(
        ts, "geocode", lambda a: {"lon": 127.3705, "lat": 36.3400, "crs": "EPSG:4326"}
    )
    plan = tile_plan("대전", radius_m=300, tile_size_m=200.0)
    full = _bbox_4326_to_5186(tuple(plan["full_bbox_4326"]))
    xs0 = min(t["bbox_5186"][0] for t in plan["tiles"])
    ys0 = min(t["bbox_5186"][1] for t in plan["tiles"])
    xs1 = max(t["bbox_5186"][2] for t in plan["tiles"])
    ys1 = max(t["bbox_5186"][3] for t in plan["tiles"])
    assert xs0 == full[0] and ys0 == full[1]
    assert xs1 == full[2] and ys1 == full[3]


# --------------------------------------------------------------------------
# generate_tile
# --------------------------------------------------------------------------

def _tile_bbox_around(lon0, lat0, half_deg=0.0015):
    b4326 = (lon0 - half_deg, lat0 - half_deg, lon0 + half_deg, lat0 + half_deg)
    b5186 = _bbox_4326_to_5186(b4326)
    offset = (b5186[0], b5186[1])
    return b4326, b5186, offset


def test_generate_tile_buildings_only():
    b4326, b5186, offset = _tile_bbox_around(127.3700, 36.3400)
    feat = _feature(0, 127.3700, 36.3400, floors=5)
    out = generate_tile(
        b4326, b5186, offset, layers={"buildings": True},
        client=FakeClient([feat]),
    )
    assert out["ok"] is True
    assert out["solids"] == 1
    assert len(out["geometry"]["buildings"]) == 1
    assert out["geometry"]["terrain"] is None  # 지형 미요청


def test_generate_tile_centroid_dedup_excludes_outside():
    """중심점이 타일 밖인 건물은 제외된다(경계 중복 제거)."""
    b4326, b5186, offset = _tile_bbox_around(127.3700, 36.3400)
    inside = _feature(0, 127.3700, 36.3400)          # 타일 중앙
    outside = _feature(1, 127.4000, 36.3400)         # 타일 동쪽 밖(~2.7km)
    out = generate_tile(
        b4326, b5186, offset, layers={"buildings": True},
        client=FakeClient([inside, outside]),
    )
    assert out["solids"] == 1  # inside만


def _patch_synth_dem_tile(monkeypatch):
    """실 DEM 없이 합성 지형 주입(generate_tile의 함수-로컬 import 대상 패치)."""
    from rasterio.transform import from_bounds as _tf

    import src.terrain.dem as dem_mod
    import src.terrain.store as store_mod
    from src.terrain.dem import DEMPatch

    monkeypatch.setattr(
        store_mod, "find_tiles",
        lambda bbox, manifest=None: [{"file": "synthetic.tif", "cell_m": 5.0}],
    )

    def _fake(paths, bbox_5186, offset):
        minx, miny, maxx, maxy = bbox_5186
        # 촘촘한 격자(≈5m 셀)로 마지막 행/열이 클립 가장자리에 근접하게 한다.
        n = 64
        tf = _tf(minx, miny, maxx, maxy, n, n)
        grid = np.full((n, n), 55.0, dtype=np.float32)
        return DEMPatch(grid=grid, transform=tf, offset=offset)

    monkeypatch.setattr(dem_mod, "clip_dem_mosaic", _fake)


def _tiny_png():
    """8×8 단색 PNG 바이트(정사영상 fetch 대역)."""
    import struct
    import zlib

    w = h = 8
    raw = bytearray()
    for _ in range(h):
        raw.append(0)
        raw += bytes((80, 120, 60)) * w

    def _chunk(typ, data):
        return (
            struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def test_generate_tile_orthophoto(monkeypatch):
    """layers.orthophoto=True + 지형 → 타일별 정사영상(base64 + 로컬 extent)."""
    import base64

    import src.pipeline as pl
    from src.geo.ortho import TileSource

    _patch_synth_dem_tile(monkeypatch)  # 지형 있어야 정사영상 생성
    small = TileSource(name="t", url_template="http://t/{z}/{x}/{y}.png", tile_size=8)
    monkeypatch.setattr(pl, "_resolve_ortho_source", lambda: (small, "KEY", "TestOrtho"))
    monkeypatch.setattr(pl.config, "ORTHO_ZOOM", 16)

    b4326, b5186, offset = _tile_bbox_around(127.3700, 36.3400)
    out = generate_tile(
        b4326, b5186, offset,
        layers={"buildings": True, "terrain": True, "orthophoto": True},
        client=FakeClient([_feature(0, 127.3700, 36.3400)]),
        ortho_fetch=lambda url: _tiny_png(),
    )
    assert out["ok"] is True
    o = out["ortho"]
    assert o is not None
    assert len(o["extent_local_m"]) == 4
    raw = base64.b64decode(o["image_b64"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # 유효 PNG


def test_generate_tile_no_ortho_without_terrain():
    """지형 미요청이면 정사영상도 없음(정사영상은 지형에 드레이프)."""
    b4326, b5186, offset = _tile_bbox_around(127.3700, 36.3400)
    out = generate_tile(
        b4326, b5186, offset, layers={"buildings": True, "orthophoto": True},
        client=FakeClient([_feature(0, 127.3700, 36.3400)]),
    )
    assert out["ortho"] is None


def test_generate_tile_terrain(monkeypatch):
    _patch_synth_dem_tile(monkeypatch)
    b4326, b5186, offset = _tile_bbox_around(127.3700, 36.3400)
    out = generate_tile(
        b4326, b5186, offset, layers={"buildings": True, "terrain": True},
        client=FakeClient([_feature(0, 127.3700, 36.3400)]),
    )
    assert out["ok"] is True
    assert out["terrain_triangles"] > 0
    assert out["geometry"]["terrain"] is not None
    verts = out["geometry"]["terrain"]["vertices"]
    assert verts
    # 이음매 제거용 margin 겹침 검증: 지형이 타일 원점(offset) 이전까지 뻗는다
    # (인접 타일과 겹쳐 gap이 없어짐). 로컬 x가 음수인 정점이 존재해야 한다.
    assert min(v[0] for v in verts) < 0
    assert min(v[1] for v in verts) < 0


def _write_road_geojson(path, b5186):
    """타일 중앙을 가로지르는 도로 폴리곤 + 중심선 GeoJSON(EPSG:5186 절대좌표)."""
    import json

    minx, miny, maxx, maxy = b5186
    midy = (miny + maxy) / 2.0
    road_poly = [
        [minx + 5, midy - 4], [maxx - 5, midy - 4],
        [maxx - 5, midy + 4], [minx + 5, midy + 4], [minx + 5, midy - 4],
    ]
    centerline = [[minx + 5, midy], [maxx - 5, midy]]
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Polygon", "coordinates": [road_poly]}},
            {"type": "Feature", "properties": {"cl": 1, "n": 2, "w": 8},
             "geometry": {"type": "LineString", "coordinates": centerline}},
        ],
    }
    path.write_text(json.dumps(gj), encoding="utf-8")
    return path


def test_generate_tile_roads(monkeypatch, tmp_path):
    """layers.roads=True + 지형 → 지형·도로·차선 통합 삼각화(geometry.roads/lanes)."""
    import src.terrain.store as store_mod

    _patch_synth_dem_tile(monkeypatch)
    b4326, b5186, offset = _tile_bbox_around(127.3700, 36.3400)
    gj = _write_road_geojson(tmp_path / "roads.geojson", b5186)

    # 도로 매니페스트 조회·경로 해석을 임시 GeoJSON으로 우회.
    monkeypatch.setattr(
        store_mod, "find_road_files", lambda bbox, manifest=None: [{"file": str(gj)}]
    )
    monkeypatch.setattr(ts.config, "road_file_path", lambda f: f)

    out = generate_tile(
        b4326, b5186, offset,
        layers={"buildings": True, "terrain": True, "roads": True},
        client=FakeClient([_feature(0, 127.3700, 36.3400)]),
    )
    assert out["ok"] is True
    assert out["road_triangles"] > 0
    roads = out["geometry"]["roads"]
    assert roads is not None and roads["triangles"]
    # 지형도 여전히 나온다(통합 표면 = 지형 + 도로 분리 메시).
    assert out["geometry"]["terrain"] is not None
    # 중심선 → 차선 폴리라인.
    assert out["geometry"]["lanes"]


def test_generate_tile_roads_skipped_without_terrain():
    """지형 미요청이면 도로도 없음(도로 z는 DEM 표고 → 지형 필요)."""
    b4326, b5186, offset = _tile_bbox_around(127.3700, 36.3400)
    out = generate_tile(
        b4326, b5186, offset, layers={"buildings": True, "roads": True},
        client=FakeClient([_feature(0, 127.3700, 36.3400)]),
    )
    assert out["ok"] is True
    assert out["geometry"]["roads"] is None
    assert out["road_triangles"] == 0
