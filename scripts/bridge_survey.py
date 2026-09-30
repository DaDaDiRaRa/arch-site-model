"""교량 코즈웨이 실측 — 도로가 수면 아래로 얼마나 잠기는가.

등고선 DEM + 도로 버닝 파이프라인은 **교량 데크를 하천 바닥으로 깎는다**. 원인이 산술적이다:

  1. `road.burn_roads`가 데크 셀을 중심선 최근접 IDW로 버닝한다. 그 중심선 z 자체가 DEM
     샘플이라 강 중간에서는 하천 바닥이다.
  2. `water.burn_water`가 수계 폴리곤 내부 **모든 셀**을 수면 표고로 덮어써 1의 결과를 지운다.
  3. `road.build_unified_surface`가 도로 정점 z를 그 DEM에서 다시 읽는다.
  4. 크라운이 도로 정점만 최대 `ROAD_CROWN_PCT × ROAD_CROWN_CAP_M` 더 낮춘다.
  5. 수면 메시는 `수면표고 + WATER_LIFT_M`.

→ 도로가 수면보다 낮아지는 폭이 구조적으로 보장된다. 이 스크립트가 그 값을 잰다.

수정 전/후 같은 명령으로 돌려 비교하는 것이 목적이므로, 출력은 **기준선 재현**에 필요한
수치만 낸다. 수정 후 기대값: 잠긴 정점 0, `road_z − water_z` 중앙값 양수.

    python scripts/bridge_survey.py --radius 400 "서울특별시 서초구 반포동 2" ...
    python scripts/bridge_survey.py --json out.json   (기본 주소 목록)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 수면에서 이 거리 안에 있는 도로 정점만 본다(교량·물가 도로) — 멀리 있는 도로는 무관.
NEAR_M = 3.0

# 도로 타일 + 수계 비축이 **모두** 있는 지점들(2026-09-29 실측 확인). 대전·광주 등은 도로
# 비축이 옛 단일 타일뿐이라 반경이 밖으로 나가 쓸 수 없다 — road_manifest 확인 후 추가할 것.
DEFAULT_SITES = [
    "서울특별시 용산구 이촌동 302",      # 한강 — 한강대교·동작대교 일대(가장 많이 잠기는 곳)
    "서울특별시 서초구 반포동 2",        # 한강 — 반포대교 일대
    "서울특별시 영등포구 여의도동 8",    # 한강 — 서강대교 일대
    "서울특별시 성동구 성수동1가 685",   # 중랑천 합류부 — 중소 하천 + 지형 기복
]


def _surface(address: str, radius_m: int):
    """주소 → build_surface 결과(도로 메시 + 수계 폴리곤 + 수면 표고)."""
    from src import config
    from src.geo.bbox import bbox_from_point
    from src.geo.geocode import clean_address, geocode
    from src.pipeline import _bbox_4326_to_5186
    from src.pipeline_surface import build_surface
    from src.terrain.dem import clip_dem_mosaic
    from src.terrain.store import find_tiles

    coord = geocode(clean_address(address))
    bbox = bbox_from_point(coord["lon"], coord["lat"], radius_m)
    b5186 = _bbox_4326_to_5186(bbox)
    offset = (b5186[0], b5186[1])

    tiles = find_tiles(bbox)
    if not tiles:
        raise RuntimeError("DEM 타일 없음")
    dem = clip_dem_mosaic([config.dem_tile_path(t["file"]) for t in tiles], b5186, offset)

    warnings: list[str] = []
    surf = build_surface(
        dem, bbox, b5186, offset,
        {"terrain": True, "roads": True, "water": True},
        [], warnings,
    )
    return surf, warnings


def measure(address: str, radius_m: int) -> dict:
    import numpy as np
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    surf, warnings = _surface(address, radius_m)
    road = surf.road
    if road is None or not surf.water_features:
        return {"address": address, "skip": "도로 또는 수계 없음", "warnings": warnings}

    # 수계 폴리곤(구멍 포함) + 폴리곤별 수면 표고. 정점마다 **가장 가까운** 수계의 z를 쓴다.
    polys, zs = [], []
    for f, wz in zip(surf.water_features, surf.water_zs):
        try:
            p = Polygon(f.rings[0], f.rings[1:] or None)
        except Exception:  # noqa: BLE001 — 링이 망가진 피처는 건너뜀
            continue
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty:
            continue
        polys.append(p)
        zs.append(wz)
    if not polys:
        return {"address": address, "skip": "유효 수계 폴리곤 없음", "warnings": warnings}

    from shapely.strtree import STRtree

    tree = STRtree(polys)
    water_u = unary_union(polys)
    V = np.asarray(road.vertices, dtype=float)
    diffs = []
    for x, y, z in V:
        from shapely.geometry import Point

        p = Point(x, y)
        if water_u.distance(p) > NEAR_M:
            continue
        diffs.append(z - zs[tree.nearest(p)])
    d = np.asarray(diffs, dtype=float)

    rec = {
        "address": address,
        "radius_m": radius_m,
        "road_vertices": int(len(V)),
        "near_water": int(d.size),
        "warnings": warnings,
    }
    if d.size:
        rec |= {
            "submerged": int((d < 0).sum()),
            "median_dz_m": round(float(np.median(d)), 3),
            "p05_dz_m": round(float(np.percentile(d, 5)), 3),
            "p95_dz_m": round(float(np.percentile(d, 95)), 3),
            "min_dz_m": round(float(d.min()), 3),
        }
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="교량 코즈웨이(도로 수면 아래) 실측")
    ap.add_argument("addresses", nargs="*", help="비우면 기본 지점 목록")
    ap.add_argument("--radius", type=int, default=400)
    ap.add_argument("--json", help="결과를 이 경로에 JSON으로 저장")
    a = ap.parse_args(argv)

    sites = a.addresses or DEFAULT_SITES
    rows = []
    for addr in sites:
        try:
            rows.append(measure(addr, a.radius))
        except Exception as e:  # noqa: BLE001 — 한 지점 실패가 전체를 막지 않게
            rows.append({"address": addr, "error": f"{type(e).__name__}: {e}"})

    tot_near = sum(r.get("near_water", 0) for r in rows)
    tot_sub = sum(r.get("submerged", 0) for r in rows)
    tot_v = sum(r.get("road_vertices", 0) for r in rows)
    print(f"지점 {len(rows)}곳 · 도로 정점 {tot_v:,}")
    print(f"수면 {NEAR_M:g}m 이내 도로 정점 {tot_near:,} · 그중 수면 아래 {tot_sub:,}")
    for r in rows:
        if "error" in r or "skip" in r:
            print(f"  {r['address']}: {r.get('error') or r['skip']}")
            continue
        if not r.get("near_water"):
            print(f"  {r['address']}: 수면 근처 도로 없음")
            continue
        print(
            f"  {r['address']}: 근접 {r['near_water']:,} · 잠김 {r['submerged']:,}"
            f" · dz 중앙 {r['median_dz_m']:+.2f}m"
            f" (p05 {r['p05_dz_m']:+.2f} / p95 {r['p95_dz_m']:+.2f} / 최저 {r['min_dz_m']:+.2f})"
        )
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f"→ {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
