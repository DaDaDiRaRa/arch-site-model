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


def _tile_box(tile: dict):
    """타일의 bounds_4326 → shapely box. 없거나 형식 오류면 None."""
    bounds = tile.get("bounds_4326")
    if not bounds or len(bounds) != 4:
        return None
    return box(*bounds)


def _tile_cell_m(tile: dict) -> float:
    """타일 격자 해상도(m). 없으면 inf(가장 거친 것으로 취급 → 정렬 후순위)."""
    cell = tile.get("cell_m")
    try:
        return float(cell) if cell is not None else float("inf")
    except (TypeError, ValueError):
        return float("inf")


def find_tiles(bbox, manifest: list[dict] | None = None) -> list[dict]:
    """질의 bbox(EPSG:4326)와 겹치는 DEM 타일을 전부 반환.

    정렬: 해상도 고운(cell_m 작은) 순 → 겹침 면적 큰 순. 여러 타일에 걸친 질의는
    이 목록을 clip_dem_mosaic로 병합한다. '완전 포함'이 아니라 '겹침' 기준이라,
    타일 경계를 걸친 주소가 조용히 지형 누락되던 문제를 해결한다.
    """
    tiles = manifest if manifest is not None else load_manifest()
    query = box(*bbox)
    hits: list[tuple[dict, float]] = []
    for tile in tiles:
        tb = _tile_box(tile)
        if tb is None:
            continue
        overlap = tb.intersection(query).area
        if overlap <= 0.0:
            continue
        hits.append((tile, overlap))
    hits.sort(key=lambda h: (_tile_cell_m(h[0]), -h[1]))
    return [tile for tile, _ in hits]


def find_tile(bbox, manifest: list[dict] | None = None) -> dict | None:
    """질의 bbox와 겹치는 대표 타일 1개(가장 고해상도·겹침 큰 것). 없으면 None.

    지형 가용 여부 판단·단일 타일 클립용. 다중 타일 병합은 find_tiles + clip_dem_mosaic.
    """
    hits = find_tiles(bbox, manifest)
    return hits[0] if hits else None


# --- 도로(Phase R) — DEM manifest와 분리된 road_manifest.json ---

def _road_manifest_path() -> Path:
    return config.GEO_STORE / "road_manifest.json"


def load_road_manifest(path: Path | None = None) -> list[dict]:
    """road_manifest.json의 도로 지역 목록. 파일 없으면 빈 목록(도로 비축 없음)."""
    path = path or _road_manifest_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("roads", [])
    if isinstance(data, list):
        return data
    return []


def find_road_files(bbox, manifest: list[dict] | None = None) -> list[dict]:
    """질의 bbox(EPSG:4326)와 겹치는 도로 지역 파일 전부(겹침 큰 순)."""
    entries = manifest if manifest is not None else load_road_manifest()
    query = box(*bbox)
    hits: list[tuple[dict, float]] = []
    for e in entries:
        bounds = e.get("bounds_4326")
        if not bounds or len(bounds) != 4:
            continue
        overlap = box(*bounds).intersection(query).area
        if overlap <= 0.0:
            continue
        hits.append((e, overlap))
    hits.sort(key=lambda h: -h[1])
    return [e for e, _ in hits]


def find_road_file(bbox, manifest: list[dict] | None = None) -> dict | None:
    """질의 bbox와 겹치는 대표 도로 파일 1개(겹침 큰 것). 없으면 None."""
    hits = find_road_files(bbox, manifest)
    return hits[0] if hits else None


# --- 수계 — water_manifest.json (road_manifest와 동형) ---

def _water_manifest_path() -> Path:
    return config.GEO_STORE / "water_manifest.json"


def load_water_manifest(path: Path | None = None) -> list[dict]:
    """water_manifest.json의 수계 지역 목록. 파일 없으면 빈 목록(수계 비축 없음)."""
    path = path or _water_manifest_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("water", [])
    if isinstance(data, list):
        return data
    return []


def find_water_file(bbox, manifest: list[dict] | None = None) -> dict | None:
    """질의 bbox(EPSG:4326)와 겹치는 대표 수계 파일 1개(겹침 큰 것). 없으면 None."""
    entries = manifest if manifest is not None else load_water_manifest()
    query = box(*bbox)
    hits: list[tuple[dict, float]] = []
    for e in entries:
        bounds = e.get("bounds_4326")
        if not bounds or len(bounds) != 4:
            continue
        overlap = box(*bounds).intersection(query).area
        if overlap <= 0.0:
            continue
        hits.append((e, overlap))
    hits.sort(key=lambda h: -h[1])
    return hits[0][0] if hits else None
