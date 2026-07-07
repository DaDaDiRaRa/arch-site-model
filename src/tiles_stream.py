"""타일 순차조립 — 대반경(1~2km)용 계획(plan) + 타일별 geometry 스트리밍.

기존 `tiles.py`(generate_tiles)는 전체 반경을 한 번에 계산해 .skp 코드로 분할한다
(응답이 통째로 커지고 서버가 전체 TIN을 한 번에 만든다). 이 모듈은 SketchUp 확장의
**순차 조립**용이다:

  1. `tile_plan(address, radius, tile_size)` → 지오코딩 1회 + 고정 origin_offset +
     타일 격자 목록(작은 JSON, 지오메트리 없음).
  2. 확장이 타일마다 `generate_tile(bbox, offset, ...)` 호출 → 그 타일만 VWorld 조회 +
     DEM 클립 → 작은 geometry JSON. 확장이 하나씩 조립(진행바/취소).

매 단계 전송·서버메모리·클라이언트 빌드가 **타일 하나 규모로 바운드**된다. origin_offset은
계획 단계에서 전체 bbox의 5186 좌하단으로 1회 고정 → 모든 타일이 SketchUp 월드좌표에서
정확히 정렬된다. 경계 건물은 "중심점이 이 타일에 속하는 것만" 조립해 중복을 제거한다.
"""

from __future__ import annotations

import math
from dataclasses import replace

from src import config
from src.geo.bbox import bbox_from_point
from src.geo.crs import to_4326
from src.geo.geocode import clean_address, geocode
from src.geo.vworld import DATASET_BUILDING, VWorldClient, VWorldError
from src.geometry.building import features_to_solids
from src.pipeline import _bbox_4326_to_5186, _build_geometry


# 지형 타일 겹침(이음매 제거): 각 타일 DEM 클립을 이 셀 수만큼 사방으로 넓힌다.
# 인접 타일이 2×(이 값)셀만큼 겹쳐 경계 틈이 사라진다(같은 DEM 값이라 정확히 포개짐).
_TERRAIN_OVERLAP_CELLS = 2.0


