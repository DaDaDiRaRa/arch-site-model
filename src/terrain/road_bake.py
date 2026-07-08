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
# 도로중심선 레이어코드(A0020000). 선은 N3L 접두 — 면(N3A)/점(N3P)은 제외한다.
_ROAD_CL_PAT = re.compile(r"A0020000", re.IGNORECASE)
# 보도(인도) 레이어코드(A0033320). 폴리곤(N3A) — 선/점 제외.
_SIDEWALK_PAT = re.compile(r"A0033320", re.IGNORECASE)

# 도로구분별 기본 노면폭(m) — A0020000 '도로폭'이 없거나 0일 때만 폴백(실측값 우선).
_DEFAULT_ROAD_WIDTH_M = {"대로": 25.0, "중로": 12.0, "소로": 6.0, "미분류": 8.0}
_FALLBACK_WIDTH_M = 4.0


def _resolve_width(width, road_class) -> float:
    """노면 버퍼 폭(m): 실측 도로폭 우선 → 도로구분 기본폭 → 고정 폴백."""
    if width is not None and width > 0:
        return float(width)
    return _DEFAULT_ROAD_WIDTH_M.get(road_class, _FALLBACK_WIDTH_M)


def _iter_poly_geoms(geom):
    """Polygon/MultiPolygon/GeometryCollection → Polygon 이터레이터."""
    t = getattr(geom, "geom_type", None)
    if t == "Polygon":
        yield geom
    elif t in ("MultiPolygon", "GeometryCollection"):
        for g in geom.geoms:
            yield from _iter_poly_geoms(g)


def _cl_props(width, n_lanes) -> dict:
    """중심선 feature properties: {"cl":1} + 유효한 도로폭('w')·차로수('n')만(다차선 마킹용)."""
    p = {"cl": 1}
    if width is not None and width > 0:
        p["w"] = round(float(width), 2)
    if n_lanes is not None and n_lanes >= 1:
        p["n"] = int(n_lanes)
    return p


def _find_shp_dedup(shp_dir: Path, pat: re.Pattern, skip_prefixes: tuple[str, ...]) -> list[Path]:
    """pat 일치 SHP(도엽 중복 제거). skip_prefixes(대문자) 접두 파일은 제외."""
    matched = sorted(
        (p for p in shp_dir.rglob("*.shp") if pat.search(p.stem)),
        key=str,
    )
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


def _find_road_shp(shp_dir: Path) -> list[Path]:
    """A0010000 '폴리곤' 레이어 SHP 목록(선/점 제외)."""
    return _find_shp_dedup(shp_dir, _ROAD_POLY_PAT, ("N3L", "N3P"))


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


def read_sidewalks(shp_dir: str | Path, target_crs: str = "EPSG:5186") -> list:
    """보도(A0033320) 폴리곤을 target_crs로 통일한 shapely Polygon 목록. 없으면 빈 목록."""
    shp_dir = Path(shp_dir)
    files = _find_shp_dedup(shp_dir, _SIDEWALK_PAT, ("N3L", "N3P"))
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


def read_road_centerlines(shp_dir: str | Path, target_crs: str = "EPSG:5186") -> list:
    """도로중심선(A0020000) → (LineString, 도로폭[m]|None, 도로구분|None, 차로수[int]|None) 튜플 목록.

    도로폭·도로구분은 경계 없는 도로 합성(synthesize_gap_roads)에, 도로폭·차로수는 다차선 마킹
    (bake_roads가 centerline feature props에 담아 런타임 clip_lane_markings가 사용)에 쓴다. 없으면 None.
    """
    shp_dir = Path(shp_dir)
    files = _find_shp_dedup(shp_dir, _ROAD_CL_PAT, ("N3A", "N3P"))
    out: list = []
    for f in files:
        gdf = gpd.read_file(f, encoding="euc-kr")
        gdf = _to_target_crs(gdf, target_crs)
        has_w = "도로폭" in gdf.columns
        has_c = "도로구분" in gdf.columns
        has_n = "차로수" in gdf.columns
        for _, r in gdf.iterrows():
            geom = r.geometry
            if geom is None or geom.is_empty:
                continue
            w = None
            if has_w:
                wv = r["도로폭"]
                try:
                    w = float(wv) if wv is not None and str(wv) != "" else None
                except (TypeError, ValueError):
                    w = None
            cls = r["도로구분"] if has_c else None
            n = None
            if has_n:
                nv = r["차로수"]
                try:
                    n = int(float(nv)) if nv is not None and str(nv) != "" else None
                except (TypeError, ValueError):
                    n = None
            parts = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
            for g in parts:
                if not g.is_empty and g.geom_type == "LineString":
                    out.append((g, w, cls, n))
    return out


