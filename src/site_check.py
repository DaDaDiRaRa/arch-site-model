"""check_site_data 핵심 로직 (사양서 §4.1).

"이 주소, 지금 만들 수 있나?" — 생성 전 취득 가능성 선검사.
주소 → 좌표 → 건물/지적 취득 가능 + 지형 비축 여부를 한 번에 리포트한다.
"""

from src import config
from src.geo.bbox import bbox_from_point
from src.geo.geocode import GeocodeError, clean_address, geocode
from src.geo.vworld import (
    DATASET_BUILDING,
    DATASET_CADASTRAL,
    VWorldClient,
    VWorldError,
)
from src.terrain.store import find_tile


def _parse_floor(value) -> int | None:
    """gro_flo_co 값을 층수(int)로. 누락/0/비정상은 None."""
    if value in (None, "", "null"):
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def count_with_floors(features: list[dict]) -> int:
    """gro_flo_co 가 유효(>0)한 건물 수."""
    return sum(
        1
        for f in features
        if _parse_floor((f.get("properties") or {}).get("gro_flo_co")) is not None
    )


def check_site_data(
    address: str, radius_m: int = 250, client: VWorldClient | None = None
) -> dict:
    """사양서 §4.1 스키마의 선검사 리포트를 반환.

    client 미지정 시 config 키로 VWorldClient를 생성한다(테스트 주입용).
    치명 오류(주소 변환·키 실패)는 {"ok": false, "error": ...} 로 반환한다.
    """
    cleaned = clean_address(address)

    # 1) 주소 → 좌표
    try:
        coord = geocode(cleaned)
    except GeocodeError as e:
        return {"ok": False, "address": cleaned, "error": str(e)}

    # 2) 좌표 + 반경 → bbox(4326)
    bbox = bbox_from_point(coord["lon"], coord["lat"], radius_m)

    # 3) 건물/지적 취득
    if client is None:
        try:
            client = VWorldClient(config.VWORLD_KEY, config.VWORLD_DOMAIN)
        except VWorldError as e:
            return {"ok": False, "address": cleaned, "error": str(e)}

    warnings: list[str] = []
    try:
        # 건물: properties만 필요 → geometry=false 로 가볍게
        buildings = client.get_features(DATASET_BUILDING, bbox, geometry=False)
        cadastral_count = client.count(DATASET_CADASTRAL, bbox)
    except VWorldError as e:
        return {"ok": False, "address": cleaned, "coord": coord, "error": str(e)}

    b_count = len(buildings)
    with_floors = count_with_floors(buildings)
    missing = b_count - with_floors
    if missing > 0:
        warnings.append(
            f"건물 {missing}개는 gro_flo_co 누락/0 → 기본 높이 적용 예정"
        )
    if b_count == 0:
        warnings.append("반경 내 건물이 없습니다 (LT_C_SPBD)")

    # 4) 지형 비축 여부
    tile = find_tile(bbox)
    terrain = {
        "available": tile is not None,
        "source": (tile.get("source", "DEM") if tile else None),
        "tile": (tile.get("file") if tile else None),
    }
    if tile is None:
        warnings.append("지형 비축 없음 → 생성 시 terrain 레이어 제외 또는 비축 추가 필요")

    return {
        "ok": b_count > 0,   # 생성 가능 최소요건 = 건물 존재
        "address": cleaned,
        "coord": coord,
        "bbox": list(bbox),
        "buildings": {
            "available": b_count > 0,
            "count": b_count,
            "with_floors": with_floors,
        },
        "cadastral": {
            "available": cadastral_count > 0,
            "count": cadastral_count,
        },
        "terrain": terrain,
        "warnings": warnings,
    }
