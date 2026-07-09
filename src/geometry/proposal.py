"""제안 매스 + 조망점 (로드맵 B-2, 경관심의). 현황이 아닌 '설계 제안'을 모델에 얹는다.

- build_proposed_mass: subject 대지 footprint를 제안 높이로 돌출(지형 앉힘) → 제안 건물 dict.
- standard_viewpoints: 사이트 주변 4방위 표준 조망점(카메라 eye/target) — 경관심의 조망 시뮬레이션용.

좌표: 로컬 미터(geometry 동일). 제안 매스는 현황 solids와 **분리**(현황 통계·QA·.3dm 오염 방지) →
geometry.proposed로만 실어 뷰어가 before/after로 렌더. elevation-renderer(2D 입면)와 상보(3D 맥락).
"""

from __future__ import annotations

import math

from src.config import DEFAULT_FLOOR_H_M
from src.geometry.building import BuildingSolid
from src.geometry.seating import seat_building


def build_proposed_mass(footprint_m, height_m, dem=None,
                        floor_h_m: float = DEFAULT_FLOOR_H_M) -> dict | None:
    """subject footprint × 제안 높이 → {footprint, base_z, height, floors, proposed}. 지형 앉힘.

    dem 있으면 seat_building(footprint 최저 표고 - 묻힘여유)로 base_z, 없으면 0. 무효 시 None.
    """
    if not footprint_m or len(footprint_m) < 3 or height_m <= 0:
        return None
    base_z = 0.0
    if dem is not None:
        solid = BuildingSolid(name="제안", footprint_m=list(footprint_m), base_z_m=0.0,
                              height_m=height_m, floors=None, attrs={})
        try:
            base_z = seat_building(solid, dem)
        except Exception:  # noqa: BLE001 — 앉힘 실패는 지면 0으로 폴백
            base_z = 0.0
    return {
        "footprint": [[round(x, 2), round(y, 2)] for x, y in footprint_m],
        "base_z": round(base_z, 3),
        "height": round(float(height_m), 3),
        "floors": max(1, round(height_m / floor_h_m)),
        "proposed": True,
    }


def standard_viewpoints(footprint_m, base_z: float, height: float,
                        dist_factor: float = 2.5) -> list[dict]:
    """사이트 주변 4방위 표준 조망점 → [{name, eye:[x,y,z], target:[x,y,z]}]. 눈높이 1.6m.

    target = 사이트 중심 + 제안 매스 중간 높이(프레이밍). eye = 중심에서 각 방위로 조망 거리만큼.
    """
    if not footprint_m:
        return []
    cx = sum(p[0] for p in footprint_m) / len(footprint_m)
    cy = sum(p[1] for p in footprint_m) / len(footprint_m)
    r = max((math.hypot(p[0] - cx, p[1] - cy) for p in footprint_m), default=20.0)
    dist = max(100.0, r * dist_factor + 60.0)
    target = [round(cx, 2), round(cy, 2), round(base_z + height * 0.5, 2)]
    eye_z = round(base_z + 1.6, 2)
    dirs = [("남측 조망", 0.0, -1.0), ("북측 조망", 0.0, 1.0),
            ("동측 조망", 1.0, 0.0), ("서측 조망", -1.0, 0.0)]
    return [
        {"name": name,
         "eye": [round(cx + dx * dist, 2), round(cy + dy * dist, 2), eye_z],
         "target": target}
        for name, dx, dy in dirs
    ]
