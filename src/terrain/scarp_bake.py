"""수치지형도 제방(C0050000) + 절토/성토면(F0030000) SHP → 지형 단차 GeoJSON 굽기.

등고선(5m 간격)으로 구운 DEM은 **좁고 급한 단차를 담지 못한다**. 두 종류가 그렇게 사라진다.

**제방(C0050000)** — 실측(충남 2026-09-29): 도면 제방고 중앙 2.00m인데 우리 DEM의 마루−하단
표고차는 중앙 **0.02m**(제방고의 1%, 39%는 음수). 제방이 지형에 사실상 없다. 마루폭이 2.5~4m로
5m 격자보다 좁아 등고선이 그 자리를 지나가지 않기 때문이다.
`HEIG`(실측 제방고)가 있는 선은 **14.2%뿐**이고 상단선→최근접 하단선 거리가 중앙 120.9m라
짝짓기도 대부분 불가하다. 그래서 **HEIG가 있는 상단선만** 취한다(사용자 결정 2026-09-29:
나머지 86%는 추정하지 않고 없는 채로 둔다). 하단선은 필요 없다 — 런타임이 토우 위치를
제방고×사면기울기로 유도한다.

**절토/성토면(F0030000)** — 이쪽은 성격이 다르다. 상단·하단이 모두 실제 지표면이라 **표고차는
이미 DEM에 있다**. 등고선이 망친 것은 그 표고차가 일어나는 **폭**(실제 약 8m를 20m 완경사로
번지게 함)과 엣지의 각짐이다. 그래서 높이를 만들지 않고 **파단선을 선명하게** 한다.
높이 속성이 없는 대신 상단선·하단선이 짝으로 들어오므로(충남 상단 31,719 / 하단 31,318),
두 선 사이를 직선 사면으로 조이고 z는 DEM에서 읽는다 — **추정 0**.

⚠️ 짝짓기를 "최근접 거리"로 판정하면 안 된다. 사면의 두 경계선은 양 끝에서 사면고 0으로
수렴해 맞닿으므로 최근접 거리가 0m로 나오는 게 당연하다(실측 중앙값 0.0m). 실제 사면 폭은
제방고×1:1.5 ≈ 7.5m다. 그래서 **평행 스트립** 기준으로 짝을 찾는다(`_pair_scarps`).

사용:
    python -m src.terrain.scarp_bake <shp_dir> --out geo_store/scarps_<지역>.geojson \
        --region "<지역명>" --tile-km 2
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from src.terrain.road_bake import _col, _find_shp_dedup, _iter_line_geoms, _read_layer

log = logging.getLogger(__name__)

# 레이어코드 — 둘 다 선(N3L). 면/점은 제외.
_LEVEE_PAT = re.compile(r"C0050000", re.IGNORECASE)
_SCARP_PAT = re.compile(r"F0030000", re.IGNORECASE)

# 필드 별칭 — 도엽별 수치지도는 한글, 연속수치지형도는 영문.
_F_HEIGHT = ("높이", "HEIG")
_F_UPDOWN = ("상하구분", "UDDI")     # 제방 BKU001/002, 사면 SJU001/002
_F_DIVI = ("구분", "DIVI")           # 사면 절토 SJD001 / 성토 SJD002

# 상단선 코드값(연속본) + 한글 라벨. 둘 다 받아 준다.
_TOP_CODES = {"BKU001", "SJU001", "상단"}
_BOT_CODES = {"BKU002", "SJU002", "하단"}

# 제방: 5m 격자에서 의미 있는 범위만. 최대값은 이상치 방어 — 충남 최댓값 31.5m는 댐이거나
# 오기이고, 무방비로 심으면 지형에 산이 생긴다.
MIN_LEVEE_H_M = 0.5
MAX_LEVEE_H_M = 12.0

# 사면 짝짓기 파라미터
SCARP_PAIR_MAX_M = 30.0      # 이 거리 안의 하단선만 후보
SCARP_MIN_PARALLEL = 0.7     # |cos(방향각)| 하한 — 교차하는 남의 선을 짝으로 잡지 않게
SCARP_SAMPLE_M = 10.0        # 상단선 샘플 간격(폭·방향 일관성 측정용)
SCARP_SIDE_FRAC = 0.8        # 하단선이 같은 쪽에 있어야 하는 샘플 비율


def _updown(val) -> str | None:
    """상하구분 값 → "top" | "bot" | None."""
    s = str(val).strip() if val is not None else ""
    if s in _TOP_CODES:
        return "top"
    if s in _BOT_CODES:
        return "bot"
    return None


def read_levees(shp_dir: str | Path, target_crs: str = "EPSG:5186", bbox=None) -> list:
    """제방 → [(LineString, 제방고[m]), ...]. **실측 높이가 있는 상단선만.**

    하단선과 HEIG 없는 상단선은 버린다(충남 89,100 → 약 9,600). 상한을 넘는 값은 클램프하고
    로그로 남긴다 — 조용히 심으면 지형에 산이 생긴다.
    """
    files = _find_shp_dedup(Path(shp_dir), _LEVEE_PAT, ("N3A", "N3P"))
    out: list = []
    clamped = 0
    for f in files:
        gdf = _read_layer(f, target_crs, bbox)
        c_h, c_ud = _col(gdf, _F_HEIGHT), _col(gdf, _F_UPDOWN)
        if not c_h:
            log.warning("제방 높이 필드가 없습니다(건너뜀): %s", f.name)
            continue
        for _, r in gdf.iterrows():
            g = r.geometry
            if g is None or g.is_empty:
                continue
            if c_ud and _updown(r[c_ud]) == "bot":
                continue                      # 하단선은 쓰지 않는다(토우는 높이로 유도)
            try:
                h = float(r[c_h])
            except (TypeError, ValueError):
                continue
            if h < MIN_LEVEE_H_M:
                continue
            if h > MAX_LEVEE_H_M:
                h = MAX_LEVEE_H_M
                clamped += 1
            for ls in _iter_line_geoms(g):
                if ls.length > 0:
                    out.append((ls, h))
    if clamped:
        log.warning("제방고가 상한 %.1fm를 넘어 클램프한 선 %d개 — 댐·오기 의심",
                    MAX_LEVEE_H_M, clamped)
    return out


def _dir(line):
    """선의 대표 방향(시작→끝 단위벡터). 짧은 구간(중앙 113m)에선 추세와 같다."""
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    dx, dy = x1 - x0, y1 - y0
    n = (dx * dx + dy * dy) ** 0.5
    return (dx / n, dy / n) if n else None


def _samples(line, step: float):
    """선을 step 간격으로 샘플한 Point 목록(양 끝 포함)."""
    n = max(1, int(line.length // step))
    return [line.interpolate(i / n, normalized=True) for i in range(n + 1)]


def _pair_score(top, bot) -> tuple[float, int] | None:
    """상단선-하단선 짝 점수 → (스트립 폭 중앙값[m], 하단선이 있는 쪽 부호) 또는 None.

    최근접 거리가 아니라 **샘플별 수직거리의 중앙값**을 쓴다 — 두 경계선은 양 끝에서
    맞닿으므로 최근접은 늘 0이고 판별력이 없다. 그리고 하단선이 상단선의 **한쪽에만**
    있어야 한다(도로 건너 남의 하단선을 짝으로 잡는 것을 막는다).
    """
    import statistics

    du, dv = _dir(top) or (None, None)
    if du is None:
        return None
    dists, sides = [], []
    for p in _samples(top, SCARP_SAMPLE_M):
        q = bot.interpolate(bot.project(p))
        dists.append(p.distance(q))
        # 진행방향 × (하단점 − 상단점) 의 부호 = 하단선이 좌/우 어느 쪽인가
        cross = du * (q.y - p.y) - dv * (q.x - p.x)
        if abs(cross) > 1e-9:
            sides.append(1 if cross > 0 else -1)
    if not dists or not sides:
        return None
    pos = sum(1 for s in sides if s > 0)
    frac = max(pos, len(sides) - pos) / len(sides)
    if frac < SCARP_SIDE_FRAC:
        return None
    return statistics.median(dists), (1 if pos * 2 >= len(sides) else -1)


def _pair_scarps(tops: list, bots: list) -> list[dict]:
    """절토/성토면 상단선 ↔ 하단선 짝짓기.

    tops/bots: [(LineString, DIVI값), ...]. 반환 [{"top","bot","divi","width_m","side"}].
    같은 하단선이 여러 상단선의 짝이 되는 1:N을 허용한다(사면이 토막나 있다).
    짝을 못 찾은 상단선은 **버린다** — 토우 위치도 하강 방향도 모르므로 심을 수 없다.
    """
    from shapely.strtree import STRtree

    if not tops or not bots:
        return []
    bgeoms = [b[0] for b in bots]
    tree = STRtree(bgeoms)
    pairs: list[dict] = []
    for tg, tdivi in tops:
        d = _dir(tg)
        if d is None:
            continue
        best = None
        for i in tree.query(tg.buffer(SCARP_PAIR_MAX_M)):
            bg, bdivi = bots[int(i)]
            if tdivi and bdivi and tdivi != bdivi:
                continue                       # 절토 상단은 절토 하단과만
            bd = _dir(bg)
            if bd is None or abs(d[0] * bd[0] + d[1] * bd[1]) < SCARP_MIN_PARALLEL:
                continue                       # 평행하지 않으면 같은 사면이 아니다
            sc = _pair_score(tg, bg)
            if sc is None or sc[0] > SCARP_PAIR_MAX_M:
                continue
            if best is None or sc[0] < best[0]:
                best = (sc[0], sc[1], bg)
        if best is not None:
            pairs.append({"top": tg, "bot": best[2], "divi": tdivi,
                          "width_m": best[0], "side": best[1]})
    return pairs


def read_scarps(shp_dir: str | Path, target_crs: str = "EPSG:5186", bbox=None) -> list[dict]:
    """절토/성토면 → 짝지어진 사면 목록. 짝짓기율을 로그로 낸다."""
    files = _find_shp_dedup(Path(shp_dir), _SCARP_PAT, ("N3A", "N3P"))
    tops: list = []
    bots: list = []
    for f in files:
        gdf = _read_layer(f, target_crs, bbox)
        c_ud, c_dv = _col(gdf, _F_UPDOWN), _col(gdf, _F_DIVI)
        if not c_ud:
            log.warning("절토/성토면 상하구분 필드가 없습니다(건너뜀): %s", f.name)
            continue
        for _, r in gdf.iterrows():
            g = r.geometry
            if g is None or g.is_empty:
                continue
            ud = _updown(r[c_ud])
            if ud is None:
                continue
            divi = str(r[c_dv]).strip() if c_dv else ""
            for ls in _iter_line_geoms(g):
                if ls.length <= 0:
                    continue
                (tops if ud == "top" else bots).append((ls, divi))
    pairs = _pair_scarps(tops, bots)
    if tops:
        log.info("절토/성토면 상단 %d / 하단 %d → 짝 %d개 (짝짓기율 %.1f%%)",
                 len(tops), len(bots), len(pairs), 100 * len(pairs) / len(tops))
    return pairs


# 절토/성토 `DIVI` → 런타임 종류. 코드값(연속본)과 한글 라벨 둘 다 받는다.
_DIVI_KIND = {"SJD001": "cut", "절토": "cut", "SJD002": "fill", "성토": "fill"}


def _scarp_manifest_path() -> Path:
    from src import config

    return config.GEO_STORE / "scarp_manifest.json"


def _replace_region_tiles_manifest(region: str, base_stem: str, entries: list[dict]) -> None:
    """같은 지역(또는 같은 파일 접두사)의 옛 항목을 지우고 새 타일 목록으로 교체."""
    import json

    path = _scarp_manifest_path()
    old: list = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        old = data.get("scarps", []) if isinstance(data, dict) else data
    keep = [
        e for e in old
        if e.get("region") != region and not str(e.get("file", "")).startswith(base_stem)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep + entries, ensure_ascii=False, indent=2), encoding="utf-8")


def bake_scarps_tiled(
    shp_dir: str | Path,
    out_path: str | Path,
    region: str,
    target_crs: str = "EPSG:5186",
    tile_km: float = 2.0,
) -> dict:
    """제방 마루선 + 절토/성토면 짝을 tile_km 격자로 나눠 담는다.

    ⚠️ **사면 짝은 자르지 않는다.** 상단선·하단선이 짝이어야 하강 방향과 사면 폭이 정해지는데
    타일 경계에서 한쪽만 잘려 나가면 그 짝은 못 쓴다(데크와 같은 이유). 닿는 타일마다 짝을
    **통째로** 담고, 런타임 `clip_scarps`가 짝 id `"p"`로 다시 묶는다.
    제방은 선 하나로 완결되므로 하드클립해도 되지만, 같은 코드로 다루려고 함께 복제한다.

    feature properties:
      제방 `{"k": "levee", "h": 실측 제방고}`
      사면 `{"k": "cut"|"fill", "u": "top"|"bot", "p": 짝 id, "d": DIVI}`
    """
    import json
    import math

    import geopandas as gpd
    from shapely.geometry import box as _box, mapping
    from shapely.strtree import STRtree

    out_path = Path(out_path)
    levees = read_levees(shp_dir, target_crs)
    pairs = read_scarps(shp_dir, target_crs)
    if not levees and not pairs:
        raise ValueError(f"제방·절토성토면이 없습니다: {shp_dir}")

    # 인덱싱용 평탄 목록: (대표 geometry, feature 목록)
    items: list[tuple] = []
    for g, h in levees:
        items.append((g, [{"type": "Feature", "properties": {"k": "levee", "h": round(h, 2)},
                           "geometry": mapping(g)}]))
    for pid, p in enumerate(pairs):
        kind = _DIVI_KIND.get(p["divi"], "fill")
        base = {"k": kind, "p": pid, "d": p["divi"]}
        items.append((
            p["top"].union(p["bot"]),
            [{"type": "Feature", "properties": {**base, "u": "top"},
              "geometry": mapping(p["top"])},
             {"type": "Feature", "properties": {**base, "u": "bot"},
              "geometry": mapping(p["bot"])}],
        ))

    geoms = [it[0] for it in items]
    tree = STRtree(geoms)
    bs = [g.bounds for g in geoms]
    minx = min(b[0] for b in bs); miny = min(b[1] for b in bs)
    maxx = max(b[2] for b in bs); maxy = max(b[3] for b in bs)

    tile_m = tile_km * 1000.0
    ncols = max(1, int(math.ceil((maxx - minx) / tile_m)))
    nrows = max(1, int(math.ceil((maxy - miny) / tile_m)))
    log.info("=== scarp tiled bake === 전역 %.1f×%.1f km → 최대 %d×%d 타일 "
             "(제방 %d · 사면 짝 %d)",
             (maxx - minx) / 1000, (maxy - miny) / 1000, nrows, ncols, len(levees), len(pairs))

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
            feats: list[dict] = []
            for i in sorted(int(i) for i in tree.query(tbox)):  # 원본 순서 고정(결정적 산출)
                g, fs = items[i]
                if not g.intersects(tbox):
                    continue
                feats.extend(fs)              # 자르지 않고 통째로
            if not feats:
                continue
            fc = {"type": "FeatureCollection", "crs_epsg": epsg, "features": feats}
            tile_out = out_path.with_name(f"{out_path.stem}_r{r}c{c}{out_path.suffix}")
            tile_out.parent.mkdir(parents=True, exist_ok=True)
            tile_out.write_text(json.dumps(fc), encoding="utf-8")
            b4326 = [float(v) for v in gpd.GeoSeries([tbox], crs=target_crs)
                     .to_crs("EPSG:4326").total_bounds]
            entries.append({"region": region, "file": tile_out.name,
                            "bounds_4326": b4326, "scarps": len(feats)})
            made.append(tile_out.name)
            tot += len(feats)

    _replace_region_tiles_manifest(region, out_path.stem, entries)
    log.info("=== scarp tiled bake 완료: %d개 타일 (선 조각 %d) → %s_r*c*.geojson (region=%s) ===",
             len(made), tot, out_path.stem, region)
    return {"tiles": len(made), "levees": len(levees), "pairs": len(pairs),
            "placed": tot, "files": made}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(
        description="수치지형도 제방(C0050000)·절토성토면(F0030000) → 지형 단차 GeoJSON 굽기")
    ap.add_argument("shp_dir", help="수치지도 SHP 상위 폴더(재귀 검색)")
    ap.add_argument("--out", required=True, help="출력 GeoJSON 경로(geo_store 하위 권장)")
    ap.add_argument("--region", required=True, help="지역명(manifest 메타)")
    ap.add_argument("--target-crs", default="EPSG:5186")
    ap.add_argument("--tile-km", type=float, default=2.0, help="타일 격자 크기(km). 기본 2")
    args = ap.parse_args(argv)
    res = bake_scarps_tiled(args.shp_dir, args.out, args.region, args.target_crs, args.tile_km)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
