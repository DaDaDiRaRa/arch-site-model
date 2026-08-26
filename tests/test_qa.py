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


def _grid_mesh(n=4, step_m=50.0, z_of=lambda i, j: 50.0):
    """n×n 격자 TIN (정점=인치 계약). z_of(i,j)는 미터."""
    verts = [
        (i * step_m * M2I, j * step_m * M2I, z_of(i, j) * M2I)
        for j in range(n) for i in range(n)
    ]
    tris = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            tris.append((a, a + 1, a + n + 1))
            tris.append((a, a + n + 1, a + n))
    return TerrainMesh(vertices=verts, triangles=tris)


def test_terrain_skirt_not_flagged_as_spike():
    """둘레 스커트(벽)를 지형 이웃으로 세지 않는다.

    벽 바닥은 지형면 정점과 같은 (x,y)에 수십 m 아래로 붙는다. 이를 이웃으로 세면
    기준면이 아래로 끌려가 **평탄한 지형의 둘레 전체**가 가짜 스파이크가 됐다
    (실측 반포동 250m: 가짜 40건, 원본 DEM은 그 자리에서 편차 0.00m).
    """
    from src.geometry.terrain_mesh import add_skirt

    # 벽 낙차는 (정점표고 − 지형최저) + depth 라 실지형에선 수십 m가 흔하다.
    # 낙차가 SPIKE_M을 넘을 만큼은 돼야 이 버그가 재현된다(얕은 벽은 그냥 안 걸림).
    mesh = add_skirt(_grid_mesh(), depth_m=40.0)          # 완전 평탄 + 깊은 벽
    assert len(mesh.vertices) > 16                        # 벽이 실제로 붙었는지
    assert "terrain_spike" not in _kinds(run_qa([], terrain_mesh=mesh))


def test_terrain_uniform_slope_not_flagged_as_spike():
    """균일 경사면은 스파이크가 아니다 — 이웃 '평균'이 아니라 '평면' 대비로 잰다.

    적응형 TIN은 평탄부를 큰 삼각형으로 덮어 변이 수백 m까지 길어진다(실측 최대 310m).
    이웃 평균 기준이면 가장자리 정점이 경사만으로도 수십 m 편차가 나 가짜 경보가 된다.
    """
    mesh = _grid_mesh(step_m=200.0, z_of=lambda i, j: i * 200.0 * 0.2)  # 20% 경사
    assert "terrain_spike" not in _kinds(run_qa([], terrain_mesh=mesh))


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
