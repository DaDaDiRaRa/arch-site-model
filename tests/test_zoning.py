"""용도지역 조회(zoning.py) — arch-law-graph 연동, HTTP 주입으로 네트워크 없이 검증."""

from src.geo.zoning import lookup_zoning

_OK = {
    "x": 127.02, "y": 37.5, "sido": "서울특별시", "sigungu": "강남구",
    "address": "서울특별시 강남구 …", "zone_name": "제2종일반주거지역", "zone_key": "urban_res_2",
}


def test_success_returns_zone():
    tr = lookup_zoning("서울 강남 …", base="http://x", get=lambda u, p: _OK)
    assert tr["zone_name"] == "제2종일반주거지역"
    assert tr["zone_key"] == "urban_res_2"
    assert tr["sigungu"] == "강남구"
    assert tr["src"] == "arch-law-graph /api/zoning"


def test_base_unset_returns_none():
    assert lookup_zoning("서울 강남", base="", get=lambda u, p: _OK) is None


def test_error_payload_returns_none():
    assert lookup_zoning("x", base="http://x", get=lambda u, p: {"error": "VWORLD_API_KEY 미설정"}) is None


def test_empty_zone_returns_none():
    bad = dict(_OK)
    bad["zone_name"] = ""
    assert lookup_zoning("x", base="http://x", get=lambda u, p: bad) is None


def test_fetch_failure_returns_none():
    assert lookup_zoning("x", base="http://x", get=lambda u, p: None) is None


def test_passes_address_and_builds_url():
    seen = {}

    def spy(url, params):
        seen["url"] = url
        seen["params"] = params
        return _OK

    lookup_zoning("서울 강남 123", base="http://host:8000/", get=spy)
    assert seen["url"] == "http://host:8000/api/zoning"
    assert seen["params"] == {"address": "서울 강남 123"}
