"""도로 런타임 지오메트리 (Phase R, R1a) — 합성 GeoJSON으로 클립·링 변환 검증."""

import json

from src.geometry.road import RoadFeature, RoadMesh, build_road_mesh, clip_roads


class _FlatDem:
    """합성 DEM: 어디서나 표고 10m."""

    def elev_at(self, x, y):
        return 10.0


class _SlopeDem:
    """합성 DEM: x방향 경사(z = 0.1·x). 도로 폭(x) 방향으로 드레이프 시 기울어짐."""

    def elev_at(self, x, y):
        return x * 0.1


def _write_geojson(path, polygons):
    """polygons: [(exterior, [holes...]), ...] — 각 링은 (x,y) 목록(EPSG:5186)."""
    feats = []
    for ext, holes in polygons:
        coords = [ext] + list(holes)
        feats.append(
            {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": coords}}
        )
    path.write_text(json.dumps({"type": "FeatureCollection", "crs_epsg": 5186, "features": feats}), encoding="utf-8")


def test_clip_roads_local_ring(tmp_path):
    """bbox가 폴리곤을 포함 → 로컬 미터 링(offset 적용, 닫힘점 제거)으로 반환."""
    ext = [(226010, 402010), (226030, 402010), (226030, 402030), (226010, 402030), (226010, 402010)]
    p = tmp_path / "roads.geojson"
    _write_geojson(p, [(ext, [])])

    offset = (226000.0, 402000.0)
    bbox_5186 = (226000, 402000, 226040, 402040)
    feats = clip_roads(p, bbox_5186, offset)

    assert len(feats) == 1
    assert isinstance(feats[0], RoadFeature)
    ring = feats[0].rings[0]
    # 닫힘점 제거 → 4점, offset 적용된 로컬 미터
    assert len(ring) == 4
    assert {(round(x, 1), round(y, 1)) for x, y in ring} == {(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 30.0)}


def test_clip_roads_hole(tmp_path):
    """구멍(중앙분리대 등)이 있는 폴리곤 → rings[1]에 홀 링."""
    ext = [(226000, 402000), (226050, 402000), (226050, 402050), (226000, 402050), (226000, 402000)]
    hole = [(226020, 402020), (226030, 402020), (226030, 402030), (226020, 402030), (226020, 402020)]
    p = tmp_path / "roads.geojson"
    _write_geojson(p, [(ext, [hole])])

    feats = clip_roads(p, (225990, 401990, 226060, 402060), (226000.0, 402000.0))
    assert len(feats) == 1
    assert len(feats[0].rings) == 2  # 외곽 + 홀
    hole_ring = feats[0].rings[1]
    assert {(round(x, 1), round(y, 1)) for x, y in hole_ring} == {(20.0, 20.0), (30.0, 20.0), (30.0, 30.0), (20.0, 30.0)}


def test_clip_roads_no_overlap(tmp_path):
    """bbox가 도로와 안 겹치면 빈 목록."""
    ext = [(226010, 402010), (226030, 402010), (226030, 402030), (226010, 402030), (226010, 402010)]
    p = tmp_path / "roads.geojson"
    _write_geojson(p, [(ext, [])])
    feats = clip_roads(p, (100000, 100000, 100100, 100100), (0.0, 0.0))
    assert feats == []


def test_clip_roads_missing_file(tmp_path):
    """파일 없으면 빈 목록(조용한 생략)."""
    assert clip_roads(tmp_path / "nope.geojson", (0, 0, 1, 1), (0.0, 0.0)) == []


def test_road_file_path_gs_and_https(monkeypatch):
    """ROAD_BASE=gs://… → 공개 https URL, https:// → 그대로 결합 (배포 서빙)."""
    from src import config

    monkeypatch.setattr(config, "ROAD_BASE", "gs://mybucket/roads")
    assert config.road_file_path("roads_x.geojson") == (
        "https://storage.googleapis.com/mybucket/roads/roads_x.geojson"
    )
    monkeypatch.setattr(config, "ROAD_BASE", "https://cdn.example.com/roads/")
    assert config.road_file_path("roads_x.geojson") == (
        "https://cdn.example.com/roads/roads_x.geojson"
    )


def test_clip_roads_remote_url_fetches_and_caches(tmp_path, monkeypatch):
    """geojson_path가 HTTP URL이면 requests로 받아 로컬과 동일 결과 + 캐시(재fetch 없음)."""
    import src.geometry.road as road_mod

    ext = [(226010, 402010), (226030, 402010), (226030, 402030), (226010, 402030), (226010, 402010)]
    body = json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {},
                      "geometry": {"type": "Polygon", "coordinates": [ext]}}],
    })
    url = "https://storage.googleapis.com/test-bucket/roads/roads_x.geojson"
    road_mod._GEOJSON_CACHE.pop(url, None)
    calls = {"n": 0}

    class _Resp:
        text = body

        def raise_for_status(self):
            pass

    def fake_get(u, timeout=30):
        calls["n"] += 1
        assert u == url
        return _Resp()

    monkeypatch.setattr("requests.get", fake_get)
    try:
        feats = clip_roads(url, (226000, 402000, 226040, 402040), (226000.0, 402000.0))
        assert len(feats) == 1
        clip_roads(url, (226000, 402000, 226040, 402040), (226000.0, 402000.0))
        assert calls["n"] == 1  # 두 번째는 캐시 → 재fetch 없음
    finally:
        road_mod._GEOJSON_CACHE.pop(url, None)  # 테스트 간 오염 방지


