"""조성 대지(깎고 채워 평평하게 만든 대지) 판별 조사 도구.

우리 지형은 수치지도 등고선(측량 시점의 원지형)이라, 그 뒤 공장·창고 부지로 평탄화된 대지는 비탈로 남는다.
"지목이 개발지 + 필지에 건물 + 원지형 고저차 큼"이면 조성 대지로 추정할 수 있는지 보려고 만든 도구.

    python scripts/graded_site_survey.py addr "충청남도 아산시 초사동 450-1" "충청남도 아산시 초사동 257-1"
    python scripts/graded_site_survey.py sample --n 300 --seed 7 --out output/graded_survey.json

addr   : 주소별 필지·건물·원지형 고저차·옹벽 근접 여부
sample : 전국 DEM 타일 범위에서 무작위 지점 → 주변 필지(건물 있는 개발지) 고저차 분포 (표본 조사)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon, shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.geo.bbox import bbox_from_point  # noqa: E402
from src.geo.crs import to_5186  # noqa: E402
from src.geo.vworld import DATASET_BUILDING, DATASET_CADASTRAL, VWorldClient  # noqa: E402
from src.terrain.dem import clip_dem_mosaic  # noqa: E402
from src.terrain.store import find_tiles, find_wall_files, load_manifest  # noqa: E402

# 개발된 땅 지목(연속지적 jibun 끝 글자): 대·공장용지·창고용지·주차장·주유소용지·학교용지·잡종지
DEVELOPED = {"대", "장", "창", "주", "유", "학", "잡"}


def jimok(jibun: str) -> str:
    j = (jibun or "").strip()
    return j[-1] if j and not j[-1].isdigit() else ""


def _poly5186(geom) -> Polygon | None:
    g = shape(geom)
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda p: p.area)
    if g.geom_type != "Polygon":
        return None
    return Polygon([to_5186(x, y) for x, y in g.exterior.coords])


def parcel_relief(P5: Polygon, dem, step: float = 2.0) -> dict | None:
    x0, y0, x1, y1 = P5.bounds
    zs = [dem.sample(x, y) for x in np.arange(x0, x1 + step, step) for y in np.arange(y0, y1 + step, step)
          if P5.contains(Point(x, y))]
    zs = np.array([z for z in zs if z is not None])
    if zs.size < 4:
        return None
    return {
        "zmin": round(float(zs.min()), 2), "zmax": round(float(zs.max()), 2),
        "relief": round(float(zs.max() - zs.min()), 2),
        "relief_p": round(float(np.percentile(zs, 90) - np.percentile(zs, 10)), 2),  # 튐값 제외 고저차
        "pad_high": round(float(np.percentile(zs, 90)), 2),                           # "높은 쪽" 평탄화 높이
    }


def analyze_area(client, lon, lat, radius=120, only_point=False):
    """지점 주변(또는 지점이 든 필지 하나)의 개발지+건물 필지별 고저차."""
    bb = bbox_from_point(lon, lat, radius)
    parcels = client.get_features(DATASET_CADASTRAL, bb, geometry=True)
    blds = client.get_features(DATASET_BUILDING, bb, geometry=True)
    tiles = find_tiles(bb)
    if not tiles:
        return {"error": "DEM 없음"}
    b5 = [(_poly5186(b["geometry"]), b["properties"]) for b in blds]
    b5 = [(p, pr) for p, pr in b5 if p is not None and p.is_valid]
    xs, ys = zip(*[to_5186(x, y) for x, y in ((bb[0], bb[1]), (bb[2], bb[3]))])
    dem = clip_dem_mosaic([config.dem_tile_path(t["file"]) for t in tiles],
                          (min(xs) - 60, min(ys) - 60, max(xs) + 60, max(ys) + 60), (0.0, 0.0))
    walls = []
    wl = find_wall_files(bb)
    if wl:
        from src.geometry.wall import clip_walls
        from src.pipeline import _bbox_4326_to_5186
        walls = [LineString(w.points) for w in clip_walls([config.wall_file_path(f["file"]) for f in wl],
                                                          _bbox_4326_to_5186(bb), (0.0, 0.0)) if len(w.points) >= 2]
    pt = Point(lon, lat)
    out = []
    for p in parcels:
        pr = p["properties"]
        if only_point and not shape(p["geometry"]).contains(pt):
            continue
        P5 = _poly5186(p["geometry"])
        if P5 is None or not P5.is_valid or P5.area < 100:
            continue
        inside = [(bp, bpr) for bp, bpr in b5 if bp.intersection(P5).area > 0.3 * bp.area]
        rel = parcel_relief(P5, dem)
        if rel is None:
            continue
        out.append({
            "jibun": pr.get("jibun"), "pnu": pr.get("pnu"), "jimok": jimok(pr.get("jibun")),
            "area": round(P5.area), "buildings": len(inside),
            "floors": [bp.get("gro_flo_co") for _, bp in inside][:5],
            "wall_on_edge": any(w.distance(P5.exterior) < 3 for w in walls),
            **rel,
        })
    return {"lon": lon, "lat": lat, "parcels": out}


def cmd_addr(args):
    from src.geo.geocode import geocode

    c = VWorldClient(config.VWORLD_KEY, config.VWORLD_DOMAIN)
    for a in args.addresses:
        g = geocode(a)
        r = analyze_area(c, g["lon"], g["lat"], only_point=True)
        for p in r.get("parcels", []):
            print(f"{a}: 지번 {p['jibun']} 지목 {p['jimok']} 면적 {p['area']}㎡ 건물 {p['buildings']}동(층수 {p['floors']}) "
                  f"원지형 {p['zmin']}~{p['zmax']}m 고저차 {p['relief']}m(튐값 제외 {p['relief_p']}m) "
                  f"높은쪽 평탄화 {p['pad_high']}m 경계 옹벽 {'있음' if p['wall_on_edge'] else '없음'}")
        if not r.get("parcels"):
            print(a, r)


def cmd_sample(args):
    rng = random.Random(args.seed)
    tiles = load_manifest()
    c = VWorldClient(config.VWORLD_KEY, config.VWORLD_DOMAIN)
    rows, tried = [], 0
    while len(rows) < args.n and tried < args.n * 6:
        tried += 1
        t = rng.choice(tiles)
        b = t["bounds_4326"]
        lon, lat = rng.uniform(b[0], b[2]), rng.uniform(b[1], b[3])
        try:
            # 건물이 있는 곳만 — 빈 산·논밭 지점은 건너뜀(개발지 조사 목적)
            if c.count(DATASET_BUILDING, bbox_from_point(lon, lat, 120)) == 0:
                continue
            r = analyze_area(c, lon, lat)
        except Exception as e:  # noqa: BLE001
            print("skip", e, file=sys.stderr)
            continue
        dev = [p for p in r.get("parcels", []) if p["jimok"] in DEVELOPED and p["buildings"] > 0]
        if not dev:
            continue
        rows.append({"lon": lon, "lat": lat, "region": t.get("region"), "parcels": dev})
        print(f"[{len(rows)}/{args.n}] {t.get('region')} 개발지+건물 필지 {len(dev)}", file=sys.stderr)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    summarize(rows)


def summarize(rows):
    ps = [p for r in rows for p in r["parcels"]]
    rel = np.array([p["relief_p"] for p in ps])
    print(f"\n표본 지점 {len(rows)}곳, 개발지+건물 필지 {len(ps)}개")
    for th in (1, 2, 3, 4, 6):
        print(f"  튐값 제외 고저차 ≥ {th}m: {int((rel >= th).sum())}개 ({100 * (rel >= th).mean():.1f}%)")
    big = [p for p in ps if p["relief_p"] >= 2]
    print("  고저차 ≥2m 필지의 지목:", Counter(p["jimok"] for p in big).most_common())
    print("  고저차 ≥2m 필지 중 경계 옹벽 등록:", sum(p["wall_on_edge"] for p in big), "/", len(big))
    reg = Counter((r["region"] or "").split(" ")[0] for r in rows for p in r["parcels"] if p["relief_p"] >= 2)
    print("  고저차 ≥2m 필지 지역:", reg.most_common(10))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("addr")
    a.add_argument("addresses", nargs="+")
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=200)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", default="output/graded_survey.json")
    args = ap.parse_args()
    {"addr": cmd_addr, "sample": cmd_sample}[args.cmd](args)


if __name__ == "__main__":
    main()
