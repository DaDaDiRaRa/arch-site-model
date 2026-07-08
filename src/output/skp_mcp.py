"""SketchUp MCP build_model 코드 생성 (사양서 §6.2, 출력 .skp).

엔진은 MCP를 직접 호출하지 않는다. build_model 에 넣을 **Python 코드 문자열**을 생성해
반환하고, 실제 호출은 오케스트레이터(Claude)가 수행한다(사양서 §4 권장 방식).

SketchUp MCP 규칙(검증됨):
  - import 금지 (GeometryInput/LoopInput/SUPoint3D/Group/model/math 등은 전역 제공)
  - 단위 = 인치 (M2I = 39.3701)
  - 좌표 X=폭, Y=깊이, Z=높이 (Z=0 지면)
  - 옆면 = 수직 쿼드 (변마다 1면)
"""

from __future__ import annotations

from src.config import M2I

# §6.2 검증된 돌출 헬퍼: 미터 footprint(+holes) → 인치 쿼드 솔리드 GeometryInput.
_EXTRUDE_HELPER = f'''\
M2I = {M2I}

def extrude_solid(fp_m, base_z_m, height_m, holes_m=None):
    holes_m = holes_m or []
    fp = [(x * M2I, y * M2I) for (x, y) in fp_m]
    holes = [[(x * M2I, y * M2I) for (x, y) in h] for h in holes_m]
    bz = base_z_m * M2I
    h = height_m * M2I
    n = len(fp)
    # 정점 배열: [outer_bottom, outer_top, hole0_bottom, hole0_top, ...]
    verts = (
        [SUPoint3D(x, y, bz) for x, y in fp]
        + [SUPoint3D(x, y, bz + h) for x, y in fp]
    )
    hole_offsets = []
    for hole in holes:
        off = len(verts)
        hole_offsets.append(off)
        verts += [SUPoint3D(x, y, bz) for x, y in hole]
        verts += [SUPoint3D(x, y, bz + h) for x, y in hole]
    g = GeometryInput()
    g.set_vertices(verts)
    lp = LoopInput()                           # 바닥(외곽, 하향)
    for i in range(n - 1, -1, -1):
        lp.add_vertex_index(i)
    floor_idx, g = g.add_face(lp)
    for off, hole in zip(hole_offsets, holes): # 바닥 홀 inner loop
        m = len(hole)
        ilp = LoopInput()
        for i in range(m):
            ilp.add_vertex_index(off + i)
        g.add_face_inner_loop(floor_idx, ilp)
    lp = LoopInput()                           # 천장(외곽, 상향)
    for i in range(n):
        lp.add_vertex_index(n + i)
    ceil_idx, g = g.add_face(lp)
    for off, hole in zip(hole_offsets, holes): # 천장 홀 inner loop
        m = len(hole)
        ilp = LoopInput()
        for i in range(m - 1, -1, -1):
            ilp.add_vertex_index(off + m + i)
        g.add_face_inner_loop(ceil_idx, ilp)
    for i in range(n):                         # 외벽 쿼드
        j = (i + 1) % n
        lp = LoopInput()
        for vi in [i, j, n + j, n + i]:
            lp.add_vertex_index(vi)
        _, g = g.add_face(lp)
    for off, hole in zip(hole_offsets, holes): # 중정 내벽 쿼드
        m = len(hole)
        for i in range(m):
            j = (i + 1) % m
            lp = LoopInput()
            for vi in [off + i, off + j, off + m + j, off + m + i]:
                lp.add_vertex_index(vi)
            _, g = g.add_face(lp)
    return g
'''

_BUILD_LOOP = '''\
built = 0
for s in SOLIDS:
    if len(s["footprint_m"]) < 3:
        continue
    grp = Group()
    model.get_entities().add_group(grp)
    g = extrude_solid(s["footprint_m"], s["base_z_m"], s["height_m"], s.get("holes_m"))
    grp.get_entities().fill(g, weld_vertices=True)
    try:
        grp.set_name(s["name"])
    except Exception:
        pass
    built += 1
'''

