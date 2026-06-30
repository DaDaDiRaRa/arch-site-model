"""generate_site_model 파이프라인 (사양서 §4.2 / §7).

Phase 2: buildings 레이어(쿼드 솔리드 → .skp).
Phase 3B: terrain 레이어 추가(DEM 클립 → TIN → 건물 앉힘).
Phase 4: .3dm 이중 출력 (rhino3dm, 레이어 분리 + origin_offset 보존).
주소 → 좌표 → 건물 취득 → 5186 변환 + origin offset → 쿼드 솔리드 → .skp/.3dm.
"""

import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from src import config
from src.geo.bbox import bbox_from_point
from src.geo.crs import origin_offset, to_5186
from src.geo.geocode import GeocodeError, clean_address, geocode
from src.geo.vworld import DATASET_BUILDING, VWorldClient, VWorldError
from src.geometry.building import (
    BuildingSolid,
    collect_5186_coords,
    features_to_solids,
    floors_of,
)
from src.output.skp_mcp import build_skp_code


def _bbox_4326_to_5186(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """EPSG:4326 bbox(minlon, minlat, maxlon, maxlat) → EPSG:5186 bbox.

    4개 코너를 모두 변환 후 min/max를 취해 투영 왜곡에 안전하게 대응한다.
    """
    minlon, minlat, maxlon, maxlat = bbox
    corners = [
        to_5186(minlon, minlat),
        to_5186(maxlon, minlat),
        to_5186(maxlon, maxlat),
        to_5186(minlon, maxlat),
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _safe_filename(address: str) -> str:
    """주소 → 파일명으로 쓸 수 있는 ASCII-safe 문자열."""
    s = re.sub(r"[^\w가-힣]+", "_", address).strip("_")
    return s[:60] or "site"


def generate(
    address: str,
    radius_m: int = 250,
    floor_h_m: float = config.DEFAULT_FLOOR_H_M,
    outputs: list[str] | None = None,
    layers: dict | None = None,
    output_dir: str | Path | None = None,
    client: VWorldClient | None = None,
) -> dict:
    """건물 매싱(+ 선택적 지형) 생성 결과를 반환.

    layers={"buildings": True, "terrain": True} 로 지형 활성화(Phase 3B).
    outputs=["skp","3dm"] 로 두 포맷 동시 출력 가능(Phase 4).
      - "3dm" 선택 시 output_dir(기본: "output/")에 .3dm 저장 후 경로 반환.
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

    # 5. 쿼드 솔리드 (base_z=0, 지형 앉힘 전)
    solids = features_to_solids(features, floor_h_m=floor_h_m, offset=offset)

    # 층수 통계/경고
    floors_list = [floors_of(f.get("properties") or {}) for f in features]
    with_floors = sum(1 for x in floors_list if x is not None)
    missing = len(features) - with_floors
    warnings: list[str] = []
    if missing > 0:
        warnings.append(
            f"건물 {missing}개는 gro_flo_co 누락/0 → 기본 1층 높이 적용 (확인 불가)"
        )

    # 6. 지형 레이어 (Phase 3B)
    terrain_mesh = None
    elev_range: list[float] | None = None

    if layers.get("terrain"):
        from src.geometry.seating import seat_building
        from src.geometry.terrain_mesh import grid_to_tin
        from src.terrain.dem import clip_dem
        from src.terrain.store import find_tile

        tile = find_tile(bbox)
        if tile is None:
            warnings.append(
                "DEM 타일 없음: 반경이 비축 DEM 밖입니다. "
                "geo_store/manifest.json 확인 또는 contour_bake 재실행 필요."
            )
        else:
            tile_path = config.GEO_STORE / tile["file"]
            bbox_5186 = _bbox_4326_to_5186(bbox)
            dem = clip_dem(tile_path, bbox_5186, offset)

            zr = dem.z_range()
            if zr is None:
                warnings.append(
                    "클립 DEM에 유효 표고 없음: 사이트가 DEM 범위 밖일 수 있습니다. "
                    "인접 도엽 SHP 추가 후 contour_bake 재실행 필요."
                )
            else:
                elev_range = list(zr)

                # 건물 앉힘: base_z 를 지형 고도로 설정
                solids = [
                    replace(s, base_z_m=seat_building(s, dem))
                    for s in solids
                ]

                terrain_mesh = grid_to_tin(dem)

    # 7. 출력
    out: dict = {}
    if "skp" in outputs:
        out["skp"] = {
            "code": build_skp_code(solids, terrain=terrain_mesh),
            "solids": len(solids),
            "terrain_triangles": len(terrain_mesh.triangles) if terrain_mesh else 0,
        }
    if "3dm" in outputs:
        from src.output.rhino import write_3dm

        odir = Path(output_dir) if output_dir else Path("output")
        fname = _safe_filename(cleaned) + ".3dm"
        saved = write_3dm(solids, terrain_mesh, odir / fname, offset)
        out["3dm"] = {
            "path": saved,
            "solids": len(solids),
            "terrain_triangles": len(terrain_mesh.triangles) if terrain_mesh else 0,
        }

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
            "elev_range_m": elev_range,
        },
        "provenance": {
            "building_src": "VWorld LT_C_SPBD",
            "floor_height_m": floor_h_m,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        "warnings": warnings,
    }
