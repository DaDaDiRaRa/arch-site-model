"""수치지형도 옹벽(F0040000) SHP → 지역 GeoJSON(EPSG:5186) 오프라인 굽기.

등고선만으로 만든 DEM은 옹벽 자리를 **완만한 경사로 뭉갠다**. 5m 격자에 2m 옹벽이 있으면
등고선은 그 자리를 지나가지 않거나 한 줄만 스쳐서, 실제로는 수직으로 뚝 떨어지는 레벨차가
비스듬한 비탈로 나온다. 대지모델에서 레벨차는 설계 검토에 직결되므로 이걸 살려야 한다.

수치지형도 F0040000은 **옹벽 상단선 + 실측 높이(m)**를 준다(경기도 실측: 옹벽 373,341개 중
90.9%가 높이 보유, 중앙값 2.0m, 총 연장 19,128km). 이 선과 높이를 런타임(geometry/wall.py)이
DEM에 수직 단차로 심는다.

**상단/하단 구분**: 연속수치지형도는 `UDDI`가 RMU001(상단)/RMU002(하단)이고 높이는 상단선에만
실린다(실측에서 RMU001 339,259개 ≈ 높이>0 339,335개로 일치). 도엽별 수치지도는 한글 `상하구분`
='상단'. 어느 쪽이든 **높이가 있는 선만** 취하면 되므로 상하 코드에 의존하지 않는다.

사용:
    python -m src.terrain.wall_bake <shp_dir> --out geo_store/walls_<지역>.geojson \
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
from src.terrain.road_bake import _col, _find_shp_dedup, _iter_line_geoms, _read_layer

log = logging.getLogger(__name__)

# 옹벽 레이어코드(F0040000). 선(N3L) — 면/점은 제외.
_WALL_PAT = re.compile(r"F0040000", re.IGNORECASE)
# 높이 필드 — 도엽별은 한글, 연속수치지형도는 영문.
_F_HEIGHT = ("높이", "HEIG")
# 너무 낮은 옹벽은 5m 격자에서 의미가 없다(경계석·연석 수준).
MIN_WALL_H_M = 0.5


def read_walls(shp_dir: str | Path, target_crs: str = "EPSG:5186", bbox=None) -> list:
    """옹벽 → [(LineString, 높이[m]), ...]. 높이 없는 선(하단선 등)은 버린다."""
    shp_dir = Path(shp_dir)
    files = _find_shp_dedup(shp_dir, _WALL_PAT, ("N3A", "N3P"))
    out: list = []
    for f in files:
        gdf = _read_layer(f, target_crs, bbox)
        c_h = _col(gdf, _F_HEIGHT)
        if not c_h:
            log.warning("옹벽 높이 필드가 없습니다(건너뜀): %s", f.name)
            continue
        for _, r in gdf.iterrows():
            geom = r.geometry
            if geom is None or geom.is_empty:
                continue
            try:
                h = float(r[c_h])
            except (TypeError, ValueError):
                continue
            if h < MIN_WALL_H_M:
                continue
            for ls in _iter_line_geoms(geom):
                if ls.length > 0:
                    out.append((ls, h))
    return out


def _wall_manifest_path() -> Path:
    return config.GEO_STORE / "wall_manifest.json"


def _replace_region_tiles_manifest(region: str, base_stem: str, entries: list[dict]) -> None:
    """같은 지역(또는 같은 파일 접두사)의 옛 항목을 지우고 새 타일 목록으로 교체."""
    path = _wall_manifest_path()
    old: list = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        old = data.get("walls", []) if isinstance(data, dict) else data
    keep = [
        e for e in old
        if e.get("region") != region and not str(e.get("file", "")).startswith(base_stem)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep + entries, ensure_ascii=False, indent=2), encoding="utf-8")


def bake_walls_tiled(
    shp_dir: str | Path,
    out_path: str | Path,
    region: str,
    target_crs: str = "EPSG:5186",
    tile_km: float = 2.0,
) -> dict:
    """옹벽 선을 tile_km 격자로 **하드 클립**해 타일별 GeoJSON을 굽는다.

    도로·수계와 동일한 공간 분할 방식(겹침도 틈도 없음). 런타임은 겹치는 타일만 읽는다.
    feature properties = {"h": 높이[m]}.
    """
    from shapely.geometry import box as _box, mapping
    from shapely.strtree import STRtree

    out_path = Path(out_path)
    walls = read_walls(shp_dir, target_crs)
    if not walls:
        raise ValueError(f"높이 있는 옹벽이 없습니다: {shp_dir}")
    geoms = [w[0] for w in walls]
    tree = STRtree(geoms)
    bs = [g.bounds for g in geoms]
    minx = min(b[0] for b in bs); miny = min(b[1] for b in bs)
    maxx = max(b[2] for b in bs); maxy = max(b[3] for b in bs)

    tile_m = tile_km * 1000.0
    ncols = max(1, int(math.ceil((maxx - minx) / tile_m)))
    nrows = max(1, int(math.ceil((maxy - miny) / tile_m)))
    log.info("=== wall tiled bake === 전역 %.1f×%.1f km → 최대 %d×%d 타일 (옹벽 %d개, 연장 %.0f km)",
             (maxx - minx) / 1000, (maxy - miny) / 1000, nrows, ncols,
             len(walls), sum(g.length for g in geoms) / 1000)

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
                g, h = walls[i]
                if not g.intersects(tbox):
                    continue
                for ls in _iter_line_geoms(g.intersection(tbox)):
                    if ls.length <= 0:
                        continue
                    feats.append({"type": "Feature", "properties": {"h": round(h, 2)},
                                  "geometry": mapping(ls)})
            if not feats:
                continue
            fc = {"type": "FeatureCollection", "crs_epsg": epsg, "features": feats}
            tile_out = out_path.with_name(f"{out_path.stem}_r{r}c{c}{out_path.suffix}")
            tile_out.parent.mkdir(parents=True, exist_ok=True)
            tile_out.write_text(json.dumps(fc), encoding="utf-8")
            b4326 = [float(v) for v in gpd.GeoSeries([tbox], crs=target_crs)
                     .to_crs("EPSG:4326").total_bounds]
            entries.append({"region": region, "file": tile_out.name,
                            "bounds_4326": b4326, "walls": len(feats)})
            made.append(tile_out.name)
            tot += len(feats)

    _replace_region_tiles_manifest(region, out_path.stem, entries)
    log.info("=== wall tiled bake 완료: %d개 타일 (옹벽 조각 %d) → %s_r*c*.geojson (region=%s) ===",
             len(made), tot, out_path.stem, region)
    return {"tiles": len(made), "walls": tot, "files": made}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="수치지형도 옹벽(F0040000) SHP → 지역 GeoJSON 굽기")
    ap.add_argument("shp_dir", help="수치지도 SHP 상위 폴더(재귀 검색)")
    ap.add_argument("--out", required=True, help="출력 GeoJSON 경로(geo_store 하위 권장)")
    ap.add_argument("--region", required=True, help="지역명(manifest 메타)")
    ap.add_argument("--target-crs", default="EPSG:5186")
    ap.add_argument("--tile-km", type=float, default=2.0, help="타일 격자 크기(km). 기본 2")
    args = ap.parse_args(argv)
    res = bake_walls_tiled(args.shp_dir, args.out, args.region, args.target_crs, args.tile_km)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
