"""주소 → 좌표 (VWorld geocoder). 사양서 §3.1.

GET https://api.vworld.kr/req/address
  service=address request=getcoord version=2.0
  crs=epsg:4326 type=PARCEL format=json key=<KEY> [domain=<DOMAIN>]
→ response.result.point.{x:경도, y:위도}

주의(사양서 §3.1): 주소는 깔끔한 지번만. "일원(...)" 같은 설명 문구는 제거한다.
"""

import re

import requests

from src import config

_ADDRESS_URL = "https://api.vworld.kr/req/address"
_TIMEOUT = 10


class GeocodeError(Exception):
    """주소를 좌표로 변환하지 못함 (미발견·API 오류 등)."""


def clean_address(address: str) -> str:
    """지번 주소에서 괄호 설명·여분 공백을 제거.

    예: "대전광역시 서구 괴정동 358 (일원)" → "대전광역시 서구 괴정동 358"
    """
    s = re.sub(r"\(.*?\)", " ", address)   # 괄호 설명 제거
    s = re.sub(r"\s+", " ", s)             # 공백 정규화
    return s.strip()


def geocode(address: str, key: str | None = None, domain: str | None = None) -> dict:
    """지번 주소 → {"lon", "lat", "crs": "EPSG:4326"}.

    실패 시 GeocodeError. key/domain 미지정 시 config 값을 사용한다.
    domain은 빈 값이면 파라미터에서 제외한다("기타" 개발키 대응).
    """
    key = key if key is not None else config.VWORLD_KEY
    domain = domain if domain is not None else config.VWORLD_DOMAIN
    if not key:
        raise GeocodeError("VWorld 키가 없습니다 (.env의 VWORLD_TEST_KEY/VWORLD_KEY).")

    cleaned = clean_address(address)
    params = {
        "service": "address",
        "request": "getcoord",
        "version": "2.0",
        "crs": "epsg:4326",
        "type": "PARCEL",
        "format": "json",
        "address": cleaned,
        "key": key,
    }
    if domain:
        params["domain"] = domain

    try:
        resp = requests.get(_ADDRESS_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise GeocodeError(f"VWorld 주소 API 호출 실패: {e}") from e
    except ValueError as e:
        raise GeocodeError(f"VWorld 주소 API 응답 파싱 실패: {e}") from e

    response = data.get("response", {})
    status = response.get("status")
    if status != "OK":
        # NOT_FOUND(미발견) / ERROR(키·파라미터 오류) 등
        err = response.get("error", {})
        detail = err.get("text") or err.get("code") or status or "unknown"
        raise GeocodeError(f"주소 변환 실패 [{status}]: {detail} (입력: {cleaned!r})")

    point = response.get("result", {}).get("point")
    if not point:
        raise GeocodeError(f"좌표 없음 (입력: {cleaned!r})")

    try:
        lon = float(point["x"])
        lat = float(point["y"])
    except (KeyError, TypeError, ValueError) as e:
        raise GeocodeError(f"좌표 파싱 실패: {point!r}") from e

    return {"lon": lon, "lat": lat, "crs": "EPSG:4326"}
