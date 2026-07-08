"""Phase 4: write_3dm 단위 테스트.

합성 BuildingSolid + TerrainMesh로 .3dm 쓰기/읽기 왕복 검증.
"""

import tempfile
from pathlib import Path

import numpy as np
import pytest
import rhino3dm
from affine import Affine

from src.config import M2I
from src.geometry.building import BuildingSolid
from src.geometry.terrain_mesh import TerrainMesh, grid_to_tin
from src.output.rhino import write_3dm
from src.terrain.dem import DEMPatch


# ---------------------------------------------------------------------------
# 합성 데이터 픽스처
# ---------------------------------------------------------------------------

def _make_solid(
    name: str = "bldg_A",
    footprint_m: list | None = None,
    base_z_m: float = 0.0,
    height_m: float = 9.0,
    floors: int | None = 3,
) -> BuildingSolid:
    fp = footprint_m or [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
    return BuildingSolid(
        name=name,
        footprint_m=fp,
        base_z_m=base_z_m,
        height_m=height_m,
        floors=floors,
        attrs={"bd_mgt_sn": "1234567890123456789"},
    )


def _make_terrain(nrows: int = 3, ncols: int = 3, cell: float = 10.0) -> TerrainMesh:
    """합성 DEMPatch → TerrainMesh (인치 단위)."""
    minx, miny = 200_000.0, 400_000.0
    maxy = miny + nrows * cell
    transform = Affine(cell, 0, minx, 0, -cell, maxy)
    grid = np.array(
        [[float(r * 2 + c) for c in range(ncols)] for r in range(nrows)],
        dtype=np.float32,
    )
    dem = DEMPatch(grid=grid, transform=transform, offset=(minx, miny))
    return grid_to_tin(dem)


# ---------------------------------------------------------------------------
# 기본 쓰기/읽기 왕복
# ---------------------------------------------------------------------------

def test_write_3dm_creates_file():
    """write_3dm 호출 후 파일이 생성되어야 한다."""
    solid = _make_solid()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        result = write_3dm([solid], None, path, offset=(220_000.0, 410_000.0))
        assert Path(result).exists()
        assert Path(result).stat().st_size > 0


def test_write_3dm_returns_absolute_path():
    """반환 경로는 절대 경로여야 한다."""
    solid = _make_solid()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        result = write_3dm([solid], None, path, offset=(220_000.0, 410_000.0))
        assert Path(result).is_absolute()


def test_write_3dm_object_count_buildings_only():
    """건물 2개, 지형 없음 → Objects 2개."""
    solids = [_make_solid("A"), _make_solid("B", footprint_m=[(20, 0), (30, 0), (30, 8), (20, 8)])]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm(solids, None, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        assert len(m.Objects) == 2


def test_write_3dm_object_count_with_terrain():
    """건물 1개 + 지형 → Objects 2개 (Extrusion + Mesh)."""
    solid = _make_solid()
    terrain = _make_terrain()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], terrain, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        assert len(m.Objects) == 2


def test_write_3dm_geometry_types():
    """건물은 Extrusion, 지형은 Mesh."""
    solid = _make_solid()
    terrain = _make_terrain()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], terrain, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        types = {type(o.Geometry).__name__ for o in m.Objects}
        assert "Extrusion" in types
        assert "Mesh" in types


def _make_road_mesh():
    from src.geometry.road import RoadMesh

    return RoadMesh(
        vertices=[(0.0, 0.0, 1.0), (12.0, 0.0, 1.0), (12.0, 8.0, 1.0), (0.0, 8.0, 1.0)],
        triangles=[(0, 1, 2), (0, 2, 3)],
        outlines=[[(0.0, 0.0, 1.0), (12.0, 0.0, 1.0), (12.0, 8.0, 1.0), (0.0, 8.0, 1.0)]],
    )


def test_write_3dm_with_roads_adds_mesh_on_roads_layer():
    """roads 지정 → 'roads' 레이어에 Mesh 객체 추가."""
    solid = _make_solid()
    road = _make_road_mesh()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], None, path, offset=(0.0, 0.0), roads=road)
        m = rhino3dm.File3dm.Read(str(path))
        # 건물 Extrusion + 도로 Mesh = 2
        assert len(m.Objects) == 2
        assert any(layer.Name == "roads" for layer in m.Layers)
        # Mesh 객체가 roads 레이어에 있어야 한다.
        meshes = [o for o in m.Objects if type(o.Geometry).__name__ == "Mesh"]
        assert meshes
        assert any(m.Layers[o.Attributes.LayerIndex].Name == "roads" for o in meshes)


def test_write_3dm_with_sidewalks_layer():
    """sidewalks 지정 → 'sidewalks' 레이어에 Mesh."""
    sw = _make_road_mesh()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([_make_solid()], None, path, offset=(0.0, 0.0), sidewalks=sw)
        m = rhino3dm.File3dm.Read(str(path))
        assert any(layer.Name == "sidewalks" for layer in m.Layers)
        meshes = [o for o in m.Objects if type(o.Geometry).__name__ == "Mesh"]
        assert any(m.Layers[o.Attributes.LayerIndex].Name == "sidewalks" for o in meshes)


