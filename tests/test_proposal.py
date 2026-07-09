"""제안 매스 + 조망점 (proposal.py, B-2) — 합성 데이터로."""

from src.geometry.proposal import build_proposed_mass, standard_viewpoints


def test_build_proposed_no_dem_base_zero():
    p = build_proposed_mass([(0, 0), (20, 0), (20, 20), (0, 20)], 30.0, dem=None, floor_h_m=3.0)
    assert p["proposed"] is True
    assert p["base_z"] == 0.0
    assert p["height"] == 30.0
    assert p["floors"] == 10
    assert len(p["footprint"]) == 4


def test_build_proposed_invalid():
    assert build_proposed_mass([(0, 0), (1, 1)], 10, None) is None      # 정점 부족
    assert build_proposed_mass([(0, 0), (20, 0), (20, 20), (0, 20)], 0, None) is None  # 높이 0


def test_standard_viewpoints_four_around_center():
    vps = standard_viewpoints([(0, 0), (20, 0), (20, 20), (0, 20)], base_z=0.0, height=30.0)
    assert len(vps) == 4
    assert {v["name"] for v in vps} == {"남측 조망", "북측 조망", "동측 조망", "서측 조망"}
    for v in vps:
        assert len(v["eye"]) == 3 and len(v["target"]) == 3
        assert abs(v["target"][0] - 10) < 1 and abs(v["target"][1] - 10) < 1   # 사이트 중심
    east = next(v for v in vps if v["name"] == "동측 조망")
    west = next(v for v in vps if v["name"] == "서측 조망")
    assert east["eye"][0] > 10 > west["eye"][0]   # 동측 eye는 동, 서측은 서
