"""지형 단차 — 제방(올리기만)·절토성토면(파단선 선명화)·`ZTargets` 순서 무관성.

`pad.burn_pads`가 폐기된 원인(같은 셀이 여러 번 내려가 필지가 더 파임)이 재발하지 않는지가
이 파일의 핵심 가드다 — `fmax`/`fmin` 누적이 멱등·교환법칙이면 그 버그는 **구조적으로 불가능**하다.
"""

import numpy as np
import pytest
from affine import Affine

from src.geometry.scarp import (
    LEVEE_CROWN_W_M,
    SCARP_MIN_DROP_M,
    ScarpFeature,
    ZTargets,
    burn_scarps,
    clip_scarps,
    scarps_to_geometry,
)
from src.terrain.dem import DEMPatch

CELL = 1.0
N = 80


def _flat(z=10.0):
    return DEMPatch(grid=np.full((N, N), z, dtype=np.float32),
                    transform=Affine(CELL, 0, 0.0, 0, -CELL, N * CELL), offset=(0.0, 0.0))


def _levee(x=40.0, h=2.0):
    """남북으로 가로지르는 마루선."""
    return ScarpFeature(kind="levee", top=[(x, 5.0), (x, 75.0)], height_m=h)


def _at(dem, x, y):
    return float(dem.sample(x, y))


# ---------------------------------------------------------------------------
# 제방 — 올리기만
# ---------------------------------------------------------------------------

def test_levee_builds_crown_at_measured_height():
    """마루가 실측 제방고만큼 올라오고, 사면이 1:2로 내려간다."""
    dem = _flat()
    out = burn_scarps(dem, [_levee(h=2.0)])
    assert _at(out, 40.0, 40.0) == pytest.approx(12.0, abs=0.2)      # 마루
    assert _at(out, 40.0 + LEVEE_CROWN_W_M / 2 + 2.0, 40.0) == pytest.approx(11.0, abs=0.4)
    assert _at(out, 40.0 + LEVEE_CROWN_W_M / 2 + 4.0, 40.0) == pytest.approx(10.0, abs=0.4)
    assert _at(out, 60.0, 40.0) == pytest.approx(10.0, abs=0.01)     # 토우 밖은 그대로


def test_levee_never_lowers_anything():
    """제방은 **올리기만** 한다 — 어떤 셀도 원 지형보다 낮아지지 않는다."""
    dem = _flat()
    out = burn_scarps(dem, [_levee()])
    assert not (out.grid < dem.grid - 1e-6).any()


def test_levee_footprint_width_follows_height():
    """발자국 폭 = 마루폭 + 2×(제방고×사면비). 높이가 크면 넓어진다."""
    dem = _flat()
    widths = []
    for h in (1.0, 3.0):
        out = burn_scarps(dem, [_levee(h=h)])
        row = out.grid[int(N / 2)] - dem.grid[int(N / 2)]
        widths.append(int((row > 0.05).sum()))
    assert widths[1] > widths[0]


# ---------------------------------------------------------------------------
# ZTargets — 순서·중복 무관 (pad 실패 회귀 가드)
# ---------------------------------------------------------------------------

def test_same_levee_twice_equals_once():
    """같은 제방을 두 번 넣어도 결과가 같다 — `burn_pads`를 폐기시킨 누적 하강의 회귀 가드."""
    dem = _flat()
    once = burn_scarps(dem, [_levee()])
    twice = burn_scarps(dem, [_levee(), _levee()])
    assert np.allclose(once.grid, twice.grid)


def test_feature_order_does_not_matter():
    """피처 순서를 바꿔도 결과가 같다(`fmax`/`fmin`은 교환법칙)."""
    dem = _flat()
    a, b = _levee(x=30.0, h=2.0), _levee(x=45.0, h=3.0)
    assert np.allclose(burn_scarps(dem, [a, b]).grid, burn_scarps(dem, [b, a]).grid)


def test_crossing_levees_are_the_max():
    """교차하는 두 제방 = 각각 따로 심은 것의 최댓값."""
    dem = _flat()
    a = _levee(x=40.0, h=2.0)
    b = ScarpFeature(kind="levee", top=[(5.0, 40.0), (75.0, 40.0)], height_m=3.0)
    both = burn_scarps(dem, [a, b]).grid
    sep = np.maximum(burn_scarps(dem, [a]).grid, burn_scarps(dem, [b]).grid)
    assert np.allclose(both, sep)


