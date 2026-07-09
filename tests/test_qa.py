"""생성물 자동 QA (qa.py) — 건물 앉힘·겹침·지형 스파이크 검사, 합성 데이터로."""

import numpy as np
from rasterio.transform import from_bounds

from src.geometry.building import BuildingSolid
from src.geometry.terrain_mesh import TerrainMesh
from src.qa import run_qa
from src.terrain.dem import DEMPatch

M2I = 39.3701


def _bldg(name, fp, base_z=0.0, height=10.0):
    return BuildingSolid(name=name, footprint_m=fp, base_z_m=base_z, height_m=height,
                         floors=3, attrs={})


def _slope_dem(slope=0.2, span=200.0, n=40, offset=(0.0, 0.0)):
    """z = slope·x 인 경사 DEM (footprint 아래 표고차 검사용)."""
    minx, miny = offset
    tf = from_bounds(minx, miny, minx + span, miny + span, n, n)
    xs = np.linspace(0, span, n)
    grid = np.tile((xs * slope).astype(np.float32), (n, 1))  # col(x)마다 z=slope·x
    return DEMPatch(grid=grid, transform=tf, offset=offset)


def _kinds(qa):
    return {f["kind"] for f in qa["findings"]}


def test_steep_site_flagged():
    """footprint가 급경사(표고차 큰) 위 → steep_site 경고."""
    dem = _slope_dem(slope=0.2)  # x 10..60 → z 2..12, 표고차 10m > 3
    b = _bldg("A", [(10, 100), (60, 100), (60, 140), (10, 140)], base_z=2.0)
    qa = run_qa([b], dem=dem)
    assert "steep_site" in _kinds(qa)


def test_building_no_terrain_flagged():
    """footprint가 DEM nan 영역(클립 구멍)에 걸침 → building_no_terrain (info)."""
    n = 40
    tf = from_bounds(0, 0, 200, 200, n, n)
    grid = np.full((n, n), 50.0, dtype=np.float32)
    grid[:, n // 2:] = np.nan          # 오른쪽 절반(x>100) nan
    dem = DEMPatch(grid=grid, transform=tf, offset=(0.0, 0.0))
    b = _bldg("B", [(120, 100), (180, 100), (180, 140), (120, 140)])  # nan 영역
    qa = run_qa([b], dem=dem)
    assert "building_no_terrain" in _kinds(qa)


def test_building_overlap_flagged():
    """두 건물 footprint 큰 겹침 → building_overlap (중복 의심)."""
    fp = [(0, 0), (40, 0), (40, 40), (0, 40)]
    fp2 = [(5, 5), (45, 5), (45, 45), (5, 45)]   # 대부분 겹침
    qa = run_qa([_bldg("A", fp), _bldg("B", fp2)])
    assert "building_overlap" in _kinds(qa)


def test_no_overlap_clean():
    """멀리 떨어진 건물은 겹침 경고 없음."""
    qa = run_qa([
        _bldg("A", [(0, 0), (10, 0), (10, 10), (0, 10)]),
        _bldg("B", [(100, 100), (110, 100), (110, 110), (100, 110)]),
    ])
    assert "building_overlap" not in _kinds(qa)


def test_footprint_invalid_and_tiny_flagged():
    """자기교차 footprint → footprint_invalid, 초소형 → footprint_tiny."""
    bowtie = _bldg("X", [(0, 0), (10, 10), (10, 0), (0, 10)])   # 자기교차
    tiny = _bldg("Y", [(0, 0), (1, 0), (1, 1), (0, 1)])         # 1m² < 2
    qa = run_qa([bowtie, tiny])
    kinds = _kinds(qa)
    assert "footprint_invalid" in kinds
    assert "footprint_tiny" in kinds


def test_terrain_spike_flagged():
    """이웃보다 크게 튀는 지형 정점 → terrain_spike."""
    zf, zs = 50 * M2I, 60 * M2I  # 중앙 정점 +10m 스파이크
    verts = [(0, 0, zf), (100 * M2I, 0, zf), (100 * M2I, 100 * M2I, zf),
             (0, 100 * M2I, zf), (50 * M2I, 50 * M2I, zs)]
    tris = [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    qa = run_qa([], terrain_mesh=TerrainMesh(vertices=verts, triangles=tris))
    assert "terrain_spike" in _kinds(qa)


def test_summary_counts():
    """summary에 총계·경고수·종류별 개수."""
    fp = [(0, 0), (40, 0), (40, 40), (0, 40)]
    qa = run_qa([_bldg("A", fp), _bldg("B", [(5, 5), (45, 5), (45, 45), (5, 45)])])
    assert qa["summary"]["total"] >= 1
    assert qa["summary"]["by_kind"].get("building_overlap", 0) >= 1


def test_findings_have_reviewer_label():
    """각 finding에 실무 라벨(label)이 붙는다 (A-3)."""
    fp = [(0, 0), (40, 0), (40, 40), (0, 40)]
    qa = run_qa([_bldg("A", fp), _bldg("B", [(5, 5), (45, 5), (45, 45), (5, 45)])])
    overlap = next(f for f in qa["findings"] if f["kind"] == "building_overlap")
    assert overlap["label"] == "건물 겹침"


def test_summary_passed_and_stamp_clean():
    """결함 0건이면 passed=True + '검수 통과' 스탬프."""
    qa = run_qa([
        _bldg("A", [(0, 0), (10, 0), (10, 10), (0, 10)]),
        _bldg("B", [(100, 100), (110, 100), (110, 110), (100, 110)]),
    ])
    assert qa["summary"]["passed"] is True
    assert "검수 통과" in qa["summary"]["stamp"]


def test_summary_not_passed_with_warning():
    """경고가 있으면 passed=False + '검토 필요' 스탬프."""
    fp = [(0, 0), (40, 0), (40, 40), (0, 40)]
    qa = run_qa([_bldg("A", fp), _bldg("B", [(5, 5), (45, 5), (45, 45), (5, 45)])])
    assert qa["summary"]["passed"] is False
    assert "검토" in qa["summary"]["stamp"]
