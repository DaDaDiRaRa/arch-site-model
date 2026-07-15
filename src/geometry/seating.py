"""건물 앉힘 — footprint 지반 기준 base_z 결정 (사양서 §6.6 + 커튼 가드).

경사지에서 건물이 뜨는 것을 막으려고 footprint 지반의 **최저점**에 바닥을 앉힌다.
단, footprint가 절벽·옹벽·단차(또는 DEM 노이즈)를 가로질러 최저점이 대표지반보다 훨씬
낮으면, 그 최저점에 그대로 박으면 건물이 구덩이 바닥까지 내려가 과장된 수직 벽('커튼')이
생겨 뒤 건물을 가린다. 그래서 대표지반(footprint 내부 조밀샘플 중앙값)보다
`BUILDING_GRADE_MAX_SKIRT_M` 이상은 내려가지 않도록 상한(커튼 가드)을 둔다.

  grade = max(지반최저, 대표지반중앙값 - MAX_SKIRT)
  base_z = grade - BURIAL_M

footprint 기복 ≤ MAX_SKIRT 인 건물(평지·완경사, 대다수)은 `중앙값 - MAX_SKIRT ≤ 최저` 라서
결과가 기존 `최저 - BURIAL_M` 과 **정확히 동일 → 무회귀**. 절벽/단차/노이즈 건물만 바닥이
대표지반 근처로 올라와 커튼이 (MAX_SKIRT + 높이)로 제한된다.
"""

from __future__ import annotations

import numpy as np

from src import config
from src.geometry.building import BuildingSolid
from src.terrain.dem import DEMPatch

BURIAL_M = 0.5          # 매몰여유(m): 지반 아래로 살짝 파묻어 틈새 방지
_GRID_STEP_M = 3.0      # footprint 내부 대표지반 샘플 격자 간격(m)
_MAX_GRID_PTS = 400     # 내부 격자 샘플 상한(대형 footprint 폭주 방지)


def _footprint_samples(footprint_m, dem: DEMPatch) -> list[float]:
    """footprint 꼭짓점 + 내부 격자에서 DEM 표고 샘플(유효값만).

    꼭짓점만으로는 대표지반(중앙값)이 거칠어(절벽 위 4점 중 2점만 낮아도 왜곡) 내부를
    격자로 훑어 '건물이 실제로 앉는 플랫폼' 표고를 제대로 잡는다. dem.sample()은 in-range
    NaN 구멍에 None을 돌려주므로(0.0 침몰 방지) 유효값만 모은다.
    """
    samples = [z for z in (dem.sample(x, y) for x, y in footprint_m) if z is not None]

    try:
        from shapely.geometry import Point, Polygon

        poly = Polygon(footprint_m)
        if poly.is_valid and not poly.is_empty:
            x0, y0, x1, y1 = poly.bounds
            step = max(_GRID_STEP_M, (x1 - x0) * (y1 - y0) / _MAX_GRID_PTS)
            nx = max(1, int((x1 - x0) / step))
            ny = max(1, int((y1 - y0) / step))
            for i in range(nx + 1):
                x = x0 + (x1 - x0) * i / nx
                for j in range(ny + 1):
                    y = y0 + (y1 - y0) * j / ny
                    if poly.contains(Point(x, y)):
                        z = dem.sample(x, y)
                        if z is not None:
                            samples.append(z)
    except Exception:  # noqa: BLE001 — shapely 실패 시 꼭짓점 샘플로만 진행
        pass

    return samples


def footprint_grade(footprint_m, dem: DEMPatch) -> float | None:
    """건물이 앉을 대표 지반표고(로컬 미터). 샘플 없으면 None.

    `max(지반최저, 대표지반중앙값 - MAX_SKIRT)` — 최저점 앉힘에 커튼 가드를 씌운 값.
    seat_building(바닥 base_z)과 QA(부유/침몰 판정)가 **같은 기준**을 쓰도록 공유한다.
    """
    samples = _footprint_samples(footprint_m, dem)
    if not samples:
        return None
    g_min = float(min(samples))
    g_med = float(np.median(samples))
    return max(g_min, g_med - config.BUILDING_GRADE_MAX_SKIRT_M)


def seat_building(solid: BuildingSolid, dem: DEMPatch) -> float:
    """건물 base_z_m 결정 (로컬 미터) = 대표지반 - BURIAL_M.

    footprint 전체가 데이터 없음이면 DEM 최저(없으면 0)로 폴백해 침몰을 막는다.
    """
    grade = footprint_grade(solid.footprint_m, dem)
    if grade is None:  # footprint 전체가 데이터 없음 → DEM 최저(없으면 0)
        zr = dem.z_range()
        return (zr[0] if zr else 0.0) - BURIAL_M
    return grade - BURIAL_M
