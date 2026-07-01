"""generate 파이프라인 — client 주입 + geocode 몽키패치 (오프라인)."""

import src.pipeline as pl
from src.geo.vworld import DATASET_CADASTRAL
from src.pipeline import generate
from tests.conftest import load_fixture


class FakeClient:
    def __init__(self, features):
        self._features = features

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        return self._features

    def count(self, dataset, bbox):
        return len(self._features)


class FakeClientMulti:
    """건물/지적 데이터셋별로 다른 피처를 반환하는 가짜 클라이언트."""

    def __init__(self, building_features, cadastral_features):
        self._building = building_features
        self._cadastral = cadastral_features

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        if dataset == DATASET_CADASTRAL:
            return self._cadastral
        return self._building

    def count(self, dataset, bbox):
        if dataset == DATASET_CADASTRAL:
            return len(self._cadastral)
        return len(self._building)


def _daejeon_features():
    return load_fixture("buildings_daejeon.json")["response"]["result"][
        "featureCollection"
    ]["features"]


def _cadastral_features():
    return load_fixture("cadastral_daejeon.json")["response"]["result"][
        "featureCollection"
    ]["features"]


# ---------------------------------------------------------------------------
# 기존 테스트
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Phase 5: provenance 완성
# ---------------------------------------------------------------------------

def test_provenance_fields(monkeypatch):
    """provenance에 radius_m, missing_floors_policy, fetched_at 포함."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        radius_m=150,
        missing_floors_policy="flag",
        client=FakeClient(_daejeon_features()),
    )
    assert out["provenance"]["radius_m"] == 150
    assert out["provenance"]["missing_floors_policy"] == "flag"
    assert "fetched_at" in out["provenance"]
    assert "T" in out["provenance"]["fetched_at"]   # ISO 8601


def test_provenance_cadastral_src(monkeypatch):
    """지적 레이어 활성화 시 provenance에 cadastral_src 포함."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        layers={"buildings": True, "cadastral": True},
        client=FakeClientMulti(_daejeon_features(), _cadastral_features()),
    )
    assert "cadastral_src" in out["provenance"]
    assert "LP_PA_CBND_BUBUN" in out["provenance"]["cadastral_src"]


def test_provenance_setback_stub(monkeypatch):
    """setback=True 시 provenance에 stub 표기 + warnings 포함."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        setback=True,
        client=FakeClient(_daejeon_features()),
    )
    assert out["provenance"].get("setback_analysis") == "stub"
    assert any("setback" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# Phase 5: missing_floors_policy — 3개 정책 동작 검증
# ---------------------------------------------------------------------------

def test_missing_policy_default(monkeypatch):
    """policy=default: 누락 건물 포함(층수 1 fallback), flagged=False."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        missing_floors_policy="default",
        client=FakeClient(_daejeon_features()),
    )
    # 픽스처: 4건물, with_floors=2, missing=2 → 4개 모두 생성
    assert out["stats"]["solids"] == 4
    assert out["stats"]["flagged"] == 0
    assert any("기본 1층" in w for w in out["warnings"])


def test_missing_policy_skip(monkeypatch):
    """policy=skip: 층수 누락 건물 제외 → solids 감소."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        missing_floors_policy="skip",
        client=FakeClient(_daejeon_features()),
    )
    # 픽스처: 층수 유효 2개(4층/10층), 누락 2개(0/null) → skip 후 2개
    assert out["stats"]["solids"] == 2
    assert out["stats"]["flagged"] == 0
    assert any("제외됨" in w for w in out["warnings"])


def test_missing_policy_flag(monkeypatch):
    """policy=flag: 누락 건물 포함하되 flagged=True + skp 접미사 포함."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        missing_floors_policy="flag",
        client=FakeClient(_daejeon_features()),
    )
    assert out["stats"]["solids"] == 4
    assert out["stats"]["flagged"] == 2   # 누락 2개
    assert "[층수미확인]" in out["outputs"]["skp"]["code"]
    assert any("buildings_unverified" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# Phase 5: 지적 레이어
# ---------------------------------------------------------------------------

def test_cadastral_layer(monkeypatch):
    """layers.cadastral=True → stats.cadastral_parcels > 0."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        layers={"buildings": True, "cadastral": True},
        client=FakeClientMulti(_daejeon_features(), _cadastral_features()),
    )
    assert out["ok"] is True
    assert out["stats"]["cadastral_parcels"] == 2
    assert out["outputs"]["skp"]["cadastral_parcels"] == 2
    assert "CADASTRAL" in out["outputs"]["skp"]["code"]


def test_cadastral_skp_code_has_pnu(monkeypatch):
    """지적 활성화 시 skp 코드에 pnu 값이 포함되어야 한다."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        layers={"buildings": True, "cadastral": True},
        client=FakeClientMulti(_daejeon_features(), _cadastral_features()),
    )
    code = out["outputs"]["skp"]["code"]
    assert "3017010800103580000" in code


def test_cadastral_disabled_by_default(monkeypatch):
    """layers.cadastral 미지정 시 CADASTRAL 블록 생성되지 않음."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        client=FakeClient(_daejeon_features()),
    )
    assert "CADASTRAL" not in out["outputs"]["skp"]["code"]
    assert out["stats"]["cadastral_parcels"] == 0


def test_skp_output_cadastral_parcels_key(monkeypatch):
    """outputs.skp에 cadastral_parcels 키가 항상 존재한다."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        client=FakeClient(_daejeon_features()),
    )
    assert "cadastral_parcels" in out["outputs"]["skp"]
