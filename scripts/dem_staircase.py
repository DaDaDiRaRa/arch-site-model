"""DEM 계단현상(terracing) 진단 — 재bake 전후 비교용.

등고선→DEM 보간이 등고선 사이를 평탄 삼각형으로 채우면 표고가 등고선 레벨(5m 배수)에
몰리고, 단면에 평탄셀(기울기≈0)이 늘어난다. 이 스크립트로 두 지표를 정량화한다.

지표:
  - quant_frac : 표고가 `step`(기본 5m) 배수의 ±`tol` 안에 드는 셀 비율.
                 계단이면 등고선 레벨에 몰려 높다.
  - flat_frac  : 국소 기울기가 `slope`(기본 0.05 = 5cm/m ≈ 2.9°) 미만인 셀 비율.
                 평탄 삼각형이 많으면 높다.
  - 표고 통계  : min/max/평균 + 최고점(봉우리 보존 확인).

사용:
    python -m scripts.dem_staircase geo_store/dem_x.tif
    python -m scripts.dem_staircase geo_store/old.tif geo_store/new.tif   # 비교
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import rasterio

# PowerShell 기본 콘솔(cp949)에서 한국어/기호 깨짐 방지.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def _finite(path: str) -> tuple[np.ndarray, float]:
    """DEM .tif → (유효 표고 격자, 셀크기 m)."""
    with rasterio.open(path) as src:
        z = src.read(1).astype(np.float64)
        cell_m = abs(src.transform.a)
    z = np.where(np.isfinite(z), z, np.nan)
    return z, cell_m


def quant_frac(z: np.ndarray, step: float = 5.0, tol: float = 0.25) -> float:
    """표고가 step 배수 ±tol 안에 드는 유효셀 비율."""
    v = z[np.isfinite(z)]
    if v.size == 0:
        return float("nan")
    resid = np.abs(v - np.round(v / step) * step)
    return float(np.mean(resid <= tol))


def flat_frac(z: np.ndarray, cell_m: float, slope: float = 0.05) -> float:
    """국소 기울기(무차원, rise/run)가 slope 미만인 유효셀 비율."""
    gy, gx = np.gradient(z, cell_m)  # m per m
    mag = np.hypot(gx, gy)
    finite = np.isfinite(mag)
    if finite.sum() == 0:
        return float("nan")
    return float(np.mean(mag[finite] < slope))


def stats(z: np.ndarray) -> dict:
    v = z[np.isfinite(z)]
    return {
        "min": float(v.min()),
        "max": float(v.max()),
        "mean": float(v.mean()),
        "n": int(v.size),
    }


def diagnose(path: str, step: float = 5.0, tol: float = 0.25, slope: float = 0.05) -> dict:
    z, cell_m = _finite(path)
    s = stats(z)
    return {
        "path": path,
        "shape": z.shape,
        "cell_m": cell_m,
        "quant_frac": quant_frac(z, step, tol),
        "flat_frac": flat_frac(z, cell_m, slope),
        **s,
    }


def _print(d: dict) -> None:
    print(f"  {d['path']}")
    print(f"    격자      : {d['shape'][0]}×{d['shape'][1]} @ {d['cell_m']:.1f}m  (유효셀 {d['n']:,})")
    print(f"    표고      : {d['min']:.1f} ~ {d['max']:.1f} m  (평균 {d['mean']:.1f})")
    print(f"    quant_frac: {d['quant_frac']*100:.1f}%   (5m 배수 +-0.25m 몰림, 낮을수록 좋음)")
    print(f"    flat_frac : {d['flat_frac']*100:.1f}%   (평탄셀 slope<0.05, 낮을수록 좋음)")


def _cli() -> None:
    ap = argparse.ArgumentParser(description="DEM 계단현상 진단")
    ap.add_argument("tif", nargs="+", help="DEM .tif (1개=진단, 2개=old new 비교)")
    ap.add_argument("--step", type=float, default=5.0, help="등고선 간격 m (기본 5)")
    ap.add_argument("--tol", type=float, default=0.25, help="배수 몰림 허용오차 m (기본 0.25)")
    ap.add_argument("--slope", type=float, default=0.05, help="평탄 판정 기울기 (기본 0.05)")
    args = ap.parse_args()

    results = [diagnose(p, args.step, args.tol, args.slope) for p in args.tif]
    for d in results:
        _print(d)
        print()

    if len(results) == 2:
        old, new = results
        dq = (new["quant_frac"] - old["quant_frac"]) * 100
        df = (new["flat_frac"] - old["flat_frac"]) * 100
        dmax = new["max"] - old["max"]
        print("=== 비교 (new - old) ===")
        print(f"    quant_frac: {dq:+.1f}%p  (음수=계단 감소 = 개선)")
        print(f"    flat_frac : {df:+.1f}%p  (음수=평탄셀 감소 = 개선)")
        print(f"    최고표고  : {dmax:+.1f}m  (봉우리 보존: 0에 가까울수록 좋음)")


if __name__ == "__main__":
    sys.exit(_cli())
