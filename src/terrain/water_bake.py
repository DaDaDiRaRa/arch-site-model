"""수치지도 E계열 수계 폴리곤 SHP → 지역 GeoJSON(EPSG:5186) 오프라인 굽기 (수계).

도로(road_bake)와 같은 패턴: 실시간 API가 없는 수계도 로컬 SHP뿐이라 오프라인으로 지역
GeoJSON에 굽고 water_manifest.json으로 조회한다. 런타임(geometry/water.py)은 json+shapely로
읽어 bbox 클립 → **표고 고정 평면 수면**으로 만든다(도로는 DEM 드레이프, 수계는 평면).

수면 = E계열 '면(N3A)' 폴리곤: 하천경계 E0010001, 실폭하천 E0032111, 호소 E0052114 등.
선(N3L 하천중심선)·점(N3P)은 제외한다.

사용법:
    python -m src.terrain.water_bake <shp_dir> --out geo_store/water_daejeon.geojson --region "대전 서구"
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

# 수계 '면' 레이어: E계열 8자리 코드(E00xxxxx). 선(N3L)/점(N3P)은 제외 — 면(N3A)만.
_WATER_PAT = re.compile(r"E0\d{6}", re.IGNORECASE)


def _find_shp_dedup(shp_dir: Path, pat: re.Pattern, skip_prefixes: tuple[str, ...]) -> list[Path]:
    """pat 일치 SHP(도엽 중복 제거). skip_prefixes(대문자) 접두 파일은 제외. road_bake와 동형."""
    matched = sorted((p for p in shp_dir.rglob("*.shp") if pat.search(p.stem)), key=str)
    seen: set[str] = set()
    out: list[Path] = []
    for p in matched:
        if p.stem.upper().startswith(skip_prefixes):
            continue
        key = _sheet_key(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def read_water_polygons(shp_dir: str | Path, target_crs: str = "EPSG:5186") -> list:
    """E계열 수계 면(N3A) 폴리곤을 target_crs로 통일한 shapely Polygon 목록으로 반환."""
    shp_dir = Path(shp_dir)
    files = _find_shp_dedup(shp_dir, _WATER_PAT, ("N3L", "N3P"))
    if not files:
        raise FileNotFoundError(f"수계 SHP(E계열 N3A 폴리곤)를 찾을 수 없습니다: {shp_dir}")
    polys = []
    for f in files:
        gdf = _to_target_crs(gpd.read_file(f, encoding="euc-kr"), target_crs)
        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "Polygon":
                polys.append(geom)
            elif geom.geom_type == "MultiPolygon":
                polys.extend(g for g in geom.geoms if not g.is_empty)
    return polys


def bake_water(
    shp_dir: str | Path,
    out_path: str | Path,
    region: str,
    target_crs: str = "EPSG:5186",
    min_area_m2: float = 4.0,
) -> dict:
    """E계열 수계 폴리곤 → GeoJSON(EPSG:5186) + water_manifest.json 갱신.

    폴리곤 feature(properties {})를 한 FeatureCollection에 담는다. 좌표는 EPSG:5186 미터.
    런타임이 표고 고정 평면 수면으로 렌더한다.
    """
    out_path = Path(out_path)
    polys = [p for p in read_water_polygons(shp_dir, target_crs) if p.area >= min_area_m2]
    if not polys:
        raise ValueError("유효 수계 폴리곤이 없습니다(슬리버 제거 후 0).")

    from shapely.geometry import mapping

    epsg = int(str(target_crs).split(":")[-1])
    features = [{"type": "Feature", "properties": {}, "geometry": mapping(p)} for p in polys]
    fc = {"type": "FeatureCollection", "crs_epsg": epsg, "features": features}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fc), encoding="utf-8")

    gs = gpd.GeoSeries(polys, crs=target_crs)
    b4326 = [float(v) for v in gs.to_crs("EPSG:4326").total_bounds]  # minx,miny,maxx,maxy

    _update_water_manifest(region, out_path.name, b4326, len(polys))
    log.info("수계 %d개 → %s (region=%s)", len(polys), out_path.name, region)
    return {"file": out_path.name, "polygons": len(polys), "bounds_4326": b4326}


def _water_manifest_path() -> Path:
    return config.GEO_STORE / "water_manifest.json"


def _update_water_manifest(region: str, file: str, bounds_4326: list, n_polys: int) -> None:
    """water_manifest.json에 항목 추가/교체(같은 file명은 갱신). road_manifest와 동형."""
    path = _water_manifest_path()
    entries: list = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("water", []) if isinstance(data, dict) else data
    entries = [e for e in entries if e.get("file") != file]
    entries.append({"region": region, "file": file, "bounds_4326": bounds_4326, "polygons": n_polys})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="수치지도 E계열 수계 SHP → 지역 GeoJSON 굽기")
    ap.add_argument("shp_dir", help="수치지도 SHP 상위 폴더(재귀 검색)")
    ap.add_argument("--out", required=True, help="출력 GeoJSON 경로(geo_store 하위 권장)")
    ap.add_argument("--region", required=True, help="지역명(manifest 메타)")
    ap.add_argument("--target-crs", default="EPSG:5186")
    ap.add_argument("--min-area", type=float, default=4.0, help="슬리버 제거 최소 면적(m²)")
    args = ap.parse_args(argv)
    res = bake_water(args.shp_dir, args.out, args.region, args.target_crs, args.min_area)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
