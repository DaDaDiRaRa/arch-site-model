"""지형 비축 매니페스트 조회 (사양서 §3.4, B안).

geo_store/manifest.json 은 사전 비축한 DEM/등고선 타일의 목록이다.
지형이 바뀌면 파일 교체 + manifest 한 줄 수정만으로 엔진 무수정 운영한다.

매니페스트 타일 항목 형식(권장):
{
  "region":      "대전 서구",
  "file":        "dem_daejeon_seogu_2026Q2.tif",
  "crs":         "EPSG:5186",                  # DEM 파일 자체의 좌표계
  "bounds_4326": [minx, miny, maxx, maxy],     # 공간 조회용 (EPSG:4326)
  "source":      "DEM",                         # DEM | CONTOUR
  "updated":     "2026-04-01",
  "sheets":      ["..."]                         # (선택) 도엽 번호
}

최상위는 타일 배열 또는 {"tiles": [...]} 둘 다 허용한다.
"""

import json
from pathlib import Path

from shapely.geometry import box

from src import config


def _manifest_path() -> Path:
    return config.GEO_STORE / "manifest.json"


def load_manifest(path: Path | None = None) -> list[dict]:
    """manifest.json 의 타일 목록을 반환. 파일 없으면 빈 목록(비축 없음)."""
    path = path or _manifest_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("tiles", [])
    if isinstance(data, list):
        return data
    return []


def find_tile(bbox, manifest: list[dict] | None = None) -> dict | None:
    """질의 bbox(EPSG:4326)를 포함하는 첫 DEM 타일을 반환. 없으면 None.

    타일의 bounds_4326 이 질의 bbox 를 완전히 포함하면 매칭.
    """
    tiles = manifest if manifest is not None else load_manifest()
    query = box(*bbox)
    for tile in tiles:
        bounds = tile.get("bounds_4326")
        if not bounds or len(bounds) != 4:
            continue
        if box(*bounds).contains(query):
            return tile
    return None
