"""건물 솔리드 생성 — 폴리곤 분해, 층수→높이, offset, 면 구조."""

import pytest

from src.geo.crs import origin_offset
from src.geometry.building import (
    BuildingSolid,
    collect_5186_coords,
    extrude_face_loops,
    features_to_solids,
    floors_of,
)
from tests.conftest import load_fixture


def _shapes():
    return load_fixture("building_shapes.json")


# --- floors_of (gro_flo_co 파싱) ---

@pytest.mark.parametrize(
    "value,expected",
    [("4", 4), ("10", 10), ("0", None), ("", None), (None, None), ("3.0", 3), ("x", None)],
)
def test_floors_of(value, expected):
    assert floors_of({"gro_flo_co": value}) == expected


# --- features_to_solids ---

def test_rectangle_one_solid_four_verts():
    feat = _shapes()["rectangle"]
    solids = features_to_solids([feat], floor_h_m=3.0)
    assert len(solids) == 1
    s = solids[0]
    assert isinstance(s, BuildingSolid)
    assert len(s.footprint_m) == 4          # 닫힘 중복 정점 제거
    assert s.floors == 4
    assert s.height_m == pytest.approx(12.0)  # 4층 × 3.0m (완료기준)


def test_lshape_six_verts():
    feat = _shapes()["lshape"]
    solids = features_to_solids([feat], floor_h_m=3.0)
    assert len(solids) == 1
    assert len(solids[0].footprint_m) == 6
    assert solids[0].height_m == pytest.approx(6.0)


def test_multipolygon_splits():
    feat = _shapes()["multipolygon"]
    solids = features_to_solids([feat], floor_h_m=3.0)
    assert len(solids) == 2                  # 폴리곤별 분리
    assert all(s.height_m == pytest.approx(9.0) for s in solids)


def test_offset_moves_near_origin():
    feats = list(_shapes().values())
    coords = collect_5186_coords(feats)
    offset = origin_offset(coords)
    solids = features_to_solids(feats, floor_h_m=3.0, offset=offset)
    all_x = [x for s in solids for x, _ in s.footprint_m]
    all_y = [y for s in solids for _, y in s.footprint_m]
    # offset 적용 후 좌표는 0 근처(작은 양수) — 100만 단위 5186 원좌표가 아님.
    assert min(all_x) == pytest.approx(0.0, abs=1e-6)
    assert min(all_y) == pytest.approx(0.0, abs=1e-6)
    assert max(all_x) < 1000 and max(all_y) < 1000


def test_missing_floors_preserved_with_default_height():
    feat = {
        "geometry": _shapes()["rectangle"]["geometry"],
        "properties": {"gro_flo_co": None, "buld_nm": "미상"},
    }
    solids = features_to_solids([feat], floor_h_m=3.0, default_floors=1)
    assert solids[0].floors is None             # 확인 불가 보존
    assert solids[0].height_m == pytest.approx(3.0)  # 기본 1층


# --- 면 구조 (사양서 §6.2: 면수 = 변수 + 2) ---

def test_extrude_face_count_rectangle():
    faces = extrude_face_loops(4)
    assert len(faces) == 6                   # 직사각형 6면
    sides = faces[2:]
    assert all(len(f) == 4 for f in sides)   # 옆면 = 쿼드
    assert len(faces[0]) == 4 and len(faces[1]) == 4  # 바닥/천장 = n각형


def test_extrude_face_count_lshape():
    assert len(extrude_face_loops(6)) == 8   # L자 8면


def test_extrude_face_count_general():
    for n in (3, 5, 8, 12):
        assert len(extrude_face_loops(n)) == n + 2


def test_extrude_rejects_degenerate():
    with pytest.raises(ValueError):
        extrude_face_loops(2)
