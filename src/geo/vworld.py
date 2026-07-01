"""VWorld data API 클라이언트 (GetFeature). 사양서 §3.2 / §3.3.

GET https://api.vworld.kr/req/data
  service=data request=GetFeature data=<DATASET>
  key=<KEY> [domain=<DOMAIN>] format=json
  geomFilter=BOX(...) geometry=true|false crs=EPSG:4326 size=<N> page=<P>
→ response.result.featureCollection.features[]

페이지네이션 내장: size(최대 1000) 단위로 page를 증가시키며 전부 취득.
"""

import time

import requests

from .bbox import to_geomfilter_box

# 데이터셋 코드 (사양서 §3.2/§3.3, 실측 검증됨)
DATASET_BUILDING = "LT_C_SPBD"        # 건물 footprint + 층수(gro_flo_co)
DATASET_CADASTRAL = "LP_PA_CBND_BUBUN"  # 대지 경계 + pnu

_DATA_URL = "https://api.vworld.kr/req/data"
_TIMEOUT = 15
_MAX_SIZE = 1000   # VWorld size 상한
_MAX_PAGES = 50    # 폭주 방지 (최대 5만 피처)

# VWorld data API(req/data)는 서버 클러스터 특성상 정상 키에도 간헐적으로
# INCORRECT_KEY(status=ERROR)를 반환한다(실측: 연속 호출 3회 중 1회꼴). 치명 오류로
# 보지 않고 재시도한다. 네트워크 오류도 재시도. (2026-07-01 실측 근거)
_RETRIES = 5
_RETRY_WAIT = 0.5           # 초. attempt마다 선형 증가(0.5, 1.0, ...)
_TRANSIENT_CODES = {"INCORRECT_KEY"}   # 재시도 대상 오류 코드


class VWorldError(Exception):
    """VWorld data API 오류 (INCORRECT_KEY, ERROR 등)."""


class VWorldClient:
    def __init__(
        self,
        key: str,
        domain: str = "",
        retries: int = _RETRIES,
        retry_wait: float = _RETRY_WAIT,
    ):
        if not key:
            raise VWorldError("VWorld 키가 없습니다 (.env의 VWORLD_TEST_KEY/VWORLD_KEY).")
        self.key = key
        self.domain = domain
        self.retries = retries        # 테스트는 retry_wait=0 으로 즉시화 가능
        self.retry_wait = retry_wait

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

    def _request_checked(self, params: dict) -> tuple[dict, str]:
        """HTTP 요청 + 상태검사, 일시적 오류는 재시도 후 (response, status) 반환.

        재시도 대상: 네트워크 오류(VWorldError), status=ERROR 이면서 코드가
        _TRANSIENT_CODES(INCORRECT_KEY)인 경우. NOT_FOUND는 정상('결과 없음').
        재시도 소진 시 마지막 오류로 raise. (VWorld data API 간헐 오류 대응)
        """
        detail = "unknown"
        for attempt in range(self.retries):
            try:
                data = self._request(params)
            except VWorldError:
                if attempt < self.retries - 1:
                    time.sleep(self.retry_wait * (attempt + 1))
                    continue
                raise
            response = data.get("response", {})
            status = response.get("status")
            if status in ("OK", "NOT_FOUND"):
                return response, status
            err = response.get("error", {}) or {}
            detail = err.get("text") or err.get("code") or status or "unknown"
            if err.get("code") in _TRANSIENT_CODES and attempt < self.retries - 1:
                time.sleep(self.retry_wait * (attempt + 1))
                continue
            raise VWorldError(f"VWorld 오류 [{status}]: {detail}")
        raise VWorldError(f"VWorld 오류 [재시도 {self.retries}회 소진]: {detail}")

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
            response, status = self._request_checked(params)
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
        response, status = self._request_checked(params)
        if status == "NOT_FOUND":
            return 0
        total = response.get("record", {}).get("total")
        try:
            return int(total)
        except (TypeError, ValueError):
            # record.total 없으면 result에서 직접 카운트(폴백)
            fc = response.get("result", {}).get("featureCollection") or {}
            return len(fc.get("features") or [])
