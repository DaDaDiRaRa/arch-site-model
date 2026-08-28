"""road_bake 합성 노면(경계 폴리곤 없는 도로를 실측 도로폭으로 버퍼) — SHP 없이 합성 지오메트리로."""

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

import json

import geopandas as gpd
import pytest

from src.terrain.road_bake import (
    read_sidewalks,
    _resolve_width,
    bake_roads_tiled,
    read_road_centerlines,
    synthesize_gap_roads,
)


def test_resolve_width_prefers_surveyed():
    assert _resolve_width(20.8, "미분류") == 20.8   # 실측 우선
    assert _resolve_width(None, "소로") == 6.0        # 도로구분 기본폭
    assert _resolve_width(0, "대로") == 25.0          # 0은 무효 → 기본폭
    assert _resolve_width(None, None) == 4.0          # 고정 폴백


def test_synthesize_fills_only_gap_outside_polygon():
    # A0010000 도로경계: x∈[0,10]. 중심선: x∈[0,30] → 10~30 구간(폴리곤 밖)만 합성해야 한다.
    poly = Polygon([(0, -5), (10, -5), (10, 5), (0, 5)])
    cl = LineString([(0, 0), (30, 0)])
    synth = synthesize_gap_roads([poly], [(cl, 4.0, "소로", 1)], min_area_m2=1.0)
    assert synth
    u = unary_union(synth)
    # 폴리곤 밖(x>10)을 실제로 채운다.
    assert u.difference(poly.buffer(0.01)).area > 10
    # 실측 폴리곤 영역(x<9)은 합성이 덮지 않는다(중복 제거).
    assert u.intersection(Polygon([(0, -5), (9, -5), (9, 5), (0, 5)])).area < 1.0
    # 폭 4m 리본 → 횡폭 ~4m.
    minx, miny, maxx, maxy = u.bounds
    assert 3.5 <= (maxy - miny) <= 4.6


def test_synthesize_no_polygons_buffers_whole_line():
    cl = LineString([(0, 0), (20, 0)])
    synth = synthesize_gap_roads([], [(cl, 6.0, "소로", 1)], min_area_m2=1.0)
    assert synth
    area = unary_union(synth).area
    assert 90 < area < 160  # 20m × 6m ≈ 120 (끝 라운드캡 포함)


def test_synthesize_skips_fully_covered_centerline():
    # 중심선이 폴리곤 안에 완전히 있으면(경계 폴리곤이 이미 덮음) 합성 없음.
    poly = Polygon([(0, -10), (30, -10), (30, 10), (0, 10)])
    cl = LineString([(5, 0), (25, 0)])
    assert synthesize_gap_roads([poly], [(cl, 4.0, "소로", 1)], min_area_m2=1.0) == []


def test_synthesize_empty_centerlines():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert synthesize_gap_roads([poly], [], min_area_m2=1.0) == []


# ---------------------------------------------------------------------------
# 연속수치지형도 호환 (영문 필드) + 스트리밍 타일 베이크
# ---------------------------------------------------------------------------

def _write_road_shp(tmp_dir, *, english: bool):
    """도로경계(면)·중심선(선)·보도(면) 합성 SHP. english=True면 연속수치지형도식 영문 필드."""
    road = Polygon([(0, 0), (200, 0), (200, 12), (0, 12)])          # 본선 노면
    gpd.GeoDataFrame({"geometry": [road]}, crs="EPSG:5186").to_file(
        tmp_dir / "N3A_A0010000.shp")
    sw = Polygon([(0, 12), (200, 12), (200, 15), (0, 15)])           # 보도
    gpd.GeoDataFrame({"geometry": [sw]}, crs="EPSG:5186").to_file(
        tmp_dir / "N3A_A0033320.shp")
    cl = LineString([(0, 6), (200, 6)])                              # 중심선
    gap = LineString([(100, 20), (100, 120)])                        # 경계 없는 골목
    # 한글 '도로구분'(12바이트)은 DBF 필드명 10바이트 제한에 잘려 저장이 안 된다 → 한글본에선 생략.
    cols = ({"RVWD": [12.0, 6.0], "RDLN": [4, 2], "RDDV": ["RDD002", "RDD009"]} if english
            else {"도로폭": [12.0, 6.0], "차로수": [4, 2]})
    gpd.GeoDataFrame({**cols, "geometry": [cl, gap]}, crs="EPSG:5186").to_file(
        tmp_dir / "N3L_A0020000.shp")


