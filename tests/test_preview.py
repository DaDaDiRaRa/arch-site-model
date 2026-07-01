"""preview_site 단위 테스트 — client 주입 + geocode 몽키패치 (오프라인)."""

import src.preview as pv
from src.preview import preview_site
from tests.conftest import load_fixture


class FakeClient:
    def __init__(self, building_features, cadastral_count=0):
        self._buildings = building_features
        self._cadastral_count = cadastral_count

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        return self._buildings

    def count(self, dataset, bbox):
        return self._cadastral_count


def _daejeon_features():
    return load_fixture("buildings_daejeon.json")["response"]["result"][
        "featureCollection"
    ]["features"]


def _courtyard_feature():
    """중정 있는 합성 피처."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[127.370, 36.340], [127.371, 36.340], [127.371, 36.341],
                 [127.370, 36.341], [127.370, 36.340]],
                [[127.3702, 36.3401], [127.3702, 36.3409], [127.3708, 36.3409],
                 [127.3708, 36.3401], [127.3702, 36.3401]],
            ],
        },
        "properties": {"gro_flo_co": "5", "buld_nm": "중정건물", "bd_mgt_sn": "CY01"},
    }


# ---------------------------------------------------------------------------
# 기본 동작
# ---------------------------------------------------------------------------

def test_preview_ok(monkeypatch):
    """건물 있는 주소 → ok=True + buildings 목록."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"})
    out = preview_site("대전광역시 서구 괴정동 358", client=FakeClient(_daejeon_features()))
    assert out["ok"] is True
    assert len(out["buildings"]) == 4


def test_preview_no_buildings(monkeypatch):
    """건물 없으면 ok=False + warnings."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.0, "lat": 36.0, "crs": "EPSG:4326"})
    out = preview_site("빈 곳", client=FakeClient([]))
    assert out["ok"] is False
    assert any("없습니다" in w for w in out["warnings"])


def test_preview_geocode_fail(monkeypatch):
    """주소 변환 실패 → ok=False + error."""
    from src.geo.geocode import GeocodeError
    monkeypatch.setattr(pv, "geocode", lambda a: (_ for _ in ()).throw(GeocodeError("실패")))
    out = preview_site("이상한 주소", client=FakeClient([]))
    assert out["ok"] is False
    assert "error" in out


# ---------------------------------------------------------------------------
# summary 필드 검증
# ---------------------------------------------------------------------------

def test_summary_fields(monkeypatch):
    """summary에 필수 필드가 모두 포함된다."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"})
    out = preview_site("대전광역시 서구 괴정동 358", client=FakeClient(_daejeon_features(), cadastral_count=3))
    s = out["summary"]
    assert s["buildings"] == 4
    assert s["with_floors"] == 2      # 픽스처: 층수 유효 2개(4층/10층)
    assert s["missing_floors"] == 2   # gro_flo_co=0, null
    assert s["max_floors"] == 10
    assert s["avg_floors"] == pytest.approx(7.0)  # (4+10)/2
    assert s["cadastral_parcels"] == 3
    assert "terrain" in s


def test_summary_no_floors(monkeypatch):
    """층수 없는 건물만 있으면 max_floors=None, avg_floors=None."""
    feat = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [
            [[127.37, 36.34], [127.371, 36.34], [127.371, 36.341], [127.37, 36.341], [127.37, 36.34]]
        ]},
        "properties": {"gro_flo_co": None, "buld_nm": "미상"},
    }
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37, "lat": 36.34, "crs": "EPSG:4326"})
    out = preview_site("test", client=FakeClient([feat]))
    assert out["summary"]["max_floors"] is None
    assert out["summary"]["avg_floors"] is None


# ---------------------------------------------------------------------------
# 건물 항목 필드 검증
# ---------------------------------------------------------------------------

def test_building_entry_has_required_keys(monkeypatch):
    """각 건물 항목에 필수 키가 있다."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"})
    out = preview_site("대전광역시 서구 괴정동 358", client=FakeClient(_daejeon_features()))
    for b in out["buildings"]:
        assert "name" in b
        assert "floors" in b
        assert "height_m" in b
        assert "footprint_area_m2" in b
        assert "has_courtyard" in b


def test_building_height_derived_from_floors(monkeypatch):
    """height_m = floors × floor_height_m."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"})
    out = preview_site("test", floor_height_m=3.5, client=FakeClient(_daejeon_features()))
    for b in out["buildings"]:
        if b["floors"] is not None:
            assert b["height_m"] == pytest.approx(b["floors"] * 3.5, abs=0.1)


def test_building_missing_floors_none(monkeypatch):
    """층수 누락 건물 → floors=None, height_m=None."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"})
    out = preview_site("test", client=FakeClient(_daejeon_features()))
    missing = [b for b in out["buildings"] if b["floors"] is None]
    assert len(missing) == 2
    assert all(b["height_m"] is None for b in missing)


def test_building_footprint_area_positive(monkeypatch):
    """footprint_area_m2 는 양수여야 한다."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"})
    out = preview_site("test", client=FakeClient(_daejeon_features()))
    for b in out["buildings"]:
        if b["footprint_area_m2"] is not None:
            assert b["footprint_area_m2"] > 0


# ---------------------------------------------------------------------------
# 중정 감지
# ---------------------------------------------------------------------------

def test_courtyard_detected(monkeypatch):
    """내부 링 있는 건물 → has_courtyard=True + summary.courtyards=1."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.370, "lat": 36.340, "crs": "EPSG:4326"})
    out = preview_site("test", client=FakeClient([_courtyard_feature()]))
    assert out["buildings"][0]["has_courtyard"] is True
    assert out["summary"]["courtyards"] == 1
    assert any("중정" in w for w in out["warnings"])


def test_no_courtyard(monkeypatch):
    """일반 건물 → has_courtyard=False."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"})
    out = preview_site("test", client=FakeClient(_daejeon_features()))
    assert all(not b["has_courtyard"] for b in out["buildings"])
    assert out["summary"]["courtyards"] == 0


# ---------------------------------------------------------------------------
# 누락 층수 경고
# ---------------------------------------------------------------------------

def test_missing_floors_warning(monkeypatch):
    """층수 누락 건물이 있으면 warnings에 포함."""
    monkeypatch.setattr(pv, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"})
    out = preview_site("test", client=FakeClient(_daejeon_features()))
    assert any("층수 미확인" in w for w in out["warnings"])


import pytest