# ---------------------------------------------------------------------------
# 정사영상 텍스처 (Tier 1)
# ---------------------------------------------------------------------------

def _write_stub_png(path: Path) -> None:
    """최소 유효 RGB PNG(2x2) 저장 — 텍스처 참조 검증용."""
    import struct
    import zlib

    raw = bytearray()
    for _ in range(2):
        raw.append(0)
        raw += bytes((200, 200, 200)) * 2

    def _chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def _find_terrain_mesh(model: rhino3dm.File3dm):
    for o in model.Objects:
        if type(o.Geometry).__name__ == "Mesh":
            return o
    return None


def test_write_3dm_ortho_sets_texcoords_and_material():
    """정사영상 지정 시 지형 메시에 텍스처 좌표 + 비트맵 머티리얼이 붙는다."""
    terrain = _make_terrain()
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "ortho.png"
        _write_stub_png(png)
        path = Path(td) / "site.3dm"
        write_3dm(
            [], terrain, path, offset=(0.0, 0.0),
            ortho_image=png, ortho_extent_m=(0.0, 0.0, 20.0, 20.0),
        )
        m = rhino3dm.File3dm.Read(str(path))
        obj = _find_terrain_mesh(m)
        assert obj is not None
        mesh = obj.Geometry
        # 텍스처 좌표가 정점 수만큼 생성됨
        assert len(mesh.TextureCoordinates) == len(mesh.Vertices)
        # orthophoto 비트맵 머티리얼 존재
        assert len(m.Materials) >= 1
        bt = m.Materials[0].GetBitmapTexture()
        assert bt is not None
        assert bt.FileName.endswith("ortho.png")


def test_building_extrudes_upward_regardless_of_winding():
    """CW/CCW 어느 footprint든 건물은 위로 돌출(바닥=base_z, 꼭대기=base_z+height).

    회귀 방지: VWorld footprint(CW)를 그대로 쓰면 Extrusion이 아래로 돌출돼
    건물이 지형 아래로 매달렸던 버그(2026-07-01).
    """
    cw = [(0.0, 0.0), (0.0, 5.0), (10.0, 5.0), (10.0, 0.0)]   # 시계방향
    ccw = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]  # 반시계
    for fp in (cw, ccw):
        solid = _make_solid(footprint_m=fp, base_z_m=50.0, height_m=12.0)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "b.3dm"
            write_3dm([solid], None, path, offset=(0.0, 0.0))
            m = rhino3dm.File3dm.Read(str(path))
            ext = [o for o in m.Objects if type(o.Geometry).__name__ == "Extrusion"][0]
            bb = ext.Geometry.GetBoundingBox()
            assert bb.Min.Z == pytest.approx(50.0, abs=0.1)   # 바닥 = base_z (지형)
            assert bb.Max.Z == pytest.approx(62.0, abs=0.1)   # 꼭대기 = base_z + height (위)


def test_write_3dm_no_ortho_leaves_terrain_untextured():
    """정사영상 미지정 시 텍스처 좌표/머티리얼이 생기지 않는다(회귀 방지)."""
    terrain = _make_terrain()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([], terrain, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        obj = _find_terrain_mesh(m)
        assert obj is not None
        assert len(obj.Geometry.TextureCoordinates) == 0


# ---------------------------------------------------------------------------
# 레이어
# ---------------------------------------------------------------------------

def test_write_3dm_layers():
    """buildings / terrain 레이어 생성 확인."""
    solid = _make_solid()
    terrain = _make_terrain()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], terrain, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        names = {m.Layers[i].Name for i in range(len(m.Layers))}
        assert "buildings" in names
        assert "terrain" in names


def test_write_3dm_building_on_correct_layer():
    """Extrusion(건물)은 buildings 레이어에 배정되어야 한다."""
    solid = _make_solid()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], None, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        layer_names = {i: m.Layers[i].Name for i in range(len(m.Layers))}
        for obj in m.Objects:
            if isinstance(obj.Geometry, rhino3dm.Extrusion):
                assert layer_names[obj.Attributes.LayerIndex] == "buildings"


def test_write_3dm_terrain_on_correct_layer():
    """Mesh(지형)은 terrain 레이어에 배정되어야 한다."""
    terrain = _make_terrain()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([], terrain, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        layer_names = {i: m.Layers[i].Name for i in range(len(m.Layers))}
        for obj in m.Objects:
            if isinstance(obj.Geometry, rhino3dm.Mesh):
                assert layer_names[obj.Attributes.LayerIndex] == "terrain"


# ---------------------------------------------------------------------------
# origin_offset 보존 (사양서 §6.1)
# ---------------------------------------------------------------------------

def test_write_3dm_offset_in_document_strings():
    """origin_offset이 문서 Strings에 기록되어야 한다."""
    solid = _make_solid()
    offset = (220_000.5, 410_000.7)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], None, path, offset=offset)
        m = rhino3dm.File3dm.Read(str(path))
        assert float(m.Strings["origin_offset_x"]) == pytest.approx(offset[0])
        assert float(m.Strings["origin_offset_y"]) == pytest.approx(offset[1])


