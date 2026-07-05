"""VWorld data API 클라이언트 (GetFeature). 사양서 §3.2 / §3.3.

GET https://api.vworld.kr/req/data
  service=data request=GetFeature data=<DATASET>
  key=<KEY> [domain=<DOMAIN>] format=json
  geomFilter=BOX(...) geometry=true|false crs=EPSG:4326 size=<N> page=<P>
→ response.result.featureCollection.features[]

페이지네이션 내장: size(최대 1000) 단위로 page를 증가시키며 전부 취득.
"""

import json
import math
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

# VWorld geomFilter BOX는 요청영역 10km² 이내만 허용(실측 오류: "16.28km²"). 그보다 크면
# 서브박스로 분할해 조회 후 병합한다(반경 ~1.5km 초과 = 대략 이 지점). 아래 값은 여유 마진.
_SINGLE_MAX_KM2 = 9.5      # 이하이면 단일 BOX 조회(분할 안 함)
_SPLIT_SIDE_KM = 3.0       # 분할 시 서브박스 한 변 최대(km) → 3×3=9km² ≤ 10
_MAX_SUBBOXES = 100        # 폭주 방지(≈반경 15km). 초과 시 명시적 오류.
_M_PER_DEG_LAT = 111_320.0


def _bbox_dims_km(bbox) -> tuple[float, float]:
    """bbox(4326 minx,miny,maxx,maxy) → (폭, 높이) km (중심 위도 기준 근사)."""
    minx, miny, maxx, maxy = bbox
    lat_c = math.radians((miny + maxy) / 2.0)
    w = abs(maxx - minx) * (_M_PER_DEG_LAT * math.cos(lat_c)) / 1000.0
    h = abs(maxy - miny) * _M_PER_DEG_LAT / 1000.0
    return w, h


def _split_bbox(bbox, max_side_km: float = _SPLIT_SIDE_KM) -> list[tuple]:
    """bbox가 10km² 한도 이하면 그대로, 크면 각 서브박스 변이 max_side_km 이하가
    되도록 nx×ny 균등 분할한 목록을 반환한다(경계는 서브박스에 걸쳐 중복될 수 있음).
    """
    minx, miny, maxx, maxy = bbox
    w_km, h_km = _bbox_dims_km(bbox)
    if w_km * h_km <= _SINGLE_MAX_KM2:
        return [tuple(bbox)]
    nx = max(1, math.ceil(w_km / max_side_km))
    ny = max(1, math.ceil(h_km / max_side_km))
    if nx * ny > _MAX_SUBBOXES:
        raise VWorldError(
            f"조회 영역이 너무 큽니다: {w_km:.1f}×{h_km:.1f}km → 서브박스 {nx*ny}개 필요 "
            f"(상한 {_MAX_SUBBOXES}). 반경을 줄이세요."
        )
    dx = (maxx - minx) / nx
    dy = (maxy - miny) / ny
    return [
        (minx + ix * dx, miny + iy * dy, minx + (ix + 1) * dx, miny + (iy + 1) * dy)
        for iy in range(ny)
        for ix in range(nx)
    ]


def _feature_key(feat: dict):
    """중복 제거 키. 경계 걸친 피처가 인접 서브박스에서 중복 반환되므로 dedup에 사용.
    id → 건물관리번호/pnu/ufid → 지오메트리 좌표 해시 순."""
    if feat.get("id"):
        return ("id", feat["id"])
    props = feat.get("properties") or {}
    for k in ("bd_mgt_sn", "pnu", "ufid"):
        if props.get(k):
            return (k, props[k])
    geom = feat.get("geometry")
    if geom:
        return ("geom", json.dumps(geom.get("coordinates"), sort_keys=True))
    return ("obj", id(feat))


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
        bbox가 VWorld의 10km² 한도를 넘으면(반경 ~1.5km 초과) 서브박스로 분할 조회 후
        병합·중복제거한다. page는 단일 BOX 시작 페이지(분할 시 무시).
        """
        size = min(size, _MAX_SIZE)
        boxes = _split_bbox(bbox)
        if len(boxes) == 1:
            return self._get_features_box(dataset, boxes[0], size, geometry, page)

        seen: set = set()
        merged: list[dict] = []
        for sub in boxes:
            for feat in self._get_features_box(dataset, sub, size, geometry, 1):
                k = _feature_key(feat)
                if k not in seen:
                    seen.add(k)
                    merged.append(feat)
        return merged

    def _get_features_box(
        self, dataset: str, bbox, size: int, geometry: bool, page: int = 1,
    ) -> list[dict]:
        """단일 BOX 조회(페이지네이션). 10km² 이내 영역 전용."""
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
        """bbox 내 피처 개수. 단일 BOX면 record.total(빠름), 분할 필요하면
        중복제거된 피처 수(정확)."""
        boxes = _split_bbox(bbox)
        if len(boxes) > 1:
            return len(self.get_features(dataset, bbox, geometry=False))
        params = self._params(dataset, boxes[0], geometry=False, size=1, page=1)
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
