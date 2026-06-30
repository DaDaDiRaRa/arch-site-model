"""bbox 생성 + geomFilter 문자열 검증."""

import math

import pytest

from src.geo.bbox import bbox_from_point, to_geomfilter_box

DAEJEON_LON = 127.37098
DAEJEON_LAT = 36.33998


def test_bbox_centered_on_point():
    minx, miny, maxx, maxy = bbox_from_point(DAEJEON_LON, DAEJEON_LAT, 250)
    # 중심은 입력 점.
    assert math.isclose((minx + maxx) / 2, DAEJEON_LON, abs_tol=1e-9)
    assert math.isclose((miny + maxy) / 2, DAEJEON_LAT, abs_tol=1e-9)
    assert minx < DAEJEON_LON < maxx
    assert miny < DAEJEON_LAT < maxy


def test_bbox_halfwidth_matches_radius():
    radius = 250
    minx, miny, maxx, maxy = bbox_from_point(DAEJEON_LON, DAEJEON_LAT, radius)
    # 위도 반폭 ≈ radius / 111320 도.
    dlat = (maxy - miny) / 2
    assert math.isclose(dlat, radius / 111_320.0, rel_tol=1e-6)
    # 경도 반폭은 위도보다 커야(cos 보정).
    dlon = (maxx - minx) / 2
    assert dlon > dlat


def test_bbox_rejects_nonpositive_radius():
    with pytest.raises(ValueError):
        bbox_from_point(DAEJEON_LON, DAEJEON_LAT, 0)


def test_geomfilter_box_format():
    s = to_geomfilter_box((1.0, 2.0, 3.0, 4.0))
    assert s == "BOX(1.0,2.0,3.0,4.0)"
