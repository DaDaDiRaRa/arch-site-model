"""교량 데크 — 버닝 제외·종단 보간·통합표면 분리 (합성 지형, 네트워크 없음).

기하 설정은 실제 결함 그대로다: 양쪽 고원(z=10) 사이에 폭 100m·z=0 하천 골짜기가 있고
도로가 그 위를 직선으로 건넌다. 데크가 없으면 도로가 골짜기로 내려앉는다.
"""

import numpy as np
from affine import Affine

from src.geometry.deck import (
    DeckFeature,
    decks_from_water,
    solve_decks,
    split_centerlines,
)
from src.geometry.road import RoadFeature, build_unified_surface, burn_roads
from src.geometry.water import WaterFeature
from src.terrain.dem import DEMPatch

M2I = 39.3701
CELL = 2.0
N = 120                      # 240m × 240m
RIVER_X0, RIVER_X1 = 70.0, 170.0     # 하천 골짜기 구간(폭 100m)
ROAD_Y0, ROAD_Y1 = 110.0, 130.0      # 도로 폭 20m
WATER_Z = 0.5


def _valley_dem():
    """서쪽 고원 z=10 → 하천 바닥 z=0 → 동쪽 고원 z=10."""
    xs = (np.arange(N) + 0.5) * CELL
    z = np.where((xs >= RIVER_X0) & (xs <= RIVER_X1), 0.0, 10.0).astype(np.float32)
    grid = np.tile(z, (N, 1))
    return DEMPatch(grid=grid, transform=Affine(CELL, 0, 0.0, 0, -CELL, N * CELL),
                    offset=(0.0, 0.0))


def _road():
    return RoadFeature(rings=[[(0.0, ROAD_Y0), (240.0, ROAD_Y0),
                               (240.0, ROAD_Y1), (0.0, ROAD_Y1)]])


def _centerline():
    y = (ROAD_Y0 + ROAD_Y1) / 2
    return [[(0.0, y), (240.0, y)]]


def _water():
    return WaterFeature(rings=[[(RIVER_X0, 0.0), (RIVER_X1, 0.0),
                                (RIVER_X1, 240.0), (RIVER_X0, 240.0)]])


def _bridge_deck():
    """도로 ∩ 하천 = 교량 발자국."""
    return DeckFeature(rings=[[(RIVER_X0, ROAD_Y0), (RIVER_X1, ROAD_Y0),
                               (RIVER_X1, ROAD_Y1), (RIVER_X0, ROAD_Y1)]])


def _solved(dem=None, water=True):
    dem = dem or _valley_dem()
    wf = [_water()] if water else None
    wz = [WATER_Z] if water else None
    return dem, solve_decks([_bridge_deck()], _centerline(), dem, wf, wz, sample_m=CELL)


# ---------------------------------------------------------------------------
# 종단 풀이
# ---------------------------------------------------------------------------

def test_profile_interpolates_between_measured_abutments():
    """데크 z = 양단 실측 표고 사이 보간 — 고원 z=10 양쪽이면 데크도 10."""
    _, surf = _solved()
    assert len(surf.profiles) == 1
    p = surf.profiles[0]
    assert 95.0 <= p.span_m <= 105.0                 # 하천 폭 100m
    assert all(abs(z - 10.0) < 0.2 for z in p.z)     # 양단 다 10 → 수평 데크
    assert surf.z_at(120.0, 120.0) > WATER_Z + 5.0   # 강 한복판도 수면 훨씬 위


def test_profile_slopes_when_banks_differ():
    """양안 표고가 다르면 종단이 단조 경사 — 중간 수직곡선을 발명하지 않는다."""
    dem = _valley_dem()
    g = dem.grid.copy()
    xs = (np.arange(N) + 0.5) * CELL
    g[:, xs > RIVER_X1] = 20.0                        # 동쪽 고원만 20m로
    dem = DEMPatch(grid=g, transform=dem.transform, offset=dem.offset)
    _, surf = _solved(dem)
    z = surf.profiles[0].z
    assert abs(z[0] - 10.0) < 0.6 and abs(z[-1] - 20.0) < 0.6
    assert all(b >= a - 1e-6 for a, b in zip(z, z[1:]))   # 단조


