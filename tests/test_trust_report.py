"""신뢰도 리포트(A-1) 단위 테스트 — 합성 result dict로 순수 함수 검증(API/파일 불필요)."""

from src.config import TERRAIN_MAX_ERROR_M
from src.trust_report import build_trust_report


def _full_result(**over):
    r = {
        "ok": True,
        "address": "대전광역시 서구 괴정동 358",
        "outputs": {
            "skp": {
                "terrain_triangles": 2467,
                "orthophoto": {"image_path": "x.png", "missing_tiles": 0, "zoom": 18},
            }
        },
        "stats": {
            "buildings": 142,
            "with_floors": 128,
            "flagged": 0,
            "elev_range_m": [35.2, 112.7],
        },
        "provenance": {
            "building_src": "VWorld LT_C_SPBD",
            "floor_height_m": 3.0,
            "missing_floors_policy": "default",
            "radius_m": 250,
            "fetched_at": "2026-07-09T03:04:00+00:00",
            "terrain_tile": "dem_daejeon_36710065_66.tif",
            "orthophoto_src": "VWorld 위성",
            "orthophoto_zoom": 18,
        },
        "qa": {"summary": {"total": 4, "warnings": 3,
                           "by_kind": {"steep_site": 2, "building_overlap": 1, "footprint_tiny": 1}}},
    }
    r.update(over)
    return r


def test_building_counts_and_pct():
    tr = build_trust_report(_full_result())
    assert tr["buildings"] == {"total": 142, "measured": 128, "estimated": 14, "measured_pct": 90}


def test_terrain_fields_from_result():
    tr = build_trust_report(_full_result())
    assert tr["terrain"]["max_error_m"] == TERRAIN_MAX_ERROR_M
    assert tr["terrain"]["triangles"] == 2467
    assert tr["terrain"]["elev_range_m"] == [35.2, 112.7]


def test_orthophoto_fields_from_result():
    tr = build_trust_report(_full_result())
    assert tr["orthophoto"]["zoom"] == 18
    assert tr["orthophoto"]["source"] == "VWorld 위성"
    assert tr["orthophoto"]["missing_tiles"] == 0


def test_qa_summary_passthrough():
    tr = build_trust_report(_full_result())
    assert tr["qa"]["warnings"] == 3


def test_caveats_height_and_estimated():
    tr = build_trust_report(_full_result())
    assert any("실측 높이가 아" in c for c in tr["caveats"])
    assert any("14동" in c for c in tr["caveats"])  # 142 - 128


def test_caveat_missing_ortho_tiles():
    tr = build_trust_report(_full_result(
        outputs={"skp": {"terrain_triangles": 10,
                         "orthophoto": {"image_path": "x", "missing_tiles": 5, "zoom": 18}}}))
    assert any("5장" in c for c in tr["caveats"])


def test_buildings_only_no_terrain_no_ortho():
    r = {
        "address": "x",
        "outputs": {"skp": {"solids": 3}},
        "stats": {"buildings": 3, "with_floors": 3},
        "provenance": {"floor_height_m": 3.0, "missing_floors_policy": "default",
                       "radius_m": 100, "fetched_at": "t", "building_src": "VWorld LT_C_SPBD"},
        "qa": None,
    }
    tr = build_trust_report(r)
    assert tr["terrain"] is None
    assert tr["orthophoto"] is None
    assert tr["qa"] is None
    assert tr["buildings"]["measured_pct"] == 100


def test_empty_result_is_safe():
    tr = build_trust_report({})
    assert tr["buildings"]["total"] == 0
    assert tr["terrain"] is None
    assert tr["orthophoto"] is None
    assert tr["caveats"]  # 최소한 높이 가정 고지는 항상
