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
    # 3개 피처(사각형/L자/멀티2) → 4개 솔리드가 SOLIDS 리터럴에 박힘.
    # ("name": 마커는 리터럴에만 등장; 루프 코드는 s["name"] 형태로 구분됨)
    assert code.count('"name": ') == 4
    assert "직사각형동" in code


def test_camera_toggle():
    assert "set_camera" in build_skp_code(_solids(), camera=True)
    assert "set_camera" not in build_skp_code(_solids(), camera=False)


def test_generated_code_is_valid_python():
    """build_model 전송 전 — 생성 코드가 구문상 유효한 Python 인지."""
    code = build_skp_code(_solids())
    compile(code, "<skp>", "exec")   # SyntaxError 면 실패