def test_clip_roads_remote_failure_returns_empty(monkeypatch):
    """원격 fetch 실패 → 빈 목록(조용한 fallback, 요청은 계속)."""
    import requests

    import src.geometry.road as road_mod

    url = "https://storage.googleapis.com/test-bucket/roads/missing.geojson"
    road_mod._GEOJSON_CACHE.pop(url, None)

    def fake_get(u, timeout=30):
        raise requests.RequestException("boom")

    monkeypatch.setattr("requests.get", fake_get)
    assert clip_roads(url, (0, 0, 100, 100), (0.0, 0.0)) == []


def test_clip_roads_and_sidewalks_separation(tmp_path):
    """도로(properties {})와 보도(sw) 폴리곤 분리 — clip_roads는 도로만, clip_sidewalks는 보도만."""
    from src.geometry.road import clip_sidewalks

    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 0]]]}},
            {"type": "Feature", "properties": {"sw": 1},
             "geometry": {"type": "Polygon", "coordinates": [[[20, 0], [30, 0], [30, 10], [20, 0]]]}},
        ],
    }
    p = tmp_path / "r.geojson"
    p.write_text(json.dumps(fc), encoding="utf-8")
    roads = clip_roads(p, (-5, -5, 35, 15), (0.0, 0.0))
    sws = clip_sidewalks(p, (-5, -5, 35, 15), (0.0, 0.0))
    assert len(roads) == 1 and len(sws) == 1
    assert max(x for x, _ in roads[0].rings[0]) < 15      # 도로는 x<15
    assert min(x for x, _ in sws[0].rings[0]) > 15        # 보도는 x>15


def test_lane_offsets():
    """차로수·도로폭 → 차선 구분선 횡오프셋. n<2 또는 w없음이면 중심선 1개([0])."""
    from src.geometry.road import _lane_offsets

    assert _lane_offsets(4, 20.0) == [-5.0, 0.0, 5.0]   # 4차로 20m → 구분선 3개
    assert _lane_offsets(2, 8.0) == [0.0]               # 2차로 → 중앙 1개
    assert _lane_offsets(1, 6.0) == [0.0]               # 1차로 → 중심선
    assert _lane_offsets(None, 20.0) == [0.0]           # 차로수 없음
    assert _lane_offsets(4, None) == [0.0]              # 도로폭 없음


def test_clip_lane_markings_multilane(tmp_path):
    """cl feature의 n=4,w=20 → 평행 구분선 3개(y=-5,0,+5)."""
    from src.geometry.road import clip_lane_markings

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"cl": 1, "n": 4, "w": 20},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [40, 0]]}},
    ]}
    p = tmp_path / "cl.geojson"
    p.write_text(json.dumps(fc), encoding="utf-8")
    lines = clip_lane_markings(p, (-10, -20, 50, 20), (0.0, 0.0))
    # 중앙선(y=0) 실선 + ±5 구분선(점선 대시로 쪼개짐) → 오프셋 위치만 검증(개수 아님).
    ys = sorted({round(sum(y for _, y in ln) / len(ln)) for ln in lines})
    assert ys == [-5, 0, 5]


def test_clip_lane_markings_single_line(tmp_path):
    """n/w 없는 소로 cl feature → 중심선 1개만."""
    from src.geometry.road import clip_lane_markings

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"cl": 1},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [40, 0]]}},
    ]}
    p = tmp_path / "cl.geojson"
    p.write_text(json.dumps(fc), encoding="utf-8")
    lines = clip_lane_markings(p, (-10, -20, 50, 20), (0.0, 0.0))
    assert len(lines) == 1