def test_lifted_above_water_is_flagged():
    """양단이 수면 아래면 물리 제약으로 들어올리되 **반드시 표시**한다."""
    dem = _valley_dem()
    flat = DEMPatch(grid=np.zeros_like(dem.grid), transform=dem.transform,
                    offset=dem.offset)                # 전역 z=0, 수면은 0.5
    surf = solve_decks([_bridge_deck()], _centerline(), flat,
                       [_water()], [WATER_Z], sample_m=CELL)
    assert surf.profiles
    assert "lifted_to_clear_water" in surf.flags
    assert min(surf.profiles[0].z) >= WATER_Z


def test_meandering_riverside_road_is_not_a_bridge():
    """하천 안을 사행하는 강변도로는 직진성 가드가 걸러낸다(가짜 교량 방지)."""
    dem = _valley_dem()
    y = (ROAD_Y0 + ROAD_Y1) / 2
    zig = [[(x, y + (20.0 if (i % 2) else -20.0))
            for i, x in enumerate(np.arange(RIVER_X0 - 10, RIVER_X1 + 10, 5.0))]]
    wide = DeckFeature(rings=[[(RIVER_X0, 0.0), (RIVER_X1, 0.0),
                               (RIVER_X1, 240.0), (RIVER_X0, 240.0)]])
    # 경간 가드를 풀어 **직진성 가드만** 시험한다(지그재그는 호장이 길어 둘 다 걸린다).
    surf = solve_decks([wide], zig, dem, [_water()], [WATER_Z], sample_m=CELL,
                       max_span_m=5000.0)
    assert not surf.profiles
    assert "not_straight" in surf.flags


def test_no_land_anchor_means_no_deck():
    """양단이 모두 물 안이면 교량이 아니다 — 만들지 않고 버린 수를 남긴다."""
    dem = _valley_dem()
    y = (ROAD_Y0 + ROAD_Y1) / 2
    inside_only = [[(RIVER_X0 + 5, y), (RIVER_X1 - 5, y)]]   # 중심선이 물 안에서 시작·끝
    deck = DeckFeature(rings=[[(RIVER_X0, ROAD_Y0), (RIVER_X1, ROAD_Y0),
                               (RIVER_X1, ROAD_Y1), (RIVER_X0, ROAD_Y1)]])
    surf = solve_decks([deck], inside_only, dem, [_water()], [WATER_Z], sample_m=CELL)
    assert not surf.profiles and surf.dropped == 1


# ---------------------------------------------------------------------------
# 데이터원 — 수계 프록시
# ---------------------------------------------------------------------------

def test_water_proxy_finds_the_crossing():
    """도로 ∩ 수계가 교량 발자국이 된다(재베이크 0 경로)."""
    decks = decks_from_water([_water()], [_road()], margin_m=4.0)
    assert len(decks) == 1
    assert decks[0].source == "water_proxy"
    from src.geometry.deck import _poly

    a = _poly(decks[0].rings).area
    assert 100 * 20 * 0.9 <= a <= (100 + 8) * 20 * 1.1      # 하천폭×도로폭(+margin)


def test_water_proxy_empty_without_crossing():
    far = RoadFeature(rings=[[(0.0, 200.0), (50.0, 200.0), (50.0, 220.0), (0.0, 220.0)]])
    assert decks_from_water([_water()], [far], margin_m=4.0) == []


# ---------------------------------------------------------------------------
# 버닝 제외
# ---------------------------------------------------------------------------

def test_burn_leaves_terrain_under_deck_untouched():
    """데크 아래 지형은 강 그대로 — 변경 셀 0. 스커트까지 제외돼야 한다."""
    dem, surf = _solved()
    kw = dict(win_m=40.0, sample_m=CELL, max_dist_m=30.0, skirt_m=12.0, max_dev=2.0)
    with_deck = burn_roads(dem, [_road()], _centerline(),
                           exclude_polys=surf.exclude_polys(), **kw)
    from shapely.geometry import Point

    poly = surf.polygon()
    tf = dem.transform
    changed = 0
    for r in range(N):
        for c in range(N):
            if abs(float(with_deck.grid[r, c]) - float(dem.grid[r, c])) < 1e-6:
                continue
            x, y = tf * (c + 0.5, r + 0.5)
            if poly.contains(Point(x, y)):
                changed += 1
    assert changed == 0