def test_read_centerlines_accepts_english_fields(tmp_path):
    """연속수치지형도는 도로폭/차로수/도로구분이 RVWD/RDLN/RDDV 영문 코드로 온다."""
    _write_road_shp(tmp_path, english=True)
    out = read_road_centerlines(tmp_path)

    assert len(out) == 2
    widths = sorted(w for _g, w, _c, _n in out)
    lanes = sorted(n for _g, _w, _c, n in out)
    assert widths == [6.0, 12.0]      # RVWD가 실측폭으로 잡혀야 갭채움이 산다
    assert lanes == [2, 4]            # RDLN이 잡혀야 다차선 마킹이 산다


def test_read_centerlines_korean_and_english_agree(tmp_path):
    """한글 필드본과 영문 필드본이 같은 값을 낸다(도엽별 ↔ 연속 호환)."""
    ko, en = tmp_path / "ko", tmp_path / "en"
    ko.mkdir(); en.mkdir()
    _write_road_shp(ko, english=False)
    _write_road_shp(en, english=True)

    a = [(w, n) for _g, w, _c, n in read_road_centerlines(ko)]
    b = [(w, n) for _g, w, _c, n in read_road_centerlines(en)]
    assert sorted(a) == sorted(b)


def test_bake_roads_tiled_stream_matches_full_load(tmp_path, monkeypatch):
    """stream=True(타일마다 읽기)와 전량 적재가 **같은 타일 GeoJSON**을 낸다."""
    from src import config
    monkeypatch.setattr(config, "GEO_STORE", tmp_path / "store")  # 실 manifest 오염 방지
    (tmp_path / "store").mkdir()
    src = tmp_path / "src"; src.mkdir()
    _write_road_shp(src, english=True)
    full_dir = tmp_path / "full"; full_dir.mkdir()
    strm_dir = tmp_path / "strm"; strm_dir.mkdir()

    a = bake_roads_tiled(src, full_dir / "roads_t.geojson", region="t-full", tile_km=0.1)
    b = bake_roads_tiled(src, strm_dir / "roads_t.geojson", region="t-strm", tile_km=0.1,
                         stream=True)

    assert a["tiles"] == b["tiles"] > 1
    assert a["polygons"] == b["polygons"]
    assert a["synthetic"] == b["synthetic"]
    assert a["files"] == b["files"]
    for name in a["files"]:
        fa = json.loads((full_dir / name).read_text(encoding="utf-8"))
        fb = json.loads((strm_dir / name).read_text(encoding="utf-8"))
        assert fa == fb


def test_sidewalk_lines_are_buffered_by_surveyed_width(tmp_path):
    """연속수치지형도는 보도가 선(N3L)+실측폭(WIDT) → 실측 폭으로 버퍼링해 면을 만든다."""
    line = LineString([(0.0, 0.0), (100.0, 0.0)])
    gpd.GeoDataFrame({"WIDT": [3.0], "geometry": [line]}, crs="EPSG:5186").to_file(
        tmp_path / "N3L_A0033320.shp")

    polys = read_sidewalks(tmp_path)

    assert len(polys) == 1
    assert polys[0].area == pytest.approx(100.0 * 3.0, rel=0.02)   # 길이 x 실측폭


def test_sidewalk_polygons_win_over_lines(tmp_path):
    """면(N3A)이 있으면 그대로 쓰고 선은 무시한다(도엽별 소스 동작 유지)."""
    gpd.GeoDataFrame({"geometry": [Polygon([(0, 0), (10, 0), (10, 4), (0, 4)])]},
                     crs="EPSG:5186").to_file(tmp_path / "N3A_A0033320.shp")
    gpd.GeoDataFrame({"WIDT": [3.0], "geometry": [LineString([(0, 0), (100, 0)])]},
                     crs="EPSG:5186").to_file(tmp_path / "N3L_A0033320.shp")

    polys = read_sidewalks(tmp_path)

    assert len(polys) == 1
    assert polys[0].area == pytest.approx(40.0)                    # 선(300m²)이 아니라 면(40m²)
