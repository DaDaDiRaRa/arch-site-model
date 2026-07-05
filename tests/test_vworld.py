"""VWorldClient — 파싱·페이지네이션·count·오류 (오프라인)."""

import copy

import pytest

from src.geo import vworld as vw
from src.geo.vworld import DATASET_BUILDING, VWorldClient, VWorldError
from tests.conftest import load_fixture, make_fake_get

BBOX = (127.368, 36.337, 127.373, 36.342)


def test_get_features_parses(monkeypatch):
    monkeypatch.setattr(vw.requests, "get", make_fake_get(load_fixture("buildings_daejeon.json")))
    client = VWorldClient("DUMMY")
    feats = client.get_features(DATASET_BUILDING, BBOX, geometry=False)
    assert len(feats) == 4
    assert feats[0]["properties"]["gro_flo_co"] == "4"


def test_get_features_omits_empty_domain(monkeypatch):
    fake = make_fake_get(load_fixture("buildings_daejeon.json"))
    monkeypatch.setattr(vw.requests, "get", fake)
    VWorldClient("DUMMY", domain="").get_features(DATASET_BUILDING, BBOX)
    assert "domain" not in fake.calls[0]["params"]
    assert fake.calls[0]["params"]["geomFilter"].startswith("BOX(")


def test_get_features_paginates(monkeypatch):
    """첫 페이지가 size만큼 꽉 차면 다음 페이지를 더 요청한다."""
    base = load_fixture("buildings_daejeon.json")
    # 1페이지: size=2 가득(2개) → 더 있음. 2페이지: 1개 → 종료.
    page1 = copy.deepcopy(base)
    page1["response"]["result"]["featureCollection"]["features"] = (
        base["response"]["result"]["featureCollection"]["features"][:2]
    )
    page2 = copy.deepcopy(base)
    page2["response"]["result"]["featureCollection"]["features"] = (
        base["response"]["result"]["featureCollection"]["features"][2:3]
    )
    fake = make_fake_get([page1, page2])
    monkeypatch.setattr(vw.requests, "get", fake)
    feats = VWorldClient("DUMMY").get_features(DATASET_BUILDING, BBOX, size=2)
    assert len(feats) == 3
    assert len(fake.calls) == 2
    assert fake.calls[1]["params"]["page"] == "2"


def test_count_uses_record_total(monkeypatch):
    monkeypatch.setattr(vw.requests, "get", make_fake_get(load_fixture("cadastral_count_daejeon.json")))
    n = VWorldClient("DUMMY").count("LP_PA_CBND_BUBUN", BBOX)
    assert n == 12


def test_not_found_is_empty(monkeypatch):
    monkeypatch.setattr(vw.requests, "get", make_fake_get(load_fixture("notfound.json")))
    client = VWorldClient("DUMMY")
    assert client.get_features(DATASET_BUILDING, BBOX) == []
    assert client.count(DATASET_BUILDING, BBOX) == 0


def test_error_status_raises(monkeypatch):
    # code 없는 일반 ERROR → 재시도 대상 아님 → 즉시 raise
    payload = {"response": {"status": "ERROR", "error": {"text": "잘못된 요청"}}}
    monkeypatch.setattr(vw.requests, "get", make_fake_get(payload))
    with pytest.raises(VWorldError):
        VWorldClient("DUMMY").get_features(DATASET_BUILDING, BBOX)


def test_transient_incorrect_key_retries_then_succeeds(monkeypatch):
    """VWorld data API의 간헐적 INCORRECT_KEY → 재시도 후 성공."""
    err = {"response": {"status": "ERROR",
                        "error": {"code": "INCORRECT_KEY", "text": "인증키 정보가 올바르지 않습니다."}}}
    ok = load_fixture("buildings_daejeon.json")
    fake = make_fake_get([err, err, ok])   # 두 번 실패 후 성공
    monkeypatch.setattr(vw.requests, "get", fake)
    client = VWorldClient("DUMMY", retry_wait=0)   # 대기 없이 즉시 재시도
    feats = client.get_features(DATASET_BUILDING, BBOX, geometry=False)
    assert len(feats) == 4
    assert len(fake.calls) == 3                    # 재시도 2회 + 성공 1회


