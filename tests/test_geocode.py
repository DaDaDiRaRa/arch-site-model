"""주소 → 좌표 (오프라인, requests.get 몽키패치)."""

import pytest

from src.geo import geocode as geo
from src.geo.geocode import GeocodeError, clean_address, geocode
from tests.conftest import load_fixture, make_fake_get


def test_clean_address_strips_parens():
    assert clean_address("대전광역시 서구 괴정동 358 (일원)") == "대전광역시 서구 괴정동 358"
    assert clean_address("  서울시   강남구  역삼동 1  ") == "서울시 강남구 역삼동 1"


def test_geocode_parses_point(monkeypatch):
    monkeypatch.setattr(geo.requests, "get", make_fake_get(load_fixture("geocode_daejeon.json")))
    out = geocode("대전광역시 서구 괴정동 358", key="DUMMY")
    assert out["crs"] == "EPSG:4326"
    assert out["lon"] == pytest.approx(127.37098)
    assert out["lat"] == pytest.approx(36.33998)


def test_geocode_omits_empty_domain(monkeypatch):
    fake = make_fake_get(load_fixture("geocode_daejeon.json"))
    monkeypatch.setattr(geo.requests, "get", fake)
    geocode("대전광역시 서구 괴정동 358", key="DUMMY", domain="")
    # 빈 domain은 파라미터에서 제외돼야 함("기타" 개발키 대응).
    assert "domain" not in fake.calls[0]["params"]
    assert fake.calls[0]["params"]["type"] == "PARCEL"


def test_geocode_not_found_raises(monkeypatch):
    payload = {"response": {"status": "NOT_FOUND", "error": {"text": "no result"}}}
    monkeypatch.setattr(geo.requests, "get", make_fake_get(payload))
    with pytest.raises(GeocodeError):
        geocode("없는 주소", key="DUMMY")


def test_geocode_missing_key_raises():
    with pytest.raises(GeocodeError):
        geocode("아무주소", key="")
