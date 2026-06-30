"""check_site_data 핵심 로직 — 취득 리포트 + warnings (오프라인, client 주입)."""

import src.site_check as sc
from src.site_check import check_site_data, count_with_floors
from tests.conftest import load_fixture


class FakeClient:
    """VWorldClient 대체 — 건물/지적 픽스처를 돌려줌."""

    def __init__(self, buildings, cadastral_count):
        self._buildings = buildings
        self._cadastral_count = cadastral_count

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        return self._buildings

    def count(self, dataset, bbox):
        return self._cadastral_count


def _buildings():
    return load_fixture("buildings_daejeon.json")["response"]["result"][
        "featureCollection"
    ]["features"]


def test_count_with_floors():
    # 픽스처: gro_flo_co 4, 10, 0, null → 유효 2개.
    assert count_with_floors(_buildings()) == 2


def test_check_site_data_report(monkeypatch):
    monkeypatch.setattr(
        sc, "geocode", lambda addr: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    # 지형 비축 있음으로 가정.
    monkeypatch.setattr(
        sc, "find_tile", lambda bbox: {"file": "dem_x.tif", "source": "DEM"}
    )
    client = FakeClient(_buildings(), cadastral_count=12)

    out = check_site_data("대전광역시 서구 괴정동 358", radius_m=250, client=client)

    assert out["ok"] is True
    assert out["coord"]["lon"] == 127.37098
    assert len(out["bbox"]) == 4
    assert out["buildings"] == {"available": True, "count": 4, "with_floors": 2}
    assert out["cadastral"] == {"available": True, "count": 12}
    assert out["terrain"]["available"] is True
    assert out["terrain"]["tile"] == "dem_x.tif"
    # 층수 누락 2개(0, null)가 warnings에 반영.
    assert any("gro_flo_co" in w and "2개" in w for w in out["warnings"])


def test_check_site_data_no_terrain_warns(monkeypatch):
    monkeypatch.setattr(
        sc, "geocode", lambda addr: {"lon": 127.37, "lat": 36.34, "crs": "EPSG:4326"}
    )
    monkeypatch.setattr(sc, "find_tile", lambda bbox: None)
    client = FakeClient(_buildings(), cadastral_count=0)

    out = check_site_data("대전광역시 서구 괴정동 358", client=client)
    assert out["terrain"]["available"] is False
    assert out["cadastral"]["available"] is False
    assert any("지형 비축 없음" in w for w in out["warnings"])


def test_check_site_data_geocode_fail(monkeypatch):
    from src.geo.geocode import GeocodeError

    def boom(addr):
        raise GeocodeError("주소 변환 실패")

    monkeypatch.setattr(sc, "geocode", boom)
    out = check_site_data("이상한 주소")
    assert out["ok"] is False
    assert "error" in out
