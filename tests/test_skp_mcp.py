"""SketchUp build_model 코드 생성 검증 (정적)."""

from src.geometry.building import features_to_solids
from src.output.skp_mcp import build_skp_code, extrude_solid_snippet
from tests.conftest import load_fixture


def _solids():
    feats = list(load_fixture("building_shapes.json").values())
    return features_to_solids(feats, floor_h_m=3.0)


def test_snippet_has_extrude_and_units():
    snip = extrude_solid_snippet()
    assert "def extrude_solid" in snip
    assert "39.3701" in snip            # M2I 인치 변환
    assert "GeometryInput" in snip
    assert "LoopInput" in snip


def test_build_code_structure():
    code = build_skp_code(_solids())
    assert "def extrude_solid" in code
    assert "SOLIDS = [" in code
    assert "model.get_entities().add_group" in code
    assert "fill(g, weld_vertices=True)" in code
    assert "result = {" in code


def test_build_code_no_imports():
    """SketchUp MCP 규칙: import 금지."""
    code = build_skp_code(_solids())
    for line in code.splitlines():
        assert not line.strip().startswith("import ")
        assert not line.strip().startswith("from ")


def test_build_code_embeds_footprints():
    code = build_skp_code(_solids())
    # 4개 피처(사각형/L자/멀티2/중정1) → 5개 솔리드가 SOLIDS 리터럴에 박힘.
    # ("name": 마커는 리터럴에만 등장; 루프 코드는 s["name"] 형태로 구분됨)
    assert code.count('"name": ') == 5
    assert "직사각형동" in code


def test_camera_toggle():
    assert "set_camera" in build_skp_code(_solids(), camera=True)
    assert "set_camera" not in build_skp_code(_solids(), camera=False)


# --- 도로(Phase R) ---

def _road_mesh():
    from src.geometry.road import RoadMesh

    return RoadMesh(
        vertices=[(0.0, 0.0, 1.0), (10.0, 0.0, 1.0), (10.0, 10.0, 1.0), (0.0, 10.0, 1.0)],
        triangles=[(0, 1, 2), (0, 2, 3)],
        outlines=[[(0.0, 0.0, 1.0), (10.0, 0.0, 1.0), (10.0, 10.0, 1.0), (0.0, 10.0, 1.0)]],
    )


def test_build_code_with_roads():
    """roads 지정 시 ROAD 리터럴 + 도로 그룹 코드가 박히고, 유효 파이썬·import 금지 유지."""
    code = build_skp_code(_solids(), roads=_road_mesh())
    assert "ROAD_VERTS = [" in code
    assert "ROAD_TRIS = [" in code
    assert 'set_name("roads")' in code
    compile(code, "<skp>", "exec")  # 구문 유효
    for line in code.splitlines():
        assert not line.strip().startswith("import ")
        assert not line.strip().startswith("from ")


def test_build_code_no_roads_omits_block():
    assert "ROAD_VERTS" not in build_skp_code(_solids())


def test_generated_code_is_valid_python():
    """build_model 전송 전 — 생성 코드가 구문상 유효한 Python 인지."""
    code = build_skp_code(_solids())
    compile(code, "<skp>", "exec")   # SyntaxError 면 실패


# --- 중정(홀) 처리 ---

def _courtyard_solid():
    feat = load_fixture("building_shapes.json")["courtyard"]
    return features_to_solids([feat], floor_h_m=3.0)[0]


def test_holes_in_extrude_helper():
    """extrude_solid 헬퍼에 holes_m 파라미터와 add_face_inner_loop 포함."""
    snip = extrude_solid_snippet()
    assert "holes_m" in snip
    assert "add_face_inner_loop" in snip


def test_courtyard_solid_has_holes_in_literal():
    """중정 건물의 SOLIDS 리터럴에 holes_m 키가 포함된다."""
    s = _courtyard_solid()
    code = build_skp_code([s])
    assert '"holes_m"' in code


def test_no_hole_solid_has_empty_holes_in_literal():
    """홀 없는 건물도 holes_m 키가 포함된다(빈 리스트)."""
    solids = _solids()
    code = build_skp_code(solids)
    assert '"holes_m": []' in code


def test_courtyard_code_valid_python():
    """중정 포함 생성 코드가 구문상 유효한 Python."""
    s = _courtyard_solid()
    code = build_skp_code([s])
    compile(code, "<skp_courtyard>", "exec")
