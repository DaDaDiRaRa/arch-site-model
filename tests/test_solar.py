"""일조·그림자 (solar.py, B-3) — 태양 위치·그림자 투영, 합성 데이터로."""

import math
from datetime import date, datetime, timezone

from src.geometry.building import BuildingSolid
from src.solar import building_shadow, shadow_offset, shadows_for_day, sun_position

SEOUL = (37.5665, 126.9780)


def _at(y, mo, d, h_utc):
    return datetime(y, mo, d, h_utc, 0, tzinfo=timezone.utc)


def test_sun_high_and_south_at_summer_noon():
    # 2026-06-21 03:00 UTC = 12:00 KST, 하지 정오 → 고도 높고 방위 남(≈180)
    alt, az = sun_position(*SEOUL, _at(2026, 6, 21, 3))
    assert alt > 60
    assert 150 < az < 210


def test_sun_low_at_winter_noon():
    # 동지 정오 서울 남중고도 ≈ 29°
    alt, az = sun_position(*SEOUL, _at(2026, 12, 21, 3))
    assert 20 < alt < 40
    assert 150 < az < 210


def test_sun_in_east_in_morning():
    # 00:00 UTC = 09:00 KST 여름 아침 → 태양 동쪽(az 60~130)
    alt, az = sun_position(*SEOUL, _at(2026, 6, 21, 0))
    assert alt > 0
    assert 60 < az < 130


def test_sun_below_horizon_at_night():
    # 15:00 UTC = 00:00 KST → 야간
    alt, _ = sun_position(*SEOUL, _at(2026, 6, 21, 15))
    assert alt < 0


def test_shadow_points_away_from_sun():
    # 남쪽 태양(az=180) → 그림자 북쪽(+y)
    off = shadow_offset(10.0, 30.0, 180.0)
    assert off is not None and abs(off[0]) < 1e-6 and off[1] > 0
    # 동쪽 태양(az=90) → 그림자 서쪽(−x)
    offe = shadow_offset(10.0, 30.0, 90.0)
    assert offe[0] < 0 and abs(offe[1]) < 1e-6


def test_shadow_longer_at_lower_sun():
    hi = shadow_offset(10.0, 60.0, 180.0)
    lo = shadow_offset(10.0, 20.0, 180.0)
    assert math.hypot(*lo) > math.hypot(*hi)


def test_no_shadow_below_horizon():
    assert shadow_offset(10.0, -5.0, 180.0) is None


def test_building_shadow_covers_footprint():
    fp = [(0, 0), (10, 0), (10, 10), (0, 10)]  # 100 m²
    shad = building_shadow(fp, 10.0, 30.0, 180.0)
    assert shad is not None
    assert shad.area >= 100.0  # footprint 포함 + 그림자 확장


def test_shadows_for_day_daylight_and_night():
    b = BuildingSolid(name="A", footprint_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
                      base_z_m=0.0, height_m=12.0, floors=4, attrs={})
    entries = shadows_for_day([b], *SEOUL, date(2026, 12, 21), [12, 23])
    noon = next(e for e in entries if e["time"] == "12:00")
    night = next(e for e in entries if e["time"] == "23:00")
    assert noon["sun_alt"] > 0 and noon["polygons"]          # 정오 그림자 존재
    assert night["sun_alt"] < 0 and night["polygons"] == []  # 야간 없음
