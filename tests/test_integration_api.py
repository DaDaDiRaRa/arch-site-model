"""실 API 통합테스트 — VWORLD_TEST_KEY/VWORLD_KEY 있을 때만 실행.

오프라인 단위테스트와 분리. 키 없으면 skip.
실행: 키를 .env 또는 환경변수에 설정 후 `pytest tests/test_integration_api.py -v`
"""

import pytest

from src import config
from src.geo.geocode import geocode
from src.site_check import check_site_data

pytestmark = pytest.mark.skipif(
    not config.VWORLD_KEY, reason="VWORLD_TEST_KEY/VWORLD_KEY 미설정 → 통합테스트 skip"
)

ADDRESS = "대전광역시 서구 괴정동 358"


def test_geocode_real():
    out = geocode(ADDRESS)
    # 사양서 §3.1 실측 근방.
    assert out["lon"] == pytest.approx(127.371, abs=0.01)
    assert out["lat"] == pytest.approx(36.340, abs=0.01)


def test_check_site_data_real():
    out = check_site_data(ADDRESS, radius_m=250)
    assert out["ok"] is True
    assert out["buildings"]["count"] > 0
    assert out["buildings"]["with_floors"] <= out["buildings"]["count"]
    # 리포트 필수 키 존재.
    for k in ("coord", "bbox", "buildings", "cadastral", "terrain", "warnings"):
        assert k in out
    print("\n실측 리포트:", out)
