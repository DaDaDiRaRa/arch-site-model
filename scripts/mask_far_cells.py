"""이미 구운 DEM 타일에서 **실데이터로부터 먼 셀**을 nodata로 바꾼다(경계 외삽 제거).

2026-09-08 이전에 구운 타일은 Delaunay 볼록껍질 안이면 실데이터가 아무리 멀어도 값이 채워졌다.
껍질은 지역의 오목한 경계(도 경계선)를 거대한 삼각형으로 가로지르므로, 각 지역 타일이 **자기
영역 밖까지** 값을 뻗쳤고 이웃 지역 실데이터와 겹치며 mosaic을 오염시켰다.

실측(2026-09-08): 시도 경계쌍 58개 중 **49개가 5m 초과 불일치, 최대 214m**. 한반도 90m DEM을
심판으로 대조하니 외삽한 쪽이 틀렸다(경계 타일 85m vs 이웃 실데이터 22m).

`contour_bake.bake_dem`은 고쳤다(먼 셀을 nodata로). 이 스크립트는 **이미 구워 배포한 타일**을
재베이크 없이 같은 상태로 만든다 — 데이터가 가까운 셀의 값은 이미 맞으므로 먼 셀만 지우면 된다.
내륙 타일은 걸리는 셀이 0.0%라 사실상 변화가 없다(실측: 경기 성남 0.0% / 전북 경계 26.0%).

사용:
    python scripts/mask_far_cells.py --dry-run          # 얼마나 지워지는지만 확인
    python scripts/mask_far_cells.py                    # 전체 보정
    python scripts/mask_far_cells.py --only jeonbuk     # 한 지역만
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.terrain.contour_bake import read_contours  # noqa: E402

# 타일 파일명 슬러그 → 등고선 소스 폴더
SOURCES: dict[str, str] = {
    "seoul": "서울특별시", "busan": "부산광역시", "daegu": "대구광역시",
    "incheon": "인천광역시", "gwangju": "광주광역시", "daejeon": "대전광역시",
    "ulsan": "울산광역시", "sejong": "세종특별자치시", "gyeonggi": "경기도",
    "gangwon": "강원특별자치도", "chungbuk": "충청북도", "chungnam": "충청남도",
    "jeonbuk": "전북특별자치도", "gyeongbuk": "경상북도", "gyeongnam": "경상남도",
    "jeju": "제주특별자치도",
}
ROOT = Path(r"D:/APPS/SHP/ctnu_도영역")
# 전남은 복구한 등고선만 있다(별도 폴더).
SPECIAL = {"jeonnam": ROOT / "전라남도" / "_복구_등고선"}

FILL_DIST_M = 200.0     # bake_dem의 기본값과 동일하게


def source_dir(slug: str) -> Path | None:
    if slug in SPECIAL:
        return SPECIAL[slug]
    name = SOURCES.get(slug)
    if not name:
        return None
    d = ROOT / name / "_shp"
    return d if d.exists() else None


def fix_tile(tif: Path, src: Path, dist_m: float, dry: bool) -> tuple[int, int]:
    """(지워진 셀, 원래 유효 셀) 반환."""
    from scipy.spatial import cKDTree

    with rasterio.open(tif) as ds:
        grid = ds.read(1).astype("float32")
        tf, b, profile = ds.transform, ds.bounds, ds.profile
    valid = np.isfinite(grid)
    if not valid.any():
        return 0, 0
    try:
        xs, ys, _ = read_contours(src, bbox=(b.left - dist_m * 2, b.bottom - dist_m * 2,
                                             b.right + dist_m * 2, b.top + dist_m * 2))
    except (ValueError, FileNotFoundError):
        # 이 타일 범위에 실데이터가 **하나도** 없다 = 전부 외삽 → 통째로 nodata.
        # (타일 격자는 지역보다 넓게 잡히므로 지역 밖에만 걸치는 타일이 생긴다.)
        if not dry:
            grid[valid] = np.nan
            with rasterio.open(tif, "w", **profile) as out:
                out.write(grid, 1)
        return int(valid.sum()), int(valid.sum())
    rows, cols = grid.shape
    gx = np.arange(cols) * tf.a + tf.c + tf.a / 2
    gy = np.arange(rows) * tf.e + tf.f + tf.e / 2
    GX, GY = np.meshgrid(gx, gy)
    d, _ = cKDTree(np.column_stack([xs, ys])).query(np.column_stack([GX.ravel(), GY.ravel()]))
    far = (d > dist_m).reshape(grid.shape) & valid
    if far.any() and not dry:
        grid[far] = np.nan
        with rasterio.open(tif, "w", **profile) as out:
            out.write(grid, 1)
    return int(far.sum()), int(valid.sum())


def main() -> int:
    ap = argparse.ArgumentParser(description="구운 DEM 타일의 경계 외삽 셀 제거")
    ap.add_argument("--geo-store", default="geo_store")
    ap.add_argument("--dist", type=float, default=FILL_DIST_M, help="실데이터 허용 거리(m)")
    ap.add_argument("--only", help="특정 슬러그만 (예: jeonbuk)")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 통계만")
    args = ap.parse_args()

    slugs = [args.only] if args.only else sorted(set(SOURCES) | set(SPECIAL))
    t0 = time.time()
    tot_far = tot_valid = tot_tiles = 0
    for slug in slugs:
        src = source_dir(slug)
        tifs = sorted(glob.glob(os.path.join(args.geo_store, f"dem_{slug}_*.tif")))
        if not tifs:
            continue
        if src is None:
            print(f"{slug:10s} 소스 폴더 없음 → 건너뜀 ({len(tifs)}타일)")
            continue
        far = val = 0
        for t in tifs:
            f, v = fix_tile(Path(t), src, args.dist, args.dry_run)
            far += f; val += v
        tot_far += far; tot_valid += val; tot_tiles += len(tifs)
        pct = 100 * far / max(1, val)
        print(f"{slug:10s} {len(tifs):4d}타일 · 제거 {far:10,} / 유효 {val:12,} ({pct:5.2f}%)", flush=True)
    print(f"\n합계 {tot_tiles}타일 · 제거 {tot_far:,} / {tot_valid:,} "
          f"({100 * tot_far / max(1, tot_valid):.2f}%) · {time.time() - t0:.0f}s"
          f"{'  [dry-run]' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