def test_exclude_none_is_unchanged_from_today():
    """exclude_polys=None이면 오늘 결과와 비트 동일(회귀 가드)."""
    dem = _valley_dem()
    kw = dict(win_m=40.0, sample_m=CELL, max_dist_m=30.0, skirt_m=12.0, max_dev=2.0)
    a = burn_roads(dem, [_road()], _centerline(), **kw)
    b = burn_roads(dem, [_road()], _centerline(), exclude_polys=None, **kw)
    assert np.array_equal(a.grid, b.grid)


def test_split_centerlines_removes_span():
    """강 중간 중심선 샘플을 버닝 트리에서 뺀다 — 아붓먼트를 끌어내리지 않게."""
    _, surf = _solved()
    rest = split_centerlines(_centerline(), surf.polygon())
    assert len(rest) == 2                     # 서쪽·동쪽 육상부 두 조각
    for piece in rest:
        for x, _y in piece:
            assert not (RIVER_X0 + 1 < x < RIVER_X1 - 1)


# ---------------------------------------------------------------------------
# 통합표면 — 데크 클래스 분리
# ---------------------------------------------------------------------------

def _unified(decks):
    dem, surf = _solved()
    if decks:
        dem = burn_roads(dem, [_road()], split_centerlines(_centerline(), surf.polygon()),
                         win_m=40.0, sample_m=CELL, max_dist_m=30.0, skirt_m=12.0,
                         max_dev=2.0, exclude_polys=surf.exclude_polys())
    return build_unified_surface(dem, 0.25, [_road()], [], CELL, M2I,
                                 centerlines=_centerline(), crown_pct=2.0,
                                 decks=surf if decks else None)


def _top_z(mesh):
    """같은 (x,y)에 윗면(노면)과 아랫면(측면 밑동)이 함께 있으므로 **윗면만** 뽑는다."""
    top: dict = {}
    for x, y, z in mesh.vertices:
        k = (round(x, 2), round(y, 2))
        top[k] = max(top.get(k, -1e9), z)
    return list(top.values())


def test_deck_mesh_is_above_water_and_separate():
    """데크 **노면**이 수면 위에 있다 — 정점을 지형과 공유하지 않는다.

    (측면(fascia)은 지면까지 내려가므로 전체 정점의 최저값으로 재면 안 된다.)
    """
    u = _unified(True)
    assert u.deck is not None and u.deck.triangles
    dz = _top_z(u.deck)
    assert min(dz) > WATER_Z                      # 노면 전체가 수면 위
    assert max(dz) - min(dz) < 1.0                # 수평 데크(양안 같은 높이)
    # 지형은 여전히 골짜기 — 데크 높이로 솟지 않았다.
    tz = [z / M2I for _x, _y, z in u.terrain.vertices]
    assert min(tz) < 1.0


def test_deck_is_flat_despite_crown():
    """크라운(횡단구배)은 데크에 적용되지 않는다 — 데크 z는 종단에서 온다."""
    a = _unified(True).deck
    zs = sorted({round(z, 3) for z in _top_z(a)})
    assert len(zs) <= 3                            # 노면은 사실상 한 평면


def test_without_decks_road_sinks_into_river():
    """대조군: 데크가 없으면 노면이 강으로 내려앉는다(이 결함을 고치는 것)."""
    u = _unified(False)
    assert u.deck is None
    mid = [z for x, _y, z in u.road.vertices if RIVER_X0 + 10 < x < RIVER_X1 - 10]
    assert mid and min(mid) < WATER_Z


def test_deck_has_side_faces_down_to_terrain():
    """데크 가장자리에서 지면까지 수직 면(교량 측면)이 선다.

    데크는 지형과 정점을 공유하지 않으므로(같은 x,y에 노면 z와 지면 z가 따로) 측면을 안
    닫으면 옆에서 봤을 때 데크와 강바닥 사이가 뚫려 보인다.
    """
    from src.geometry.road import DECK_FASCIA_MIN_M

    u = _unified(True)
    zs = [z for _x, _y, z in u.deck.vertices]
    top = _top_z(u.deck)
    # 노면보다 한참 아래까지 내려간 정점이 있어야 한다(= 측면 밑동)
    assert min(zs) < min(top) - DECK_FASCIA_MIN_M
    # 측면 밑동은 그 자리 지형 높이다 — 강바닥(0)까지 내려간다
    assert min(zs) < 1.0
    # 그래도 노면 자체는 수면 위에 그대로 있다
    assert min(top) > WATER_Z