def _bbox_5186_to_4326(
    bbox_5186: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """EPSG:5186 bbox → EPSG:4326 bbox (4코너 변환 후 min/max)."""
    minx, miny, maxx, maxy = bbox_5186
    corners = [
        to_4326(minx, miny), to_4326(maxx, miny),
        to_4326(maxx, maxy), to_4326(minx, maxy),
    ]
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    return (min(lons), min(lats), max(lons), max(lats))


def _tile_grid(
    bbox_5186: tuple[float, float, float, float], tile_size_m: float
) -> list[tuple[int, int, tuple[float, float, float, float]]]:
    """5186 bbox를 tile_size_m 격자로 분할 → [(ix, iy, tile_bbox_5186), ...]."""
    minx, miny, maxx, maxy = bbox_5186
    nx = max(1, math.ceil((maxx - minx) / tile_size_m))
    ny = max(1, math.ceil((maxy - miny) / tile_size_m))
    out = []
    for iy in range(ny):
        for ix in range(nx):
            tminx = minx + ix * tile_size_m
            tminy = miny + iy * tile_size_m
            tmaxx = min(tminx + tile_size_m, maxx)
            tmaxy = min(tminy + tile_size_m, maxy)
            out.append((ix, iy, (tminx, tminy, tmaxx, tmaxy)))
    return out


def tile_plan(
    address: str, radius_m: int = 1000, tile_size_m: float = 250.0
) -> dict:
    """주소 + 반경 → 고정 origin_offset + 타일 격자 목록(지오메트리 없음).

    반환:
      {"ok": True, "origin_offset": [ox, oy], "tile_size_m", "coord",
       "full_bbox_4326", "tiles": [{"tile_id", "bbox_4326", "bbox_5186"}, ...]}
    치명 오류는 예외로 전파(호출자가 처리) — geocode 실패 등.
    """
    if tile_size_m <= 0:
        raise ValueError(f"tile_size_m must be > 0, got {tile_size_m}")

    cleaned = clean_address(address)
    coord = geocode(cleaned)  # GeocodeError 전파
    full_4326 = bbox_from_point(coord["lon"], coord["lat"], radius_m)
    full_5186 = _bbox_4326_to_5186(full_4326)
    offset = (full_5186[0], full_5186[1])  # 전체 좌하단 = 고정 기준

    tiles = []
    for ix, iy, tb5186 in _tile_grid(full_5186, tile_size_m):
        tiles.append({
            "tile_id": f"{ix}_{iy}",
            "bbox_4326": list(_bbox_5186_to_4326(tb5186)),
            "bbox_5186": list(tb5186),
        })

    return {
        "ok": True,
        "address": cleaned,
        "coord": coord,
        "origin_offset": list(offset),
        "tile_size_m": tile_size_m,
        "full_bbox_4326": list(full_4326),
        "tiles": tiles,
    }


def generate_tile(
    bbox_4326: tuple[float, float, float, float],
    bbox_5186: tuple[float, float, float, float],
    offset: tuple[float, float],
    layers: dict | None = None,
    floor_h_m: float = config.DEFAULT_FLOOR_H_M,
    missing_floors_policy: str = "default",
    client: VWorldClient | None = None,
) -> dict:
    """한 타일의 geometry JSON. 중심점이 이 타일에 속하는 건물만(경계 중복 제거).

    offset은 tile_plan이 준 전체 기준을 그대로 사용해야 타일들이 정렬된다.
    반환: {"ok": True, "geometry": {...}, "solids": n, "terrain_triangles": n}.
    """
    layers = layers or {"buildings": True}
    offset = (float(offset[0]), float(offset[1]))
    bbox_4326 = tuple(bbox_4326)
    bbox_5186 = tuple(bbox_5186)

    if client is None:
        client = VWorldClient(config.VWORLD_KEY, config.VWORLD_DOMAIN)

    try:
        features = client.get_features(DATASET_BUILDING, bbox_4326, geometry=True)
    except VWorldError as e:
        return {"ok": False, "error": str(e)}

    solids = features_to_solids(
        features, floor_h_m=floor_h_m, offset=offset,
        missing_policy=missing_floors_policy,
    )

    # 경계 중복 제거: footprint 중심점(5186)이 이 타일 안인 건물만.
    minx, miny, maxx, maxy = bbox_5186
    ox, oy = offset

    def _in_tile(s) -> bool:
        fp = s.footprint_m
        if not fp:
            return False
        cx = sum(p[0] for p in fp) / len(fp) + ox
        cy = sum(p[1] for p in fp) / len(fp) + oy
        return minx <= cx < maxx and miny <= cy < maxy

    solids = [s for s in solids if _in_tile(s)]

    # 지형: 이 타일 bbox를 margin만큼 넓혀 DEM 클립 → 앉힘 → TIN.
    #
    # margin(=DEM 셀 몇 칸)만큼 넓히면 인접 타일 지형이 경계에서 서로 겹친다. 같은
    # DEM 값이라 겹침부가 정확히 포개져 "이음매(gap)"가 사라진다(타일이 각자 자기
    # bbox만 클립하면 경계에서 ~1셀씩 벌어져 틈이 보임). 건물은 위에서 이미 중심점
    # 기준으로 걸러 중복이 없다 — **지형만** 겹치므로 건물 중복은 없다.
    terrain_mesh = None
    if layers.get("terrain"):
        from src.geometry.seating import seat_building
        from src.geometry.terrain_mesh import build_tin
        from src.terrain.dem import clip_dem_mosaic
        from src.terrain.store import find_tiles

        probe = find_tiles(bbox_4326)
        if probe:
            cell = float(probe[0].get("cell_m") or 5.0)
            margin = cell * _TERRAIN_OVERLAP_CELLS
            clip_5186 = (
                bbox_5186[0] - margin, bbox_5186[1] - margin,
                bbox_5186[2] + margin, bbox_5186[3] + margin,
            )
            # 넓힌 영역이 이웃 DEM 타일에 걸칠 수 있으니 find_tiles도 넓혀 다시.
            dem_tiles = find_tiles(_bbox_5186_to_4326(clip_5186))
            paths = [config.dem_tile_path(t["file"]) for t in dem_tiles]
            dem = None
            try:
                dem = clip_dem_mosaic(paths, clip_5186, offset)
            except Exception:  # noqa: BLE001 — 타일 열기 실패 시 지형 생략
                dem = None
            if dem is not None and dem.z_range() is not None:
                solids = [replace(s, base_z_m=seat_building(s, dem)) for s in solids]
                terrain_mesh = build_tin(dem, config.TERRAIN_MAX_ERROR_M)

    geometry = _build_geometry(solids, terrain_mesh, None)
    return {
        "ok": True,
        "geometry": geometry,
        "solids": len(solids),
        "terrain_triangles": len(terrain_mesh.triangles) if terrain_mesh else 0,
    }
