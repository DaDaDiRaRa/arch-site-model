"""좌표변환 검증 — 대전 좌표 왕복변환 오차 < 0.01m (사양서 Phase 0 완료기준)."""

from src.geo.crs import to_5186, to_4326, origin_offset, apply_offset

# 사양서 §3.1 실측: "대전광역시 서구 괴정동 358" → (127.37098, 36.33998)
DAEJEON_LON = 127.37098
DAEJEON_LAT = 36.33998


def test_to_5186_returns_central_belt_coords():
    """대전 좌표가 EPSG:5186 중부원점 평면좌표로 변환된다."""
    x, y = to_5186(DAEJEON_LON, DAEJEON_LAT)
    # 중부원점(EPSG:5186): X(동) 20만대, Y(북) 40만대 범위(대전 권역).
    assert 150_000 < x < 350_000, f"x out of expected range: {x}"
    assert 350_000 < y < 500_000, f"y out of expected range: {y}"


def test_roundtrip_under_1cm():
    """4326 → 5186 → 4326 왕복 후 다시 5186 거리오차 < 0.01m."""
    x, y = to_5186(DAEJEON_LON, DAEJEON_LAT)
    lon2, lat2 = to_4326(x, y)
    x2, y2 = to_5186(lon2, lat2)
    # 평면(meter)에서의 왕복 오차로 거리 측정.
    err = ((x - x2) ** 2 + (y - y2) ** 2) ** 0.5
    assert err < 0.01, f"왕복 오차 {err} m >= 0.01 m"


def test_origin_offset_is_min_corner():
    coords = [(200_010.0, 400_050.0), (200_000.0, 400_100.0), (200_030.0, 400_005.0)]
    assert origin_offset(coords) == (200_000.0, 400_005.0)


def test_apply_offset_moves_to_origin():
    coords = [(200_010.0, 400_050.0), (200_000.0, 400_005.0)]
    off = origin_offset(coords)
    moved = apply_offset(coords, off)
    # 오프셋 적용 후 최소 꼭짓점은 (0,0).
    assert min(p[0] for p in moved) == 0.0
    assert min(p[1] for p in moved) == 0.0
    assert moved[0] == (10.0, 45.0)


def test_origin_offset_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        origin_offset([])
