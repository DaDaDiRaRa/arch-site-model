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

# --- 지형 TIN ---
# 지형 삼각망 방식: >0 이면 오차 한계 적응형 TIN(그 수직오차[m] 이내 보장, 평지는 큰
# 삼각형·복잡한 곳만 촘촘 → 삼각형 대폭 감소, 넓은 반경도 가벼움). 0 이면 균일 격자
# (어디든 5m마다 삼각형). 기본 0.25m = 25cm 정확도. 정확도 더 원하면 낮추고(무거워짐),
# 더 가볍게는 올린다.
try:
    TERRAIN_MAX_ERROR_M = float(os.environ.get("TERRAIN_MAX_ERROR_M", "0.25"))
except ValueError:
    TERRAIN_MAX_ERROR_M = 0.25

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


# --- 도로(Phase R) ---
# 도로 노면 벡터(수치지도 A0010000 도로경계)를 오프라인 굽기로 지역별 GeoJSON(EPSG:5186)에
# 담아 geo_store에 두고, road_manifest.json으로 조회한다. 런타임은 json+shapely로 읽어(DEM처럼
# geopandas 런타임 의존 없이) bbox 클립·지형 드레이프한다. 배포 서빙은 DEM과 동일 패턴으로 추후.
ROAD_BASE = os.environ.get("ROAD_BASE", str(GEO_STORE))

# 도로 노면 메시(R1b) 내부 샘플 간격(m). 폴리곤 내부를 이 간격 격자로 샘플해 DEM 드레이프
# 삼각화한다. 작을수록 곡면을 촘촘히 따라가나 삼각형↑. DEM 5m보다 촘촘한 2.5m 기본.
try:
    ROAD_CELL_M = float(os.environ.get("ROAD_CELL_M", "2.5"))
except ValueError:
    ROAD_CELL_M = 2.5


def road_file_path(filename: str) -> str:
    """road_manifest의 도로 파일명 → 실제 읽기 경로. dem_tile_path와 동형(로컬↔원격)."""
    base = ROAD_BASE
    if base.startswith("gs://"):
        base = "/vsigs/" + base[len("gs://"):]
    if base.startswith("/vsi") or "://" in base:
        return base.rstrip("/") + "/" + filename
    return str(Path(base) / filename)
