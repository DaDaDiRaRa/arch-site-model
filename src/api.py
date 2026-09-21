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

import json
import math
import os
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import PlainTextResponse
from pydantic import BaseModel, Field, model_validator

from src import config
from src.pipeline import generate as _generate
from src.server import mcp as _mcp

# 생성물 저장 루트(잡별 하위 폴더). 기본은 OS 임시 폴더 — Cloud Run 컨테이너 파일시스템은
# 읽기전용일 수 있으나 /tmp는 항상 쓰기 가능. JOBS_DIR 환경변수로 재정의 가능.
JOBS_DIR = Path(
    os.environ.get("JOBS_DIR") or (Path(tempfile.gettempdir()) / "arch_site_model_jobs")
).resolve()

# 잡 산출물 공유 저장소(GCS 버킷명). Cloud Run은 인스턴스가 여러 개라 생성(POST)과 다운로드(GET)가
# 다른 인스턴스로 갈 수 있고, 그 인스턴스의 /tmp엔 잡이 없어 "잡 없음" 404가 났다(2026-09-21 로그 실측).
# 설정 시 생성 직후 잡 파일을 gs://<버킷>/<job_id>/에 올리고, 로컬에 없으면 버킷에서 받아 서빙한다.
# 버킷은 비공개 + 수명주기 1일 삭제. 미설정(로컬 개발)이면 로컬 폴더만 쓴다.
JOBS_GCS_BUCKET = os.environ.get("JOBS_GCS_BUCKET", "")


def _gcs_bucket():
    from google.cloud import storage  # 지연 import — 로컬/테스트엔 불필요

    return storage.Client().bucket(JOBS_GCS_BUCKET)


def _upload_job(job_id: str, job_dir: Path) -> None:
    """잡 폴더의 다운로드 대상 파일(.3dm, *_ortho.png)을 공유 버킷에 올린다. 실패는 경고만."""
    if not JOBS_GCS_BUCKET or not job_dir.is_dir():
        return
    try:
        bucket = _gcs_bucket()
        for p in [*job_dir.glob("*.3dm"), *job_dir.glob("*_ortho.png"), *job_dir.glob("*_package.zip")]:
            bucket.blob(f"{job_id}/{p.name}").upload_from_filename(str(p))
    except Exception as e:  # noqa: BLE001 — 업로드 실패해도 같은 인스턴스 다운로드는 동작
        print(f"[jobs] GCS 업로드 실패 {job_id}: {config.scrub_secrets(str(e))}")


def _fetch_job(job_id: str, job_dir: Path) -> None:
    """로컬에 없는 잡을 공유 버킷에서 job_dir로 내려받는다(없으면 아무것도 안 함)."""
    if not JOBS_GCS_BUCKET:
        return
    try:
        blobs = list(_gcs_bucket().list_blobs(prefix=f"{job_id}/"))
        if not blobs:
            return
        job_dir.mkdir(parents=True, exist_ok=True)
        for b in blobs:
            name = b.name.split("/", 1)[1]
            if name and _safe_component(name):
                b.download_to_filename(str(job_dir / name))
    except Exception as e:  # noqa: BLE001
        print(f"[jobs] GCS 다운로드 실패 {job_id}: {config.scrub_secrets(str(e))}")


# 타일 bbox span(m) 상한 — 미인증 /api/generate_tile 자원증폭 방지(정상 타일 ≤ tile_size + margin).
_MAX_TILE_SPAN_M = 3000.0
# 지도 영역 지정(/api/generate bbox_4326) 한 변 상한(m) — 반경 상한 2000m의 지름.
_MAX_AREA_SIDE_M = 4000.0


