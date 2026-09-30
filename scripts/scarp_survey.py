"""지형 단차(제방·절토/성토면) 소스 품질 실측.

`src/terrain/scarp_bake.py`를 심기 전에 **소스가 쓸 만한지**를 숫자로 확인하는 스크립트다.
세 가지를 잰다.

1. **제방 실측 높이 보유율** — `HEIG`가 있는 선만 쓰기로 했으므로(추정 금지) 그 비율이 곧
   커버리지다. 충남 기준선: 상단선의 14.2%, 제방고 중앙 2.0m.
2. **제방이 DEM에 얼마나 담겼는가** — 이 작업의 근거. 마루선 위 DEM 표고 − 양옆 15m 지점의
   높은 쪽. 충남 기준선: 도면 2.0m vs DEM **−0.67m**, 음수가 93.4% — 마루가 주변보다 오히려
   낮게 읽힌다. (하단선과 짝지어 재는 다른 방법으로는 +0.02m였다. 방법에 따라 값은 달라지지만
   결론은 같다 — **제방이 지형에 없다**.) 심은 뒤 다시 돌려 `h ± 0.2m`가 되는지 본다.
3. **사면 짝짓기율과 스트립 폭** — 설계의 유일한 미검증 가정이었다. 충남 기준선: 짝짓기율
   93.1%, 폭 p10 2.5 / p50 6.8 / p90 15.9 m. 짝짓기율이 70% 밑이면 `_pair_scarps` 재검토.

    python scripts/scarp_survey.py D:\\APPS\\SHP\\ctnu_도영역\\충청남도\\_shp --json out.json

⚠️ 2번은 DEM 비축이 있는 지역만 가능하다(`geo_store/manifest.json`).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.terrain.scarp_bake import (  # noqa: E402
    _F_HEIGHT,
    _F_UPDOWN,
    _LEVEE_PAT,
    _updown,
    read_levees,
    read_scarps,
)

DEM_SAMPLES = 400        # DEM 대조 표본 수(전수는 불필요 — 분포만 본다)
SIDE_PROBE_M = 15.0      # 마루에서 양옆으로 이만큼 나가 지반 표고를 읽는다


def levee_coverage(shp_dir: Path) -> dict:
    """제방 상단선 중 실측 높이 보유율 — 원본 전량을 센다."""
    from src.terrain.road_bake import _col, _find_shp_dedup, _read_layer

    tops = heights = 0
    vals: list[float] = []
    for f in _find_shp_dedup(shp_dir, _LEVEE_PAT, ("N3A", "N3P")):
        gdf = _read_layer(f, "EPSG:5186", None)
        c_h, c_ud = _col(gdf, _F_HEIGHT), _col(gdf, _F_UPDOWN)
        if not c_h:
            continue
        for _, r in gdf.iterrows():
            if c_ud and _updown(r[c_ud]) != "top":
                continue
            tops += 1
            try:
                h = float(r[c_h])
            except (TypeError, ValueError):
                continue
            if h > 0:
                heights += 1
                vals.append(h)
    out = {"top_lines": tops, "with_height": heights,
           "height_pct": round(100 * heights / tops, 1) if tops else 0.0}
    if vals:
        a = np.asarray(vals)
        out |= {"h_median_m": round(float(np.median(a)), 2),
                "h_p10_m": round(float(np.percentile(a, 10)), 2),
                "h_p90_m": round(float(np.percentile(a, 90)), 2),
                "h_max_m": round(float(a.max()), 2)}
    return out


def levee_in_dem(levees: list) -> dict:
    """도면 제방고 vs 우리 DEM이 담은 표고차.

    마루선 위 DEM 표고 − 양옆 SIDE_PROBE_M 지점의 **높은 쪽**. 제방이 DEM에 있으면 이 값이
    제방고에 가깝고, 없으면 0에 가깝다.
    """
    from src import config
    from src.geo.crs import to_4326
    from src.terrain.dem import clip_dem_mosaic
    from src.terrain.store import find_tiles

    # DEM 비축이 있는 구역으로 표본을 좁힌다 — 제방이 많은 곳 주변 9×9km 한 덩이.
    cents = [(g.centroid.x, g.centroid.y, h, g) for g, h in levees]
    cents.sort(key=lambda t: (t[0], t[1]))
    cx, cy = cents[len(cents) // 2][0], cents[len(cents) // 2][1]
    sel = [c for c in cents if abs(c[0] - cx) < 4500 and abs(c[1] - cy) < 4500]
    lo, hi = to_4326(cx - 5000, cy - 5000), to_4326(cx + 5000, cy + 5000)
    tiles = find_tiles((lo[0], lo[1], hi[0], hi[1]))
    if not tiles or not sel:
        return {"skip": "DEM 타일 없음 또는 표본 없음"}
    dem = clip_dem_mosaic([config.dem_tile_path(t["file"]) for t in tiles],
                          (cx - 5000, cy - 5000, cx + 5000, cy + 5000), (0.0, 0.0))

    got, drawn = [], []
    for x, y, h, g in sel[:DEM_SAMPLES]:
        pts = list(g.coords)
        if len(pts) < 2:
            continue
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        dx, dy = x2 - x1, y2 - y1
        n = (dx * dx + dy * dy) ** 0.5
        if n < 1:
            continue
        nx, ny = -dy / n, dx / n
        zc = dem.sample(x, y)
        za = dem.sample(x + nx * SIDE_PROBE_M, y + ny * SIDE_PROBE_M)
        zb = dem.sample(x - nx * SIDE_PROBE_M, y - ny * SIDE_PROBE_M)
        if None in (zc, za, zb):
            continue
        got.append(zc - max(za, zb))
        drawn.append(h)
    if not got:
        return {"skip": "DEM 범위 안 제방 표본 없음"}
    a, d = np.asarray(got), np.asarray(drawn)
    return {
        "samples": int(a.size),
        "drawn_median_m": round(float(np.median(d)), 2),
        "dem_median_m": round(float(np.median(a)), 2),
        "dem_p90_m": round(float(np.percentile(a, 90)), 2),
        "negative_pct": round(100 * float((a < 0).mean()), 1),
        "captured_pct": round(100 * float(np.median(a)) / float(np.median(d)), 1),
    }


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="제방·절토/성토면 소스 품질 실측")
    ap.add_argument("shp_dir", help="C0050000/F0030000이 풀려 있는 SHP 폴더(재귀)")
    ap.add_argument("--json", help="결과 저장 경로")
    ap.add_argument("--skip-dem", action="store_true", help="DEM 대조 생략(빠름)")
    a = ap.parse_args(argv)
    d = Path(a.shp_dir)

    out: dict = {"shp_dir": str(d)}
    out["levee_coverage"] = levee_coverage(d)
    levees = read_levees(d)
    out["levee_used"] = {
        "lines": len(levees),
        "length_km": round(sum(g.length for g, _ in levees) / 1000, 1),
    }
    if not a.skip_dem and levees:
        out["levee_in_dem"] = levee_in_dem(levees)

    pairs = read_scarps(d)
    if pairs:
        w = np.asarray([p["width_m"] for p in pairs])
        out["scarp"] = {
            "pairs": len(pairs),
            "width_p10_m": round(float(np.percentile(w, 10)), 1),
            "width_p50_m": round(float(np.percentile(w, 50)), 1),
            "width_p90_m": round(float(np.percentile(w, 90)), 1),
        }
    else:
        out["scarp"] = {"pairs": 0}

    c = out["levee_coverage"]
    print(f"\n제방 상단선 {c['top_lines']:,} 중 실측 높이 {c['with_height']:,} "
          f"({c['height_pct']}%) · 제방고 중앙 {c.get('h_median_m')}m "
          f"(최대 {c.get('h_max_m')}m)")
    print(f"쓸 제방 {out['levee_used']['lines']:,}개 · 연장 {out['levee_used']['length_km']:,}km")
    if "levee_in_dem" in out:
        m = out["levee_in_dem"]
        if "skip" in m:
            print(f"DEM 대조: {m['skip']}")
        else:
            print(f"DEM 대조 {m['samples']}표본 — 도면 {m['drawn_median_m']}m vs "
                  f"DEM {m['dem_median_m']}m = {m['captured_pct']}% "
                  f"(음수 {m['negative_pct']}%)")
    s = out["scarp"]
    if s["pairs"]:
        print(f"사면 짝 {s['pairs']:,} · 스트립 폭 p10 {s['width_p10_m']} / "
              f"p50 {s['width_p50_m']} / p90 {s['width_p90_m']} m")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print(f"→ {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
