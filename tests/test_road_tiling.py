"""도로 타일링 (메트로 서빙): 하드클립 헬퍼 + 런타임 다파일 클립 + manifest 지역보존.

메트로(서울 등)는 도로가 수백 MB 단일 파일이 돼 런타임이 요청마다 전량 파싱·선형스캔한다.
DEM처럼 공간 타일로 하드클립해 쪼개면 런타임은 겹치는 타일만 읽는다. 여기선 SHP 없이 합성
지오메트리로 (a) 타일 하드클립 헬퍼, (b) clip_*가 여러 타일 파일을 합쳐 읽는지(하드클립이라
조각이 안 겹침 → 그냥 이어붙임), (c) manifest 교체가 다른 지역을 보존하는지 검증한다.
"""

import json

from shapely.geometry import LineString, Polygon, box
from shapely.strtree import STRtree

from src.geometry import road
from src.terrain import road_bake


def test_clip_polys_to_hard_clips_to_tile():
    """타일 박스를 걸치는 폴리곤은 정확히 박스로 잘린다(교집합)."""
    polys = [Polygon([(0, 0), (20, 0), (20, 10), (0, 10)])]  # x∈[0,20]
    tree = STRtree(polys)
    tbox = box(0, 0, 10, 10)  # 왼쪽 절반만
    clipped = road_bake._clip_polys_to(tree, polys, tbox, min_area_m2=1.0)
    assert clipped
    total = sum(p.area for p in clipped)
    assert abs(total - 100.0) < 1e-6  # 20×10의 왼쪽 절반 = 10×10 = 100
    # 타일 밖(x>10)으로 새지 않는다.
    assert max(p.bounds[2] for p in clipped) <= 10.0 + 1e-9


def test_clip_cls_to_splits_line_and_keeps_props():
    """중심선은 타일 경계서 조각나되 각 조각이 (폭·구분·차로수)를 유지한다."""
    cl = LineString([(0, 5), (20, 5)])
    tree = STRtree([cl])
    tbox = box(0, 0, 10, 10)
    parts = road_bake._clip_cls_to(tree, [(cl, 8.0, "중로", 2)], tbox)
    assert parts
    for geom, w, cls, n in parts:
        assert (w, cls, n) == (8.0, "중로", 2)
        assert geom.bounds[2] <= 10.0 + 1e-9  # 타일 안으로 잘림


def _write_tile(path, coords, props=None):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": props or {},
             "geometry": {"type": "Polygon", "coordinates": [coords]}}
        ],
    }
    path.write_text(json.dumps(fc), encoding="utf-8")
    return str(path)


def test_load_features_multi_concats_and_skips_missing(tmp_path):
    a = _write_tile(tmp_path / "a.geojson", [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])
    b = _write_tile(tmp_path / "b.geojson", [[10, 0], [20, 0], [20, 10], [10, 10], [10, 0]])
    feats = road._load_features([a, b, str(tmp_path / "missing.geojson")])
    assert len(feats) == 2  # 두 타일 합침, 없는 파일은 건너뜀
    # 단일 경로 하위호환.
    assert len(road._load_features(a)) == 1


def test_clip_roads_stitches_across_tiles(tmp_path):
    """한 도로가 x=10 경계로 두 타일에 하드클립돼 있어도, 두 타일을 함께 클립하면 온전히 복원."""
    a = _write_tile(tmp_path / "roads_x_r0c0.geojson",
                    [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])
    b = _write_tile(tmp_path / "roads_x_r0c1.geojson",
                    [[10, 0], [20, 0], [20, 10], [10, 10], [10, 0]])
    bbox = (-1, -1, 21, 21)
    feats = road.clip_roads([a, b], bbox, offset=(0.0, 0.0))
    assert len(feats) == 2  # 타일당 조각 1개
    total = sum(Polygon(f.rings[0]).area for f in feats)
    assert abs(total - 200.0) < 1e-6  # 두 조각 합 = 20×10 원본 면적, 중복 없음


def test_replace_region_tiles_manifest_preserves_other_regions(tmp_path, monkeypatch):
    """서울 타일로 교체해도 대전 단일 항목은 보존, 이전 서울 단일/타일 항목은 제거."""
    mpath = tmp_path / "road_manifest.json"
    mpath.write_text(json.dumps([
        {"region": "대전", "file": "roads_daejeon.geojson", "bounds_4326": [1, 1, 2, 2], "polygons": 5},
        {"region": "서울", "file": "roads_seoul.geojson", "bounds_4326": [3, 3, 4, 4], "polygons": 9},
        {"region": "서울", "file": "roads_seoul_r9c9.geojson", "bounds_4326": [3, 3, 4, 4], "polygons": 1},
    ]), encoding="utf-8")
    monkeypatch.setattr(road_bake, "_road_manifest_path", lambda: mpath)

    road_bake._replace_region_tiles_manifest("서울", "roads_seoul", [
        {"region": "서울", "file": "roads_seoul_r0c0.geojson", "bounds_4326": [3, 3, 3.5, 3.5], "polygons": 4},
    ])
    out = json.loads(mpath.read_text(encoding="utf-8"))
    files = {e["file"] for e in out}
    assert "roads_daejeon.geojson" in files          # 다른 지역 보존
    assert "roads_seoul.geojson" not in files        # 이전 단일 제거
    assert "roads_seoul_r9c9.geojson" not in files   # 이전 타일 제거
    assert "roads_seoul_r0c0.geojson" in files       # 새 타일 추가