def test_persistent_incorrect_key_exhausts_and_raises(monkeypatch):
    """INCORRECT_KEY가 계속되면 재시도 소진 후 raise (실제 잘못된 키 등)."""
    err = {"response": {"status": "ERROR",
                        "error": {"code": "INCORRECT_KEY", "text": "인증키 정보가 올바르지 않습니다."}}}
    fake = make_fake_get(err)   # 매번 동일 오류
    monkeypatch.setattr(vw.requests, "get", fake)
    client = VWorldClient("DUMMY", retries=3, retry_wait=0)
    with pytest.raises(VWorldError):
        client.count(DATASET_BUILDING, BBOX)
    assert len(fake.calls) == 3   # 정확히 retries 회 시도


def test_empty_key_raises():
    with pytest.raises(VWorldError):
        VWorldClient("")


# ---------------------------------------------------------------------------
# bbox 분할 (VWorld 10km²/쿼리 한도 우회 → 반경 2km+ 지원)
# ---------------------------------------------------------------------------

def test_split_bbox_single_for_small():
    from src.geo.vworld import _split_bbox
    assert len(_split_bbox(BBOX)) == 1   # 0.25km² → 분할 안 함(기존 동작)


def test_split_bbox_multiple_bounded_and_covers():
    from src.geo.vworld import _SINGLE_MAX_KM2, _bbox_dims_km, _split_bbox
    big = (127.0, 37.48, 127.05, 37.52)   # ~4.4×4.5km ≈ 20km²
    boxes = _split_bbox(big)
    assert len(boxes) == 4
    for b in boxes:                       # 각 서브박스 한도 이내
        w, h = _bbox_dims_km(b)
        assert w * h <= _SINGLE_MAX_KM2
    assert min(b[0] for b in boxes) == pytest.approx(127.0)   # 합집합이 원본 커버
    assert max(b[2] for b in boxes) == pytest.approx(127.05)
    assert min(b[1] for b in boxes) == pytest.approx(37.48)
    assert max(b[3] for b in boxes) == pytest.approx(37.52)


def test_split_bbox_too_large_raises():
    from src.geo.vworld import VWorldError as VE
    from src.geo.vworld import _split_bbox
    with pytest.raises(VE, match="너무 큽"):
        _split_bbox((126.0, 36.5, 127.5, 37.7))   # ~130km 변 → 서브박스 폭주


def test_get_features_splits_merges_dedups(monkeypatch):
    """큰 bbox → 서브박스 분할 조회, 경계 중복 피처(id 동일)는 하나로 병합."""
    def f(i):
        return {"id": f"LT_C_SPBD.{i}", "properties": {"gro_flo_co": "3"},
                "geometry": {"type": "Point", "coordinates": [0, 0]}}

    def r(feats):
        return {"response": {"status": "OK",
                             "result": {"featureCollection": {"features": feats}}}}

    # 4개 서브박스: box0=[1,2], box1=[2,3](2 중복), box2=[4], box3=[]
    fake = make_fake_get([r([f(1), f(2)]), r([f(2), f(3)]), r([f(4)]), r([])])
    monkeypatch.setattr(vw.requests, "get", fake)
    big = (127.0, 37.48, 127.05, 37.52)
    feats = VWorldClient("DUMMY").get_features(DATASET_BUILDING, big)
    assert sorted(x["id"] for x in feats) == [
        "LT_C_SPBD.1", "LT_C_SPBD.2", "LT_C_SPBD.3", "LT_C_SPBD.4"]
    assert len(fake.calls) == 4   # 서브박스당 1콜
