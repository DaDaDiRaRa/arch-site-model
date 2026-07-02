"""generate_site_tiles — 대량건물 시 build_model 코드를 타일 단위로 분할 (백로그 5).

배경: build_skp_code() 자체는 건물 2000동도 <10ms/500KB로 빠르지만, 그 문자열
전체가 오케스트레이터(Claude)의 build_model 호출 인자로 컨텍스트에 들어가야 한다.
반경 500m+ 밀집 지역은 건물이 수백~천 단위가 되어 단일 호출로는 감당이 안 된다.

설계: VWorld 조회 + origin_offset은 전체 반경에 대해 **한 번만** 수행(중복 조회/
좌표 불일치 방지). 이후 footprint(지적은 parcel) 중심점을 기준으로 tile_size_m
격자에 배정해 타일별로 build_skp_code()를 나눠 호출한다.
"""

from __future__ import annotations

from src import config
from src.geo.bbox import bbox_from_point
from src.geo.crs import origin_offset
from src.geo.geocode import GeocodeError, clean_address, geocode
from src.geo.vworld import DATASET_BUILDING, DATASET_CADASTRAL, VWorldClient, VWorldError
from src.geometry.building import collect_5186_coords, features_to_solids, floors_of
from src.output.skp_mcp import build_skp_code
from src.pipeline import _bbox_4326_to_5186


