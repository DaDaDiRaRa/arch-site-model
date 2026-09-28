"""조성 대지 평탄화(pad grading) — 깎고 채워 평평하게 만든 대지를 지형에 반영.

우리 지형은 수치지도 등고선(측량 시점의 **원지형**)이라, 그 뒤 공장·창고·주택 부지로 평탄화된
대지는 비탈로 남는다(실측 2026-09-28 아산 초사동 450-1: 지목 대·1층 2동인데 필지 안 고저차 4.4m,
현장 사진은 높은 쪽 높이로 평평하게 조성 + 옹벽).

**추정이다.** "어느 높이로 평평하게 했는가"는 데이터로 알 수 없어, 사진에서 확인한 흔한 방식인
"높은 쪽 기준"(필지 원지형 상위 백분위)을 쓴다. 그래서 기본은 꺼짐이고, 켜면 결과에 추정이라고
표시한다. 정확히 하려면 현황측량도(DXF)나 1m 라이다 DEM이 필요하다(TODO).

전국 표본(2026-09-28, 건물 있는 지점 200곳·개발지 필지 2,126개): 고저차 2m 이상이 30%,
그중 경계 옹벽이 수치지도에 등록된 건 34%뿐. 그래서 "고저차만" 보면 과잉 적용이 되고,
공장·창고 등 지목이나 등록 옹벽이 있는 필지(13%)가 확실한 후보다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# 개발된 땅(연속지적 지목 한 글자)
DEVELOPED = {"대", "장", "창", "주", "유", "학", "잡"}
# 평평한 바닥이 거의 필수인 용도 — 조성 대지 확신도가 높다(공장·창고·주차장·주유소·학교)
STRONG = {"장", "창", "주", "유", "학"}

MIN_RELIEF_M = 2.0        # 이보다 평평하면 손대지 않는다
PAD_PCTL = 90.0           # "높은 쪽" 기준 — 튀는 한 점에 끌려가지 않게 최고점 대신 상위 백분위


@dataclass
class Pad:
    """평탄화 대상 필지 하나."""

    pnu: str
    ring: list[tuple[float, float]]   # 로컬 미터 외곽 링
    pad_z: float                      # 평탄화 높이(m)
    relief_m: float                   # 원지형 고저차(상위90%−하위10%)
    area_m2: float
    jimok: str
    reason: str                       # "target"(대상 필지) | "jimok" | "wall" | "relief"
    fill_m: float = 0.0               # 가장 많이 채운 깊이(pad − 최저)
    cut_m: float = 0.0                # 가장 많이 깎은 깊이(최고 − pad)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pnu": self.pnu, "pad_z": round(self.pad_z, 2), "relief_m": round(self.relief_m, 2),
            "area_m2": round(self.area_m2), "jimok": self.jimok, "reason": self.reason,
            "fill_m": round(self.fill_m, 2), "cut_m": round(self.cut_m, 2),
            "ring": [[round(x, 2), round(y, 2)] for x, y in self.ring],
        }


def _polygon(ring):
    from shapely.geometry import Polygon

    p = Polygon(ring)
    if not p.is_valid:
        p = p.buffer(0)
    return p if (not p.is_empty and p.geom_type == "Polygon") else None


def _samples(poly, dem, step: float = 2.0) -> np.ndarray:
    from shapely.geometry import Point

    x0, y0, x1, y1 = poly.bounds
    zs = [
        dem.sample(x, y)
        for x in np.arange(x0, x1 + step, step)
        for y in np.arange(y0, y1 + step, step)
        if poly.contains(Point(x, y))
    ]
    return np.array([z for z in zs if z is not None], dtype=float)


def detect_pads(
    parcels, dem, footprints=None, target_pnu: str | None = None,
    include_candidates: bool = False, walls=None, min_relief_m: float = MIN_RELIEF_M,
) -> list[Pad]:
    """평탄화할 필지를 고른다.

    target_pnu: 설계 대상 필지(주소가 가리키는 필지) — 지목·고저차만 맞으면 무조건 포함.
    include_candidates: 주변 필지까지 — 지목이 공장·창고 등이거나 경계에 옹벽이 등록된 필지만
      (전국 표본상 13%). 고저차만 큰 필지는 자연 비탈일 수 있어 넣지 않는다.
    """
    from shapely.geometry import LineString, Polygon

    if dem is None or not parcels:
        return []
    fps = [_polygon(f) for f in (footprints or [])]
    fps = [f for f in fps if f is not None]
    wlines = [LineString(w.points) for w in (walls or []) if len(getattr(w, "points", [])) >= 2]

    pads: list[Pad] = []
    for p in parcels:
        poly = _polygon(p.footprint_m)
        if poly is None or poly.area < 100:
            continue
        is_target = target_pnu is not None and p.pnu == target_pnu
        if not is_target:
            if not include_candidates or p.jimok not in DEVELOPED:
                continue
        zs = _samples(poly, dem)
        if zs.size < 4:
            continue
        relief = float(np.percentile(zs, 90) - np.percentile(zs, 10))
        if relief < min_relief_m:
            continue
        has_bld = any(f.intersection(poly).area > 0.3 * f.area for f in fps)
        on_edge = any(w.distance(poly.exterior) < 3.0 for w in wlines)
        if is_target:
            reason = "target"
        elif p.jimok in STRONG:
            reason = "jimok"
        elif on_edge:
            reason = "wall"
        else:
            continue          # 지목 '대'만으로는 자연 비탈과 구분 불가 — 건드리지 않는다
        if not has_bld and not is_target:
            continue
        pad_z = float(np.percentile(zs, PAD_PCTL))
        pads.append(Pad(
            pnu=p.pnu, ring=list(poly.exterior.coords)[:-1], pad_z=pad_z, relief_m=relief,
            area_m2=float(poly.area), jimok=p.jimok, reason=reason,
            fill_m=float(pad_z - zs.min()), cut_m=float(zs.max() - pad_z),
        ))
    return pads


def burn_pads(dem, pads: list[Pad]):
    """필지 안 지형을 pad_z로 평탄화한다. 새 DEMPatch 반환(원본 불변).

    경계는 격자 한 칸(보통 5m) 안에서 원지형으로 떨어진다 — 실제 옹벽처럼 수직은 아니지만 급한
    비탈로 보인다. 옹벽 버닝(wall.burn_walls)을 경계 구간마다 부르는 방식은 같은 셀이 여러 번
    내려가 필지 안이 더 파였다(실측 2026-09-28: 필지 안 고저차 3.7m → 5.6m). 그래서 쓰지 않는다.
    수직 단차가 필요하면 실측 옹벽 데이터(layers.walls)나 현황측량도를 쓴다.
    """
    grid = getattr(dem, "grid", None)
    if grid is None or grid.size == 0 or not pads:
        return dem

    from rasterio.features import rasterize

    from src.terrain.dem import DEMPatch

    tf = dem.transform
    ox, oy = dem.offset
    rows, cols = grid.shape
    new = grid.astype(float).copy()
    for pad in pads:
        poly = _polygon([(x + ox, y + oy) for x, y in pad.ring])   # rasterize는 절대 5186 좌표
        if poly is None:
            continue
        mask = rasterize([(poly, 1)], out_shape=(rows, cols), transform=tf, fill=0,
                         all_touched=True).astype(bool)
        mask &= ~np.isnan(new)
        new[mask] = pad.pad_z
    return DEMPatch(grid=new.astype(np.float32), transform=tf, offset=dem.offset)