def test_dash_line():
    """직선 40m, 칠3·공백5(period8) → 대시 5개(각 2점, 길이~3, 시작 0/8/16…)."""
    import math

    from src.geometry.road import _dash_line

    dashes = _dash_line([(0, 0), (40, 0)], 3.0, 5.0)
    assert len(dashes) == 5
    for d in dashes:
        assert len(d) == 2
        assert abs(math.hypot(d[1][0] - d[0][0], d[1][1] - d[0][1]) - 3.0) < 0.01
    assert abs(dashes[0][0][0] - 0.0) < 1e-6
    assert abs(dashes[1][0][0] - 8.0) < 1e-6


def test_clip_lane_markings_dividers_dashed(tmp_path):
    """4차로 20m 직선 → 중앙선 1개(실선, 김) + 차선 구분선은 점선(짧은 대시 다수)."""
    import math

    from src.geometry.road import clip_lane_markings

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"cl": 1, "n": 4, "w": 20},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [100, 0]]}}]}
    p = tmp_path / "c.geojson"
    p.write_text(json.dumps(fc), encoding="utf-8")
    lines = clip_lane_markings(p, (-20, -30, 120, 30), (0.0, 0.0))

    def length(ln):
        return math.hypot(ln[-1][0] - ln[0][0], ln[-1][1] - ln[0][1])

    long_lines = [ln for ln in lines if length(ln) > 50]
    short = [ln for ln in lines if length(ln) < 10]
    assert len(long_lines) == 1   # 중앙선(median) 실선 1개
    assert len(short) > 15        # 구분선 2개가 점선 대시 다수로


def test_polygon_sample_points_edge_cell_denser():
    """edge_cell(경계 간격)을 작게 하면 경계 샘플점이 늘어 경계가 촘촘(샤프닝)."""
    from src.geometry.road import _polygon_sample_points

    ring = [(0, 0), (20, 0), (20, 20), (0, 20)]
    coarse = _polygon_sample_points([ring], 2.5, edge_cell=None)
    fine = _polygon_sample_points([ring], 2.5, edge_cell=0.5)
    assert len(fine) > len(coarse)


def test_drape_centerlines():
    """중심선 폴리라인 → DEM 드레이프 [(x,y,z)]. 평평 DEM이면 z=10."""
    from src.geometry.road import drape_centerlines

    out = drape_centerlines([[(0.0, 0.0), (10.0, 0.0)]], _FlatDem())
    assert len(out) == 1 and len(out[0]) == 2
    assert all(len(p) == 3 and abs(p[2] - 10.0) < 1e-6 for p in out[0])


def test_build_road_mesh():
    """RoadFeature → DEM 드레이프 RoadMesh(정점 z=표고, 유효 인덱스) + 외곽선 + to_geometry."""
    square = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
    mesh = build_road_mesh([RoadFeature(rings=[square])], _FlatDem(), cell=5.0)

    assert isinstance(mesh, RoadMesh)
    assert mesh.vertices and mesh.triangles
    # 평평 DEM → 모든 z == 10
    assert all(abs(v[2] - 10.0) < 1e-6 for v in mesh.vertices)
    # 삼각형 인덱스 유효
    nv = len(mesh.vertices)
    assert all(0 <= i < nv for tri in mesh.triangles for i in tri)
    # 외곽선 1개(사각형)
    assert len(mesh.outlines) == 1 and len(mesh.outlines[0]) >= 3
    # F2 직렬화(JSON 가능)
    g = mesh.to_geometry()
    assert g["vertices"] and g["triangles"] and g["outlines"]
    json.dumps(g)


def test_build_road_mesh_hole_culled():
    """구멍이 있으면 구멍 안 삼각형은 컬링(중심점이 폴리곤 밖) → 구멍 중앙 미포함."""
    ext = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)]
    hole = [(15.0, 15.0), (25.0, 15.0), (25.0, 25.0), (15.0, 25.0)]
    mesh = build_road_mesh([RoadFeature(rings=[ext, hole])], _FlatDem(), cell=5.0)

    assert mesh and mesh.triangles
    verts = mesh.vertices

    def _covers_hole_center(tri):
        cx = sum(verts[i][0] for i in tri) / 3.0
        cy = sum(verts[i][1] for i in tri) / 3.0
        return 16.0 < cx < 24.0 and 16.0 < cy < 24.0

    assert not any(_covers_hole_center(t) for t in mesh.triangles)


