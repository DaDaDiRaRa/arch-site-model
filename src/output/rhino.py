"""rhino3dm .3dm 출력 어댑터 (Phase 4-5, 사양서 §4.2).

건물: Extrusion (footprint 닫힌 PolylineCurve → Z 방향 돌출, 캡 포함)
지형: Mesh (삼각망, 인치 → 미터 역변환)
지적: PolylineCurve at Z=0 (대지 경계, Phase 5)
레이어: buildings / buildings_unverified / terrain / cadastral
좌표계: 로컬 미터 (BuildingSolid.footprint_m / base_z_m / height_m 그대로 사용)
origin_offset: 문서 Strings + 각 객체 UserString에 이중 기록 (좌표 복원용)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import rhino3dm

from src.config import M2I

if TYPE_CHECKING:
    from src.geometry.building import BuildingSolid
    from src.geometry.cadastral import CadastralParcel
    from src.geometry.terrain_mesh import TerrainMesh


def write_3dm(
    solids: list[BuildingSolid],
    terrain: TerrainMesh | None,
    path: str | Path,
    offset: tuple[float, float],
    cadastral: list[CadastralParcel] | None = None,
) -> str:
    """BuildingSolid(+TerrainMesh+CadastralParcel) → .3dm 파일.

    solids: BuildingSolid 목록 (로컬 미터, base_z_m/height_m 포함).
      flagged=True 인 건물은 buildings_unverified 레이어에 배치.
    terrain: TerrainMesh | None. vertices는 인치(SketchUp 단위) → /M2I로 미터 환산.
    path: 저장 경로 (.3dm 확장자 권장)
    offset: origin_offset (ox, oy) EPSG:5186 원점 오프셋. 로컬→절대좌표: abs = local + offset.
    cadastral: CadastralParcel 목록 (Phase 5). None/빈 목록 이면 지적 레이어 생략.
    반환: 저장된 파일의 절대 경로 문자열.
    """
    model = rhino3dm.File3dm()

    # --- 레이어 설정 ---
    l_bldg = rhino3dm.Layer()
    l_bldg.Name = "buildings"
    l_bldg.Color = (70, 130, 180, 255)        # steel blue
    idx_bldg = model.Layers.Add(l_bldg)

    l_flag = rhino3dm.Layer()
    l_flag.Name = "buildings_unverified"
    l_flag.Color = (210, 120, 60, 255)         # orange — floors 미확인
    idx_flag = model.Layers.Add(l_flag)

    l_terr = rhino3dm.Layer()
    l_terr.Name = "terrain"
    l_terr.Color = (100, 150, 80, 255)         # olive green
    idx_terr = model.Layers.Add(l_terr)

    l_cada = rhino3dm.Layer()
    l_cada.Name = "cadastral"
    l_cada.Color = (220, 200, 100, 255)        # sandy yellow
    idx_cada = model.Layers.Add(l_cada)

    # origin_offset → 문서 수준 Strings (좌표 복원용, 사양서 §6.1)
    ox, oy = offset
    model.Strings["origin_offset_x"] = str(ox)
    model.Strings["origin_offset_y"] = str(oy)

    # 건물 Extrusion (flagged → buildings_unverified 레이어)
    for solid in solids:
        layer = idx_flag if solid.flagged else idx_bldg
        _add_building(model, solid, layer, ox, oy)

    # 지형 Mesh
    if terrain is not None:
        _add_terrain(model, terrain, idx_terr)

    # 지적 경계 PolylineCurve
    if cadastral:
        for parcel in cadastral:
            _add_cadastral(model, parcel, idx_cada)

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    model.Write(str(p), 7)
    return str(p.resolve())


def _add_building(
    model: rhino3dm.File3dm,
    solid: BuildingSolid,
    layer_idx: int,
    ox: float,
    oy: float,
) -> None:
    """BuildingSolid → Extrusion (외벽) + 중정 내벽 Extrusion (홀 있을 때)."""
    fp = solid.footprint_m
    base_z = solid.base_z_m
    height = solid.height_m

    if len(fp) < 3 or height <= 0:
        return

    def _make_attrs(name: str) -> rhino3dm.ObjectAttributes:
        attrs = rhino3dm.ObjectAttributes()
        attrs.LayerIndex = layer_idx
        attrs.Name = name
        attrs.SetUserString("origin_offset_x", str(ox))
        attrs.SetUserString("origin_offset_y", str(oy))
        attrs.SetUserString("flagged", "1" if solid.flagged else "0")
        if solid.floors is not None:
            attrs.SetUserString("floors", str(solid.floors))
        if solid.attrs:
            for k, v in solid.attrs.items():
                if v is not None:
                    attrs.SetUserString(k, str(v))
        return attrs

    # 외벽 Extrusion (캡 포함)
    pts = [rhino3dm.Point3d(x, y, base_z) for x, y in fp]
    pts.append(pts[0])
    profile = rhino3dm.PolylineCurve(pts)
    ext = rhino3dm.Extrusion.Create(profile, height, True)
    if ext is not None and ext.IsValid:
        model.Objects.AddExtrusion(ext, _make_attrs(solid.name))

    # 중정 내벽 Extrusion (홀 링마다 별도 객체)
    for i, hole in enumerate(solid.holes_m or []):
        if len(hole) < 3:
            continue
        hpts = [rhino3dm.Point3d(x, y, base_z) for x, y in hole]
        hpts.append(hpts[0])
        hprofile = rhino3dm.PolylineCurve(hpts)
        hext = rhino3dm.Extrusion.Create(hprofile, height, False)  # 캡 없음 = 내벽만
        if hext is not None and hext.IsValid:
            model.Objects.AddExtrusion(hext, _make_attrs(f"{solid.name}_hole{i}"))


def _add_terrain(
    model: rhino3dm.File3dm,
    terrain: TerrainMesh,
    layer_idx: int,
) -> None:
    """TerrainMesh → rhino3dm Mesh.

    TerrainMesh.vertices는 SketchUp 인치 단위이므로 /M2I 로 미터로 환산.
    """
    if not terrain.vertices or not terrain.triangles:
        return

    mesh = rhino3dm.Mesh()
    for xi, yi, zi in terrain.vertices:
        mesh.Vertices.Add(xi / M2I, yi / M2I, zi / M2I)
    for a, b, c in terrain.triangles:
        mesh.Faces.AddFace(a, b, c)
    mesh.Normals.ComputeNormals()
    mesh.Compact()

    attrs = rhino3dm.ObjectAttributes()
    attrs.LayerIndex = layer_idx
    attrs.Name = "terrain"

    model.Objects.AddMesh(mesh, attrs)


def _add_cadastral(
    model: rhino3dm.File3dm,
    parcel: CadastralParcel,
    layer_idx: int,
) -> None:
    """CadastralParcel → rhino3dm PolylineCurve at Z=0 (대지 경계선)."""
    fp = parcel.footprint_m
    if len(fp) < 3:
        return

    pts = [rhino3dm.Point3d(x, y, 0.0) for x, y in fp]
    pts.append(pts[0])   # 닫기
    curve = rhino3dm.PolylineCurve(pts)

    attrs = rhino3dm.ObjectAttributes()
    attrs.LayerIndex = layer_idx
    attrs.Name = parcel.pnu

    model.Objects.AddCurve(curve, attrs)
