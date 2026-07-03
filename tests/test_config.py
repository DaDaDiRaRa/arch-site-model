"""config.dem_tile_path — DEM 타일 경로 해석 (로컬 ↔ 원격 /vsigs·gs://).

manifest는 항상 로컬이지만 타일은 DEM_TILE_BASE로 위치를 해석한다(전국=GCS COG).
"""
from pathlib import Path

from src import config


def test_dem_tile_path_local(monkeypatch):
    monkeypatch.setattr(config, "DEM_TILE_BASE", "geo_store")
    assert config.dem_tile_path("dem_x.tif") == str(Path("geo_store") / "dem_x.tif")


def test_dem_tile_path_vsigs(monkeypatch):
    monkeypatch.setattr(config, "DEM_TILE_BASE", "/vsigs/my-bucket/dem")
    assert config.dem_tile_path("dem_x.tif") == "/vsigs/my-bucket/dem/dem_x.tif"


def test_dem_tile_path_gs_translated_to_vsigs(monkeypatch):
    monkeypatch.setattr(config, "DEM_TILE_BASE", "gs://my-bucket/dem")
    assert config.dem_tile_path("dem_x.tif") == "/vsigs/my-bucket/dem/dem_x.tif"


def test_dem_tile_path_strips_trailing_slash(monkeypatch):
    monkeypatch.setattr(config, "DEM_TILE_BASE", "/vsigs/b/dem/")
    assert config.dem_tile_path("dem_x.tif") == "/vsigs/b/dem/dem_x.tif"
