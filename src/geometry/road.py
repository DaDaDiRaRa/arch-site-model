"""도로 노면 런타임 지오메트리 (Phase R).

road_manifest 지역 GeoJSON(EPSG:5186)을 질의 bbox로 클립해 로컬 미터 링으로 변환한다.
런타임은 json+shapely만 사용(geopandas 없이 — DEM과 동일 원칙). z는 파이프라인에서 DEM 드레이프.

R1a는 도로 '외곽선'(링)만 낸다. R1b에서 폴리곤 내부를 삼각화한 노면 메시로 확장한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import box, shape


@dataclass
class RoadFeature:
    """도로 폴리곤 하나. rings[0]=외곽, 이후=구멍(중앙분리대 등). 로컬 미터(offset 적용)."""

    rings: list[list[tuple[float, float]]]


def _ring_local(coords, offset) -> list[tuple[float, float]]:
    ox, oy = offset
    pts = [(float(x) - ox, float(y) - oy) for x, y in coords]
    if len(pts) >= 2 and pts[0] == pts[-1]:  # 닫힘점 제거(LineLoop 자동 폐합)
        pts = pts[:-1]
    return pts


def _iter_polys(geom):
    t = geom.geom_type
    if t == "Polygon":
        yield geom
    elif t == "MultiPolygon":
        yield from geom.geoms
    elif t == "GeometryCollection":
        for g in geom.geoms:
            yield from _iter_polys(g)


def clip_roads(geojson_path: str | Path, bbox_5186, offset) -> list[RoadFeature]:
    """지역 도로 GeoJSON을 bbox_5186으로 클립 → 로컬 미터 RoadFeature 목록.

    bbox_5186: (minx, miny, maxx, maxy) EPSG:5186. offset: origin_offset(건물·지형과 공통 기준).
    bbox와 겹치는 폴리곤만, bbox로 잘라서 반환. 파일 없으면 빈 목록.
    """
    path = Path(geojson_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data.get("features", []) if isinstance(data, dict) else []
    clip = box(*bbox_5186)
    out: list[RoadFeature] = []
    for f in feats:
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            g = shape(geom)
        except Exception:  # noqa: BLE001 — 깨진 지오메트리는 건너뜀
            continue
        if g.is_empty or not g.intersects(clip):
            continue
        for poly in _iter_polys(g.intersection(clip)):
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            ext = _ring_local(poly.exterior.coords, offset)
            if len(ext) < 3:
                continue
            holes = [
                _ring_local(r.coords, offset)
                for r in poly.interiors
                if len(r.coords) >= 4
            ]
            out.append(RoadFeature(rings=[ext] + holes))
    return out