def _centroid_m(footprint_m: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(footprint_m)
    return (sum(p[0] for p in footprint_m) / n, sum(p[1] for p in footprint_m) / n)


def _tile_key(cx: float, cy: float, tile_size_m: float) -> tuple[int, int]:
    return (int(cx // tile_size_m), int(cy // tile_size_m))


def _bucket_by_tile(items, footprint_of, tile_size_m: float) -> dict[tuple[int, int], list]:
    buckets: dict[tuple[int, int], list] = {}
    for item in items:
        cx, cy = _centroid_m(footprint_of(item))
        key = _tile_key(cx, cy, tile_size_m)
        buckets.setdefault(key, []).append(item)
    return buckets


def _split_terrain_by_tile(mesh, tile_size_m: float) -> dict[tuple[int, int], object]:
    """TerrainMesh(전체) → 타일별 TerrainMesh. 경계 정점은 타일마다 중복 생성해도 무해
    (각 타일이 SketchUp에서 독립 그룹이라 용접(weld) 대상이 아님)."""
    from src.geometry.terrain_mesh import TerrainMesh

    tri_buckets: dict[tuple[int, int], list] = {}
    for tri in mesh.triangles:
        pts = [mesh.vertices[i] for i in tri]
        cx = sum(p[0] for p in pts) / 3.0 / config.M2I
        cy = sum(p[1] for p in pts) / 3.0 / config.M2I
        key = _tile_key(cx, cy, tile_size_m)
        tri_buckets.setdefault(key, []).append(pts)

    result: dict[tuple[int, int], object] = {}
    for key, tris in tri_buckets.items():
        verts: list[tuple[float, float, float]] = []
        idx_tris: list[tuple[int, int, int]] = []
        for pts in tris:
            base = len(verts)
            verts.extend(pts)
            idx_tris.append((base, base + 1, base + 2))
        result[key] = TerrainMesh(vertices=verts, triangles=idx_tris)
    return result


def generate_tiles(
    address: str,
    radius_m: int = 500,
    tile_size_m: float = 200.0,
    floor_h_m: float = config.DEFAULT_FLOOR_H_M,
    layers: dict | None = None,
    missing_floors_policy: str = "default",
    client: VWorldClient | None = None,
) -> dict:
    """건물(+선택적 지적/지형)을 tile_size_m 격자로 분할해 타일별 skp code를 반환.

    layers 옵션은 generate()와 동일: {"buildings": True[, "terrain": True][, "cadastral": True]}.
    VWorld 조회와 origin_offset은 전체 반경에 대해 1회만 수행 — 타일 경계 중복/좌표
    불일치가 없다(지오메트리는 pipeline.generate()와 동일 코드 경로 재사용).

    반환:
      {"ok": True, "tiles": [{"tile_id", "tile_bbox_m", "code", "solids",
       "cadastral_parcels"}, ...], "stats": {...}, "warnings": [...]}
    치명 오류는 {"ok": False, "error": ...}.
    """
    if tile_size_m <= 0:
        raise ValueError(f"tile_size_m must be > 0, got {tile_size_m}")

    layers = layers or {"buildings": True}
    cleaned = clean_address(address)

    # 1. 주소 → 좌표
    try:
        coord = geocode(cleaned)
    except GeocodeError as e:
        return {"ok": False, "address": cleaned, "error": str(e)}

    # 2. 좌표 + 반경 → bbox (단일 조회, pipeline.generate()와 동일)
    bbox = bbox_from_point(coord["lon"], coord["lat"], radius_m)

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

    coords_5186 = collect_5186_coords(features)
    offset = origin_offset(coords_5186)

    solids = features_to_solids(
        features, floor_h_m=floor_h_m, offset=offset, missing_policy=missing_floors_policy,
    )

    floors_list = [floors_of(f.get("properties") or {}) for f in features]
    with_floors = sum(1 for x in floors_list if x is not None)
    missing_count = len(features) - with_floors
    warnings: list[str] = []
    if missing_count > 0:
        policy_msg = {
            "default": f"건물 {missing_count}개는 gro_flo_co 누락/0 → 기본 1층 높이 적용 (확인 불가)",
            "skip":    f"건물 {missing_count}개는 gro_flo_co 누락/0 → 제외됨 (policy=skip)",
            "flag":    f"건물 {missing_count}개는 gro_flo_co 누락/0 → buildings_unverified 레이어 (policy=flag)",
        }
        warnings.append(policy_msg.get(missing_floors_policy, policy_msg["default"]))

    # 지형 레이어(옵션): 전체 반경 1회 클립 후 건물 앉힘 + 타일별 분할
    terrain_by_tile: dict[tuple[int, int], object] = {}
    if layers.get("terrain"):
        from dataclasses import replace

        from src.geometry.seating import seat_building
        from src.geometry.terrain_mesh import grid_to_tin
        from src.terrain.dem import clip_dem_mosaic
        from src.terrain.store import find_tiles

        dem_tiles = find_tiles(bbox)
        if not dem_tiles:
            warnings.append(
                "DEM 타일 없음: 반경이 비축 DEM 밖입니다. "
                "geo_store/manifest.json 확인 또는 contour_bake 재실행 필요."
            )
        else:
            tile_paths = [config.GEO_STORE / t["file"] for t in dem_tiles]
            bbox_5186 = _bbox_4326_to_5186(bbox)
            dem = clip_dem_mosaic(tile_paths, bbox_5186, offset)
            zr = dem.z_range()
            if zr is None:
                warnings.append(
                    "클립 DEM에 유효 표고 없음: 사이트가 DEM 범위 밖일 수 있습니다. "
                    "인접 도엽 SHP 추가 후 contour_bake 재실행 필요."
                )
            else:
                solids = [replace(s, base_z_m=seat_building(s, dem)) for s in solids]
                full_mesh = grid_to_tin(dem)
                terrain_by_tile = _split_terrain_by_tile(full_mesh, tile_size_m)

    # 지적 레이어(옵션): 전체 반경 1회 조회
    cadastral_parcels = None
    if layers.get("cadastral"):
        from src.geometry.cadastral import features_to_parcels

        try:
            cada_features = client.get_features(DATASET_CADASTRAL, bbox, geometry=True)
        except VWorldError as e:
            warnings.append(f"지적 취득 실패 (계속 진행): {e}")
            cada_features = []

        if cada_features:
            cadastral_parcels = features_to_parcels(cada_features, offset)
        else:
            warnings.append("반경 내 지적 피처 없음 (LP_PA_CBND_BUBUN)")

    # 건물/지적을 tile_size_m 격자로 배정
    solid_buckets = _bucket_by_tile(solids, lambda s: s.footprint_m, tile_size_m)
    cadastral_buckets = (
        _bucket_by_tile(cadastral_parcels, lambda p: p.footprint_m, tile_size_m)
        if cadastral_parcels
        else {}
    )

    all_keys = sorted(set(solid_buckets) | set(cadastral_buckets) | set(terrain_by_tile))

    tiles_out = []
    for key in all_keys:
        tx, ty = key
        tile_solids = solid_buckets.get(key, [])
        tile_cadastral = cadastral_buckets.get(key)
        tile_terrain = terrain_by_tile.get(key)
        code = build_skp_code(tile_solids, terrain=tile_terrain, cadastral=tile_cadastral)
        tiles_out.append({
            "tile_id": f"{tx}_{ty}",
            "tile_bbox_m": [
                tx * tile_size_m, ty * tile_size_m,
                (tx + 1) * tile_size_m, (ty + 1) * tile_size_m,
            ],
            "code": code,
            "solids": len(tile_solids),
            "cadastral_parcels": len(tile_cadastral) if tile_cadastral else 0,
            "terrain_triangles": len(tile_terrain.triangles) if tile_terrain else 0,
        })

    return {
        "ok": True,
        "address": cleaned,
        "coord": coord,
        "bbox": list(bbox),
        "tile_size_m": tile_size_m,
        "tiles": tiles_out,
        "stats": {
            "buildings": len(features),
            "solids": len(solids),
            "with_floors": with_floors,
            "tile_count": len(tiles_out),
            "cadastral_parcels": len(cadastral_parcels) if cadastral_parcels else 0,
            "origin_offset": list(offset),
        },
        "warnings": warnings,
    }