def _json_streaming(payload: dict) -> StreamingResponse:
    """dict를 청크 스트리밍 JSON으로 응답 — Cloud Run의 32MiB **비스트리밍** 응답 상한 우회.

    도로를 켜면 통합표면(지형+도로+보도+차선) geometry가 반경 500만 돼도 32MB를 넘겨,
    앱이 200을 내도 Cloud Run이 "Response size too large"로 잘라 클라이언트엔 500이 갔다.
    스트리밍(chunked transfer)은 이 상한이 적용되지 않으므로 대반경 단일 응답을 그대로 보낸다.
    (전체 body를 메모리에 만들지만 4Gi로 충분 — 목적은 오직 chunked 전송.)
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _gen():
        for i in range(0, len(body), 1 << 20):  # 1MB 청크
            yield body[i:i + (1 << 20)]

    return StreamingResponse(_gen(), media_type="application/json")


def _sweep_old_jobs(ttl_seconds: float = 7200.0) -> None:
    """오래된 잡 폴더 삭제(디스크 누적/DoS 방지). best-effort — 실패 무시."""
    import shutil
    import time

    try:
        now = time.time()
        for d in JOBS_DIR.iterdir():
            try:
                if d.is_dir() and now - d.stat().st_mtime > ttl_seconds:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass

# --- MCP (/mcp) --------------------------------------------------------------
# src/server.py 의 FastMCP 인스턴스를 이 앱에 얹는다. arch-law-graph 와 달리 이 앱은
# mcp 가 requirements.txt 에 이미 fastapi 와 같은 venv 로 있어(핀 충돌 없음) 별도 서비스가
# 아니라 여기 mount 로 끝난다.
#
# streamable_http_app() 이 만드는 하위 Starlette 앱은 자기 lifespan(session_manager.run())을
# 들고 있는데, Starlette 의 lifespan 이벤트는 mount 된 하위 앱까지 전파되지 않는다
# (mcp 소스 mcp/server/fastmcp/server.py 의 session_manager 프로퍼티 docstring 이 이 결합을
#  "advanced use case" 로 명시). 그래서 이 앱의 lifespan 에서 명시적으로 돌려준다.
_mcp_asgi_app = _mcp.streamable_http_app()  # session_manager 를 여기서 지연 생성한다


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    async with _mcp.session_manager.run():
        yield


# 나머지 REST API(/api/*, /health)는 지금처럼 공개로 둔다 — 여기서 막는 건 /mcp 뿐이다.
# 법령처럼 공개 정보가 아니라 지형·건물 3D **생성 연산**이라 값싼 공개는 안 된다
# (판단 근거: kunwon-ops 저장소 docs/plan-mcp-gateway.md §7).
# 키가 아예 안 설정돼 있으면(로컬 개발 등) fail-closed — 조용히 공개로 새지 않는다.
_MCP_SHARED_KEY = os.environ.get("ARCH_SITE_MODEL_MCP_KEY")


class _McpAuthMiddleware:
    """`/mcp` 전용 인증. Bearer 토큰이 ARCH_SITE_MODEL_MCP_KEY 와 일치해야 통과."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        token = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {_MCP_SHARED_KEY}" if _MCP_SHARED_KEY else None
        if not expected or token != expected:
            resp = PlainTextResponse("Unauthorized", status_code=401)
            await resp(scope, receive, send)
            return
        await self._app(scope, receive, send)


app = FastAPI(
    title="arch-site-model API",
    version="1.0",
    description="주소 → 지형·건물 3D 대지모델 + 정사영상 텍스처 (.3dm / SketchUp 확장용 데이터).",
    lifespan=_lifespan,
)



