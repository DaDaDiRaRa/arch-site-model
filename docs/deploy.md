# 배포 가이드 — 백엔드(FastAPI) + SketchUp 확장

주소 → 3D 대지모델(지형·건물·정사영상) 을 팀이 쓰도록 배포하는 구조.

```
[팀원 PC: SketchUp + .rbz 확장]  ──HTTP──▶  [백엔드: FastAPI 도커]
   └ 주소 입력 → .skp 조립 + 드레이프          └ 엔진 + VWorld/NGII 키 + .3dm 생성
[Rhino 사용자]  ──HTTP──▶  백엔드에서 텍스처 .3dm 다운로드 (확장 불필요)
```

- **백엔드**: 무거운 파이썬 스택(GDAL/rasterio 등)을 **한 곳에 중앙화**. 팀원은 설치 불필요.
- **SketchUp 확장**: 가벼운 Ruby(HTTP 호출) — 각자 PC에 설치. `.skp` 텍스처는 데스크톱에서만 가능.

---

## 1. 로컬 실행

**백엔드만** (API + 문서):
```powershell
.venv\Scripts\Activate.ps1
uvicorn src.api:app --reload --port 8000
# http://localhost:8000/docs  (Swagger UI)
```

**프론트엔드 개발 서버** (React, 백엔드로 프록시):
```powershell
cd frontend
npm install        # 최초 1회
npm run dev        # http://localhost:5173 (/api 는 :8000 백엔드로 프록시)
```

**프론트 빌드 후 백엔드가 통합 서빙** (프로덕션과 동일):
```powershell
cd frontend; npm run build      # → frontend/dist
cd ..; uvicorn src.api:app --port 8000
# http://localhost:8000  (프론트) · /docs (API)
```
FastAPI는 `frontend/dist`가 있으면 루트에서 자동 서빙(없으면 API 전용).

API:
- `GET  /health` — 헬스 체크
- `POST /api/generate` — 주소 → 모델. `.3dm`·정사영상 PNG 다운로드 URL 반환
- `GET  /api/files/{job_id}/3dm` — 텍스처 `.3dm` 다운로드
- `GET  /api/files/{job_id}/ortho` — 정사영상 PNG 다운로드

요청 예:
```json
POST /api/generate
{
  "address": "대전광역시 서구 괴정동 358",
  "radius_m": 250,
  "layers": {"buildings": true, "terrain": true, "orthophoto": true},
  "outputs": ["3dm"]
}
```

---

## 2. 도커 빌드/실행

```powershell
docker build -t arch-site-model .
docker run -p 8080:8080 --env-file .env arch-site-model
# http://localhost:8080/docs
```

- `.env`(VWORLD_KEY 등)는 **이미지에 넣지 않음** — 런타임 주입(`--env-file` 또는 `-e`).
- `geo_store/`의 **manifest(`manifest.json`·`road_manifest.json`)만 git 추적 → 이미지에 포함**.
  실제 데이터(DEM `dem_*.tif`·도로 `roads_*.geojson`)는 gitignore라 이미지에 없음 → **GCS에서 서빙**
  (§5). 이미지엔 데이터 0바이트.

---

## 3. 자동 배포 (GitHub → Cloud Run, 다른 앱과 동일)

`.github/workflows/deploy.yml` — **main에 push하면 자동으로**: pytest 실행 → 통과 시
`google-github-actions/deploy-cloudrun@v2`로 `source: .` 배포(리전 `asia-northeast3`).
PR에서는 테스트만 돌고 배포는 안 됨.

**최초 1회 GCP 셋업 (네가):**
1. GitHub 레포 `arch-site-model` → Settings → Secrets → **`GCP_SA_KEY`** 추가
   (다른 앱과 동일한 서비스계정 JSON 키. 해당 SA에 Cloud Run 배포 권한 필요).
2. 최초 배포 후 서비스에 **키 연결**(안 하면 앱은 뜨지만 VWorld 호출 실패).
   `VWORLD_KEY` 시크릿은 프로젝트 `arch-diagnose`에 **이미 존재** → 새로 만들 필요 없이 연결만:
   ```bash
   gcloud run services update arch-site-model --region asia-northeast3 \
     --update-env-vars ORTHO_SOURCE=vworld \
     --update-secrets VWORLD_KEY=VWORLD_KEY:latest
   ```
   이후 배포는 이 설정을 보존한다. (`--update-*`는 기존 env/secret을 보존하며 추가/수정 —
   `--set-*`는 전체 교체라 다른 값이 날아가니 주의.)

→ 이후로는 **커밋·push만 하면 자동 배포**. 아래는 수동 배포 참고.

## 4. GCP Cloud Run 수동 배포

```bash
# 1) 이미지 빌드·푸시 (Artifact Registry)
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT/REPO/arch-site-model

# 2) 배포 (키는 --set-env-vars 또는 Secret Manager)
gcloud run deploy arch-site-model \
  --image REGION-docker.pkg.dev/PROJECT/REPO/arch-site-model \
  --region REGION \
  --set-env-vars "VWORLD_KEY=...,ORTHO_SOURCE=vworld" \
  --allow-unauthenticated       # ← 지금은 오픈. 인증은 아래 참고
```

