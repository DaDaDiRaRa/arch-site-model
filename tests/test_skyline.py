"""스카이라인 종/횡단면(skyline.py, B-2) — 실루엣·before/after, 합성 데이터로."""

from src.geometry.building import BuildingSolid
from src.geometry.skyline import build_skylines


def _b(fp, bz, h):
    return BuildingSolid(name="x", footprint_m=fp, base_z_m=bz, height_m=h, floors=None, attrs={})


def test_skylines_two_axes():
    sk = build_skylines([_b([(0, 0), (10, 0), (10, 10), (0, 10)], 0, 20)])
    assert len(sk) == 2
    names = {s["name"] for s in sk}
    assert any("횡단" in n for n in names) and any("종단" in n for n in names)
    prof = sk[0]
    assert len(prof["t"]) == len(prof["before"]) == len(prof["after"])


def test_before_after_differ_with_proposal():
    existing = [_b([(0, 0), (10, 0), (10, 10), (0, 10)], 0.0, 10.0)]
    proposed = {"footprint": [[0, 0], [10, 0], [10, 10], [0, 10]], "base_z": 0.0, "height": 50.0}
    prof = build_skylines(existing, proposed)[0]
    after_max = max(v for v in prof["after"] if v is not None)
    before_max = max(v for v in prof["before"] if v is not None)
    assert after_max >= 50.0        # 제안 50m가 스카이라인에 반영
    assert after_max > before_max   # after가 before보다 높음


def test_empty_solids_no_skyline():
    assert build_skylines([]) == []
