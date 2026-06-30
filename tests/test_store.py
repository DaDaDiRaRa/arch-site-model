"""지형 비축 매니페스트 조회 (find_tile)."""

from src.terrain.store import find_tile, load_manifest
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


def test_find_tile_partial_overlap_not_matched():
    # 타일 경계를 걸치는 bbox는 '포함'이 아니므로 매칭 안 됨.
    bbox = (127.381, 36.349, 127.390, 36.351)
    assert find_tile(bbox, manifest=_manifest()) is None


def test_load_manifest_missing_returns_empty(tmp_path):
    assert load_manifest(tmp_path / "nope.json") == []


def test_load_manifest_reads_file():
    tiles = load_manifest(FIXTURES / "manifest_sample.json")
    assert len(tiles) == 2
