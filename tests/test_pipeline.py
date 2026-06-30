"""generate 파이프라인 — client 주입 + geocode 몽키패치 (오프라인)."""

import src.pipeline as pl
from src.pipeline import generate
from tests.conftest import load_fixture


class FakeClient:
    def __init__(self, features):
        self._features = features

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        return self._features


def _daejeon_features():
    return load_fixture("buildings_daejeon.json")["response"]["result"][
        "featureCollection"
    ]["features"]


def test_generate_buildings(monkeypatch):
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        radius_m=250,
        floor_h_m=3.0,
        client=FakeClient(_daejeon_features()),
    )
    assert out["ok"] is True
    # 픽스처: 4개 건물(사각형), 층수 4/10/0/null → with_floors 2, 누락 2.
    assert out["stats"]["buildings"] == 4
    assert out["stats"]["solids"] == 4
    assert out["stats"]["with_floors"] == 2
    assert len(out["stats"]["origin_offset"]) == 2
    assert out["stats"]["origin_offset"][0] > 0   # 5186 원점(복원용) 저장됨
    assert isinstance(out["outputs"]["skp"]["code"], str)
    assert "def extrude_solid" in out["outputs"]["skp"]["code"]
    assert any("gro_flo_co" in w for w in out["warnings"])
    assert out["provenance"]["building_src"] == "VWorld LT_C_SPBD"


def test_generate_no_buildings(monkeypatch):
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.0, "lat": 36.0, "crs": "EPSG:4326"}
    )
    out = generate("빈 곳", client=FakeClient([]))
    assert out["ok"] is False
    assert "건물" in out["error"]


def test_generate_geocode_fail(monkeypatch):
    from src.geo.geocode import GeocodeError

    def boom(a):
        raise GeocodeError("주소 변환 실패")

    monkeypatch.setattr(pl, "geocode", boom)
    out = generate("이상한 주소")
    assert out["ok"] is False
    assert "error" in out
