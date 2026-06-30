"""generate_site_model 파이프라인 (사양서 §4.2 / §7).

Phase 2: layers={"buildings": True} 만 처리. 지형·지적은 stub(이후 Phase).
주소 → 좌표 → 건물 취득 → 5186 변환 + origin offset → 쿼드 솔리드 → .skp 코드.
"""

from datetime import datetime, timezone

from src import config
from src.geo.bbox import bbox_from_point
from src.geo.crs import origin_offset
from src.geo.geocode import GeocodeError, clean_address, geocode
from src.geo.vworld import DATASET_BUILDING, VWorldClient, VWorldError
from src.geometry.building import (
    collect_5186_coords,
    features_to_solids,
    floors_of,
)
from src.output.skp_mcp import build_skp_code


def generate(
    address: str,
    radius_m: int = 250,
    floor_h_m: float = config.DEFAULT_FLOOR_H_M,
    outputs: list[str] | None = None,
    layers: dict | None = None,
    client: VWorldClient | None = None,
) -> dict:
    """건물 매싱 생성 결과(좌표 데이터 + .skp 코드)를 반환.

    client 미지정 시 config 키로 VWorldClient 생성(테스트 주입용).
    치명 오류는 {"ok": False, "error": ...} 로 반환한다.
    """
    outputs = outputs or ["skp"]
    layers = layers or {"buildings": True}
    cleaned = clean_address(address)

    # 1. 주소 → 좌표
    try:
        coord = geocode(cleaned)
    except GeocodeError as e:
        return {"ok": False, "address": cleaned, "error": str(e)}

    # 2. 좌표 + 반경 → bbox
    bbox = bbox_from_point(coord["lon"], coord["lat"], radius_m)

    # 3. 건물 취득 (geometry=true)
    if client is None:
        try:
            client = VWorldClient(config.VWORLD_KEY, config.VWORLD_DOMAIN)
        except VWorldError as e:
            return {"ok": False, "address": cleaned, "coord": coord, "error": str(e)}
    try:
        features = client.get_features(DATASET_BUILDING, bbox, geometry=True)
    except VWorldError as e:
        return {"ok": False, "address": cleaned, "coord": coord, "error": str(e)}

    if not features:
        return {
            "ok": False,
            "address": cleaned,
            "coord": coord,
            "bbox": list(bbox),
            "error": "반경 내 건물이 없습니다 (LT_C_SPBD).",
        }

    # 4. 5186 변환 + origin offset 산출 (사양서 §6.1)
    coords_5186 = collect_5186_coords(features)
    offset = origin_offset(coords_5186)

    # 5. 쿼드 솔리드
    solids = features_to_solids(features, floor_h_m=floor_h_m, offset=offset)

    # 층수 통계/경고
    floors = [floors_of(f.get("properties") or {}) for f in features]
    with_floors = sum(1 for x in floors if x is not None)
    missing = len(features) - with_floors
    warnings = []
    if missing > 0:
        warnings.append(
            f"건물 {missing}개는 gro_flo_co 누락/0 → 기본 1층 높이 적용 (확인 불가)"
        )

    # 6. 출력
    out: dict = {}
    if "skp" in outputs:
        out["skp"] = {
            "code": build_skp_code(solids),
            "solids": len(solids),
        }
    if "3dm" in outputs:
        out["3dm"] = {"error": "3dm 출력은 Phase 4 예정"}

    return {
        "ok": True,
        "address": cleaned,
        "coord": coord,
        "bbox": list(bbox),
        "outputs": out,
        "stats": {
            "buildings": len(features),
            "solids": len(solids),
            "with_floors": with_floors,
            "origin_offset": list(offset),   # 복원용 — 필수 저장 (사양서 §6.1)
            "elev_range_m": None,            # 지형은 Phase 3
        },
        "provenance": {
            "building_src": "VWorld LT_C_SPBD",
            "floor_height_m": floor_h_m,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        "warnings": warnings,
    }
