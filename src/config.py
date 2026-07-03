"""전역 설정 및 환경변수.

환경변수는 `.env`(예시: `.env.example`)에서 로드된다.
테스트 키(`VWORLD_TEST_KEY`)를 운영 키(`VWORLD_KEY`)보다 우선한다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트의 .env 를 로드(이미 설정된 환경변수는 덮어쓰지 않음).
load_dotenv()

# --- VWorld API ---
# 테스트 키 우선, 없으면 운영 키. (사양서 §11: 테스트/운영 키 분리 유지)
VWORLD_KEY = os.environ.get("VWORLD_TEST_KEY") or os.environ.get("VWORLD_KEY")
VWORLD_DOMAIN = os.environ.get("VWORLD_DOMAIN", "")

# --- 단위/기본값 ---
M2I = 39.3701              # meter → inch (SketchUp MCP는 인치 단위)
DEFAULT_FLOOR_H_M = 3.0    # 기본 층고 (m)

# --- 정사영상(orthophoto) 텍스처 ---
# 소스: "vworld"(기존 VWORLD_KEY 재사용) | "ngii"(NGII_KEY 발급 후 사용, 공공누리 1유형).
# 기술은 동일 — TileSource만 바뀐다. NGII 키 승인 시 .env에 NGII_KEY 채우고 ORTHO_SOURCE=ngii.
ORTHO_SOURCE = os.environ.get("ORTHO_SOURCE", "vworld")
NGII_KEY = os.environ.get("NGII_KEY", "")

try:
    ORTHO_ZOOM = int(os.environ.get("ORTHO_ZOOM", "18"))
except ValueError:
    ORTHO_ZOOM = 18

# --- 지형 비축 스토어 (B안) ---
# 프로젝트 루트 기준 geo_store/ — manifest.json + 로컬 베이크 산출물.
# manifest는 작은 index라 항상 로컬(GEO_STORE)에 둔다. DEM 타일(대용량)만 아래
# DEM_TILE_BASE로 위치를 해석한다(로컬 디렉터리 ↔ GCS COG /vsigs 윈도우 읽기).
GEO_STORE = Path(os.environ.get("GEO_STORE", "geo_store"))

# DEM 타일 읽기 베이스. 기본은 로컬 GEO_STORE. 전국 확장 시 GCS로 두고
#   DEM_TILE_BASE=/vsigs/<버킷>/<프리픽스>  (또는 gs://<버킷>/<프리픽스>)
# 로 바꾸면 clip_dem 경로만 원격이 되고 나머지 코드는 그대로다. (사용자 PC엔 DEM 0바이트)
DEM_TILE_BASE = os.environ.get("DEM_TILE_BASE", str(GEO_STORE))


def dem_tile_path(filename: str) -> str:
    """manifest의 타일 파일명 → 실제 읽기 경로(rasterio.open용 문자열).

    로컬이면 OS 경로, 원격(gs://·/vsigs/·기타 URI)이면 슬래시로 결합한다.
    gs://는 GDAL 가상 파일시스템 /vsigs/로 변환한다.
    """
    base = DEM_TILE_BASE
    if base.startswith("gs://"):
        base = "/vsigs/" + base[len("gs://"):]
    if base.startswith("/vsi") or "://" in base:
        return base.rstrip("/") + "/" + filename
    return str(Path(base) / filename)
