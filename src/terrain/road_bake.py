"""수치지도 A0010000 도로경계 폴리곤 SHP → 지역 GeoJSON(EPSG:5186) 오프라인 굽기 (Phase R).

도로 노면은 실시간 표고 API가 없는 지형(DEM)과 사정이 같다 — 로컬 SHP뿐이다. 그래서 지형과 동일
패턴으로 지역 GeoJSON에 오프라인으로 굽고, 런타임은 road_manifest.json으로 조회해 bbox 클립 +
지형 드레이프한다(런타임은 json+shapely만 씀 — geopandas 런타임 의존 없음, DEM과 동일 원칙).

사용법:
    python -m src.terrain.road_bake <shp_dir> \
        --out geo_store/roads_daejeon.geojson --region "대전 서구"

shp_dir(재귀) 안의 도로경계 폴리곤 레이어(N3A_A0010000)만 읽는다. 선(N3L)·점(N3P)은 건너뛴다.
동부원점(5187) SHP는 5186으로 재투영해 통일(DEM과 동일 — 안 하면 ~2° 어긋남).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import geopandas as gpd

from src import config
from src.terrain.contour_bake import _sheet_key, _to_target_crs

log = logging.getLogger(__name__)

# 도로경계 폴리곤 레이어코드(A0010000). 폴리곤은 N3A 접두 — 선(N3L)/점(N3P)은 제외한다.
_ROAD_POLY_PAT = re.compile(r"A0010000", re.IGNORECASE)


def _find_road_shp(shp_dir: Path) -> list[Path]:
    """A0010000 '폴리곤' 레이어 SHP 목록(도엽 중복 제거). 선/점 레이어는 건너뜀."""
    matched = sorted(
        (p for p in shp_dir.rglob("*.shp") if _ROAD_POLY_PAT.search(p.stem)),
        key=str,
    )
    seen: set[str] = set()
    out: list[Path] = []
    for p in matched:
        stem = p.stem.upper()
        if stem.startswith("N3L") or stem.startswith("N3P"):  # 선/점 레이어 제외
            continue
        key = _sheet_key(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def read_road_polygons(shp_dir: str | Path, target_crs: str = "EPSG:5186") -> list:
    """도로경계 폴리곤을 target_crs로 통일한 shapely Polygon 목록으로 반환."""
    shp_dir = Path(shp_dir)
    files = _find_road_shp(shp_dir)
    if not files:
        raise FileNotFoundError(f"도로경계 SHP(A0010000 폴리곤)를 찾을 수 없습니다: {shp_dir}")
    polys = []
    for f in files:
        gdf = gpd.read_file(f, encoding="euc-kr")
        gdf = _to_target_crs(gdf, target_crs)
        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "Polygon":
                polys.append(geom)
            elif geom.geom_type == "MultiPolygon":
                polys.extend(g for g in geom.geoms if not g.is_empty)
    return polys


def bake_roads(
    shp_dir: str | Path,
    out_path: str | Path,
    region: str,
    target_crs: str = "EPSG:5186",
    min_area_m2: float = 1.0,
) -> dict:
    """도로경계 폴리곤 → GeoJSON(EPSG:5186) 저장 + road_manifest.json 갱신.

    GeoJSON은 지오메트리만 담는다(A0010000은 UFID 외 속성 없음). 좌표는 EPSG:5186 미터.
    """
    out_path = Path(out_path)
    polys = read_road_polygons(shp_dir, target_crs)
    polys = [p for p in polys if p.area >= min_area_m2]  # 슬리버 제거
    if not polys:
        raise ValueError("유효 도로 폴리곤이 없습니다(슬리버 제거 후 0).")

    from shapely.geometry import mapping

    epsg = int(str(target_crs).split(":")[-1])
    features = [{"type": "Feature", "properties": {}, "geometry": mapping(p)} for p in polys]
    fc = {"type": "FeatureCollection", "crs_epsg": epsg, "features": features}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fc), encoding="utf-8")

    # 조회용 4326 엔벨로프 (geopandas 재투영으로 정확히)
    gs = gpd.GeoSeries(polys, crs=target_crs)
    b4326 = [float(v) for v in gs.to_crs("EPSG:4326").total_bounds]  # minx,miny,maxx,maxy

    _update_road_manifest(region, out_path.name, b4326, len(polys))
    log.info("도로 %d개 → %s (region=%s)", len(polys), out_path.name, region)
    return {"file": out_path.name, "polygons": len(polys), "bounds_4326": b4326}


def _road_manifest_path() -> Path:
    return config.GEO_STORE / "road_manifest.json"


def _update_road_manifest(region: str, file: str, bounds_4326: list, n_polys: int) -> None:
    """road_manifest.json에 항목 추가/교체(같은 file명은 갱신). DEM manifest와 분리."""
    path = _road_manifest_path()
    entries: list = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("roads", []) if isinstance(data, dict) else data
    entries = [e for e in entries if e.get("file") != file]
    entries.append(
        {"region": region, "file": file, "bounds_4326": bounds_4326, "polygons": n_polys}
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="도로경계 SHP → 지역 GeoJSON 굽기 (Phase R)")
    ap.add_argument("shp_dir", help="수치지도 SHP 상위 폴더(재귀 검색)")
    ap.add_argument("--out", required=True, help="출력 GeoJSON 경로(geo_store 하위 권장)")
    ap.add_argument("--region", required=True, help="지역명(manifest 메타)")
    ap.add_argument("--target-crs", default="EPSG:5186")
    ap.add_argument("--min-area", type=float, default=1.0, help="슬리버 제거 최소 면적(m²)")
    args = ap.parse_args(argv)
    res = bake_roads(args.shp_dir, args.out, args.region, args.target_crs, args.min_area)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
