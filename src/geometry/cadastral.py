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


def clip_parcels(
    parcels: list[CadastralParcel],
    extent: tuple[float, float, float, float],
    step_m: float = 5.0,
) -> list[CadastralParcel]:
    """필지를 모델 범위(로컬 미터 x0,y0,x1,y1)로 자르고 변을 step_m 간격으로 조밀화.

    VWorld는 bbox에 걸치기만 한 필지도 전체를 준다 — 도로 필지는 수 km 뻗어, 지형 밖 부분이
    드레이프 표고 0으로 떨어져 모델 아래 허공에 선이 깔렸다(2026-09-21 SketchUp 실기 검증).
    잘린 필지는 범위 경계를 따라 닫힌다(지형 테두리와 겹쳐 눈에 띄지 않음). 조밀화는 지형
    드레이프 시 긴 변이 지형을 관통·부유하지 않게 한다.
    """
    from shapely.geometry import Polygon, box

    frame = box(*extent)
    out: list[CadastralParcel] = []
    for p in parcels:
        try:
            poly = Polygon(p.footprint_m)
            if not poly.is_valid:
                poly = poly.buffer(0)
            cut = poly.intersection(frame)
        except Exception:  # noqa: BLE001 — 깨진 필지는 건너뜀
            continue
        if cut.is_empty:
            continue
        if cut.geom_type == "MultiPolygon":
            cut = max(cut.geoms, key=lambda g: g.area)
        if cut.geom_type != "Polygon" or cut.area < 1.0:
            continue
        ring = list(cut.exterior.coords)[:-1]
        dense: list[tuple[float, float]] = []
        for i, (x1, y1) in enumerate(ring):
            x2, y2 = ring[(i + 1) % len(ring)]
            n = max(1, int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 // step_m))
            dense += [(x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n) for k in range(n)]
        if len(dense) >= 3:
            out.append(CadastralParcel(pnu=p.pnu, footprint_m=dense))
    return out


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
