"""FastAPI 백엔드 — 배포용 HTTP API (팀 공유 / GCP Cloud Run 등).

엔진(`pipeline.generate`)을 HTTP로 감싼다. 두 소비자를 서빙:
  - Rhino 사용자: 텍스처 `.3dm` 다운로드 (`files.3dm`)
  - SketchUp 확장: 지오메트리 데이터 + 정사영상 URL (추후 `/api/geometry`)

배포: 이 앱을 도커 컨테이너로 만들어 사내 서버 또는 GCP Cloud Run에 올린다.
인증(IAP/공유토큰)은 인프라·미들웨어 레이어에서 추후 추가(앱 코드 무관).

로컬 실행:  uvicorn src.api:app --reload --port 8000
문서:       http://localhost:8000/docs
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import config
from src.pipeline import generate as _generate

# 생성물 저장 루트(잡별 하위 폴더). 기본은 OS 임시 폴더 — Cloud Run 컨테이너 파일시스템은
# 읽기전용일 수 있으나 /tmp는 항상 쓰기 가능. JOBS_DIR 환경변수로 재정의 가능.
JOBS_DIR = Path(
    os.environ.get("JOBS_DIR") or (Path(tempfile.gettempdir()) / "arch_site_model_jobs")
).resolve()

app = FastAPI(
    title="arch-site-model API",
    version="1.0",
    description="주소 → 지형·건물 3D 대지모델 + 정사영상 텍스처 (.3dm / SketchUp 확장용 데이터).",
)


class GenerateRequest(BaseModel):
    address: str = Field(..., description="대지 주소")
    radius_m: int = Field(250, ge=10, le=2000, description="반경(m)")
    floor_height_m: float = Field(config.DEFAULT_FLOOR_H_M, gt=0, description="기본 층고(m)")
    layers: dict = Field(
        default_factory=lambda: {"buildings": True, "terrain": True, "orthophoto": True},
        description='레이어 토글. 예: {"buildings":true,"terrain":true,"orthophoto":true}',
    )
    outputs: list[str] = Field(
        default_factory=lambda: ["3dm"], description='출력 포맷: ["3dm"] | ["skp"] | 둘 다'
    )
    missing_floors_policy: str = Field(
        "default", description='층수 누락 처리: "default"|"skip"|"flag"'
    )
    proposed_height_m: float | None = Field(
        None, gt=0, description="제안 건물 높이(m) — 조망·스카이라인 B-2. subject 대지에 매스 배치"
    )


class TilePlanRequest(BaseModel):
    address: str = Field(..., description="대지 주소")
    radius_m: int = Field(1000, ge=10, le=3000, description="반경(m)")
    tile_size_m: float = Field(250.0, gt=0, le=1000, description="타일 한 변(m)")


class GenerateTileRequest(BaseModel):
    bbox_4326: list[float] = Field(..., description="타일 bbox [minlon,minlat,maxlon,maxlat]")
    bbox_5186: list[float] = Field(..., description="타일 bbox EPSG:5186 [minx,miny,maxx,maxy]")
    origin_offset: list[float] = Field(..., description="tile_plan이 준 고정 offset [ox,oy]")
    layers: dict = Field(
        default_factory=lambda: {"buildings": True, "terrain": True},
        description="레이어 토글(타일 조립은 buildings/terrain만)",
    )
    floor_height_m: float = Field(config.DEFAULT_FLOOR_H_M, gt=0, description="기본 층고(m)")
    missing_floors_policy: str = Field("default", description='층수 누락: "default"|"skip"|"flag"')


def _safe_component(s: str) -> bool:
    """경로 조각이 안전한지(디렉터리 탈출 방지)."""
    return bool(re.fullmatch(r"[A-Za-z0-9._가-힣-]+", s)) and s not in (".", "..")


@app.get("/health")
def health() -> dict:
    """헬스 체크 (Cloud Run readiness)."""
    return {"ok": True, "service": "arch-site-model", "ortho_source": config.ORTHO_SOURCE}


@app.post("/api/generate")
def generate_endpoint(req: GenerateRequest) -> dict:
    """주소 → 모델 생성. `.3dm`은 다운로드 URL로, 통계·provenance·warnings 반환.

    생성물(.3dm, 정사영상 PNG)은 잡 폴더에 저장되고 `files.*`의 URL로 내려받는다.
    (.3dm 텍스처 참조가 유효하려면 PNG가 같은 폴더에 있어야 하므로 함께 서빙.)
    """
    job_id = uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id

    result = _generate(
        req.address,
        radius_m=req.radius_m,
        floor_h_m=req.floor_height_m,
        outputs=req.outputs,
        layers=req.layers,
        output_dir=str(job_dir),
        missing_floors_policy=req.missing_floors_policy,
        include_geometry=True,   # 브라우저 3D 미리보기용 지오메트리 JSON (F2)
        proposed_height_m=req.proposed_height_m,
    )

    if not result.get("ok"):
        # 생성 실패(주소 오류·건물 없음 등)는 4xx로 전달
        raise HTTPException(status_code=400, detail=result.get("error", "생성 실패"))

    # 다운로드 URL은 ASCII 종류키(3dm/ortho)로 — 한글 파일명 URL 인코딩 문제 회피.
    # 실제 파일명(한글 가능)은 다운로드 시 Content-Disposition으로 전달.
    files: dict[str, str] = {}
    out3dm = result.get("outputs", {}).get("3dm")
    if out3dm:
        files["3dm"] = f"/api/files/{job_id}/3dm"
    # 정사영상 PNG는 출력 포맷과 무관하게 생성됨 → 다운로드 URL 제공. .3dm은 Rhino가
    # 텍스처로 참조, .skp 확장은 PNG를 받아 지형에 직접 드레이프(B2).
    ortho_ready = (result.get("geometry") or {}).get("ortho_extent_m") or (
        out3dm and out3dm.get("orthophoto")
    )
    if ortho_ready:
        files["ortho_png"] = f"/api/files/{job_id}/ortho"

    return {
        "ok": True,
        "job_id": job_id,
        "files": files,
        "geometry": result.get("geometry"),  # 3D 미리보기용 (로컬 미터)
        "outputs": result.get("outputs"),
        "stats": result.get("stats"),
        "provenance": result.get("provenance"),
        "warnings": result.get("warnings"),
        "qa": result.get("qa"),   # 자동 QA findings (layers.qa=True 시)
        "trust_report": result.get("trust_report"),  # 데이터 신뢰도 리포트 (A-1)
        "shadows": result.get("shadows"),  # 일조·그림자 분석 (B-3, layers.shadows=True 시)
        "zoning": result.get("zoning"),  # 용도지역 (arch-law-graph 연동, layers.zoning=True 시)
        "setback": result.get("setback"),  # 정북일조 사선 봉투 (B-1', layers.setback=True 시)
        "skyline": result.get("skyline"),  # 스카이라인 종/횡단면 (B-2, proposed_height_m 시)
    }


@app.post("/api/tile_plan")
def tile_plan_endpoint(req: TilePlanRequest) -> dict:
    """대반경 순차조립용 계획: 주소 → 고정 origin_offset + 타일 격자 목록(지오메트리 없음).

    SketchUp 확장이 이 목록을 받아 타일마다 /api/generate_tile을 순차 호출한다.
    """
    from src.geo.geocode import GeocodeError
    from src.tiles_stream import tile_plan

    try:
        return tile_plan(req.address, radius_m=req.radius_m, tile_size_m=req.tile_size_m)
    except GeocodeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/generate_tile")
def generate_tile_endpoint(req: GenerateTileRequest) -> dict:
    """한 타일의 geometry JSON. tile_plan의 bbox·offset을 그대로 전달받아 처리한다."""
    from src.tiles_stream import generate_tile

    if len(req.bbox_4326) != 4 or len(req.bbox_5186) != 4 or len(req.origin_offset) != 2:
        raise HTTPException(
            status_code=400,
            detail="bbox_4326/bbox_5186는 4개, origin_offset은 2개여야 합니다",
        )
    result = generate_tile(
        tuple(req.bbox_4326), tuple(req.bbox_5186), tuple(req.origin_offset),
        layers=req.layers, floor_h_m=req.floor_height_m,
        missing_floors_policy=req.missing_floors_policy,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "타일 생성 실패"))
    return result


@app.get("/api/files/{job_id}/{kind}")
def get_file(job_id: str, kind: str) -> FileResponse:
    """잡 폴더의 생성물 다운로드. kind: "3dm"(모델) | "ortho"(정사영상 PNG).

    URL은 ASCII 종류키만 받는다(경로 탈출·한글 URL 문제 차단). 실제 파일은 잡
    폴더에서 확장자/접미사로 찾아 원본 파일명(한글 가능)으로 내려준다.
    """
    if not _safe_component(job_id) or kind not in ("3dm", "ortho"):
        raise HTTPException(status_code=400, detail="잘못된 요청")
    job_dir = (JOBS_DIR / job_id).resolve()
    if not str(job_dir).startswith(str(JOBS_DIR)) or not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="잡 없음")

    matches = (
        list(job_dir.glob("*_ortho.png")) if kind == "ortho"
        else [p for p in job_dir.glob("*.3dm")]
    )
    if not matches:
        raise HTTPException(status_code=404, detail="파일 없음")
    path = matches[0]
    return FileResponse(path, filename=path.name)


# --- 프론트엔드 정적 서빙 (빌드된 React) ---------------------------------------
# 반드시 모든 API 라우트 정의 이후에 마운트(루트 "/"가 API를 가리지 않도록).
# 빌드 산출물(frontend/dist)이 있을 때만 마운트 → 백엔드 단독 실행(개발/테스트)에도 무해.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
