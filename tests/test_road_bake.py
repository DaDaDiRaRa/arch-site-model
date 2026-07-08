"""road_bake 합성 노면(경계 폴리곤 없는 도로를 실측 도로폭으로 버퍼) — SHP 없이 합성 지오메트리로."""

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from src.terrain.road_bake import _resolve_width, synthesize_gap_roads


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