class GenerateRequest(BaseModel):
    address: str = Field("", description="대지 주소 (bbox_4326 지정 시 라벨용·생략 가능)")
    radius_m: int = Field(250, ge=10, le=2000, description="반경(m)")
    bbox_4326: list[float] | None = Field(
        None, description="지도에서 고른 영역 [minlon,minlat,maxlon,maxlat]. 지정 시 주소·반경 대신 사용",
    )
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

    @model_validator(mode="after")
    def _address_or_area(self):
        if self.bbox_4326 is None:
            if not self.address.strip():
                raise ValueError("address 또는 bbox_4326 중 하나는 필요합니다")
            return self
        b = self.bbox_4326
        if len(b) != 4 or any(not math.isfinite(v) for v in b):
            raise ValueError("bbox_4326은 유한한 숫자 4개 [minlon,minlat,maxlon,maxlat]")
        if not (124.0 <= b[0] < b[2] <= 132.0 and 33.0 <= b[1] < b[3] <= 39.0):
            raise ValueError("bbox_4326이 한국 범위 밖이거나 min/max 순서가 틀렸습니다")
        # 한 변 ≤ _MAX_AREA_SIDE_M (반경 상한 2000m의 지름) — 미인증 자원증폭 방지
        from src.pipeline import _bbox_4326_to_5186

        x0, y0, x1, y1 = _bbox_4326_to_5186(tuple(b))
        if max(x1 - x0, y1 - y0) > _MAX_AREA_SIDE_M or min(x1 - x0, y1 - y0) < 20:
            raise ValueError(f"영역 한 변은 20m 이상 {_MAX_AREA_SIDE_M:.0f}m 이하여야 합니다")
        return self


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
    _sweep_old_jobs()  # 오래된 잡 폴더 정리(디스크 누적 방지)

    result = _generate(
        req.address,
        radius_m=req.radius_m,
        floor_h_m=req.floor_height_m,
        outputs=req.outputs,
        layers=req.layers,
        output_dir=str(job_dir),
        missing_floors_policy=req.missing_floors_policy,
        include_geometry=True,   # 브라우저 3D 미리보기용 지오메트리 JSON (F2)
        bbox_4326=tuple(req.bbox_4326) if req.bbox_4326 else None,
    )

    if not result.get("ok"):
        # 생성 실패(주소 오류·건물 없음 등)는 4xx로 전달. 시크릿 마스킹.
        raise HTTPException(status_code=400, detail=config.scrub_secrets(result.get("error", "생성 실패")))

    _upload_job(job_id, job_dir)  # 다른 인스턴스에서도 다운로드되도록 공유 버킷에 보관

    # 다운로드 URL은 ASCII 종류키(3dm/ortho)로 — 한글 파일명 URL 인코딩 문제 회피.
    # 실제 파일명(한글 가능)은 다운로드 시 Content-Disposition으로 전달.
    files: dict[str, str] = {}
    out3dm = result.get("outputs", {}).get("3dm")
    if out3dm:
        files["3dm"] = f"/api/files/{job_id}/3dm"
    if (result.get("outputs") or {}).get("dae"):
        # SketchUp·Rhino 패키지 zip(.dae + .3dm + 정사영상 + 좌표 안내문)
        files["package"] = f"/api/files/{job_id}/package"
    # 정사영상 PNG는 출력 포맷과 무관하게 생성됨 → 다운로드 URL 제공. .3dm은 Rhino가
    # 텍스처로 참조, .skp 확장은 PNG를 받아 지형에 직접 드레이프(B2).
    ortho_ready = (result.get("geometry") or {}).get("ortho_extent_m") or (
        out3dm and out3dm.get("orthophoto")
    )
    if ortho_ready:
        files["ortho_png"] = f"/api/files/{job_id}/ortho"

    # 스트리밍으로 응답 — 도로 켠 대반경 geometry가 Cloud Run 32MiB 비스트리밍 상한을 넘겨
    # 500 나던 문제 우회(_json_streaming 참조). 소반경 응답도 동일 경로(무해).
    return _json_streaming({
        "ok": True,
        "job_id": job_id,
        "files": files,
        "geometry": result.get("geometry"),  # 3D 미리보기용 (로컬 미터)
        "outputs": result.get("outputs"),
        "stats": result.get("stats"),
        "provenance": result.get("provenance"),
        "warnings": [config.scrub_secrets(w) for w in (result.get("warnings") or [])],
        "qa": result.get("qa"),   # 자동 QA findings (layers.qa=True 시)
        "trust_report": result.get("trust_report"),  # 데이터 신뢰도 리포트 (A-1)
        "zoning": result.get("zoning"),  # 용도지역 (arch-law-graph 연동, layers.zoning=True 시)
    })


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
    import math

    vals = (*req.bbox_4326, *req.bbox_5186, *req.origin_offset)
    if any(not math.isfinite(v) for v in vals):
        raise HTTPException(status_code=400, detail="bbox 값이 유효하지 않습니다")
    # 5186 span(m) 상한 — 미인증 자원증폭 방지(거대 bbox로 VWorld/DEM 남용 차단).
    sx = req.bbox_5186[2] - req.bbox_5186[0]
    sy = req.bbox_5186[3] - req.bbox_5186[1]
    if not (0 < sx <= _MAX_TILE_SPAN_M and 0 < sy <= _MAX_TILE_SPAN_M):
        raise HTTPException(
            status_code=400,
            detail=f"타일 bbox span 허용 범위 초과 (한 변 ≤ {_MAX_TILE_SPAN_M:.0f}m)",
        )
    result = generate_tile(
        tuple(req.bbox_4326), tuple(req.bbox_5186), tuple(req.origin_offset),
        layers=req.layers, floor_h_m=req.floor_height_m,
        missing_floors_policy=req.missing_floors_policy,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "타일 생성 실패"))
    return result


@app.get("/api/geocode")
def geocode_endpoint(address: str) -> dict:
    """주소 → 좌표. 웹 지도가 검색한 주소로 이동할 때 쓴다(VWorld 키는 서버에만)."""
    from src.geo.geocode import GeocodeError, clean_address, geocode

    cleaned = clean_address(address)
    try:
        c = geocode(cleaned)
    except GeocodeError as e:
        raise HTTPException(status_code=404, detail=config.scrub_secrets(str(e)))
    return {"address": cleaned, "lon": c["lon"], "lat": c["lat"]}


# 배경지도 타일 중계 — VWorld WMTS를 서버 키로 받아 넘긴다(키를 브라우저에 싣지 않고, 키 등록
# 도메인 제약도 서버 쪽 한 곳으로). 지도 영역 선택 UI 전용이라 레이어·줌을 좁게 허용한다.
_BASEMAP_LAYERS = {"base": ("Base", "png"), "satellite": ("Satellite", "jpeg"), "hybrid": ("Hybrid", "png")}


