"""지형 비축 매니페스트 조회 (find_tile / find_tiles)."""

from src.terrain.store import find_tile, find_tiles, load_manifest
from tests.conftest import FIXTURES, load_fixture


def _manifest():
    return load_fixture("manifest_sample.json")["tiles"]


def test_find_tile_contained():
    # 대전 서구 타일(127.360~127.382, 36.330~36.350) 안의 작은 bbox.
    bbox = (127.369, 36.339, 127.373, 36.341)
    tile = find_tile(bbox, manifest=_manifest())
    assert tile is not None
    assert tile["file"] == "dem_daejeon_seogu_2026Q2.tif"
    assert tile["source"] == "DEM"


def test_find_tile_outside_returns_none():
    bbox = (128.0, 37.0, 128.01, 37.01)  # 어떤 타일에도 없음
    assert find_tile(bbox, manifest=_manifest()) is None


def test_find_tile_partial_overlap_now_matched():
    # 타일 경계를 걸치는 bbox도 이제 '겹침'으로 매칭된다(다중 타일 병합 지원).
    bbox = (127.381, 36.349, 127.390, 36.351)
    tile = find_tile(bbox, manifest=_manifest())
    assert tile is not None
    assert tile["file"] == "dem_daejeon_seogu_2026Q2.tif"


def test_find_tiles_returns_all_overlapping():
    # 두 타일이 만나는 접점(127.360, 36.350) 주변을 걸치는 bbox → 둘 다 반환.
    bbox = (127.355, 36.345, 127.365, 36.355)
    files = {t["file"] for t in find_tiles(bbox, manifest=_manifest())}
    assert files == {
        "dem_daejeon_seogu_2026Q2.tif",
        "dem_daejeon_yuseong_2026Q2.tif",
    }


def test_find_tiles_outside_returns_empty():
    assert find_tiles((128.0, 37.0, 128.01, 37.01), manifest=_manifest()) == []


def test_find_tiles_prefers_finer_resolution():
    manifest = [
        {"file": "coarse.tif", "bounds_4326": [127.0, 36.0, 128.0, 37.0], "cell_m": 90},
        {"file": "fine.tif", "bounds_4326": [127.4, 36.4, 127.6, 36.6], "cell_m": 5},
    ]
    bbox = (127.45, 36.45, 127.46, 36.46)  # 두 타일 모두 겹침
    tiles = find_tiles(bbox, manifest=manifest)
    assert [t["file"] for t in tiles] == ["fine.tif", "coarse.tif"]


def test_load_manifest_missing_returns_empty(tmp_path):
    assert load_manifest(tmp_path / "nope.json") == []


def test_load_manifest_reads_file():
    tiles = load_manifest(FIXTURES / "manifest_sample.json")
    assert len(tiles) == 2
