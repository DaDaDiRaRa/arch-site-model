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


def scrub_secrets(text: str) -> str:
    """응답/로그 문자열에서 알려진 API 키 값을 마스킹 (URL의 key= 유출 방지)."""
    if not text:
        return text
    for secret in (VWORLD_KEY, NGII_KEY):
        if secret and secret in text:
            text = text.replace(secret, "***")
    return text

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
# geopandas 런타임 의존 없이) bbox 클립·지형 드레이프한다.
# 배포 서빙: ROAD_BASE=gs://<버킷>/roads (또는 https://…) 로 두면 도로 GeoJSON을 공개 GCS에서
# HTTP로 받아 읽는다(road_file_path가 https URL로 변환, road._read_geojson_text가 fetch+캐시).
# 미설정 시 로컬 geo_store. manifest는 항상 git 추적, 데이터(gitignore)는 원격 — DEM과 동일 원칙.
ROAD_BASE = os.environ.get("ROAD_BASE", str(GEO_STORE))

# 도로 노면 메시(R1b) 내부 샘플 간격(m). 폴리곤 내부를 이 간격 격자로 샘플해 DEM 드레이프
# 삼각화한다. 작을수록 곡면을 촘촘히 따라가나 삼각형↑. DEM 5m보다 촘촘한 2.5m 기본.
try:
    ROAD_CELL_M = float(os.environ.get("ROAD_CELL_M", "2.5"))
except ValueError:
    ROAD_CELL_M = 2.5

# 도로 평탄화(R2a) — 중심선 종단 프로파일 평활 + KD-트리 단면 평탄.
# ROAD_SMOOTH_WIN_M: 중심선 종단 이동평균 창(m). 클수록 매끈하나 급경사서 뜰 수 있음.
# ROAD_CL_SAMPLE_M : 중심선 조밀화 간격(m) — 종단 샘플·KD-트리 점 밀도.
# ROAD_CL_MAX_DIST_M: 노면 정점에서 이 거리 밖에 중심선이 없으면 평탄화 생략(드레이프 유지).
def _envf(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default

ROAD_SMOOTH_WIN_M = _envf("ROAD_SMOOTH_WIN_M", 40.0)
ROAD_CL_SAMPLE_M = _envf("ROAD_CL_SAMPLE_M", 5.0)
ROAD_CL_MAX_DIST_M = _envf("ROAD_CL_MAX_DIST_M", 30.0)

# 도로 버닝(R2b) 스커트 밴드 폭(m). 도로 밖 이 거리까지 지형을 도로 높이↔자연 표고로 선형
# 블렌딩해 절토/성토 비탈을 만든다(지형이 도로를 덮거나 뜨는 이음매 제거). 0이면 스커트 없음.
ROAD_SKIRT_M = _envf("ROAD_SKIRT_M", 12.0)

# 평활 종단 프로파일이 실제(원본) 지면에서 벗어날 수 있는 최대치(m). 종단 평활(창 40m)이 국소
# 파임/솟음을 다리처럼 건너뛰어 도로가 6~7m씩 뜨거나 파이는 것을 막는다 — 평활값을
# [원본−dev, 원본+dev]로 클램프해 도로가 지면을 바짝 따라가게(작은 절토/성토만 허용).
ROAD_MAX_DEV_M = _envf("ROAD_MAX_DEV_M", 2.0)

# 도로 크라운(횡단구배, R2b 정제). 노면을 중심선에서 가장자리로 이 %만큼 낮춰 볼록한 배수형상.
# ROAD_CROWN_CAP_M: 크라운 적용 최대 편측거리(m) — 넓은 면(광장)서 과도한 하강 방지. 0%면 평평.
ROAD_CROWN_PCT = _envf("ROAD_CROWN_PCT", 2.0)
ROAD_CROWN_CAP_M = _envf("ROAD_CROWN_CAP_M", 15.0)

# 도로/보도 경계 densify 간격(m) — 내부 격자(ROAD_CELL_M)보다 촘촘히 해 경계가 곡선을 정밀히
# 따라가게(경계 샤프닝). 작을수록 경계 선명·삼각형↑. 통합표면 경계에만 적용(내부는 ROAD_CELL_M).
ROAD_EDGE_CELL_M = _envf("ROAD_EDGE_CELL_M", 1.0)


# 수계(E계열) GeoJSON 서빙 위치 — 도로(ROAD_BASE)와 동형. 미설정 시 로컬 geo_store.
WATER_BASE = os.environ.get("WATER_BASE", str(GEO_STORE))

# --- 용도지역(zoning) — 형제 앱 arch-law-graph 연동 ---
# 사이트 용도지역은 arch-law-graph의 GET /api/zoning?address= 로 조회한다(경계 존중: zoning=법령
# 클러스터 소유). ZONING_BASE=arch-law-graph 서비스 base URL(예: http://localhost:8000 또는 배포 URL).
# 미설정/미도달 시 조용히 생략(warnings) — DEM/도로와 동일 원칙. 그쪽 서비스가 떠 있고 VWORLD_KEY도
# 설정돼 있어야 한다. 배포(Cloud Run): --set-env-vars ZONING_BASE=<arch-law-graph URL>.
ZONING_BASE = os.environ.get("ZONING_BASE", "")
# 수면 삼각화 격자 간격(m). 수면은 평면이라 도로(2.5m)보다 성겨도 됨 → 삼각형 절약.
WATER_CELL_M = _envf("WATER_CELL_M", 10.0)


def water_file_path(filename: str) -> str:
    """water_manifest의 수계 파일명 → 실제 읽기 위치. road_file_path와 동형(로컬↔GCS HTTP)."""
    base = WATER_BASE
    if base.startswith("gs://"):
        base = "https://storage.googleapis.com/" + base[len("gs://"):]
    if base.startswith(("http://", "https://")):
        return base.rstrip("/") + "/" + filename
    return str(Path(base) / filename)


def road_file_path(filename: str) -> str:
    """road_manifest의 도로 파일명 → 실제 읽기 위치(로컬 경로 또는 HTTP(S) URL).

    도로 GeoJSON은 런타임이 json+shapely로 읽으므로(DEM처럼 GDAL 아님) 원격은 HTTP(S)로 받는다
    (DEM의 /vsigs와 다른 점 — 그쪽은 GDAL 윈도우 읽기):
      - gs://<버킷>/<프리픽스>  → https://storage.googleapis.com/<버킷>/<프리픽스> (공개 버킷)
      - http(s)://...           → 그대로 결합
      - 그 외(로컬 디렉터리)     → OS 경로
    """
    base = ROAD_BASE
    if base.startswith("gs://"):
        base = "https://storage.googleapis.com/" + base[len("gs://"):]
    if base.startswith(("http://", "https://")):
        return base.rstrip("/") + "/" + filename
    return str(Path(base) / filename)