# 부지/매싱 → 상공 시점 카메라 (사양서 카메라 스킬 site preset).
_CAMERA = '''\
xs, ys, zs = [], [], [0.0]
for s in SOLIDS:
    for (x, y) in s["footprint_m"]:
        xs.append(x * M2I); ys.append(y * M2I)
    zs.append((s["base_z_m"] + s["height_m"]) * M2I)
if xs:
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    zmin, zmax = 0.0, max(zs)
    cx = (xmin + xmax) / 2.0
    cy = (ymin + ymax) / 2.0
    diag = math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2) or 1.0
    eye = SUPoint3D(cx, cy - diag * 0.3, zmax + diag * 0.9)
    target = SUPoint3D(cx, cy + diag * 0.1, 0)
    up = SUVector3D(0, 0, 1)
    cam = Camera()
    cam.set_orientation(eye, target, up)
    cam.enable_perspective()
    cam.set_perspective_frustum_fov(30.0)
    model.set_camera(cam)
'''


# Phase 3B: 지형 삼각망 생성 + 내부 엣지 소프트닝
_TERRAIN_BUILD = '''\
if TERRAIN_VERTS:
    grp_t = Group()
    model.get_entities().add_group(grp_t)
    g_t = GeometryInput()
    g_t.set_vertices([SUPoint3D(x, y, z) for (x, y, z) in TERRAIN_VERTS])
    for tri in TERRAIN_TRIS:
        lp = LoopInput()
        for vi in tri:
            lp.add_vertex_index(vi)
        _, g_t = g_t.add_face(lp)
    grp_t.get_entities().fill(g_t, weld_vertices=True)
    try:
        grp_t.set_name("terrain")
    except Exception:
        pass
    try:
        for edge in grp_t.get_entities().edges():
            edge.set_soft(True)
            edge.set_smooth(True)
    except Exception:
        pass
'''

# Phase R: 도로 노면/보도 — DEM 드레이프 삼각 메시(인치, 소프트 엣지). prefix/그룹으로 일반화.
def _mesh_build(prefix: str, name: str, grp: str) -> str:
    return f'''\
if {prefix}_VERTS and {prefix}_TRIS:
    {grp} = Group()
    model.get_entities().add_group({grp})
    g_x = GeometryInput()
    g_x.set_vertices([SUPoint3D(x, y, z) for (x, y, z) in {prefix}_VERTS])
    for tri in {prefix}_TRIS:
        lp = LoopInput()
        for vi in tri:
            lp.add_vertex_index(vi)
        _, g_x = g_x.add_face(lp)
    {grp}.get_entities().fill(g_x, weld_vertices=True)
    try:
        {grp}.set_name("{name}")
    except Exception:
        pass
    try:
        for edge in {grp}.get_entities().edges():
            edge.set_soft(True)
            edge.set_smooth(True)
    except Exception:
        pass
'''

# Phase 5: 지적 경계 — Z=0 평면 폴리곤 (면 생성)
_CADASTRAL_BUILD = '''\
for p in CADASTRAL:
    if len(p["footprint_m"]) < 3:
        continue
    fp = [(x * M2I, y * M2I) for (x, y) in p["footprint_m"]]
    n = len(fp)
    g = GeometryInput()
    g.set_vertices([SUPoint3D(x, y, 0.0) for x, y in fp])
    lp = LoopInput()
    for i in range(n):
        lp.add_vertex_index(i)
    _, g = g.add_face(lp)
    grp = Group()
    model.get_entities().add_group(grp)
    grp.get_entities().fill(g, weld_vertices=True)
    try:
        grp.set_name(p["pnu"])
    except Exception:
        pass
'''


def extrude_solid_snippet() -> str:
    """재사용 가능한 extrude_solid 헬퍼 텍스트(M2I 포함)."""
    return _EXTRUDE_HELPER


def _terrain_literal(mesh) -> str:
    """TerrainMesh → build_model 코드에 박을 Python 리터럴."""
    verts = ", ".join(f"({x!r}, {y!r}, {z!r})" for x, y, z in mesh.vertices)
    tris = ", ".join(f"({a}, {b}, {c})" for a, b, c in mesh.triangles)
    return f"TERRAIN_VERTS = [{verts}]\nTERRAIN_TRIS = [{tris}]\n"