**콜드 스타트**: GDAL 이미지가 커서 scale-to-zero면 첫 요청이 몇 초 느림.
즉답이 필요하면 `--min-instances=1`(소액 상시비용).

**키 보안**: 운영은 `--set-env-vars` 대신 Secret Manager 권장:
```bash
gcloud run deploy ... --set-secrets "VWORLD_KEY=vworld-key:latest"
```

---

## 5. 데이터 서빙 (DEM·도로 = 공개 GCS)

DEM(`dem_*.tif`)·도로(`roads_*.geojson`)는 용량이 커 **gitignore** → 이미지에 없다. 공개 GCS
버킷에 올리고 환경변수로 위치를 알려주면, 앱이 원격에서 읽는다(manifest만 이미지에 포함). 지역
추가 = 굽기 → 업로드 → manifest 커밋(엔진 무수정).

**DEM**(GDAL `/vsicurl` 윈도우 읽기):
```bash
# 굽기: contour_bake → dem_to_cog(COG 변환) 후
gcloud storage cp geo_store/dem_*.tif gs://arch-site-model-dem/dem/
```
```bash
gcloud run services update arch-site-model --region asia-northeast3 \
  --update-env-vars DEM_TILE_BASE=/vsicurl/https://storage.googleapis.com/arch-site-model-dem/dem
```

**도로**(json+shapely라 GDAL 아님 → 앱이 HTTP로 fetch+캐시):
```bash
# 굽기: python -m src.terrain.road_bake <SHP폴더> --out geo_store/roads_<지역>.geojson --region "<지역>"
#   (경계 폴리곤 없는 소로·골목은 실측 도로폭으로 자동 합성 — synthesize_gap_roads)
gcloud storage cp geo_store/roads_*.geojson gs://arch-site-model-dem/roads/
```
```bash
gcloud run services update arch-site-model --region asia-northeast3 \
  --update-env-vars ROAD_BASE=gs://arch-site-model-dem/roads
#   ROAD_BASE는 gs://…를 https://storage.googleapis.com/…로 변환해 읽는다(공개 버킷 필요).
```

- **⚠️ `--update-env-vars`를 쓸 것 (`--set-env-vars` 아님)**: `--set-env-vars`는 **기존 env를 전부
  교체(삭제)**한다 → `DEM_TILE_BASE`·`ORTHO_SOURCE` 등이 날아가 DEM 서빙이 깨진다. `--update-env-vars`는
  지정한 것만 추가/수정하고 나머지는 보존한다. (secret으로 붙인 `VWORLD_KEY`는 env-vars 변경에 영향 없음.)
- **버킷 공개 읽기 필수**: 객체가 공개(allUsers:objectViewer)여야 `/vsicurl`·HTTP fetch가 된다.
- **manifest는 git 추적**(`manifest.json`·`road_manifest.json`) → 커밋하면 배포 이미지에 들어간다.
  데이터 파일명이 manifest와 일치해야 조회된다.
- 미설정 시(로컬 개발) `DEM_TILE_BASE`/`ROAD_BASE`는 로컬 `geo_store` 기본 → 로컬 파일 사용.
- **도로 미설정 증상**: 클라우드에서 지형·건물은 나오는데 도로만 조용히 생략 → `ROAD_BASE` 확인.

---

## 4. 인증 (지금은 생략, 나중에 추가 — 앱 코드 무관)

현재 `--allow-unauthenticated`(오픈). **주의**: 오픈 엔드포인트는 누구나 호출 →
VWorld/NGII 키 쿼터 소진·차단 위험. 나중에 아래 중 하나로:

- **IAP** (권장, 팀 전용): Cloud Run 앞에 로드밸런서 + IAP → 조직 구글계정만 접근. 앱 무수정.
- **공유 토큰**(가벼움): 확장이 헤더에 비밀 토큰 → 백엔드 미들웨어에서 검증(~10줄 추가).
- **IAM invoker**: `--no-allow-unauthenticated` + 호출자에 `roles/run.invoker`.

---

## 5. 정사영상 소스 — VWorld → NGII 전환

배포 시 `.env`/`--set-env-vars`만 바꾸면 됨(코드 무수정):
```
ORTHO_SOURCE=ngii
NGII_KEY=<data.go.kr 발급키>
```
NGII(공공누리 1유형)는 출처표시 시 상업 이용 허용 → 배포판 권장.
착수 전 NGII WMTS GetCapabilities로 타일격자(3857/5179)·레이어명 확정 필요.

---

## 6. SketchUp 확장(.rbz) — Phase B (진행 예정)

`.skp` 텍스처는 데스크톱 SketchUp에서만 가능하므로 확장으로 배포.
확장이 `POST /api/generate`(또는 지오메트리 전용 엔드포인트) 호출 → 로컬에서 조립+드레이프.
상세 설계: `docs/orthophoto_texture_plan.md`.