def test_ztargets_conflict_is_counted_and_lo_wins():
    tg = ZTargets((2, 2))
    tg.raise_to(0, 0, 12.0)
    tg.lower_to(0, 0, 8.0)                 # 모순
    assert tg.conflicts() == 1
    out = tg.apply(np.full((2, 2), 10.0))
    assert out[0, 0] == 12.0               # 실측 높이(lo)가 이긴다


def test_protect_footprints_are_untouched():
    """건물 발자국은 건드리지 않는다 — 건물은 버닝 전 지면에 앉아 있다."""
    dem = _flat()
    fp = [(36.0, 36.0), (44.0, 36.0), (44.0, 44.0), (36.0, 44.0)]
    out = burn_scarps(dem, [_levee()], protect_footprints=[fp])
    assert _at(out, 40.0, 40.0) == pytest.approx(10.0, abs=0.01)     # 발자국 안 = 원지형
    assert _at(out, 40.0, 60.0) > 11.5                                # 밖은 제방


def test_noop_without_features():
    dem = _flat()
    assert burn_scarps(dem, []) is dem
    assert burn_scarps(dem, None) is dem


def test_does_not_mutate_input():
    dem = _flat()
    before = dem.grid.copy()
    burn_scarps(dem, [_levee()])
    assert np.array_equal(dem.grid, before)


# ---------------------------------------------------------------------------
# 절토/성토면 — 표고를 만들지 않고 폭을 조인다
# ---------------------------------------------------------------------------

def _smeared_dem(x0=30.0, x1=50.0, z_hi=20.0, z_lo=10.0):
    """등고선이 뭉개 놓은 지형: 폭 20m에 걸쳐 완만히 내려간다(실제 단차는 그보다 좁다)."""
    xs = (np.arange(N) + 0.5) * CELL
    t = np.clip((xs - x0) / (x1 - x0), 0.0, 1.0)
    z = (z_hi + (z_lo - z_hi) * t).astype(np.float32)
    return DEMPatch(grid=np.tile(z, (N, 1)), transform=Affine(CELL, 0, 0.0, 0, -CELL, N * CELL),
                    offset=(0.0, 0.0))


def _slope_pair(xt=36.0, xb=44.0, divi="SJD002"):
    """실제 사면은 36~44m(폭 8m) — DEM이 20m로 번지게 해 놓은 것을 조인다."""
    return ScarpFeature(kind="fill", divi=divi,
                        top=[(xt, 5.0), (xt, 75.0)], bot=[(xb, 5.0), (xb, 75.0)])


def _drop_in_strip(dem, xt=36.0, xb=44.0, y=40.0, x_far=20.0, x_near=60.0):
    """전체 표고차 중 **실제 사면 구간 안에서** 일어나는 비율.

    사면 바깥의 완만한 경사는 진짜 자연 지형일 수 있어 평탄화하지 않는다(그건 표고 발명이다).
    그래서 "전이폭"이 아니라 "단차가 사면 구간에 얼마나 모였는가"로 잰다.
    """
    total = _at(dem, x_far, y) - _at(dem, x_near, y)
    return (_at(dem, xt, y) - _at(dem, xb, y)) / total if total else 0.0


def test_slope_concentrates_drop_into_the_real_face():
    """뭉개져 퍼져 있던 단차가 실제 사면 구간(폭 8m)으로 모인다."""
    dem = _smeared_dem()
    before = _drop_in_strip(dem)
    after = _drop_in_strip(burn_scarps(dem, [_slope_pair()]))
    assert before < 0.5              # 등고선 DEM은 단차의 절반도 사면에 못 담는다
    assert after > 0.6
    assert after > before * 1.4


def test_slope_preserves_elevation_difference():
    """표고차는 **보존**한다 — 폭만 조이지 높이를 발명하지 않는다."""
    dem = _smeared_dem()
    out = burn_scarps(dem, [_slope_pair()])
    for y in (20.0, 40.0, 60.0):
        assert _at(out, 20.0, y) == pytest.approx(_at(dem, 20.0, y), abs=0.2)   # 고지대
        assert _at(out, 60.0, y) == pytest.approx(_at(dem, 60.0, y), abs=0.2)   # 저지대


def test_slope_leaves_far_cells_alone():
    """스트립·probe 밴드 밖은 변하지 않는다."""
    dem = _smeared_dem()
    out = burn_scarps(dem, [_slope_pair()])
    assert _at(out, 5.0, 40.0) == pytest.approx(_at(dem, 5.0, 40.0), abs=0.01)
    assert _at(out, 75.0, 40.0) == pytest.approx(_at(dem, 75.0, 40.0), abs=0.01)