def synthesize_gap_roads(polys, centerlines, min_area_m2: float = 1.0) -> list:
    """경계 폴리곤(A0010000)이 없는 도로(중심선만 있는 소로·골목)를 실측 도로폭으로 버퍼해 노면 합성.

    각 중심선의 'A0010000 폴리곤 밖' 구간만 도로폭/2로 버퍼 → union → A0010000 union을 빼
    중복 제거. 경계 폴리곤이 있는 도로는 실측 경계(교차부 형상까지 정확)를 그대로 쓰고, 없는
    곳만 실측 폭 리본으로 메운다 — 구간별 가장 정확한 소스를 쓰는 하이브리드. 반환: Polygon 목록.
    """
    from shapely.ops import unary_union
    from shapely.prepared import prep

    if not centerlines:
        return []
    poly_union = unary_union(polys) if polys else None
    prep_u = prep(poly_union) if poly_union is not None else None

    buffers = []
    for g, w, cls, _n in centerlines:
        if poly_union is None or not prep_u.intersects(g):
            outside = g                       # 폴리곤과 안 겹침 → 통째로 합성 대상
        else:
            outside = g.difference(poly_union)  # 겹치면 밖 구간만
        if outside.is_empty or outside.length < 1.0:
            continue                          # 사실상 폴리곤이 덮음 → 건너뜀
        buffers.append(outside.buffer(_resolve_width(w, cls) / 2.0, cap_style=1, join_style=1))
    if not buffers:
        return []
    synth = unary_union(buffers)
    if poly_union is not None:
        synth = synth.difference(poly_union)  # 실측 폴리곤과 겹침 최종 제거
    return [p for p in _iter_poly_geoms(synth) if p.area >= min_area_m2]


def bake_roads(
    shp_dir: str | Path,
    out_path: str | Path,
    region: str,
    target_crs: str = "EPSG:5186",
    min_area_m2: float = 1.0,
    fill_gaps: bool = True,
) -> dict:
    """도로경계 폴리곤(A0010000) + 도로중심선(A0020000) → GeoJSON(EPSG:5186) + manifest 갱신.

    실측 경계 폴리곤(properties {}) + (fill_gaps 시) 경계 없는 도로를 실측 도로폭으로 버퍼링한
    합성 노면(properties {"syn":1}) + 중심선(properties {"cl":1}) + 보도({"sw":1})를 한
    FeatureCollection에 담는다. 중심선은 R2 평탄화(종단 프로파일)·차선 마킹의 척추로도 쓴다.
    합성 노면은 A0010000이 소로·골목을 빠뜨려 생기는 커버리지 구멍을 실측 폭으로 메운다. 좌표=5186 m.
    """
    out_path = Path(out_path)
    polys = read_road_polygons(shp_dir, target_crs)
    polys = [p for p in polys if p.area >= min_area_m2]  # 슬리버 제거
    if not polys:
        raise ValueError("유효 도로 폴리곤이 없습니다(슬리버 제거 후 0).")
    centerlines = read_road_centerlines(shp_dir, target_crs)  # (geom, 도로폭, 도로구분)
    sidewalks = [p for p in read_sidewalks(shp_dir, target_crs) if p.area >= min_area_m2]

    # 경계 폴리곤 없는 도로(소로·골목)를 실측 도로폭으로 버퍼해 합성 → 커버리지 보완.
    synth = synthesize_gap_roads(polys, centerlines, min_area_m2) if fill_gaps else []

    from shapely.geometry import mapping

    epsg = int(str(target_crs).split(":")[-1])
    features = [{"type": "Feature", "properties": {}, "geometry": mapping(p)} for p in polys]
    features += [
        {"type": "Feature", "properties": {"syn": 1}, "geometry": mapping(p)} for p in synth
    ]
    features += [
        {"type": "Feature",
         "properties": _cl_props(w, n),
         "geometry": mapping(g)}
        for g, w, _c, n in centerlines
    ]
    features += [
        {"type": "Feature", "properties": {"sw": 1}, "geometry": mapping(p)}
        for p in sidewalks
    ]
    fc = {"type": "FeatureCollection", "crs_epsg": epsg, "features": features}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fc), encoding="utf-8")

    # 조회용 4326 엔벨로프 — 실측+합성 도로 폴리곤 전체 범위 기준.
    gs = gpd.GeoSeries(polys + synth, crs=target_crs)
    b4326 = [float(v) for v in gs.to_crs("EPSG:4326").total_bounds]  # minx,miny,maxx,maxy

    _update_road_manifest(region, out_path.name, b4326, len(polys) + len(synth))
    log.info(
        "도로 %d개(+합성 %d) + 중심선 %d개 + 보도 %d개 → %s (region=%s)",
        len(polys), len(synth), len(centerlines), len(sidewalks), out_path.name, region,
    )
    return {
        "file": out_path.name,
        "polygons": len(polys),
        "synthetic": len(synth),
        "centerlines": len(centerlines),
        "sidewalks": len(sidewalks),
        "bounds_4326": b4326,
    }


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
    ap.add_argument(
        "--no-fill-gaps", dest="fill_gaps", action="store_false",
        help="경계 폴리곤 없는 도로를 실측 도로폭으로 버퍼링해 메우는 합성을 끔(A0010000만)",
    )
    args = ap.parse_args(argv)
    res = bake_roads(
        args.shp_dir, args.out, args.region, args.target_crs, args.min_area, args.fill_gaps
    )
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
