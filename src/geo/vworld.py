"""VWorld data API 클라이언트 (GetFeature). 사양서 §3.2 / §3.3.

GET https://api.vworld.kr/req/data
  service=data request=GetFeature data=<DATASET>
  key=<KEY> [domain=<DOMAIN>] format=json
  geomFilter=BOX(...) geometry=true|false crs=EPSG:4326 size=<N> page=<P>
→ response.result.featureCollection.features[]

페이지네이션 내장: size(최대 1000) 단위로 page를 증가시키며 전부 취득.
"""

import requests

from .bbox import to_geomfilter_box

# 데이터셋 코드 (사양서 §3.2/§3.3, 실측 검증됨)
DATASET_BUILDING = "LT_C_SPBD"        # 건물 footprint + 층수(gro_flo_co)
DATASET_CADASTRAL = "LP_PA_CBND_BUBUN"  # 대지 경계 + pnu

_DATA_URL = "https://api.vworld.kr/req/data"
_TIMEOUT = 15
_MAX_SIZE = 1000   # VWorld size 상한
_MAX_PAGES = 50    # 폭주 방지 (최대 5만 피처)


class VWorldError(Exception):
    """VWorld data API 오류 (INCORRECT_KEY, ERROR 등)."""


class VWorldClient:
    def __init__(self, key: str, domain: str = ""):
        if not key:
            raise VWorldError("VWorld 키가 없습니다 (.env의 VWORLD_TEST_KEY/VWORLD_KEY).")
        self.key = key
        self.domain = domain

    def _params(self, dataset, bbox, *, geometry: bool, size: int, page: int) -> dict:
        params = {
            "service": "data",
            "request": "GetFeature",
            "data": dataset,
            "key": self.key,
            "format": "json",
            "geomFilter": to_geomfilter_box(bbox),
            "geometry": "true" if geometry else "false",
            "crs": "EPSG:4326",
            "size": str(size),
            "page": str(page),
        }
        if self.domain:  # "기타" 개발키는 domain 불필요 → 빈 값이면 제외
            params["domain"] = self.domain
        return params

    def _request(self, params: dict) -> dict:
        try:
            resp = requests.get(_DATA_URL, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise VWorldError(f"VWorld data API 호출 실패: {e}") from e
        except ValueError as e:
            raise VWorldError(f"VWorld data API 응답 파싱 실패: {e}") from e

    @staticmethod
    def _check_status(response: dict) -> str:
        """response.status 검사. NOT_FOUND는 '결과 없음'으로 정상 취급."""
        status = response.get("status")
        if status in ("OK", "NOT_FOUND"):
            return status
        err = response.get("error", {})
        detail = err.get("text") or err.get("code") or status or "unknown"
        raise VWorldError(f"VWorld 오류 [{status}]: {detail}")

    def get_features(
        self, dataset: str, bbox, size: int = _MAX_SIZE, page: int = 1,
        geometry: bool = True,
    ) -> list[dict]:
        """bbox 내 모든 피처를 GeoJSON Feature dict 목록으로 반환 (페이지네이션 내장).

        size는 1000 상한. geometry=False면 좌표 없이 properties만 받아 가볍게 조회.
        """
        size = min(size, _MAX_SIZE)
        features: list[dict] = []
        while page <= _MAX_PAGES:
            params = self._params(dataset, bbox, geometry=geometry, size=size, page=page)
            data = self._request(params)
            response = data.get("response", {})
            status = self._check_status(response)
            if status == "NOT_FOUND":
                break
            fc = response.get("result", {}).get("featureCollection") or {}
            batch = fc.get("features") or []
            features.extend(batch)
            if len(batch) < size:   # 마지막 페이지
                break
            page += 1
        return features

    def count(self, dataset: str, bbox) -> int:
        """bbox 내 피처 개수만 반환 (geometry=false, record.total 사용)."""
        params = self._params(dataset, bbox, geometry=False, size=1, page=1)
        data = self._request(params)
        response = data.get("response", {})
        status = self._check_status(response)
        if status == "NOT_FOUND":
            return 0
        total = response.get("record", {}).get("total")
        try:
            return int(total)
        except (TypeError, ValueError):
            # record.total 없으면 result에서 직접 카운트(폴백)
            fc = response.get("result", {}).get("featureCollection") or {}
            return len(fc.get("features") or [])
