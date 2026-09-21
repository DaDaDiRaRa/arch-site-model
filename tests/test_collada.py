"""COLLADA(.dae) 출력 — 합성 데이터로 구조·단위·텍스처·건물 솔리드 검증."""

import xml.etree.ElementTree as ET

from src.config import M2I
from src.geometry.building import BuildingSolid
from src.geometry.road import RoadMesh
from src.geometry.terrain_mesh import TerrainMesh
from src.output.collada import write_dae

NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}


def _solid(**kw):
    base = dict(
        name="1234",
        footprint_m=[(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)],
        holes_m=[[(5.0, 5.0), (15.0, 5.0), (15.0, 15.0), (5.0, 15.0)]],  # 중정
        base_z_m=10.0,
        height_m=17.5,
        floors=5,
        attrs={"buld_nm": "테스트동"},
    )
    base.update(kw)
    return BuildingSolid(**base)


def _terrain():
    # 인치 단위(SketchUp) — write_dae가 /M2I로 미터 환산해야 한다.
    v = [(0, 0, 10), (30, 0, 10), (30, 30, 12), (0, 30, 12)]
    return TerrainMesh(vertices=[(x * M2I, y * M2I, z * M2I) for x, y, z in v], triangles=[(0, 1, 2), (0, 2, 3)])


def _parse(path):
    return ET.parse(path).getroot()


def test_units_axis_and_offset(tmp_path):
    p = write_dae(tmp_path / "a.dae", [_solid()], _terrain(), offset=(200000.0, 450000.0))
    root = _parse(p)
    unit = root.find("c:asset/c:unit", NS)
    assert unit.get("meter") == "1"
    assert root.find("c:asset/c:up_axis", NS).text == "Z_UP"
    assert "origin_offset_x=200000.000" in root.find("c:asset/c:keywords", NS).text


def test_terrain_in_meters_with_ortho_uv(tmp_path):
    p = write_dae(
        tmp_path / "a.dae", [], _terrain(),
        ortho_image="site_ortho.png", ortho_extent_m=(0.0, 0.0, 30.0, 30.0),
    )
    root = _parse(p)
    assert root.find("c:library_images/c:image/c:init_from", NS).text == "site_ortho.png"
    g = root.find("c:library_geometries/c:geometry[@id='g_terrain']", NS)
    pos = [float(v) for v in g.find(".//c:float_array[@id='g_terrain-pos-a']", NS).text.split()]
    assert max(pos) <= 30.0 + 1e-6          # 인치 아님 → 미터
    uv = [float(v) for v in g.find(".//c:float_array[@id='g_terrain-uv-a']", NS).text.split()]
    assert min(uv) >= -1e-6 and max(uv) <= 1.0 + 1e-6
    tri = g.find(".//c:triangles", NS)
    assert tri.get("material") == "mat_ortho"
    assert len(tri.findall("c:input", NS)) == 2   # VERTEX + TEXCOORD


def test_building_closed_solid_with_courtyard(tmp_path):
    p = write_dae(tmp_path / "a.dae", [_solid()])
    root = _parse(p)
    g = root.find("c:library_geometries/c:geometry[@id='g_b0']", NS)
    assert g.get("name") == "5층 테스트동"
    # SketchUp은 library_nodes 구조일 때만 한 동씩 컴포넌트로 살리고, 이름은 첫 공백에서 자른다
    lib = [n.get("name") for n in root.findall("c:library_nodes/c:node", NS)]
    assert "5층_테스트동" in lib and "건물" in lib
    scene = root.find("c:library_visual_scenes/c:visual_scene/c:node", NS)
    assert scene.find("c:instance_node", NS).get("url") == "#n_buildings-c"
    tri = g.find(".//c:triangles", NS)
    n_tri = int(tri.get("count"))
    # 벽 = (외곽 4변 + 중정 4변) × 2 = 16, 지붕·바닥 각 (링 면적 300㎡ 삼각분할) ≥ 8
    assert n_tri >= 16 + 8 * 2
    pos = [float(v) for v in g.find(".//c:float_array", NS).text.split()]
    zs = pos[2::3]
    assert min(zs) == 10.0 and max(zs) == 27.5
    # 지붕 삼각형들이 중정(5~15)을 덮지 않는다 — 지붕 z에서 모든 삼각형 무게중심이 링 안
    idx = [int(i) for i in tri.find("c:p", NS).text.split()]
    for k in range(0, len(idx), 3):
        pts = [(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]) for i in idx[k:k + 3]]
        if all(z == 27.5 for _, _, z in pts):
            cx = sum(x for x, _, _ in pts) / 3
            cy = sum(y for _, y, _ in pts) / 3
            assert not (5 < cx < 15 and 5 < cy < 15)


def test_estimated_floors_marked(tmp_path):
    s = _solid(floors=None, floors_source="default", attrs={})
    root = _parse(write_dae(tmp_path / "a.dae", [s]))
    g = root.find("c:library_geometries/c:geometry[@id='g_b0']", NS)
    assert g.get("name").endswith("[층수추정]")
    assert g.find(".//c:triangles", NS).get("material") == "mat_building_est"


def test_lines_and_faces_layers(tmp_path):
    road = RoadMesh(vertices=[(0, 0, 1), (5, 0, 1), (5, 5, 1)], triangles=[(0, 1, 2)], outlines=[])
    planning = [{"cat": "district_plan", "label": "지구단위계획구역", "name": "X", "line": [[0, 0, 1], [10, 0, 1]]}]
    p = write_dae(
        tmp_path / "a.dae", [], roads=road,
        lanes=[[(0, 0, 1), (5, 0, 1)]], planning=planning,
    )
    root = _parse(p)
    nodes = [n.get("name") for n in root.findall("c:library_visual_scenes/c:visual_scene/c:node", NS)]
    assert nodes == ["도로", "차선", "도시계획"]
    assert root.find(".//c:geometry[@id='g_plan_district_plan']//c:lines", NS).get("count") == "1"
    mats = [m.get("name") for m in root.findall("c:library_materials/c:material", NS)]
    assert "도시계획_지구단위계획구역" in mats


def test_terrain_skirt_split_and_faces_outward():
    from src.output.collada import _split_terrain

    # 윗면 1장(아래를 향하게 잘못 감김) + 동쪽 벽 1장(안쪽을 향하게 감김)
    verts = [(0, 0, 10), (10, 0, 10), (10, 10, 10), (10, 0, 0), (10, 10, 0)]
    top, side = _split_terrain(verts, [(0, 2, 1), (1, 4, 3)])
    assert len(top) == 1 and len(side) == 1

    def normal(t):
        a, b, c = (verts[i] for i in t)
        u = [b[k] - a[k] for k in range(3)]
        w = [c[k] - a[k] for k in range(3)]
        return (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])

    assert normal(top[0])[2] > 0          # 윗면은 위로
    assert normal(side[0])[0] > 0         # 동쪽 벽은 바깥(+X)으로