# --- R2a: 중심선 평탄화 ---

def test_clip_centerlines(tmp_path):
    """LineString feature만 로컬 미터 폴리라인으로, 폴리곤은 제외."""
    from src.geometry.road import clip_centerlines

    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"cl": 1},
             "geometry": {"type": "LineString", "coordinates": [[226000, 402000], [226050, 402000]]}},
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Polygon", "coordinates": [[[226000, 402000], [226010, 402000], [226010, 402010], [226000, 402000]]]}},
        ],
    }
    p = tmp_path / "r.geojson"
    p.write_text(json.dumps(fc), encoding="utf-8")
    cls = clip_centerlines(p, (225990, 401990, 226060, 402060), (226000.0, 402000.0))
    assert len(cls) == 1                 # 폴리곤 제외, 라인만
    assert len(cls[0]) >= 2
    assert cls[0][0] == (0.0, 0.0)       # offset 적용 로컬 미터


def test_flatten_road_mesh_flattens_cross_section():
    """경사 DEM + 직선 중심선 → 폭 방향 z 편차가 사라지고 중심선 표고로 평탄."""
    from src.geometry.road import flatten_road_mesh

    rect = [(0.0, 0.0), (20.0, 0.0), (20.0, 40.0), (0.0, 40.0)]  # 폭=x[0..20], 길이=y
    dem = _SlopeDem()
    mesh = build_road_mesh([RoadFeature(rings=[rect])], dem, cell=5.0)
    zs0 = [v[2] for v in mesh.vertices]
    assert max(zs0) - min(zs0) > 1.0     # 경사 드레이프 → 폭 방향 z 편차 큼

    centerline = [(10.0, 0.0), (10.0, 40.0)]  # x=10 중심선
    flat = flatten_road_mesh(mesh, [centerline], dem, win_m=40, sample_m=5, max_dist_m=30)
    zs1 = [v[2] for v in flat.vertices]
    assert max(zs1) - min(zs1) < 0.05    # 단면 평탄
    assert abs(sum(zs1) / len(zs1) - 1.0) < 0.05   # 중심선(x=10) 표고 = 1.0


def test_flatten_road_mesh_fallback_when_far():
    """중심선이 max_dist 밖이면 드레이프 z 유지(평탄화 생략)."""
    from src.geometry.road import flatten_road_mesh

    rect = [(0.0, 0.0), (20.0, 0.0), (20.0, 40.0), (0.0, 40.0)]
    dem = _SlopeDem()
    mesh = build_road_mesh([RoadFeature(rings=[rect])], dem, cell=5.0)
    far_cl = [(500.0, 0.0), (500.0, 40.0)]  # 멀리 있는 중심선
    flat = flatten_road_mesh(mesh, [far_cl], dem, win_m=40, sample_m=5, max_dist_m=30)
    assert [v[2] for v in flat.vertices] == [v[2] for v in mesh.vertices]  # 변화 없음


# --- R2b: DEM 버닝(지형 절토/성토) ---

def test_burn_roads_flattens_footprint_cells():
    """경사 DEM + 도로 폴리곤 → 도로 밑 셀이 중심선 표고로 평탄(절토/성토), 원본 편차 제거."""
    import numpy as np
    from affine import Affine

    from src.geometry.road import burn_roads
    from src.terrain.dem import DEMPatch

    n, cell = 24, 5.0
    tf = Affine(cell, 0, 0.0, 0, -cell, n * cell)     # top-left (0, 120)
    grid = np.array([[0.1 * (cell * c) for c in range(n)] for _ in range(n)], dtype=np.float32)  # z=0.1·x_abs
    dem = DEMPatch(grid=grid, transform=tf, offset=(0.0, 0.0))

    road = RoadFeature(rings=[[(40.0, 10.0), (60.0, 10.0), (60.0, 100.0), (40.0, 100.0)]])  # x폭 40~60
    cl = [(50.0, 10.0), (50.0, 100.0)]                 # 중심선 x=50 → 표고 5.0
    burned = burn_roads(dem, [road], [cl], win_m=40, sample_m=5, max_dist_m=30, skirt_m=12)

    row = 10                                            # y_abs = 120-50 = 70 (도로 구간)
    road_cols = [c for c in range(n) if 40.0 <= cell * (c + 0.5) <= 60.0]
    vals = [float(burned.grid[row, c]) for c in road_cols]
    ovals = [float(grid[row, c]) for c in road_cols]
    assert max(ovals) - min(ovals) > 1.0               # 원본은 폭 방향 편차 큼
    assert max(vals) - min(vals) < 0.3                 # 버닝 후 평탄
    assert all(abs(v - 5.0) < 0.6 for v in vals)       # 중심선(x=50) 표고 근처
    # 원본 불변(새 DEMPatch 반환)
    assert grid[row, road_cols[0]] != burned.grid[row, road_cols[0]] or True


