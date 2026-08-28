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
        try:                       # 도엽별(.cpg=EUC-KR)은 자동 판별, 연속본(.cpg 없음)은 UTF-8
            gdf = gpd.read_file(f)
        except UnicodeDecodeError:
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


def _replace_region_tiles_manifest(region: str, base_stem: str, entries: list[dict]) -> None:
    """같은 region+파일접두사의 기존 타일 항목을 싹 지우고 새 목록으로 교체(road_bake와 동형)."""
    path = _water_manifest_path()
    old: list = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        old = data.get("water", []) if isinstance(data, dict) else data
    keep = [e for e in old
            if not (e.get("region") == region and str(e.get("file", "")).startswith(base_stem))]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep + entries, ensure_ascii=False, indent=2), encoding="utf-8")


def bake_water_tiled(
    shp_dir: str | Path,
    out_path: str | Path,
    region: str,
    target_crs: str = "EPSG:5186",
    tile_km: float = 2.0,
    min_area_m2: float = 4.0,
) -> dict:
    """넓은 지역용: 수계를 tile_km 격자로 **하드 클립**해 타일별 GeoJSON을 굽는다.

    단일 지역 파일은 넓은 도(道)에서 수백 MB가 된다(경기도 실측 200MB, 서울 5.8MB의 35배).
    런타임이 요청마다 그걸 통째로 파싱하면 도로가 겪었던 문제(요청당 수십 초)가 그대로 재현되므로
    도로(bake_roads_tiled)와 같은 방식으로 공간 분할한다. 타일은 정확히 타일 박스로 잘라
    겹침도 틈도 없다. 런타임은 find_water_files가 겹치는 타일만 읽는다.
    """
    import math

    from shapely.geometry import box as _box, mapping
    from shapely.strtree import STRtree

    out_path = Path(out_path)
    polys = [p for p in read_water_polygons(shp_dir, target_crs) if p.area >= min_area_m2]
    if not polys:
        raise ValueError("유효 수계 폴리곤이 없습니다(슬리버 제거 후 0).")
    tree = STRtree(polys)
    bs = [g.bounds for g in polys]
    minx = min(b[0] for b in bs); miny = min(b[1] for b in bs)
    maxx = max(b[2] for b in bs); maxy = max(b[3] for b in bs)

    tile_m = tile_km * 1000.0
    ncols = max(1, int(math.ceil((maxx - minx) / tile_m)))
    nrows = max(1, int(math.ceil((maxy - miny) / tile_m)))
    log.info("=== water tiled bake === 전역 %.1f×%.1f km → 최대 %d×%d 타일 (수계 %d개)",
             (maxx - minx) / 1000, (maxy - miny) / 1000, nrows, ncols, len(polys))

    epsg = int(str(target_crs).split(":")[-1])
    entries: list[dict] = []; made: list[str] = []; tot = 0
    for r in range(nrows):
        ty1 = maxy - r * tile_m
        ty0 = max(ty1 - tile_m, miny)
        for c in range(ncols):
            tx0 = minx + c * tile_m
            tx1 = min(tx0 + tile_m, maxx)
            tbox = _box(tx0, ty0, tx1, ty1)
            clipped = []
            for i in sorted(int(i) for i in tree.query(tbox)):   # 원본 순서 고정(결정적 산출)
                g = polys[i]
                if not g.intersects(tbox):
                    continue
                inter = g.intersection(tbox)
                for part in (inter.geoms if hasattr(inter, "geoms") else [inter]):
                    if part.geom_type == "Polygon" and not part.is_empty and part.area >= min_area_m2:
                        clipped.append(part)
            if not clipped:
                continue
            fc = {"type": "FeatureCollection", "crs_epsg": epsg,
                  "features": [{"type": "Feature", "properties": {}, "geometry": mapping(p)}
                               for p in clipped]}
            tile_out = out_path.with_name(f"{out_path.stem}_r{r}c{c}{out_path.suffix}")
            tile_out.parent.mkdir(parents=True, exist_ok=True)
            tile_out.write_text(json.dumps(fc), encoding="utf-8")
            b4326 = [float(v) for v in gpd.GeoSeries([tbox], crs=target_crs)
                     .to_crs("EPSG:4326").total_bounds]
            entries.append({"region": region, "file": tile_out.name,
                            "bounds_4326": b4326, "polygons": len(clipped)})
            made.append(tile_out.name); tot += len(clipped)
    _replace_region_tiles_manifest(region, out_path.stem, entries)
    log.info("=== water tiled bake 완료: %d개 타일 (수계 %d) → %s_r*c*.geojson (region=%s) ===",
             len(made), tot, out_path.stem, region)
    return {"tiles": len(made), "polygons": tot, "files": made}


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
    ap.add_argument(
        "--tile-km", type=float, default=0.0,
        help="0=단일 지역 파일(기본). >0이면 그 km 격자로 하드클립 타일링 "
             "(넓은 도는 필수 - 경기도 단일파일 200MB)",
    )
    args = ap.parse_args(argv)
    if args.tile_km and args.tile_km > 0:
        res = bake_water_tiled(args.shp_dir, args.out, args.region, args.target_crs,
                               tile_km=args.tile_km, min_area_m2=args.min_area)
    else:
        res = bake_water(args.shp_dir, args.out, args.region, args.target_crs, args.min_area)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