def test_write_3dm_offset_in_object_user_string():
    """각 Extrusion 객체의 UserString에도 origin_offset이 기록되어야 한다."""
    solid = _make_solid()
    offset = (123_456.0, 456_789.0)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], None, path, offset=offset)
        m = rhino3dm.File3dm.Read(str(path))
        for obj in m.Objects:
            if isinstance(obj.Geometry, rhino3dm.Extrusion):
                assert float(obj.Attributes.GetUserString("origin_offset_x")) == pytest.approx(offset[0])
                assert float(obj.Attributes.GetUserString("origin_offset_y")) == pytest.approx(offset[1])


# ---------------------------------------------------------------------------
# 좌표/높이 일관성 (skp와 비교)
# ---------------------------------------------------------------------------

def test_write_3dm_building_name_preserved():
    """객체 이름이 solid.name과 일치해야 한다."""
    solid = _make_solid(name="테스트건물")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], None, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        names = [obj.Attributes.Name for obj in m.Objects]
        assert "테스트건물" in names


def test_write_3dm_floors_in_user_string():
    """floors 정보가 UserString에 기록되어야 한다."""
    solid = _make_solid(floors=5)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], None, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        for obj in m.Objects:
            if isinstance(obj.Geometry, rhino3dm.Extrusion):
                assert obj.Attributes.GetUserString("floors") == "5"


def test_write_3dm_terrain_vertex_count():
    """지형 Mesh 정점 수가 TerrainMesh 정점 수와 동일해야 한다."""
    terrain = _make_terrain(3, 3)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([], terrain, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        for obj in m.Objects:
            if isinstance(obj.Geometry, rhino3dm.Mesh):
                assert len(obj.Geometry.Vertices) == len(terrain.vertices)


def test_write_3dm_terrain_face_count():
    """지형 Mesh 면 수가 TerrainMesh 삼각형 수와 동일해야 한다."""
    terrain = _make_terrain(4, 4)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([], terrain, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        for obj in m.Objects:
            if isinstance(obj.Geometry, rhino3dm.Mesh):
                assert obj.Geometry.Faces.Count == len(terrain.triangles)


def test_write_3dm_terrain_z_in_meters():
    """지형 Mesh Z 좌표는 미터여야 한다 (인치 /M2I 역변환 확인)."""
    terrain = _make_terrain(2, 2, cell=10.0)
    # grid[0,0]=0m, grid[1,1]=3m → 인치로 변환 후 저장, Rhino에서 읽으면 다시 미터여야 함
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([], terrain, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        for obj in m.Objects:
            if isinstance(obj.Geometry, rhino3dm.Mesh):
                verts = obj.Geometry.Vertices
                zs = [verts[i].Z for i in range(len(verts))]
                # 최대 표고: grid[1,1] = 1*2+1 = 3m
                assert max(zs) == pytest.approx(3.0, abs=0.01)
                assert min(zs) == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# 엣지 케이스
# ---------------------------------------------------------------------------

def test_write_3dm_empty_solids():
    """빈 solids 목록은 오류 없이 처리된다."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "empty.3dm"
        write_3dm([], None, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        assert len(m.Objects) == 0


def test_write_3dm_solid_with_none_floors():
    """floors=None(층수 미확인) 건물도 오류 없이 출력되어야 한다."""
    solid = _make_solid(floors=None)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], None, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        assert len(m.Objects) == 1


def test_write_3dm_creates_output_dir():
    """output_dir가 존재하지 않아도 자동 생성되어야 한다."""
    solid = _make_solid()
    with tempfile.TemporaryDirectory() as td:
        nested = Path(td) / "deep" / "nested"
        path = nested / "site.3dm"
        write_3dm([solid], None, path, offset=(0.0, 0.0))
        assert path.exists()


def test_write_3dm_base_z_applied():
    """건물 base_z_m이 Extrusion 기저 평면 Z에 반영되어야 한다.

    Extrusion.PathStart.Z == base_z_m (로컬 미터).
    """
    solid = _make_solid(base_z_m=5.0, height_m=9.0)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "site.3dm"
        write_3dm([solid], None, path, offset=(0.0, 0.0))
        m = rhino3dm.File3dm.Read(str(path))
        for obj in m.Objects:
            if isinstance(obj.Geometry, rhino3dm.Extrusion):
                # PathStart is the bottom plane origin point
                z_start = obj.Geometry.PathStart.Z
                assert z_start == pytest.approx(5.0, abs=0.01)