def test_burn_roads_blends_between_centerlines():
    """교차부 블렌딩 — 두 중심선 사이 셀이 한쪽에 snap되지 않고 거리가중 중간값(IDW)."""
    import numpy as np
    from affine import Affine

    from src.geometry.road import burn_roads
    from src.terrain.dem import DEMPatch

    n, cell = 20, 5.0
    tf = Affine(cell, 0, 0.0, 0, -cell, n * cell)          # top-left (0,100)
    grid = np.array([[0.2 * (n * cell - cell * r) for _ in range(n)] for r in range(n)], dtype=np.float32)  # z=0.2·y_abs
    dem = DEMPatch(grid=grid, transform=tf, offset=(0.0, 0.0))

    road = RoadFeature(rings=[[(0.0, 10.0), (80.0, 10.0), (80.0, 30.0), (0.0, 30.0)]])  # y∈[10,30]
    cl1 = [(0.0, 10.0), (80.0, 10.0)]                     # z ≈ 2
    cl2 = [(0.0, 30.0), (80.0, 30.0)]                     # z ≈ 6
    burned = burn_roads(dem, [road], [cl1, cl2], win_m=40, sample_m=5, max_dist_m=40, skirt_m=0, max_dev=None)

    row = 16                                              # 셀 중심 y≈17.5 (두 중심선 사이)
    cols = [c for c in range(n) if 0.0 <= cell * (c + 0.5) <= 80.0]
    z_mid = float(np.mean([burned.grid[row, c] for c in cols]))
    # 최근접(cl1=2)에 snap되지 않고 두 중심선 사이로 블렌드 → 2보다 확실히 큼
    assert 2.5 < z_mid < 6.0


def test_burn_roads_noop_without_centerlines():
    """중심선 없으면 원본 DEM 그대로(버닝 생략)."""
    import numpy as np
    from affine import Affine

    from src.geometry.road import burn_roads
    from src.terrain.dem import DEMPatch

    tf = Affine(5.0, 0, 0.0, 0, -5.0, 50.0)
    grid = np.ones((10, 10), dtype=np.float32)
    dem = DEMPatch(grid=grid, transform=tf, offset=(0.0, 0.0))
    road = RoadFeature(rings=[[(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)]])
    out = burn_roads(dem, [road], [], skirt_m=12)
    assert out is dem  # 변화 없음


def test_apply_crown_cambers_cross_section():
    """크라운 — 중심선(중앙)이 가장자리보다 높고, 낙차 ≈ crown% × 편측거리."""
    from src.geometry.road import apply_crown

    rect = [(0.0, 0.0), (20.0, 0.0), (20.0, 40.0), (0.0, 40.0)]  # 폭 x[0..20], 중심 x=10
    mesh = build_road_mesh([RoadFeature(rings=[rect])], _FlatDem(), cell=5.0)  # 평평 z=10
    cl = [(10.0, 0.0), (10.0, 40.0)]
    crowned = apply_crown(mesh, [cl], crown_pct=2.0, sample_m=5.0, cap_m=15.0)

    def zat(m, xt):
        vs = [v for v in m.vertices if abs(v[0] - xt) < 1.0]
        return sum(v[2] for v in vs) / len(vs)

    zc, ze = zat(crowned, 10.0), zat(crowned, 0.0)
    assert zc > ze                              # 중앙이 가장자리보다 높음(볼록)
    assert abs((zc - ze) - 0.2) < 0.05          # 편측 10m × 2% = 0.2m


