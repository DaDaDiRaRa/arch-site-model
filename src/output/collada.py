"""COLLADA(.dae) 출력 — SketchUp이 확장 없이 File > Import로 여는 대지모델.

.3dm(rhino.py)과 같은 피처를 같은 로컬 미터 좌표로 싣는다:
  지형(정사영상 UV 텍스처) · 건물(중정 포함 닫힌 솔리드, 추정 층수는 주황 재질) · 도로/보도/수계 면 ·
  차선 · 지적선(지형 드레이프) · 옹벽 상단선 · 도시계획 경계선(분류별).
단위는 <unit meter="1">, 축은 Z_UP — SketchUp이 실제 크기로 들인다. 카테고리마다 <node> 하나라
SketchUp에서 그룹으로 묶여 들어온다(건물은 한 동씩 하위 노드 → 개별 그룹, 이름에 층수).

외부 라이브러리 없이 XML을 직접 쓴다. 건물 바닥/지붕은 shapely 제약 들로네 삼각분할(홀 지원).
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import shapely
from shapely.geometry import Polygon

from src.config import M2I

# 재질: id → (이름, RGB 0~1)
_MATERIALS = {
    "mat_building": ("건물", (0.86, 0.86, 0.84)),
    "mat_building_est": ("건물_층수추정", (0.95, 0.62, 0.35)),
    "mat_terrain": ("지형", (0.55, 0.62, 0.45)),
    "mat_terrain_side": ("지형_단면", (0.55, 0.45, 0.33)),   # 흙색 — 대지모델 둘레 벽
    "mat_road": ("도로", (0.45, 0.47, 0.50)),
    "mat_sidewalk": ("보도", (0.69, 0.67, 0.63)),
    "mat_water": ("수계", (0.23, 0.43, 0.65)),
    "mat_lane": ("차선", (0.91, 0.78, 0.29)),
    "mat_cadastral": ("지적", (0.86, 0.78, 0.39)),
    "mat_wall": ("옹벽", (0.55, 0.36, 0.24)),
}


def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if v == v else "0"


def _su_name(name: str) -> str:
    """SketchUp은 가져올 때 노드 이름을 첫 공백에서 자른다("5층 테스트동" → "5층") — 공백을 _로."""
    return escape("_".join(str(name).split()))


def _signed_area(pts) -> float:
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _orient(ring, ccw: bool):
    ring = list(ring)
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    return ring if (_signed_area(ring) > 0) == ccw else ring[::-1]


class _Mesh:
    """삼각형(선택적 UV) 또는 선분 묶음. 정점은 공유 없이 차곡차곡 — 단순·안전."""

    def __init__(self, gid: str, name: str, material: str, kind: str = "triangles", uv_extent=None):
        self.gid, self.name, self.material, self.kind = gid, name, material, kind
        self.pos: list[float] = []
        self.idx: list[int] = []
        self.uv_extent = uv_extent  # (x0,y0,x1,y1) — 평면투영 UV
        self.n = 0

    def _v(self, x, y, z) -> int:
        self.pos += [x, y, z]
        self.n += 1
        return self.n - 1

    def tri(self, a, b, c):
        self.idx += [self._v(*a), self._v(*b), self._v(*c)]

    def add_indexed(self, verts, tris):
        base = self.n
        for x, y, z in verts:
            self._v(x, y, z)
        for a, b, c in tris:
            self.idx += [base + a, base + b, base + c]

    def polyline(self, pts):
        if len(pts) < 2:
            return
        ids = [self._v(*p) for p in pts]
        for i in range(len(ids) - 1):
            self.idx += [ids[i], ids[i + 1]]

    def empty(self) -> bool:
        return not self.idx

    def xml(self) -> str:
        pos = " ".join(_f(v) for v in self.pos)
        g = self.gid
        src = [
            f'<source id="{g}-pos"><float_array id="{g}-pos-a" count="{len(self.pos)}">{pos}</float_array>'
            f'<technique_common><accessor source="#{g}-pos-a" count="{self.n}" stride="3">'
            '<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>'
            "</accessor></technique_common></source>"
        ]
        inputs = f'<input semantic="VERTEX" source="#{g}-vtx" offset="0"/>'
        if self.uv_extent is not None:
            x0, y0, x1, y1 = self.uv_extent
            dx, dy = (x1 - x0) or 1.0, (y1 - y0) or 1.0
            uv = []
            for i in range(self.n):
                uv += [(self.pos[3 * i] - x0) / dx, (self.pos[3 * i + 1] - y0) / dy]
            src.append(
                f'<source id="{g}-uv"><float_array id="{g}-uv-a" count="{len(uv)}">'
                f'{" ".join(f"{v:.5f}" for v in uv)}</float_array>'
                f'<technique_common><accessor source="#{g}-uv-a" count="{self.n}" stride="2">'
                '<param name="S" type="float"/><param name="T" type="float"/>'
                "</accessor></technique_common></source>"
            )
            inputs += f'<input semantic="TEXCOORD" source="#{g}-uv" offset="0" set="0"/>'
        per = 3 if self.kind == "triangles" else 2
        prim = (
            f'<{self.kind} material="{self.material}" count="{len(self.idx) // per}">{inputs}'
            f'<p>{" ".join(map(str, self.idx))}</p></{self.kind}>'
        )
        return (
            f'<geometry id="{g}" name="{escape(self.name)}"><mesh>{"".join(src)}'
            f'<vertices id="{g}-vtx"><input semantic="POSITION" source="#{g}-pos"/></vertices>'
            f"{prim}</mesh></geometry>"
        )


def _split_terrain(verts, tris):
    """지형 삼각형 → (윗면, 둘레 벽). 윗면은 법선이 위(+Z), 벽은 모델 중심 반대쪽(바깥)을 향하게 정렬."""
    cx = sum(v[0] for v in verts) / len(verts)
    cy = sum(v[1] for v in verts) / len(verts)
    top, side = [], []
    for a, b, c in tris:
        (ax, ay, az), (bx, by, bz), (qx, qy, qz) = verts[a], verts[b], verts[c]
        ux, uy, uz = bx - ax, by - ay, bz - az
        wx, wy, wz = qx - ax, qy - ay, qz - az
        nx, ny, nz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
        norm = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
        if abs(nz) / norm < 0.2:  # 거의 수직 = 스커트
            mx, my = (ax + bx + qx) / 3 - cx, (ay + by + qy) / 3 - cy
            side.append((a, b, c) if nx * mx + ny * my >= 0 else (a, c, b))
        else:
            top.append((a, b, c) if nz > 0 else (a, c, b))
    return top, side


def _building_mesh(solid, gid: str, material: str) -> _Mesh | None:
    fp = solid.footprint_m
    if len(fp) < 3 or solid.height_m <= 0:
        return None
    z0, z1 = solid.base_z_m, solid.base_z_m + solid.height_m
    outer = _orient(fp, ccw=True)
    holes = [_orient(h, ccw=False) for h in (solid.holes_m or []) if len(h) >= 3]
    try:
        poly = Polygon(outer, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        tris = shapely.constrained_delaunay_triangles(poly) if poly.geom_type == "Polygon" else None
    except Exception:  # noqa: BLE001 — 깨진 footprint는 벽만
        tris = None
    # SketchUp 아웃라이너에서 읽히는 이름: "5층 건물명" (이름 없으면 관리번호), 추정이면 표시.
    label = (solid.attrs or {}).get("buld_nm") or solid.name
    floors = f"{solid.floors}층" if solid.floors else "층수미상"
    name = f"{floors} {label}" + ("" if solid.floors_source == "measured" and not solid.flagged else " [층수추정]")
    m = _Mesh(gid, name, material)
    for t in (tris.geoms if tris is not None else []):
        c = list(t.exterior.coords)[:3]
        if _signed_area(c) < 0:
            c = c[::-1]
        a, b, cc = c
        m.tri((a[0], a[1], z1), (b[0], b[1], z1), (cc[0], cc[1], z1))       # 지붕(위 향)
        m.tri((a[0], a[1], z0), (cc[0], cc[1], z0), (b[0], b[1], z0))       # 바닥(아래 향)
    # 벽: 외곽 CCW·중정 CW로 맞춰 두면 (a→b→b위→a위) 법선이 항상 바깥(중정 쪽 포함)을 향한다.
    for ring in [outer, *holes]:
        for i in range(len(ring)):
            (ax, ay), (bx, by) = ring[i][:2], ring[(i + 1) % len(ring)][:2]
            m.tri((ax, ay, z0), (bx, by, z0), (bx, by, z1))
            m.tri((ax, ay, z0), (bx, by, z1), (ax, ay, z1))
    return None if m.empty() else m


def _effect(mid: str, rgb, image_id: str | None = None) -> str:
    if image_id:
        diffuse = (
            f'<newparam sid="{mid}-surf"><surface type="2D"><init_from>{image_id}</init_from></surface></newparam>'
            f'<newparam sid="{mid}-samp"><sampler2D><source>{mid}-surf</source></sampler2D></newparam>'
        )
        color = f'<texture texture="{mid}-samp" texcoord="UVSET0"/>'
    else:
        diffuse = ""
        color = f'<color>{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} 1</color>'
    return (
        f'<effect id="{mid}-fx"><profile_COMMON>{diffuse}<technique sid="common"><lambert>'
        f"<diffuse>{color}</diffuse></lambert></technique></profile_COMMON>"
        '<extra><technique profile="GOOGLEEARTH"><double_sided>1</double_sided></technique></extra>'
        "</effect>"
    )


def write_dae(
    path: str | Path,
    solids,
    terrain=None,
    offset: tuple[float, float] = (0.0, 0.0),
    roads=None,
    sidewalks=None,
    water=None,
    lanes=None,
    cadastral=None,
    drape=None,
    walls=None,
    planning=None,
    ortho_image: str | None = None,
    ortho_extent_m=None,
) -> str:
    """대지모델 → .dae. ortho_image는 .dae 기준 **상대 경로**(같은 zip 안 파일명)로 기록한다.

    terrain.vertices는 인치(SketchUp 단위) → /M2I 미터. 도로·수계 등 RoadMesh는 이미 미터.
    반환: 저장된 절대 경로.
    """
    from src.geo.planning import COLORS as PLAN_COLORS
    from src.geo.planning import LABELS as PLAN_LABELS
    from src.geometry.road import ROAD_LIFT_M

    groups: list[tuple[str, str, list[_Mesh]]] = []  # (node id, 이름, 메시들)
    materials = dict(_MATERIALS)

    # 지형(+정사영상 UV). 윗면과 둘레 벽(스커트)을 나눈다 — 스커트는 안쪽을 향해 만들어져 SketchUp에서
    # 뒷면색(하늘색)으로 보였고(2026-09-21 실기), 정사영상이 세로로 늘어나 붙는다. 방향을 바깥으로 맞추고
    # 흙색 재질로 분리한다.
    if terrain is not None and terrain.vertices and terrain.triangles:
        use_tex = bool(ortho_image and ortho_extent_m)
        mat = "mat_ortho" if use_tex else "mat_terrain"
        verts = [(x / M2I, y / M2I, z / M2I) for x, y, z in terrain.vertices]
        top_tris, side_tris = _split_terrain(verts, terrain.triangles)
        tm = _Mesh("g_terrain", "지형", mat, uv_extent=tuple(ortho_extent_m) if use_tex else None)
        tm.add_indexed(verts, top_tris)
        parts = [tm]
        if side_tris:
            sm = _Mesh("g_terrain_side", "지형_단면", "mat_terrain_side")
            sm.add_indexed(verts, side_tris)
            parts.append(sm)
        groups.append(("n_terrain", "지형", parts))

    # 건물 — 한 동씩 노드(SketchUp 개별 그룹). 추정 층수는 주황 재질.
    bl = []
    for i, s in enumerate(solids or []):
        est = s.flagged or s.floors_source != "measured"
        m = _building_mesh(s, f"g_b{i}", "mat_building_est" if est else "mat_building")
        if m is not None:
            bl.append(m)
    if bl:
        groups.append(("n_buildings", "건물", bl))

    # 면 레이어(도로·보도·수계)
    for key, label, mesh, mat, lift in (
        ("road", "도로", roads, "mat_road", ROAD_LIFT_M),
        ("sidewalk", "보도", sidewalks, "mat_sidewalk", ROAD_LIFT_M),
        ("water", "수계", water, "mat_water", 0.0),
    ):
        if mesh is not None and mesh.vertices and mesh.triangles:
            m = _Mesh(f"g_{key}", label, mat)
            m.add_indexed([(x, y, z + lift) for x, y, z in mesh.vertices], mesh.triangles)
            groups.append((f"n_{key}", label, [m]))

    # 선 레이어
    if lanes:
        m = _Mesh("g_lanes", "차선", "mat_lane", kind="lines")
        for line in lanes:
            m.polyline([(x, y, z + 0.05) for x, y, z in line])
        if not m.empty():
            groups.append(("n_lanes", "차선", [m]))
    if cadastral:
        m = _Mesh("g_cadastral", "지적", "mat_cadastral", kind="lines")
        for p in cadastral:
            ring = list(p.footprint_m)
            if len(ring) >= 3:
                m.polyline([(x, y, (drape(x, y) + 0.1) if drape else 0.0) for x, y in ring + ring[:1]])
        if not m.empty():
            groups.append(("n_cadastral", "지적", [m]))
    if walls:
        m = _Mesh("g_walls", "옹벽", "mat_wall", kind="lines")
        for w in walls:
            m.polyline([tuple(p) for p in (w.get("points") or [])])
        if not m.empty():
            groups.append(("n_walls", "옹벽", [m]))
    if planning:
        by_cat: dict[str, _Mesh] = {}
        for item in planning:
            cat = item.get("cat", "other")
            if cat not in by_cat:
                mid = f"mat_plan_{cat}"
                r, g, b = PLAN_COLORS.get(cat, (130, 130, 130))
                materials[mid] = (f"도시계획_{PLAN_LABELS.get(cat, cat)}", (r / 255, g / 255, b / 255))
                by_cat[cat] = _Mesh(f"g_plan_{cat}", f"도시계획_{PLAN_LABELS.get(cat, cat)}", mid, kind="lines")
            by_cat[cat].polyline([tuple(p) for p in item.get("line") or []])
        ms = [m for m in by_cat.values() if not m.empty()]
        if ms:
            groups.append(("n_planning", "도시계획", ms))

    # --- XML 조립 ---
    used = {m.material for _, _, ms in groups for m in ms}
    images = ""
    effects, mats = [], []
    if "mat_ortho" in used:
        images = (
            '<library_images><image id="img_ortho" name="정사영상">'
            f"<init_from>{escape(str(ortho_image))}</init_from></image></library_images>"
        )
        effects.append(_effect("mat_ortho", None, "img_ortho"))
        mats.append('<material id="mat_ortho" name="정사영상"><instance_effect url="#mat_ortho-fx"/></material>')
    for mid, (name, rgb) in materials.items():
        if mid in used:
            effects.append(_effect(mid, rgb))
            mats.append(f'<material id="{mid}" name="{escape(name)}"><instance_effect url="#{mid}-fx"/></material>')

    # 계층은 library_nodes + instance_node로 쓴다 — SketchUp은 이 구조일 때만 노드를 컴포넌트로
    # 살린다. visual_scene에 node를 중첩만 하면 전부 정의 하나로 합쳐 버렸다(2026-09-21 SketchUp 2026
    # 실험: 중첩 node 3동 → 컴포넌트 1개 18면, library_nodes → 컴포넌트 3개). 합쳐지면 건물 그룹이
    # 사라지고, 지형 모서리 정리(확장 import_softener)가 건물까지 둥글게 만든다.
    ident = "<matrix>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</matrix>"
    geoms, lib_nodes, nodes = [], [], []
    for nid, gname, ms in groups:
        children = []
        for m in ms:
            geoms.append(m.xml())
            lib_nodes.append(
                f'<node id="{m.gid}-c" name="{_su_name(m.name)}"><instance_geometry url="#{m.gid}">'
                f'<bind_material><technique_common><instance_material symbol="{m.material}" target="#{m.material}">'
                '<bind_vertex_input semantic="UVSET0" input_semantic="TEXCOORD" input_set="0"/>'
                "</instance_material></technique_common></bind_material></instance_geometry></node>"
            )
            children.append(f'<node id="{m.gid}-i" name="{_su_name(m.name)}">{ident}<instance_node url="#{m.gid}-c"/></node>')
        lib_nodes.append(f'<node id="{nid}-c" name="{_su_name(gname)}">{"".join(children)}</node>')
        nodes.append(f'<node id="{nid}" name="{_su_name(gname)}">{ident}<instance_node url="#{nid}-c"/></node>')

    ox, oy = offset
    doc = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
        "<asset><contributor><authoring_tool>arch-site-model</authoring_tool>"
        f"<comments>로컬 미터 좌표. EPSG:5186 절대좌표 = 로컬 + origin_offset({ox:.3f}, {oy:.3f})</comments>"
        "</contributor>"
        f"<keywords>origin_offset_x={ox:.3f} origin_offset_y={oy:.3f} crs=EPSG:5186</keywords>"
        '<unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>'
        f"{images}<library_effects>{''.join(effects)}</library_effects>"
        f"<library_materials>{''.join(mats)}</library_materials>"
        f"<library_geometries>{''.join(geoms)}</library_geometries>"
        f"<library_nodes>{''.join(lib_nodes)}</library_nodes>"
        f'<library_visual_scenes><visual_scene id="scene" name="대지모델">{"".join(nodes)}</visual_scene>'
        "</library_visual_scenes>"
        '<scene><instance_visual_scene url="#scene"/></scene></COLLADA>'
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return str(p.resolve())


def write_readme(path: str | Path, result_like: dict) -> str:
    """zip에 넣는 좌표·출처 안내문. result_like: stats·provenance·trust_report·coord·address."""
    from src.geo.crs import to_4326

    st = result_like.get("stats") or {}
    pv = result_like.get("provenance") or {}
    ox, oy = st.get("origin_offset") or (0.0, 0.0)
    lon, lat = to_4326(ox, oy)
    er = st.get("elev_range_m")
    tr = (result_like.get("trust_report") or {}).get("buildings") or {}
    lines = [
        "arch-site-model 대지모델 — 좌표·출처 안내",
        "=" * 48,
        f"대상: {result_like.get('address', '')}",
        f"생성: {pv.get('fetched_at', '')}",
        "",
        "[단위·좌표]",
        "- 1 unit = 1 m (.dae <unit meter=1>, .3dm 단위 Meters)",
        "- 모델 원점(0,0,0) = 아래 EPSG:5186 좌표. 절대좌표 = 로컬 + 원점",
        f"  EPSG:5186 (중부원점)  X {ox:.3f}  Y {oy:.3f}",
        f"  WGS84                 경도 {lon:.8f}  위도 {lat:.8f}",
        "- 표고(Z)는 해발 표고(m) 그대로다(임의 기준 아님).",
        "- GIS 도면과 맞추려면 전체를 (0,0)→(X,Y)로 Move. .3dm에는 같은 값이 문서 Strings에도 있다.",
        "",
        "[데이터 출처]",
        f"- 건물: {pv.get('building_src', '')} — 층수×{pv.get('floor_height_m', '')}m",
        f"  실측 층수 {tr.get('measured', '—')}동 / 추정 {tr.get('estimated', '—')}동 (추정 건물은 주황, 이름에 [층수추정])",
    ]
    if pv.get("terrain_tile"):
        lines.append("- 지형: 수치지형도(1:5,000) 등고선·표고점으로 구운 5m DEM (NGII)")
        if er:
            lines.append(f"  표고 범위 {er[0]:.1f} ~ {er[1]:.1f} m")
    if pv.get("orthophoto_src"):
        lines.append(f"- 정사영상: {pv.get('orthophoto_src')} (zoom {pv.get('orthophoto_zoom')})")
    if pv.get("cadastral_src"):
        lines.append(f"- 지적: {pv.get('cadastral_src')}")
    if pv.get("planning_src"):
        lines.append(f"- 도시계획: {pv.get('planning_src')} — 결정선 표시만, 법적 판단 아님")
    lines += [
        "",
        "[파일]",
        "- *.dae : SketchUp File > Import (텍스처는 같은 폴더의 PNG를 참조 — zip을 풀고 여세요)",
        "- *.3dm : Rhino (같은 PNG 참조)",
    ]
    p = Path(path)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return str(p.resolve())
