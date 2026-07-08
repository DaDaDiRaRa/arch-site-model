"""도로 런타임 지오메트리 (Phase R, R1a) — 합성 GeoJSON으로 클립·링 변환 검증."""

import json

from src.geometry.road import RoadFeature, RoadMesh, build_road_mesh, clip_roads


class _FlatDem:
    """합성 DEM: 어디서나 표고 10m."""

    def elev_at(self, x, y):
        return 10.0


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