@app.get("/api/basemap/{layer}/{z}/{x}/{y}")
def basemap_tile(layer: str, z: int, x: int, y: int):
    from fastapi.responses import Response

    if layer not in _BASEMAP_LAYERS or not (6 <= z <= 19) or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise HTTPException(status_code=400, detail="잘못된 타일 요청")
    if not config.VWORLD_KEY:
        raise HTTPException(status_code=503, detail="VWORLD 키 없음")
    name, ext = _BASEMAP_LAYERS[layer]
    from src.geo.ortho import _default_fetch

    url = f"https://api.vworld.kr/req/wmts/1.0.0/{config.VWORLD_KEY}/{name}/{z}/{y}/{x}.{ext}"
    data = _default_fetch(url)
    if not data:
        raise HTTPException(status_code=404, detail="타일 없음")
    media = "image/jpeg" if ext == "jpeg" else "image/png"
    return Response(content=data, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/extension.rbz")
def extension_rbz(request: Request):
    """SketchUp 확장(.rbz)을 현재 소스로 즉석 패키징해 내려준다. 백엔드 주소는 이 사이트 주소.

    팀원이 설명서(/guide.html)에서 바로 받아 확장 관리자로 설치한다 — 빌드 산출물을 따로 돌리지 않아
    항상 서버와 같은 버전이다. Cloud Run은 TLS를 앞단에서 끊으므로 x-forwarded-proto로 https를 복원.
    """
    import importlib.util

    from fastapi.responses import Response

    src = Path(__file__).resolve().parent.parent / "sketchup_ext" / "build_rbz.py"
    if not src.exists():
        raise HTTPException(status_code=404, detail="확장 소스 없음")
    spec = importlib.util.spec_from_file_location("_asm_build_rbz", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    base = os.environ.get("PUBLIC_BASE_URL") or f"{proto}://{request.headers.get('host', request.url.netloc)}"
    data = mod.build_bytes(base.rstrip("/"))
    return Response(
        content=data, media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="arch_site_model.rbz"'},
    )


@app.get("/api/files/{job_id}/{kind}")
def get_file(job_id: str, kind: str) -> FileResponse:
    """잡 폴더의 생성물 다운로드. kind: "3dm"(모델) | "ortho"(정사영상 PNG).

    URL은 ASCII 종류키만 받는다(경로 탈출·한글 URL 문제 차단). 실제 파일은 잡
    폴더에서 확장자/접미사로 찾아 원본 파일명(한글 가능)으로 내려준다.
    """
    if not _safe_component(job_id) or kind not in ("3dm", "ortho", "package"):
        raise HTTPException(status_code=400, detail="잘못된 요청")
    job_dir = (JOBS_DIR / job_id).resolve()
    try:
        job_dir.relative_to(JOBS_DIR)  # 경로 탈출 방지(startswith prefix 매칭 아님)
    except ValueError:
        raise HTTPException(status_code=404, detail="잡 없음")
    if not job_dir.is_dir():
        _fetch_job(job_id, job_dir)  # 생성이 다른 인스턴스에서 됐으면 공유 버킷에서 가져옴
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="잡 없음")

    pattern = {"ortho": "*_ortho.png", "package": "*_package.zip", "3dm": "*.3dm"}[kind]
    matches = list(job_dir.glob(pattern))
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

# --- MCP 마운트 (라우팅 이후, 최종 wrap) -----------------------------------------
# Starlette Mount 는 "/mcp/{나머지}" 정규식이라 트레일링 슬래시 없는 "/mcp" 자체는
# 못 잡는다(실측: 404). 그런데 위 "/" 프론트엔드 캐치올은 "/{나머지}" 라서 "/mcp" 를
# 정확히 잡아버리고 존재하지 않는 파일로 처리해 역시 404 를 낸다 — Mount 조합만으로는
# "/mcp"(슬래시 없음) 가 항상 캐치올에 새 버린다.
# 그래서 라우팅 전 단계(순수 ASGI 래퍼)에서 프리픽스를 직접 잘라 우회한다.
class _McpMount:
    """"/mcp" 와 "/mcp/*" 를 FastMCP 로 보낸다. 나머지 전부(REST API·정적 파일)는
    그대로 FastAPI 앱에 넘긴다."""

    def __init__(self, inner_app, mcp_app, prefix="/mcp"):
        self._app = inner_app
        self._mcp_app = mcp_app
        self._prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            if path == self._prefix or path.startswith(self._prefix + "/"):
                sub_scope = dict(scope)
                sub_scope["path"] = path[len(self._prefix):] or "/"
                await self._mcp_app(sub_scope, receive, send)
                return
        await self._app(scope, receive, send)


# uvicorn 은 이 모듈의 "app" 심볼을 그대로 가져간다(Dockerfile CMD 참조) — FastAPI
# lifespan(= _mcp.session_manager.run())은 그대로 트리거된다: 이 래퍼는 http 요청만
# 가로채고 lifespan/websocket 스코프는 손대지 않고 그대로 넘긴다.
app = _McpMount(app, _McpAuthMiddleware(_mcp_asgi_app))
