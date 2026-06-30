"""전역 설정 및 환경변수.

환경변수는 `.env`(예시: `.env.example`)에서 로드된다.
테스트 키(`VWORLD_TEST_KEY`)를 운영 키(`VWORLD_KEY`)보다 우선한다.
"""

import os
from pathlib import Path

# --- VWorld API ---
# 테스트 키 우선, 없으면 운영 키. (사양서 §11: 테스트/운영 키 분리 유지)
VWORLD_KEY = os.environ.get("VWORLD_TEST_KEY") or os.environ.get("VWORLD_KEY")
VWORLD_DOMAIN = os.environ.get("VWORLD_DOMAIN", "")

# --- 단위/기본값 ---
M2I = 39.3701              # meter → inch (SketchUp MCP는 인치 단위)
DEFAULT_FLOOR_H_M = 3.0    # 기본 층고 (m)

# --- 지형 비축 스토어 (B안) ---
# 프로젝트 루트 기준 geo_store/ — DEM/등고선 비축 + manifest.json
GEO_STORE = Path(os.environ.get("GEO_STORE", "geo_store"))
