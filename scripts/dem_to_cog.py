"""geo_store DEM 타일 → COG(Cloud-Optimized GeoTIFF) 변환 + GCS 업로드 안내.

전국 배포: 타일을 GCS 버킷에 COG로 두고 앱 환경변수
    DEM_TILE_BASE=/vsigs/<버킷>/<프리픽스>
로 설정하면 clip_dem이 /vsigs 윈도우 읽기로 필요한 창(수백 KB)만 범위요청한다.
COG는 내부 타일링(+오버뷰)이라 이 윈도우 읽기가 효율적이다(striped GeoTIFF는 스트립
통째로 받아야 해 비효율). manifest는 로컬(git)에 그대로 두고 타일만 버킷에 올린다.

사용:
    # 변환만
    python scripts/dem_to_cog.py geo_store --out cog_out
    # 변환 + 업로드/설정 명령 출력
    python scripts/dem_to_cog.py geo_store --out cog_out --bucket my-bucket --prefix dem
"""
from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import rasterio
from rasterio.shutil import copy as rio_copy


def to_cog(src: str | Path, dst: str | Path) -> None:
    """단일 GeoTIFF → COG. 내부 512 타일 + deflate + 오버뷰(bilinear)."""
    rio_copy(
        str(src), str(dst),
        driver="COG",
        compress="DEFLATE",
        blocksize=512,
        overview_resampling="bilinear",
        num_threads="ALL_CPUS",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="DEM 타일 → COG 변환 + GCS 업로드 안내")
    ap.add_argument("src_dir", help="원본 tif 폴더 (예: geo_store)")
    ap.add_argument("--out", required=True, help="COG 출력 폴더")
    ap.add_argument("--glob", default="*.tif", help="변환 대상 패턴 (기본 *.tif)")
    ap.add_argument("--bucket", help="GCS 버킷명 (주면 업로드/설정 명령 출력)")
    ap.add_argument("--prefix", default="dem", help="버킷 내 프리픽스 (기본 dem)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.src_dir, args.glob)))
    if not files:
        raise SystemExit(f"대상 tif 없음: {args.src_dir}/{args.glob}")

    for f in files:
        dst = out / Path(f).name
        to_cog(f, dst)
        print(f"COG: {dst.name}")
    print(f"\n{len(files)}개 COG 생성 → {out}")

    if args.bucket:
        print("\n# 1) 업로드")
        print(f"gcloud storage cp {out}/*.tif gs://{args.bucket}/{args.prefix}/")
        print("\n# 2) Cloud Run 서비스계정에 버킷 읽기 권한(1회)")
        print(f"gcloud storage buckets add-iam-policy-binding gs://{args.bucket} \\")
        print("    --member=serviceAccount:<CLOUD_RUN_SA> --role=roles/storage.objectViewer")
        print("\n# 3) 앱 환경변수")
        print(f"DEM_TILE_BASE=/vsigs/{args.bucket}/{args.prefix}")


if __name__ == "__main__":
    main()
