"""preview_site 핵심 로직.

"만들기 전에 뭐가 들어가나?" — 모델 생성 없이 건물 목록·층수·예상 규모를 리포트.
check_site_data가 "만들 수 있나?"를 답하면, preview_site는 "뭐가 들어갈까?"를 답한다.
"""

from __future__ import annotations

from shapely.geometry import Polygon as ShapelyPolygon, shape

from src import config
from src.geo.bbox import bbox_from_point
from src.geo.crs import to_5186
from src.geo.geocode import GeocodeError, clean_address, geocode
from src.geo.vworld import (
    DATASET_BUILDING,
    DATASET_CADASTRAL,
    VWorldClient,
    VWorldError,
)
from src.terrain.store import find_tile


def _parse_floor(value) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _footprint_area_m2(geom: dict) -> float:
    """GeoJSON geometry → 전체 외곽 면적 합계(㎡, EPSG:5186 근사)."""
    if not geom:
        return 0.0
    g = shape(geom)
    polys = list(g.geoms) if g.geom_type == "MultiPolygon" else ([g] if g.geom_type == "Polygon" else [])
    total = 0.0
    for poly in polys:
        pts_5186 = [to_5186(float(x), float(y)) for x, y in poly.exterior.coords]
        if len(pts_5186) >= 3:
            total += ShapelyPolygon(pts_5186).area
    return total


def _has_courtyard(geom: dict) -> bool:
    """Polygon/MultiPolygon에 내부 링(중정) 이 있으면 True."""
    if not geom:
        return False
    g = shape(geom)
    polys = list(g.geoms) if g.geom_type == "MultiPolygon" else ([g] if g.geom_type == "Polygon" else [])
    return any(list(poly.interiors) for poly in polys)


def _building_entry(feat: dict, floor_height_m: float) -> dict:
    """피처 1개 → 건물 항목 dict."""
    props = feat.get("properties") or {}
    floors = _parse_floor(props.get("gro_flo_co"))
    geom = feat.get("geometry")
    return {
        "name": props.get("buld_nm") or props.get("bd_mgt_sn") or "unknown",
        "bd_mgt_sn": props.get("bd_mgt_sn"),
        "floors": floors,
        "height_m": round(floors * floor_height_m, 1) if floors is not None else None,
        "footprint_area_m2": round(_footprint_area_m2(geom), 1) if geom else None,
        "has_courtyard": _has_courtyard(geom) if geom else False,
    }


def preview_site(
    address: str,
    radius_m: int = 250,
    floor_height_m: float = config.DEFAULT_FLOOR_H_M,
    client: VWorldClient | None = None,
) -> dict:
    """사람 검토용 건물 목록·규모 미리보기 (모델 생성 없음).

    반환:
      ok        : 건물이 1개 이상 있으면 True
      summary   : 건물 수·층수 통계·지형·지적 요약
      buildings : 건물별 이름·층수·면적·중정 여부 목록
      warnings  : 누락 층수 등 주의사항
    """
    cleaned = clean_address(address)

    try:
        coord = geocode(cleaned)
    except GeocodeError as e:
        return {"ok": False, "address": cleaned, "error": str(e)}

    bbox = bbox_from_point(coord["lon"], coord["lat"], radius_m)

    if client is None:
        try:
            client = VWorldClient(config.VWORLD_KEY, config.VWORLD_DOMAIN)
        except VWorldError as e:
            return {"ok": False, "address": cleaned, "error": str(e)}

    warnings: list[str] = []
    try:
        features = client.get_features(DATASET_BUILDING, bbox, geometry=True)
        cadastral_count = client.count(DATASET_CADASTRAL, bbox)
    except VWorldError as e:
        return {"ok": False, "address": cleaned, "coord": coord, "error": str(e)}

    buildings = [_building_entry(f, floor_height_m) for f in features]
    b_count = len(buildings)
    with_floors = sum(1 for b in buildings if b["floors"] is not None)
    missing = b_count - with_floors
    courtyards = sum(1 for b in buildings if b["has_courtyard"])

    if missing > 0:
        warnings.append(f"층수 미확인 건물 {missing}개 — 기본 {floor_height_m}m 적용 예정")
    if b_count == 0:
        warnings.append("반경 내 건물이 없습니다 (LT_C_SPBD)")
    if courtyards > 0:
        warnings.append(f"중정(홀) 있는 건물 {courtyards}개 포함")

    floors_list = [b["floors"] for b in buildings if b["floors"] is not None]
    tile = find_tile(bbox)

    summary = {
        "buildings": b_count,
        "with_floors": with_floors,
        "missing_floors": missing,
        "max_floors": max(floors_list) if floors_list else None,
        "avg_floors": round(sum(floors_list) / len(floors_list), 1) if floors_list else None,
        "courtyards": courtyards,
        "cadastral_parcels": cadastral_count,
        "terrain": {
            "available": tile is not None,
            "tile": tile.get("file") if tile else None,
        },
    }

    return {
        "ok": b_count > 0,
        "address": cleaned,
        "coord": coord,
        "bbox": list(bbox),
        "summary": summary,
        "buildings": buildings,
        "warnings": warnings,
    }
