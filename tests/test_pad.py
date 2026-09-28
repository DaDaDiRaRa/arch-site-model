"""조성 대지 평탄화 — 판별 규칙과 지형 평탄화(합성 DEM, 네트워크 없음)."""

import numpy as np
from affine import Affine

from src.geometry.cadastral import CadastralParcel
from src.geometry.pad import burn_pads, detect_pads
from src.geometry.seating import seat_building
from src.geometry.building import BuildingSolid
from src.terrain.dem import DEMPatch

OX, OY = 200000.0, 450000.0


def _slope_dem(rows=40, cols=40, cell=2.0, dz_per_m=0.1):
    """서→동으로 10% 올라가는 비탈(로컬 0~80m 에서 표고 50~58m)."""
    xs = np.arange(cols) * cell
    z = (50.0 + xs * dz_per_m).astype(np.float32)[None, :].repeat(rows, axis=0)
    top = OY + rows * cell
    return DEMPatch(grid=z, transform=Affine(cell, 0, OX, 0, -cell, top), offset=(OX, OY))


def _parcel(pnu="P1", jimok="대", x0=20, y0=20, x1=60, y1=60):
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return CadastralParcel(pnu=pnu, footprint_m=ring, jibun=f"1-1 {jimok}", jimok=jimok)


def _building(x0=30, y0=30, x1=45, y1=45):
    return BuildingSolid(name="b", footprint_m=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                         base_z_m=0.0, height_m=3.5, floors=1, attrs={})


def test_target_parcel_is_graded_to_high_side():
    dem, p = _slope_dem(), _parcel()
    pads = detect_pads([p], dem, footprints=[_building().footprint_m], target_pnu="P1")
    assert len(pads) == 1
    pad = pads[0]
    # 대지 20~60m 구간 표고 52~56m → "높은 쪽"(상위 90%) 기준이라 55.5m 이상
    assert 55.0 <= pad.pad_z <= 56.0 and pad.reason == "target"
    assert pad.fill_m > 3.0 and pad.cut_m < 1.0


def test_burn_flattens_inside_and_building_sits_on_pad():
    dem, p = _slope_dem(), _parcel()
    pads = detect_pads([p], dem, footprints=[_building().footprint_m], target_pnu="P1")
    flat = burn_pads(dem, pads)
    zs = [flat.sample(x, y) for x in range(25, 56, 5) for y in range(25, 56, 5)]
    assert max(zs) - min(zs) < 0.01                      # 필지 안은 평평
    assert abs(zs[0] - pads[0].pad_z) < 0.01
    assert flat.sample(5, 40) == dem.sample(5, 40)       # 필지 밖은 그대로
    base_before = seat_building(_building(), dem)
    base_after = seat_building(_building(), flat)
    assert base_after > base_before + 1.0                # 건물이 평탄화된 대지 위로 올라옴


def test_candidate_rules_only_take_strong_evidence():
    dem = _slope_dem()
    house = _parcel("P_대", "대")                         # 지목 '대'만 — 자연 비탈과 구분 불가 → 제외
    factory = _parcel("P_장", "장")                       # 공장용지 → 포함
    field_ = _parcel("P_전", "전")                        # 개발지 아님 → 제외
    fps = [_building().footprint_m]
    got = {p.pnu: p.reason for p in detect_pads([house, factory, field_], dem, footprints=fps,
                                                include_candidates=True)}
    assert got == {"P_장": "jimok"}
    # 경계에 실측 옹벽이 있으면 '대'도 후보
    class W:
        points = [(20, 10), (20, 70)]
    got2 = {p.pnu: p.reason for p in detect_pads([house], dem, footprints=fps,
                                                 include_candidates=True, walls=[W()])}
    assert got2 == {"P_대": "wall"}


def test_flat_parcel_and_no_building_are_left_alone():
    flat_dem = _slope_dem(dz_per_m=0.01)                  # 고저차 0.8m — 기준 미만
    assert detect_pads([_parcel()], flat_dem, footprints=[_building().footprint_m], target_pnu="P1") == []
    # 건물 없는 주변 필지는 후보에서 제외(대상 필지는 예외)
    assert detect_pads([_parcel("P_장", "장")], _slope_dem(), footprints=[], include_candidates=True) == []
