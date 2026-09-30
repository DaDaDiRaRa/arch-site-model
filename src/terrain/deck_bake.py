"""수치지형도 교량·지하차도·터널 폴리곤 → 데크 GeoJSON(EPSG:5186) 오프라인 굽기.

등고선 DEM에는 교량 데크가 없어 도로가 하천 바닥까지 구워진다. 런타임(`geometry/deck.py`)이
교량 발자국을 **도로 버닝 제외 마스크**로 쓰고 그 위에 실측 양단 표고를 이은 데크를 띄운다.

수계 프록시(도로 ∩ 수계)만으로도 물을 건너는 교량은 잡히지만, 이 레이어가 있으면
**물 없는 곳의 교량**(마른 골짜기·철도 횡단)과 **터널·지하차도**까지 처리된다.

| 레이어 | 내용 | 쓰는 필드 |
|---|---|---|
| `A0070000` | 교량 (충남 17,312개) | `KIND`·`RVNM`(하천명)·`NAME`. **높이 없음** |
| `A0090000` | 지하차도·고가차도 (65개) | `DIVI`(구분)·`HEIG`(통과높이) |
| `A0110020` | 터널 (176개) | `HEIG`(통과높이) |

`HEIG`는 **통과높이(clearance)이지 데크고가 아니다** — 데크 표고는 런타임이 양단 실측에서
구한다. 고가차도는 아붓먼트가 지면이라 2점 보간이 물리적으로 틀려 런타임에서 비활성이다
(데이터만 실어 둔다; DSM이 생기면 승격).

⚠️ **타일 하드클립을 하지 않는다.** 옹벽·도로·수계는 타일 박스로 잘라 담지만, 데크는
**폴리곤의 양 끝이 종단 z를 정의**하므로 자르면 그 정보가 파괴된다(잘린 반쪽은 아붓먼트가
어디였는지 모른다). 그래서 닿는 타일마다 **폴리곤을 통째로 복제**하고 전역 id `"i"`를 실어
런타임(`clip_decks`)이 중복을 제거한다. 데이터가 작아(충남 17.5천 개) 복제 비용이 무의미하다.

사용:
    python -m src.terrain.deck_bake <shp_dir> --out geo_store/decks_<지역>.geojson \
        --region "<지역명>" --tile-km 2
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
from pathlib import Path

import geopandas as gpd

from src import config
from src.terrain.road_bake import _col, _find_shp_dedup, _read_layer

log = logging.getLogger(__name__)

# 레이어코드 → 런타임 종류. 면(N3A)만 — 선/점은 제외.
_PATS = (
    (re.compile(r"A0070000", re.IGNORECASE), "bridge"),
    (re.compile(r"A0090000", re.IGNORECASE), "grade"),     # DIVI로 viaduct/underpass 판별
    (re.compile(r"A0110020", re.IGNORECASE), "tunnel"),
)
_F_HEIGHT = ("높이", "HEIG")
_F_DIVI = ("구분", "DIVI")
_F_NAME = ("명칭", "NAME", "하천명", "RVNM")

# A0090000 `구분` — 코드값(연속본)과 한글 라벨 둘 다 받는다.
_VIADUCT = {"OCD001", "고가차도"}
_UNDERPASS = {"OCD002", "지하차도"}

MIN_DECK_AREA_M2 = 4.0    # 이보다 작은 조각은 5m 격자에서 의미가 없다


def _kind_of(base: str, divi) -> str:
    if base != "grade":
        return base
    s = str(divi).strip() if divi is not None else ""
    if s in _VIADUCT:
        return "viaduct"
    if s in _UNDERPASS:
        return "underpass"
    return "underpass"        # 구분 불명은 보수적으로 지하(노면 생략) 취급


def read_decks(shp_dir: str | Path, target_crs: str = "EPSG:5186", bbox=None) -> list[dict]:
    """교량·지하차도·터널 → [{"geom","kind","h","name"}, ...] (면만)."""
    shp_dir = Path(shp_dir)
    out: list[dict] = []
    for pat, base in _PATS:
        for f in _find_shp_dedup(shp_dir, pat, ("N3L", "N3P")):
            gdf = _read_layer(f, target_crs, bbox)
            c_h, c_d, c_n = _col(gdf, _F_HEIGHT), _col(gdf, _F_DIVI), _col(gdf, _F_NAME)
            for _, r in gdf.iterrows():
                g = r.geometry
                if g is None or g.is_empty:
                    continue
                h = None
                if c_h:
                    try:
                        h = float(r[c_h])
                    except (TypeError, ValueError):
                        h = None
                    if h is not None and h <= 0:
                        h = None
                kind = _kind_of(base, r[c_d] if c_d else None)
                name = str(r[c_n]).strip() if c_n and r[c_n] is not None else ""
                for part in getattr(g, "geoms", [g]):
                    if part.geom_type != "Polygon" or part.area < MIN_DECK_AREA_M2:
                        continue
                    out.append({"geom": part, "kind": kind, "h": h, "name": name})
    return out


def _deck_manifest_path() -> Path:
    return config.GEO_STORE / "deck_manifest.json"


def _replace_region_tiles_manifest(region: str, base_stem: str, entries: list[dict]) -> None:
    """같은 지역(또는 같은 파일 접두사)의 옛 항목을 지우고 새 타일 목록으로 교체."""
    path = _deck_manifest_path()
    old: list = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        old = data.get("decks", []) if isinstance(data, dict) else data
    keep = [
        e for e in old
        if e.get("region") != region and not str(e.get("file", "")).startswith(base_stem)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep + entries, ensure_ascii=False, indent=2), encoding="utf-8")


def bake_decks_tiled(
    shp_dir: str | Path,
    out_path: str | Path,
    region: str,
    target_crs: str = "EPSG:5186",
    tile_km: float = 2.0,
) -> dict:
    """데크 폴리곤을 tile_km 격자로 나눠 담되 **자르지 않는다**(닿는 타일마다 통째로 복제).

    feature properties = {"k": 종류, "h": 통과높이|생략, "n": 명칭|생략, "i": 전역 id}.
    """
    from shapely.geometry import box as _box, mapping
    from shapely.strtree import STRtree

    out_path = Path(out_path)
    decks = read_decks(shp_dir, target_crs)
    if not decks:
        raise ValueError(f"교량·터널 면이 없습니다: {shp_dir}")
    geoms = [d["geom"] for d in decks]
    tree = STRtree(geoms)
    bs = [g.bounds for g in geoms]
    minx = min(b[0] for b in bs); miny = min(b[1] for b in bs)
    maxx = max(b[2] for b in bs); maxy = max(b[3] for b in bs)

    tile_m = tile_km * 1000.0
    ncols = max(1, int(math.ceil((maxx - minx) / tile_m)))
    nrows = max(1, int(math.ceil((maxy - miny) / tile_m)))
    kinds: dict[str, int] = {}
    for d in decks:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    log.info("=== deck tiled bake === 전역 %.1f×%.1f km → 최대 %d×%d 타일 (면 %d개: %s)",
             (maxx - minx) / 1000, (maxy - miny) / 1000, nrows, ncols, len(decks),
             ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))

    epsg = int(str(target_crs).split(":")[-1])
    entries: list[dict] = []
    made: list[str] = []
    tot = 0
    for r in range(nrows):
        ty1 = maxy - r * tile_m
        ty0 = max(ty1 - tile_m, miny)
        for c in range(ncols):
            tx0 = minx + c * tile_m
            tx1 = min(tx0 + tile_m, maxx)
            tbox = _box(tx0, ty0, tx1, ty1)
            feats = []
            for i in sorted(int(i) for i in tree.query(tbox)):  # 원본 순서 고정(결정적 산출)
                d = decks[i]
                # 면적이 있는 겹침만 — 타일 경계선에 닿기만 한 것까지 복제하지 않는다.
                if d["geom"].intersection(tbox).area <= 0:
                    continue
                props: dict = {"k": d["kind"], "i": i}   # i = 전역 id → 런타임 중복 제거
                if d["h"] is not None:
                    props["h"] = round(d["h"], 2)
                if d["name"]:
                    props["n"] = d["name"]
                # 자르지 않는다 — 양 끝이 종단 z를 정의하므로 통째로.
                feats.append({"type": "Feature", "properties": props,
                              "geometry": mapping(d["geom"])})
            if not feats:
                continue
            fc = {"type": "FeatureCollection", "crs_epsg": epsg, "features": feats}
            tile_out = out_path.with_name(f"{out_path.stem}_r{r}c{c}{out_path.suffix}")
            tile_out.parent.mkdir(parents=True, exist_ok=True)
            tile_out.write_text(json.dumps(fc), encoding="utf-8")
            b4326 = [float(v) for v in gpd.GeoSeries([tbox], crs=target_crs)
                     .to_crs("EPSG:4326").total_bounds]
            entries.append({"region": region, "file": tile_out.name,
                            "bounds_4326": b4326, "decks": len(feats)})
            made.append(tile_out.name)
            tot += len(feats)

    _replace_region_tiles_manifest(region, out_path.stem, entries)
    log.info("=== deck tiled bake 완료: %d개 타일 (면 조각 %d, 원본 %d) → %s_r*c*.geojson "
             "(region=%s) ===", len(made), tot, len(decks), out_path.stem, region)
    return {"tiles": len(made), "decks": len(decks), "placed": tot,
            "kinds": kinds, "files": made}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(
        description="수치지형도 교량·지하차도·터널 면 → 데크 GeoJSON 굽기")
    ap.add_argument("shp_dir", help="수치지도 SHP 상위 폴더(재귀 검색)")
    ap.add_argument("--out", required=True, help="출력 GeoJSON 경로(geo_store 하위 권장)")
    ap.add_argument("--region", required=True, help="지역명(manifest 메타)")
    ap.add_argument("--target-crs", default="EPSG:5186")
    ap.add_argument("--tile-km", type=float, default=2.0, help="타일 격자 크기(km). 기본 2")
    args = ap.parse_args(argv)
    res = bake_decks_tiled(args.shp_dir, args.out, args.region, args.target_crs, args.tile_km)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
