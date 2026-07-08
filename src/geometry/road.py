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


@dataclass
class RoadMesh:
    """DEM 드레이프된 병합 노면 메시 + 외곽선. 좌표=로컬 미터. F2/.3dm/.skp 공용."""

    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    outlines: list[list[tuple[float, float, float]]]

    def to_geometry(self) -> dict:
        """F2 뷰어용 JSON(cm 반올림)."""
        return {
            "vertices": [[round(x, 2), round(y, 2), round(z, 2)] for x, y, z in self.vertices],
            "triangles": [[int(a), int(b), int(c)] for a, b, c in self.triangles],
            "outlines": [
                [[round(x, 2), round(y, 2), round(z, 2)] for x, y, z in ring]
                for ring in self.outlines
            ],
        }


# 노면을 지형 바로 위로 살짝 띄우는 리프트(m) — .3dm/.skp z-fighting 방지(F2는 뷰어에서 별도).
ROAD_LIFT_M = 0.1


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


# --- R1b: 노면 드레이프 메시 ------------------------------------------------

def _z(dem, x: float, y: float) -> float:
    """로컬 (x,y) → DEM 표고. dem 없으면 0.0(평면)."""
    return float(dem.elev_at(x, y)) if dem is not None else 0.0


def _densify_ring(ring, cell: float):
    """링 각 변을 cell 간격 이하로 잘게 나눈 점 목록(닫힘점 없이 순회)."""
    import math

    out = []
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]  # 마지막→처음으로 폐합
        out.append((x0, y0))
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg > cell:
            steps = int(seg // cell)
            for k in range(1, steps + 1):
                t = k / (steps + 1)
                out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


def _drape_polygon(rings, dem, cell: float):
    """폴리곤(외곽+구멍) 하나 → DEM 드레이프 삼각 메시 (정점[(x,y,z)], 삼각형[(i,j,k)]).

    경계(densify) + 내부 격자점을 모아 Delaunay 후, 삼각형 중심이 폴리곤 밖(또는 구멍
    안)이면 컬링한다(오목·구멍 지원). 정점 z = DEM 표고. scipy/shapely 실패 시 빈 메시.
    """
    import numpy as np
    from scipy.spatial import Delaunay
    from shapely.geometry import Point, Polygon
    from shapely.prepared import prep

    ext = rings[0]
    holes = [r for r in rings[1:] if len(r) >= 3]
    try:
        poly = Polygon(ext, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
    except Exception:  # noqa: BLE001
        return [], []
    if poly.is_empty or poly.area <= 0.0 or poly.geom_type != "Polygon":
        return [], []

    pae = prep(poly)
    pts: set[tuple[float, float]] = set()
    for ring in rings:  # 경계점(잘게)
        for px, py in _densify_ring(ring, cell):
            pts.add((round(px, 3), round(py, 3)))
    minx, miny, maxx, maxy = poly.bounds  # 내부 격자점
    for x in np.arange(minx, maxx + cell, cell):
        for y in np.arange(miny, maxy + cell, cell):
            if pae.contains(Point(x, y)):
                pts.add((round(float(x), 3), round(float(y), 3)))

    pt_list = list(pts)
    if len(pt_list) < 3:
        return [], []
    arr = np.array(pt_list, dtype=float)
    try:
        d = Delaunay(arr)
    except Exception:  # noqa: BLE001 — 공선점 등
        return [], []

    tris = []
    for s in d.simplices:
        a, b, c = int(s[0]), int(s[1]), int(s[2])
        cx = (arr[a, 0] + arr[b, 0] + arr[c, 0]) / 3.0
        cy = (arr[a, 1] + arr[b, 1] + arr[c, 1]) / 3.0
        if pae.contains(Point(cx, cy)):  # 폴리곤 밖/구멍 삼각형 컬링
            tris.append((a, b, c))
    verts = [(px, py, _z(dem, px, py)) for px, py in pt_list]
    return verts, tris


def build_road_mesh(features, dem, cell: float = 2.5) -> RoadMesh | None:
    """RoadFeature 목록 → 병합 노면 메시(RoadMesh, 로컬 미터). F2/.3dm/.skp 공용.

    각 폴리곤을 _drape_polygon으로 삼각화·드레이프해 하나의 정점/삼각형 버퍼로 병합하고,
    외곽선(링)도 드레이프해 담는다. 삼각화 실패 폴리곤도 외곽선은 남는다(조용한 열화).
    유효 지오메트리가 전혀 없으면 None.
    """
    verts: list = []
    tris: list = []
    outlines: list = []
    for f in features:
        v, t = _drape_polygon(f.rings, dem, cell)
        base = len(verts)
        verts.extend(v)
        tris.extend((a + base, b + base, c + base) for a, b, c in t)
        for ring in f.rings:
            if len(ring) >= 3:
                outlines.append([(x, y, _z(dem, x, y)) for x, y in ring])

    if not verts and not outlines:
        return None
    return RoadMesh(vertices=verts, triangles=tris, outlines=outlines)