def _solids_literal(solids) -> str:
    """BuildingSolid 목록 → build_model 코드에 박을 Python 리터럴 문자열.

    flagged=True 건물은 name 뒤에 ' [층수미확인]' 접미사를 붙인다.
    holes_m 있으면 포함(중정 내부 링).
    """
    items = []
    for s in solids:
        fp = ", ".join(f"({x!r}, {y!r})" for x, y in s.footprint_m)
        name = s.name + (" [층수미확인]" if s.flagged else "")
        holes = "[" + ", ".join(
            "[" + ", ".join(f"({x!r}, {y!r})" for x, y in h) + "]"
            for h in (s.holes_m or [])
        ) + "]"
        items.append(
            "    {"
            f'"name": {name!r}, '
            f'"footprint_m": [{fp}], '
            f'"holes_m": {holes}, '
            f'"base_z_m": {s.base_z_m!r}, '
            f'"height_m": {s.height_m!r}'
            "},"
        )
    return "SOLIDS = [\n" + "\n".join(items) + "\n]\n"


def _cadastral_literal(parcels) -> str:
    """CadastralParcel 목록 → build_model 코드에 박을 Python 리터럴."""
    items = []
    for p in parcels:
        fp = ", ".join(f"({x!r}, {y!r})" for x, y in p.footprint_m)
        items.append(f'    {{"pnu": {p.pnu!r}, "footprint_m": [{fp}]}},')
    return "CADASTRAL = [\n" + "\n".join(items) + "\n]\n"


def _mesh_literal(mesh, prefix: str) -> str:
    """RoadMesh(로컬 미터) → build_model 코드 리터럴. 미터→인치(×M2I) + 노면 리프트."""
    from src.geometry.road import ROAD_LIFT_M

    verts = ", ".join(
        f"({x * M2I!r}, {y * M2I!r}, {(z + ROAD_LIFT_M) * M2I!r})" for x, y, z in mesh.vertices
    )
    tris = ", ".join(f"({a}, {b}, {c})" for a, b, c in mesh.triangles)
    return f"{prefix}_VERTS = [{verts}]\n{prefix}_TRIS = [{tris}]\n"


def build_skp_code(solids, terrain=None, cadastral=None, roads=None, sidewalks=None, camera: bool = True) -> str:
    """solids → SketchUp MCP build_model 에 넣을 완전한 Python 코드 문자열.

    terrain: TerrainMesh (Phase 3B). None 이면 건물만 출력(Phase 2 호환).
    cadastral: list[CadastralParcel] (Phase 5). None 이면 지적 레이어 생략.
    roads/sidewalks: RoadMesh (Phase R). None/삼각형 없음이면 생략.
    camera=True 면 상공 시점 카메라를 설정한다.
    """
    has_terrain = terrain is not None
    has_cadastral = cadastral is not None and len(cadastral) > 0
    has_roads = roads is not None and bool(getattr(roads, "triangles", None))
    has_sidewalks = sidewalks is not None and bool(getattr(sidewalks, "triangles", None))
    if has_terrain and has_cadastral:
        phase = "Phase 5"
    elif has_terrain:
        phase = "Phase 3B"
    elif has_cadastral:
        phase = "Phase 5"
    else:
        phase = "Phase 2"

    parts = [
        f"# arch-site-model — generated building massing + terrain ({phase})",
        _EXTRUDE_HELPER,
        _solids_literal(solids),
        _BUILD_LOOP,
    ]
    if has_terrain:
        parts.append(_terrain_literal(terrain))
        parts.append(_TERRAIN_BUILD)
    if has_roads:
        parts.append(_mesh_literal(roads, "ROAD"))
        parts.append(_mesh_build("ROAD", "roads", "grp_r"))
    if has_sidewalks:
        parts.append(_mesh_literal(sidewalks, "SIDEWALK"))
        parts.append(_mesh_build("SIDEWALK", "sidewalks", "grp_s"))
    if has_cadastral:
        parts.append(_cadastral_literal(cadastral))
        parts.append(_CADASTRAL_BUILD)
    if camera:
        parts.append(_CAMERA)
    parts.append('result = {"buildings_built": built, "solids": len(SOLIDS)}')
    return "\n".join(parts)
