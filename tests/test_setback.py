"""정북일조 사선 봉투(setback.py, B-1') — 진북·subject parcel·규칙·봉투 메시, 합성 데이터로."""

from src.geometry.cadastral import CadastralParcel
from src.geometry.setback import (
    build_setback_envelope,
    find_subject_parcel,
    north_azimuth_deg,
    setback_max_height,
    true_north_local,
)


def test_true_north_near_zero_on_central_meridian():
    # EPSG:5186 중부원점 자오선 127°E → 진북 ≈ 격자북(+Y), 수렴각 ≈ 0
    nd = true_north_local(127.0, 37.5)
    assert abs(north_azimuth_deg(nd)) < 0.05


def test_true_north_convergence_off_meridian():
    # 자오선에서 먼 경도일수록 수렴각(격자북≠진북) 커짐
    az_near = abs(north_azimuth_deg(true_north_local(127.5, 37.5)))
    az_far = abs(north_azimuth_deg(true_north_local(129.0, 37.5)))
    assert az_far > az_near > 0


def test_setback_profile_shape():
    assert setback_max_height(1.0) == 0.0     # 1.5m 이내 → 0
    assert setback_max_height(1.5) == 10.0    # 기본이격 충족 → 10m 캡
    assert setback_max_height(5.0) == 10.0    # threshold/2 까지 평평
    assert setback_max_height(8.0) == 16.0    # 이후 h = 2·d 사선


def test_find_subject_parcel_contains():
    a = CadastralParcel(pnu="A" * 19, footprint_m=[(0, 0), (20, 0), (20, 20), (0, 20)])
    b = CadastralParcel(pnu="B" * 19, footprint_m=[(100, 100), (120, 100), (120, 120), (100, 120)])
    assert find_subject_parcel([a, b], (10, 10)) is a
    assert find_subject_parcel([a, b], (110, 110)) is b
    assert find_subject_parcel([a, b], (500, 500)) is None


def test_build_envelope_slopes_up_to_south():
    # 20×20 필지, 진북 +Y. 북단(y=20) d=0 → z 낮음, 남단(y=0) d 큼 → z 큼.
    env = build_setback_envelope([(0, 0), (20, 0), (20, 20), (0, 20)], (0.0, 1.0))
    assert env is not None
    zs = [v[2] for v in env["vertices"]]
    assert env["triangles"]
    assert max(zs) > 30.0    # 남쪽 d~18 → z~36
    assert min(zs) >= 0.0


def test_build_envelope_invalid_returns_none():
    assert build_setback_envelope([(0, 0), (1, 1)], (0.0, 1.0)) is None
