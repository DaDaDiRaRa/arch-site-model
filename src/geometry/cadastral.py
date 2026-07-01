"""대지 경계(지적, LP_PA_CBND_BUBUN) → CadastralParcel (Phase 5).

외곽 링만 취득. pnu(19자리 필지코드) 보존.
좌표계: 로컬 미터 (BuildingSolid와 동일 origin_offset 적용).
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import shape

from src.geo.crs import apply_offset, to_5186


@dataclass
class CadastralParcel:
    pnu: str
    footprint_m: list[tuple[float, float]]  # 로컬 미터 (origin_offset 적용)


def _largest_exterior(geom: dict) -> list[tuple[float, float]] | None:
    """Polygon/MultiPolygon → 가장 큰 외곽 링 (lon, lat, 닫힘점 제거)."""
    if not geom:
        return None
    try:
        g = shape(geom)
    except (ValueError, Exception):
        return None
    if g.geom_type == "MultiPolygon":
        polys = sorted(g.geoms, key=lambda p: p.area, reverse=True)
        poly = polys[0] if polys else None
    elif g.geom_type == "Polygon":
        poly = g
    else:
        return None
    if poly is None:
        return None
    coords = [(float(x), float(y)) for x, y in poly.exterior.coords]
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords if len(coords) >= 3 else None


def features_to_parcels(
    features: list[dict],
    offset: tuple[float, float],
) -> list[CadastralParcel]:
    """LP_PA_CBND_BUBUN 피처(GeoJSON Feature) → CadastralParcel 목록.

    geometry 없거나 꼭짓점 < 3 이면 해당 피처를 건너뜀.
    """
    parcels: list[CadastralParcel] = []
    for feat in features:
        props = feat.get("properties") or {}
        pnu = str(props.get("pnu") or props.get("bub_cd") or "unknown")
        ring = _largest_exterior(feat.get("geometry"))
        if ring is None:
            continue
        fp_5186 = [to_5186(lon, lat) for lon, lat in ring]
        fp_local = apply_offset(fp_5186, offset)
        parcels.append(CadastralParcel(pnu=pnu, footprint_m=fp_local))
    return parcels
