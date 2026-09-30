"""교량·터널 면 베이크 — 타일에 **자르지 않고** 담고 런타임이 중복을 제거한다.

옹벽·도로·수계는 타일 박스로 하드클립하지만 데크는 그러면 안 된다. 폴리곤의 **양 끝이
종단 z를 정의**하므로(아붓먼트) 자르면 잘린 쪽이 어디였는지 알 수 없게 된다.
"""

import json

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from src.geometry.deck import _poly, clip_decks
from src.terrain.deck_bake import bake_decks_tiled, read_decks

# 타일 경계(2km)를 **가로지르는** 교량 — 자르면 반쪽만 남는다.
BRIDGE = Polygon([(1800, 1000), (2600, 1000), (2600, 1020), (1800, 1020)])
TUNNEL = Polygon([(500, 3000), (900, 3000), (900, 3020), (500, 3020)])


@pytest.fixture
def shp_dir(tmp_path):
    d = tmp_path / "shp"
    d.mkdir()
    gpd.GeoDataFrame(
        {"KIND": ["BRK001"], "NAME": ["시험교"], "RVNM": ["시험천"]},
        geometry=[BRIDGE], crs="EPSG:5186",
    ).to_file(d / "N3A_A0070000.shp", encoding="utf-8")
    gpd.GeoDataFrame(
        {"HEIG": [4.5], "NAME": ["시험터널"]},
        geometry=[TUNNEL], crs="EPSG:5186",
    ).to_file(d / "N3A_A0110020.shp", encoding="utf-8")
    gpd.GeoDataFrame(
        {"DIVI": ["OCD002"], "HEIG": [3.8]},
        geometry=[Polygon([(100, 100), (300, 100), (300, 120), (100, 120)])],
        crs="EPSG:5186",
    ).to_file(d / "N3A_A0090000.shp", encoding="utf-8")
    return d


def test_read_decks_classifies_kinds(shp_dir):
    """레이어코드 → 종류. A0090000은 `구분`으로 고가/지하를 가른다."""
    got = {d["kind"]: d for d in read_decks(shp_dir)}
    assert set(got) == {"bridge", "tunnel", "underpass"}
    assert got["tunnel"]["h"] == 4.5          # HEIG = 통과높이(데크고 아님)
    assert got["bridge"]["h"] is None         # 교량엔 높이 필드가 없다
    assert got["bridge"]["name"] == "시험교"


def test_bake_does_not_clip_and_replicates(shp_dir, tmp_path, monkeypatch):
    """타일 경계를 넘는 교량은 **양쪽 타일에 통째로** 들어간다(같은 전역 id)."""
    monkeypatch.setattr("src.config.GEO_STORE", tmp_path)
    out = tmp_path / "decks_t.geojson"
    res = bake_decks_tiled(shp_dir, out, "시험지역", tile_km=2.0)
    assert res["decks"] == 3
    assert res["placed"] > res["decks"]       # 경계를 넘는 교량이 복제됨

    tiles = sorted(tmp_path.glob("decks_t_r*c*.geojson"))
    holding = []
    for t in tiles:
        for f in json.loads(t.read_text(encoding="utf-8"))["features"]:
            if f["properties"]["k"] != "bridge":
                continue
            g = Polygon(f["geometry"]["coordinates"][0])
            holding.append((t.name, f["properties"]["i"], g.area))
    assert len(holding) == 2                  # 두 타일에 들어감
    ids = {h[1] for h in holding}
    assert len(ids) == 1                      # 같은 전역 id
    for _n, _i, area in holding:
        assert area == pytest.approx(BRIDGE.area)   # **온전한** 폴리곤


def test_clip_decks_dedupes_replicas(shp_dir, tmp_path, monkeypatch):
    """런타임은 복제본을 전역 id로 한 번만 읽는다."""
    monkeypatch.setattr("src.config.GEO_STORE", tmp_path)
    out = tmp_path / "decks_t.geojson"
    bake_decks_tiled(shp_dir, out, "시험지역", tile_km=2.0)
    tiles = [str(p) for p in sorted(tmp_path.glob("decks_t_r*c*.geojson"))]

    feats = clip_decks(tiles, (1500, 900, 3000, 1100), (0.0, 0.0))
    bridges = [f for f in feats if f.kind == "bridge"]
    assert len(bridges) == 1
    assert _poly(bridges[0].rings).area == pytest.approx(BRIDGE.area)


def test_manifest_records_tiles(shp_dir, tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.GEO_STORE", tmp_path)
    bake_decks_tiled(shp_dir, tmp_path / "decks_t.geojson", "시험지역", tile_km=2.0)
    man = json.loads((tmp_path / "deck_manifest.json").read_text(encoding="utf-8"))
    assert man and all(e["region"] == "시험지역" for e in man)
    assert all(len(e["bounds_4326"]) == 4 and e["decks"] > 0 for e in man)


def test_tiny_fragments_are_dropped(tmp_path):
    """5m 격자에서 의미 없는 조각은 버린다(잡음 방지)."""
    d = tmp_path / "shp"
    d.mkdir()
    gpd.GeoDataFrame(
        {"KIND": ["BRK001", "BRK001"]},
        geometry=[box(0, 0, 1, 1), BRIDGE],      # 1m² 조각 + 정상 교량
        crs="EPSG:5186",
    ).to_file(d / "N3A_A0070000.shp", encoding="utf-8")
    assert len(read_decks(d)) == 1
