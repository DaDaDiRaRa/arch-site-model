"""SketchUp MCP build_model 코드 생성 (사양서 §6.2, 출력 .skp).

엔진은 MCP를 직접 호출하지 않는다. build_model 에 넣을 **Python 코드 문자열**을 생성해
반환하고, 실제 호출은 오케스트레이터(Claude)가 수행한다(사양서 §4 권장 방식).

SketchUp MCP 규칙(검증됨):
  - import 금지 (GeometryInput/LoopInput/SUPoint3D/Group/model/math 등은 전역 제공)
  - 단위 = 인치 (M2I = 39.3701)
  - 좌표 X=폭, Y=깊이, Z=높이 (Z=0 지면)
  - 옆면 = 수직 쿼드 (변마다 1면)
"""

from src.config import M2I

# §6.2 검증된 돌출 헬퍼: 미터 footprint → 인치 쿼드 솔리드 GeometryInput.
_EXTRUDE_HELPER = f'''\
M2I = {M2I}

def extrude_solid(fp_m, base_z_m, height_m):
    fp = [(x * M2I, y * M2I) for (x, y) in fp_m]
    bz = base_z_m * M2I
    h = height_m * M2I
    n = len(fp)
    g = GeometryInput()
    g.set_vertices(
        [SUPoint3D(x, y, bz) for x, y in fp]
        + [SUPoint3D(x, y, bz + h) for x, y in fp]
    )
    lp = LoopInput()                          # 바닥(하향)
    for i in range(n - 1, -1, -1):
        lp.add_vertex_index(i)
    _, g = g.add_face(lp)
    lp = LoopInput()                          # 천장(상향)
    for i in range(n):
        lp.add_vertex_index(n + i)
    _, g = g.add_face(lp)
    for i in range(n):                        # 옆면 = 쿼드
        j = (i + 1) % n
        lp = LoopInput()
        for vi in [i, j, n + j, n + i]:
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
    g = extrude_solid(s["footprint_m"], s["base_z_m"], s["height_m"])
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


def extrude_solid_snippet() -> str:
    """재사용 가능한 extrude_solid 헬퍼 텍스트(M2I 포함)."""
    return _EXTRUDE_HELPER


def _solids_literal(solids) -> str:
    """BuildingSolid 목록 → build_model 코드에 박을 Python 리터럴 문자열."""
    items = []
    for s in solids:
        fp = ", ".join(f"({x!r}, {y!r})" for x, y in s.footprint_m)
        items.append(
            "    {"
            f'"name": {s.name!r}, '
            f'"footprint_m": [{fp}], '
            f'"base_z_m": {s.base_z_m!r}, '
            f'"height_m": {s.height_m!r}'
            "},"
        )
    return "SOLIDS = [\n" + "\n".join(items) + "\n]\n"


def build_skp_code(solids, terrain=None, camera: bool = True) -> str:
    """solids → SketchUp MCP build_model 에 넣을 완전한 Python 코드 문자열.

    terrain 은 Phase 3 예약(현재 미사용). camera=True 면 상공 시점 카메라를 설정한다.
    """
    parts = [
        "# arch-site-model — generated building massing (Phase 2)",
        _EXTRUDE_HELPER,
        _solids_literal(solids),
        _BUILD_LOOP,
    ]
    if camera:
        parts.append(_CAMERA)
    parts.append('result = {"buildings_built": built, "solids": len(SOLIDS)}')
    return "\n".join(parts)
