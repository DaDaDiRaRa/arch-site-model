"""연속수치지형도 [도 영역] zip에서 **파이프라인이 쓰는 레이어만** 골라 해제.

받은 zip은 8개 카테고리가 다 들어있지만 우리가 읽는 건 지형(F)·교통(A)·수계(E)뿐이다.
건물은 VWorld 실시간, 용도지역은 형제 앱 소관이라 굽지 않는다. 전부 풀면 디스크만 몇 배로 먹는다.

zip 내부가 평면(폴더 없이 N3L_F0010000.shp …)이라 **레이어마다 따로** 풀어야 이름이 안 겹친다.
등고선은 `.z01~.zNN + .zip` 분할압축이라 조각이 같은 폴더에 있어야 하며, Bandizip이 이어붙인다.

사용:
    python scripts/extract_layers.py 경기도
    python scripts/extract_layers.py 경기도 --root D:/APPS/SHP/ctnu_도영역
"""
from __future__ import annotations

import argparse
import re
import subprocess
import time
from pathlib import Path

# 파이프라인이 실제로 읽는 레이어코드
#   F0010000 등고선 · F0020000 표고점        → contour_bake (DEM)
#   F0040000 옹벽(상단선+실측 높이)            → wall_bake (지형에 수직 단차)
#   A0010000 도로경계 · A0020000 중심선 · A0033320 보도 → road_bake
#   E0______ 하천·호소                        → water_bake
# (F0030000 절토/성토면은 아직 소비하는 코드가 없어 제외 — 쓰게 되면 여기 추가)
WANTED = re.compile(r"F0010000|F0020000|F0040000|A0010000|A0020000|A0033320|_E0\d{6}")
BANDIZIP = Path(r"C:\Program Files\Bandizip\bz.exe")


def extract_region(region_dir: Path, bz: Path = BANDIZIP) -> dict:
    """region_dir 안의 필요한 zip만 region_dir/_shp 로 해제."""
    if not bz.exists():
        raise FileNotFoundError(f"Bandizip이 없습니다: {bz} (분할압축 해제에 필요)")
    out = region_dir / "_shp"
    out.mkdir(exist_ok=True)
    zips = [z for z in sorted(region_dir.glob("*.zip")) if WANTED.search(z.name)]
    if not zips:
        raise FileNotFoundError(f"대상 zip이 없습니다: {region_dir}")
    t0 = time.time()
    failed: list[str] = []
    for i, z in enumerate(zips, 1):
        r = subprocess.run([str(bz), "x", f"-o:{out}", "-y", str(z)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=7200)
        if r.returncode != 0:
            failed.append(z.name)
        print(f"  [{i}/{len(zips)}] {z.name[:60]:60s} {'ok' if r.returncode == 0 else '실패'}", flush=True)
    shp = sorted(out.rglob("*.shp"))
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    return {"shp": len(shp), "gb": size / 1e9, "sec": time.time() - t0, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="연속수치지형도 도 영역 → 필요한 레이어만 해제")
    ap.add_argument("region", help="시도 폴더명 (예: 경기도)")
    ap.add_argument("--root", default=r"D:/APPS/SHP/ctnu_도영역", help="도 영역 보관 루트")
    ap.add_argument("--bz", default=str(BANDIZIP), help="Bandizip 콘솔 실행파일 경로")
    args = ap.parse_args(argv)

    res = extract_region(Path(args.root) / args.region, Path(args.bz))
    print(f"\n완료: shp {res['shp']}개 · {res['gb']:.2f} GB · {res['sec']:.0f}s")
    if res["failed"]:
        print("실패한 zip:", ", ".join(res["failed"]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
