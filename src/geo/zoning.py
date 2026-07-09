"""용도지역(zoning) 조회 — 형제 앱 arch-law-graph 연동 (경계 존중: zoning=법령 클러스터 소유).

arch-law-graph의 `GET /api/zoning?address=` 를 호출해 사이트 용도지역(zone_name)·zone_key·
시도/시군구를 받는다. 실질은 VWorld LT_C_UQ111(용도지역) 래퍼라 raw 공간데이터지만, zone_key가
향후 법령 표준(건폐/용적) 연결에 쓰인다. `config.ZONING_BASE` 미설정/미도달/에러 시 None(조용한
fallback — DEM·도로와 동일 원칙). arch-law-graph 서비스가 떠 있고 그쪽 VWORLD_KEY도 설정돼야 한다.
"""

from __future__ import annotations

from src import config


def _http_get(url: str, params: dict) -> dict | None:
    """requests GET → json dict. 미도달/타임아웃/파싱 실패 시 None."""
    try:
        import requests

        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001 — 네트워크/파싱 실패는 조용히 생략(fallback)
        return None


def lookup_zoning(address: str, base: str | None = None, get=None) -> dict | None:
    """arch-law-graph GET /api/zoning?address= → 용도지역 dict. 미설정/실패/에러 시 None.

    반환(성공): {"zone_name","zone_key","sido","sigungu","address","src"}.
    get: 테스트 주입용 (url, params)->dict|None. 기본은 requests.
    """
    base = (base if base is not None else config.ZONING_BASE).strip()
    if not base or not address:
        return None
    getter = get or _http_get
    data = getter(base.rstrip("/") + "/api/zoning", {"address": address})
    # arch-law-graph는 키 미설정/주소 미발견 시 HTTP 200 + {"error": ...}, 무매칭 시 zone_name None/"".
    if not isinstance(data, dict) or data.get("error") or not data.get("zone_name"):
        return None
    return {
        "zone_name": data.get("zone_name"),
        "zone_key": data.get("zone_key"),
        "sido": data.get("sido"),
        "sigungu": data.get("sigungu"),
        "address": data.get("address"),
        "src": "arch-law-graph /api/zoning",
    }
