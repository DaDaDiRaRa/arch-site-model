"""건물 앉힘 — footprint 최저 꼭짓점 기준 base_z 결정 (사양서 §6.6).

경사지에서 건물이 뜨는 것을 막기 위해 중심 1점이 아닌
footprint 각 꼭짓점의 DEM 표고 최솟값을 사용한다.
"""

from __future__ import annotations

from src.geometry.building import BuildingSolid
from src.terrain.dem import DEMPatch

BURIAL_M = 0.5  # 묻힘여유(m): 최저 꼭짓점 아래로 살짝 파묻어 틈새 방지


def seat_building(solid: BuildingSolid, dem: DEMPatch) -> float:
    """건물 base_z_m 결정 (로컬 미터).

    footprint 각 꼭짓점 아래 DEM 표고 중 최솟값 - BURIAL_M.
    경사가 급해도 건물 바닥이 지형에서 뜨지 않는다.
    """
    # sample()은 in-range NaN 구멍에 None을 돌려줘(elev_at의 0.0 침몰 방지) 유효 정점만 쓴다.
    valid = [z for z in (dem.sample(x, y) for x, y in solid.footprint_m) if z is not None]
    if not valid:  # footprint 전체가 데이터 없음 → DEM 최저(없으면 0)
        zr = dem.z_range()
        return (zr[0] if zr else 0.0) - BURIAL_M
    return min(valid) - BURIAL_M
