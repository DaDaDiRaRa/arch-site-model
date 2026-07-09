"""수계 런타임 지오메트리 (수계).

water_manifest 지역 GeoJSON(EPSG:5186)을 질의 bbox로 클립 → **표고 고정 평면 수면**으로 만든다.
도로(road.py)와 대비: 도로는 DEM에 드레이프(지형 따라감), 수계는 **평면**(수면은 수평) + 지형을
물 아래로 버닝(지형이 수면 위로 삐져나오지 않게). road.py 헬퍼(_read_geojson_text 등)를 재사용한다.

수면 표고 = 폴리곤 경계(둑)의 DEM 저백분위 — 하천경계(E0010001)가 곧 물가라 그 표고가 수면.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from src.geometry.road import _drape_polygon, _iter_polys, _read_geojson_text, _ring_local, _z

# 수면을 버닝된 지형 바로 위로 살짝 띄우는 리프트(m) — z-fighting 방지 소량.
WATER_LIFT_M = 0.05
# 수면 표고 = 경계 DEM 저백분위(%). 25 = 낮은 둑(물가) 쪽으로 기울여 물이 둑 위로 안 뜨게.
WATER_Z_PCT = 25.0


@dataclass
class WaterFeature:
    """수계 폴리곤 하나. rings[0]=외곽, 이후=구멍(하중도 등). 로컬 미터(offset 적용)."""

    rings: list[list[tuple[float, float]]]


@dataclass
class WaterMesh:
    """평면 수면 메시 + 물가선. 좌표=로컬 미터. F2/.3dm/.skp 공용."""

    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    outlines: list[list[tuple[float, float, float]]]

    def to_geometry(self) -> dict:
        return {
            "vertices": [[round(x, 2), round(y, 2), round(z, 2)] for x, y, z in self.vertices],
            "triangles": [[int(a), int(b), int(c)] for a, b, c in self.triangles],
            "outlines": [
                [[round(x, 2), round(y, 2), round(z, 2)] for x, y, z in ring]
                for ring in self.outlines
            ],
        }


def clip_water(geojson_path, bbox_5186, offset) -> list[WaterFeature]:
    """지역 GeoJSON 수계 폴리곤을 bbox 클립 → 로컬 미터 WaterFeature. 로컬/HTTP 경로 모두."""
    from shapely.geometry import box, shape

    text = _read_geojson_text(geojson_path)
    if text is None:
        return []
    data = json.loads(text)
    feats = data.get("features", []) if isinstance(data, dict) else []
    clip = box(*bbox_5186)
    out: list[WaterFeature] = []
    for f in feats:
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            g = shape(geom)
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty or not g.intersects(clip):
            continue
        for poly in _iter_polys(g.intersection(clip)):
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            ext = _ring_local(poly.exterior.coords, offset)
            if len(ext) < 3:
                continue
            holes = [_ring_local(r.coords, offset) for r in poly.interiors if len(r.coords) >= 4]
            out.append(WaterFeature(rings=[ext] + holes))
    return out


def water_surface_z(rings, dem, pct: float = WATER_Z_PCT) -> float:
    """폴리곤 경계(둑)의 DEM 저백분위 = 수면 표고(m). dem 없으면 0."""
    if dem is None:
        return 0.0
    import numpy as np

    zs = [_z(dem, x, y) for ring in rings for x, y in ring]
    if not zs:
        return 0.0
    return float(np.percentile(zs, pct))


def surface_zs(features, dem) -> list[float]:
    """각 수계 폴리곤의 수면 표고 목록(버닝 전 DEM 기준)."""
    return [water_surface_z(f.rings, dem) for f in features]


def build_water_mesh(features, water_zs, dem, cell: float = 10.0, lift: float = WATER_LIFT_M):
    """WaterFeature 목록 → 평면 수면 메시(WaterMesh, 로컬 미터). 각 폴리곤은 자기 수면 z로 평평.

    삼각화는 _drape_polygon 재사용(오목·구멍 지원)하되 z는 DEM 대신 수면 z(고정)로 덮는다.
    dem은 삼각화용(x,y)일 뿐 — z는 water_zs. 유효 지오메트리 없으면 None.
    """
    verts: list = []
    tris: list = []
    outlines: list = []
    for f, wz in zip(features, water_zs):
        z = wz + lift
        v, t = _drape_polygon(f.rings, dem, cell)
        base = len(verts)
        verts.extend((x, y, z) for x, y, _ in v)      # 평면(고정 z)
        tris.extend((a + base, b + base, c + base) for a, b, c in t)
        for ring in f.rings:
            if len(ring) >= 3:
                outlines.append([(x, y, z) for x, y in ring])
    if not verts and not outlines:
        return None
    return WaterMesh(vertices=verts, triangles=tris, outlines=outlines)


def burn_water(dem, features, water_zs):
    """수계 폴리곤 내부 DEM 셀을 수면 z로 세팅 → 지형이 물 위로 삐져나오지 않게(평평한 수저).

    새 DEMPatch 반환(원본 불변). 수계 없으면 원본 그대로.
    """
    grid = getattr(dem, "grid", None)
    if grid is None or grid.size == 0 or not features:
        return dem
    import numpy as np
    from rasterio.features import rasterize
    from shapely.geometry import Polygon

    from src.terrain.dem import DEMPatch

    rows, cols = grid.shape
    tf = dem.transform
    ox, oy = dem.offset
    new = grid.astype(float).copy()
    for f, wz in zip(features, water_zs):
        ext = [(x + ox, y + oy) for x, y in f.rings[0]]
        holes = [[(x + ox, y + oy) for x, y in r] for r in f.rings[1:] if len(r) >= 3]
        try:
            poly = Polygon(ext, holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
        except Exception:  # noqa: BLE001
            continue
        mask = rasterize(
            [(poly, 1)], out_shape=(rows, cols), transform=tf, fill=0, all_touched=True
        ).astype(bool)
        new[mask] = wz
    return DEMPatch(grid=new.astype(np.float32), transform=tf, offset=dem.offset)