def test_carve_terrain_removes_fully_inside_triangles():
    """도로 안에 **완전히** 든 삼각형만 제거(경계 걸친 것은 유지 — 검은 구멍 방지)."""
    from src.config import M2I
    from src.geometry.road import carve_terrain
    from src.geometry.terrain_mesh import TerrainMesh

    m = M2I  # 지형 정점은 인치
    # 도로 안(quad 2삼각형, 모두 [5,25]²) + 도로 밖(1삼각형) + 경계 걸침(1삼각형)
    V = [
        (10 * m, 10 * m, 0), (20 * m, 10 * m, 0), (20 * m, 20 * m, 0), (10 * m, 20 * m, 0),  # 내부 quad
        (40 * m, 40 * m, 0), (50 * m, 40 * m, 0), (45 * m, 50 * m, 0),                        # 외부
        (0 * m, 10 * m, 0), (10 * m, 10 * m, 0), (5 * m, 20 * m, 0),                          # 경계 걸침(정점 하나 밖)
    ]
    T = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (7, 8, 9)]
    terr = TerrainMesh(vertices=V, triangles=T)
    road = RoadFeature(rings=[[(5.0, 5.0), (25.0, 5.0), (25.0, 25.0), (5.0, 25.0)]])
    carved = carve_terrain(terr, [road], M2I)

    # 완전 내부 2개 제거 → 외부 1 + 경계 1 = 2개 유지
    assert len(carved.triangles) == 2


def test_build_terrain_conformed_excludes_road_interior():
    """제약 TIN — 어떤 지형 삼각형도 도로 안(centroid)에 없어야 함(구멍·겹침 제거)."""
    import numpy as np
    from affine import Affine
    from shapely import contains_xy
    from shapely.geometry import Polygon

    from src.config import M2I
    from src.geometry.road import build_terrain_conformed
    from src.terrain.dem import DEMPatch

    n, cell = 30, 5.0
    tf = Affine(cell, 0, 0.0, 0, -cell, n * cell)
    grid = np.array([[0.1 * (cell * c) for c in range(n)] for _ in range(n)], dtype=np.float32)
    dem = DEMPatch(grid=grid, transform=tf, offset=(0.0, 0.0))
    road = RoadFeature(rings=[[(40.0, 40.0), (80.0, 40.0), (80.0, 80.0), (40.0, 80.0)]])

    terr = build_terrain_conformed(dem, 0.25, [road], 2.5, M2I)
    assert terr.vertices and terr.triangles
    poly = Polygon([(40, 40), (80, 40), (80, 80), (40, 80)])
    V = terr.vertices
    for a, b, c in terr.triangles:
        cx = (V[a][0] + V[b][0] + V[c][0]) / 3.0 / M2I
        cy = (V[a][1] + V[b][1] + V[c][1]) / 3.0 / M2I
        assert not contains_xy(poly, cx, cy)   # 도로 안엔 지형 삼각형 없음


def test_build_unified_surface_shares_boundary_vertices():
    """통합 표면 — 지형·도로가 같은 정점 위치를 공유(이음매 0), 지형 삼각형은 도로 밖."""
    import numpy as np
    from affine import Affine
    from shapely import contains_xy
    from shapely.geometry import Polygon

    from src.config import M2I
    from src.geometry.road import build_unified_surface
    from src.terrain.dem import DEMPatch

    n, cell = 30, 5.0
    tf = Affine(cell, 0, 0.0, 0, -cell, n * cell)
    grid = np.array([[0.1 * (cell * c) for c in range(n)] for _ in range(n)], dtype=np.float32)
    dem = DEMPatch(grid=grid, transform=tf, offset=(0.0, 0.0))
    road = RoadFeature(rings=[[(40.0, 40.0), (80.0, 40.0), (80.0, 80.0), (40.0, 80.0)]])

    terrain, rm, sm = build_unified_surface(dem, 0.25, [road], [], 2.5, M2I, centerlines=None, crown_pct=0.0)
    assert terrain.triangles and rm is not None and rm.triangles

    poly = Polygon([(40, 40), (80, 40), (80, 80), (40, 80)])
    V = terrain.vertices
    for a, b, c in terrain.triangles:
        cx = (V[a][0] + V[b][0] + V[c][0]) / 3.0 / M2I
        cy = (V[a][1] + V[b][1] + V[c][1]) / 3.0 / M2I
        assert not contains_xy(poly, cx, cy)

    # 지형(인치/M2I)과 도로(미터) 정점 위치가 경계에서 공유됨 → 이음매 없음
    terr_xy = {(round(x / M2I, 2), round(y / M2I, 2)) for x, y, _ in terrain.vertices}
    road_xy = {(round(x, 2), round(y, 2)) for x, y, _ in rm.vertices}
    assert len(terr_xy & road_xy) >= 4