def test_inverted_pair_is_dropped():
    """하단이 상단보다 높은 짝(짝짓기 오류)은 버린다 — 지형을 거꾸로 세우지 않게."""
    dem = _smeared_dem()
    flipped = ScarpFeature(kind="cut", divi="SJD001",
                           top=[(44.0, 5.0), (44.0, 75.0)],    # 낮은 쪽을 '상단'이라 주장
                           bot=[(36.0, 5.0), (36.0, 75.0)])
    out = burn_scarps(dem, [flipped])
    assert np.allclose(out.grid, dem.grid)


def test_flat_pair_below_min_drop_is_dropped():
    """사면고가 SCARP_MIN_DROP_M 미만이면 버린다."""
    dem = _flat()                                   # 완전 평지 → 단차 0
    out = burn_scarps(dem, [_slope_pair()])
    assert np.allclose(out.grid, dem.grid)
    assert SCARP_MIN_DROP_M > 0


# ---------------------------------------------------------------------------
# 입력·출력
# ---------------------------------------------------------------------------

def test_clip_scarps_reads_levee_and_pairs(tmp_path):
    import json

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"k": "levee", "h": 2.5},
         "geometry": {"type": "LineString", "coordinates": [[10, 10], [10, 70]]}},
        {"type": "Feature", "properties": {"k": "fill", "u": "top", "p": 7, "d": "SJD002"},
         "geometry": {"type": "LineString", "coordinates": [[30, 10], [30, 70]]}},
        {"type": "Feature", "properties": {"k": "fill", "u": "bot", "p": 7, "d": "SJD002"},
         "geometry": {"type": "LineString", "coordinates": [[38, 10], [38, 70]]}},
        # 짝 없는 상단선 — 토우도 방향도 모르므로 버린다
        {"type": "Feature", "properties": {"k": "cut", "u": "top", "p": 9, "d": "SJD001"},
         "geometry": {"type": "LineString", "coordinates": [[60, 10], [60, 70]]}},
    ]}
    p = tmp_path / "s.geojson"
    p.write_text(json.dumps(fc), encoding="utf-8")

    got = clip_scarps(p, (0, 0, 80, 80), (0.0, 0.0))
    kinds = sorted(f.kind for f in got)
    assert kinds == ["fill", "levee"]
    lev = next(f for f in got if f.kind == "levee")
    assert lev.height_m == 2.5 and lev.bot is None
    pair = next(f for f in got if f.kind == "fill")
    assert pair.bot and pair.divi == "SJD002"


def test_scarps_to_geometry_drapes_z():
    dem = _flat(12.0)
    g = scarps_to_geometry([_levee(h=2.0)], dem)
    assert g and g[0]["kind"] == "levee" and g[0]["h"] == 2.0
    assert all(abs(p[2] - 12.0) < 0.01 for p in g[0]["points"])


def test_levee_rises_by_height_at_every_sample_including_ends():
    """마루 **전 구간**(양 끝 포함)이 실측 제방고만큼 올라간다.

    회귀 가드: 종단 평활이 `np.convolve(mode="same")`의 0 패딩을 쓰면 선 끝에서 기준
    지반고가 0 쪽으로 끌려가 `raise_to`가 아무 일도 하지 않는다(실측: 상승량이 도면고의
    13%, p10 0%). 경사 지형에서 더 잘 드러나므로 램프 DEM으로 시험한다.
    """
    xs = (np.arange(N) + 0.5) * CELL
    grid = np.tile((50.0 + xs * 0.1).astype(np.float32), (N, 1))   # 서→동 10% 상승
    dem = DEMPatch(grid=grid, transform=Affine(CELL, 0, 0.0, 0, -CELL, N * CELL),
                   offset=(0.0, 0.0))
    h = 2.0
    lev = ScarpFeature(kind="levee", top=[(40.0, 8.0), (40.0, 72.0)], height_m=h)
    out = burn_scarps(dem, [lev])
    rises = [_at(out, 40.0, y) - _at(dem, 40.0, y) for y in (9.0, 20.0, 40.0, 60.0, 71.0)]
    assert min(rises) > h * 0.8, f"끝부분이 안 올라감: {rises}"
    assert max(rises) < h * 1.2
