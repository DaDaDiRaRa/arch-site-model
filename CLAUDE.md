# arch-site-model — Claude Code 인계 문서

> 대지 주소 입력만으로 주변 지형·건물 3D 대지모델을 자동 생성하는 MCP 서버 + 배포 웹앱.
> KBS TopoMap의 수동 워크플로우를 "주소 딸깍 + API 취득"으로 대체.

---

## 다음 작업 (TODO)

> 완료하면 해당 줄을 삭제한다(항상 "남은 일"만 남게 유지).

- [ ] **전국 5m DEM 확장**: 목표 = 전국 어디 주소든 지형+건물 자동 생성. **자동/API 취득은 불가 확인
      (2026-07-02)** — 실시간 표고 API 없음(VWorld 3D Data API 2019 폐쇄), VWorld WFS 169레이어에
      등고선·표고점 없음, 무료 공개DEM은 90m뿐(대지모델엔 거침, EPSG:5179). 따라서 5m는 여전히
      **지역별 1:5,000 수치지형도 SHP 수동 다운로드 → `contour_bake`로 5m DEM(EPSG:5186) 굽기 →
      `geo_store/`+manifest**가 현실적 경로(건물·지적은 VWorld 실시간이라 이미 전국). 다중 타일 경계
      질의 대응 완료(`find_tiles`+`clip_dem_mosaic`, 2026-07-02). 배치 베이크 헬퍼 완성
      (`contour_bake.bake_tiled` + CLI `--tile-km`/`--margin-m`: 등고선 1회 읽고 타일별 서브셋
      보간, margin 여유·전역 격자 픽셀정합). **대전 전역 13타일(10km, ~115MB) 베이크·mosaic
      연속성 검증 완료(2026-07-02).** **남은 것: (a) 저장 변곡점 — geo_store는 현재 git 추적
      →Cloud Build 이미지 인데, 전국(~10GB)은 git/이미지 불가 → GCS COG + `/vsigs` 윈도우 읽기로
      이전 필요(clip_dem 경로만 로컬→`/vsigs`; 사용자 PC엔 DEM 0바이트), (b) 추가 지역 SHP 확보
      (사람 손 — 시·도 단위 연속수치지형도로 받으면 클릭 수 절감).** 상세 [[nationwide-dem-ngii-source]].
- [ ] **Phase B — SketchUp 확장(.rbz)**: `sketchup_ext/`. **B1(지형+건물, 텍스처 없음) 코드 완성**
      — 확장이 `/api/generate`(geometry JSON) 호출 → 데스크톱 SketchUp에서 지형 mesh+건물 돌출 조립.
      백엔드 계약은 실요청으로 검증(2026-07-02). **남은 것: (a) 사용자 SketchUp 2021+ 설치·실행
      테스트**(데스크톱 GUI라 무인검증 불가 — 작성→테스트 루프), **(b) B2 정사영상 텍스처 드레이프**
      (`layers.orthophoto=true`+`outputs=["3dm"]`→PNG 다운로드→`Face#position_material` 평면투영).
      상세 `docs/sketchup_extension.md`, 텍스처 설계 `docs/orthophoto_texture_plan.md` §5.
- [ ] **F2 뷰어 색상·표현 개선(폴리시)**: 현재 `Viewer3D.tsx`는 건물=단색 steel blue(미확인=주황),
      지형=정사영상/올리브. 기능은 동작 — 표현만 개선 여지: 층수별 색/높이 그라디언트, 반투명·와이어,
      건물 외곽선(edges), 그림자·AO, 지적 경계 표시, 배경/조명 튜닝 등. 우선순위 낮음(동작 우선).
- [ ] **지형 계단현상 — 격자 솔버로 완전제거(선택)**: 1차 개선 완료(guarded CloughTocher,
      quant 25.2→22.3%·flat 55.2→48.5%, 무인공물·봉우리 보존 — 2026-07-02 승격). 남은 건 **부분 개선**
      한계 돌파: 라플라스 harmonic 인필 또는 ANUDEM류 반복 격자 솔버(등고선 셀 고정 → 최대원리로
      오버슈트 없이 사이 매끔). 구현·런타임 비용 큼. 필요성 낮으면 skip 가능. `contour_bake.py`.
- [ ] **NGII 정사영상 소스**(보류): 서버사이드 접근 막힘(브라우저 전용 키 정황) + EPSG:5179 타일
      구현 필요. 규격은 [[orthophoto-texture-blocker]] 메모리에 저장됨. 키 서버사이드 접근이
      풀리면 5179 `TileSource` 추가만 하면 됨.
- [ ] **이격면(setback) 실연동**(블로커): arch-law-diagnose가 좁은 API 계약 노출할 때까지 보류.

---

## 프로젝트 목적

```text
입력:  대지 주소 + 반경(m)
처리:  주소→좌표(VWorld) + 건물/지적 API + 로컬 DEM 비축 → 좌표·층수·지형 계산
출력:  .skp (SketchUp MCP, 네이티브) + .3dm (rhino3dm, Phase 4)
```

**핵심 원칙**: gro_flo_co(실제 층수)로 직접 돌출 — AI 추정 0%, "재현이지 상상이 아님".

---

## 환경 설정

```powershell
# 1. 가상환경 활성화
.venv\Scripts\Activate.ps1

# 2. 환경변수 (.env 작성)
Copy-Item .env.example .env
# .env에 VWORLD_TEST_KEY / VWORLD_KEY / VWORLD_DOMAIN 채우기
# VWorld 키 발급: https://www.vworld.kr

# 3. MCP 서버 실행 (Claude 연동)
python -m src.server

# 4. 테스트 실행
python -m pytest tests/ -v
```

**웹앱 실행 (배포용 FastAPI 백엔드 + React 프론트):**

```powershell
# 백엔드만 (API + Swagger 문서)
uvicorn src.api:app --reload --port 8000       # http://localhost:8000/docs

# 프론트엔드 개발 서버 (React, /api → :8000 프록시)
cd frontend; npm install; npm run dev          # http://localhost:5173

# 프론트 빌드 → 백엔드 통합 서빙 (프로덕션과 동일)
cd frontend; npm run build; cd ..
uvicorn src.api:app --port 8000                # http://localhost:8000 (프론트+API)
```

FastAPI는 `frontend/dist`가 있으면 루트에서 자동 서빙(없으면 API 전용). 도커/Cloud Run
배포·인증·정사영상 소스(VWorld↔NGII) 전환은 **`docs/deploy.md`** 참조 — main push 시
GitHub Actions(`.github/workflows/deploy.yml`)로 pytest 통과 후 자동 배포. 배포 아키텍처
개요는 [[deployment-architecture]] 메모리.

**브라우저 3D 미리보기(F2)**: `/api/generate` 응답에 `geometry`(로컬 미터: 건물 footprint/
base_z/height/flagged + 지형 vertices/triangles + ortho_extent)가 포함되고, `Viewer3D.tsx`가
three.js로 지형 mesh+건물 돌출을 렌더(+정사영상 평면 드레이프). `pipeline.generate(include_geometry=True)`
일 때만 직렬화(MCP 응답 비대화 방지 — 기본 False). rhino3dm/WASM 미사용(생성 Extrusion에 렌더
메시가 없어 3DMLoader가 건물을 못 그림 → geometry JSON 직접 렌더로 결정).

**`src/config.py` 주요 설정값:**

- `VWORLD_KEY`: `VWORLD_TEST_KEY` 우선, 없으면 `VWORLD_KEY`
- `M2I = 39.3701`: 미터→인치 (SketchUp MCP는 인치 단위)
- `DEFAULT_FLOOR_H_M = 3.0`: 기본 층고
- `ORTHO_SOURCE = "vworld"`: 정사영상 소스 (`"vworld"` 기본, `VWORLD_KEY` 재사용 | `"ngii"` 공공누리) — [[orthophoto-texture-blocker]]
- `NGII_KEY = ""`: NGII 정사영상 키 (발급 후 `ORTHO_SOURCE=ngii`와 함께 사용)
- `ORTHO_ZOOM = 18`: 정사영상 WMTS 줌 레벨
- `GEO_STORE = Path("geo_store")`: DEM 비축 디렉터리

---

## 모듈 구조

```text
src/
  server.py              FastMCP 서버 진입점 (MCP 도구 4개 등록, Claude 연동)
  api.py                 FastAPI 백엔드 (배포용 HTTP API — /api/generate, 파일 다운로드, frontend/dist 서빙)
  pipeline.py            generate_site_model 파이프라인
  tiles.py               generate_site_tiles — 대량건물 타일분할 (백로그5)
  site_check.py          check_site_data 핵심 로직
  preview.py             preview_site 핵심 로직
  config.py              전역 설정·환경변수
  geo/
    geocode.py           주소 → 좌표 (VWorld address API)
    bbox.py              반경 → bbox (EPSG:4326)
    crs.py               EPSG:4326 ↔ 5186 변환 + origin_offset
    vworld.py            VWorld data API 클라이언트 (페이지네이션 내장)
    ortho.py             정사영상 WMTS 타일수학 + TileSource + 모자이크(재투영→PNG, Tier 1)
  geometry/
    building.py          LT_C_SPBD features → BuildingSolid (쿼드 솔리드, 홀 포함)
    terrain_mesh.py      DEMPatch → TerrainMesh (TIN 삼각망, Phase 3B)
    seating.py           BuildingSolid + DEMPatch → base_z 앉힘 (Phase 3B)
    cadastral.py         LP_PA_CBND_BUBUN features → CadastralParcel (Phase 5)
  output/
    skp_mcp.py           BuildingSolid(+TerrainMesh+CadastralParcel) → SketchUp MCP 코드 문자열
    rhino.py             BuildingSolid(+TerrainMesh+CadastralParcel) → .3dm (Phase 4)
  terrain/
    store.py             manifest.json 조회 (find_tiles 겹치는 타일 전부·고해상도 우선 / find_tile 대표 1개)
    contour_bake.py      수치지형도 등고선 SHP → DEM(.tif) 오프라인 굽기 (Phase 3A)
    dem.py               DEM 타일 클립 + 표고 보간 (Phase 3B) + clip_dem_mosaic(다중 타일 rasterio.merge 병합)

geo_store/
  manifest.json          비축 DEM 타일 목록
  dem_*.tif              GeoTIFF DEM 파일 (EPSG:5186)

frontend/                React + Vite + Tailwind 웹 UI (주소 입력 → /api/generate 호출 → .3dm/정사영상 다운로드)
  src/App.tsx            메인 폼·결과 화면
  src/Viewer3D.tsx       브라우저 3D 미리보기 (three.js — geometry JSON을 지형 mesh+건물 돌출로 렌더, 정사영상 드레이프) [F2]
  dist/                  빌드 산출물 (FastAPI가 루트에서 서빙)

sketchup_ext/            SketchUp 확장(.rbz) — 주소→백엔드 geometry JSON→SketchUp 조립 (Phase B) [B1: 지형+건물]
  arch_site_model.rb     로더(SketchupExtension 등록)
  arch_site_model/       main(메뉴·HtmlDialog)·api_client(Sketchup::Http)·builder(지형mesh+건물돌출)·settings·dialog.html
  build_rbz.py           확장 폴더 → dist/arch_site_model.rbz 패키징

docs/
  deploy.md              배포 가이드 (로컬 실행·도커·Cloud Run·인증·NGII 전환)
  orthophoto_texture_plan.md  정사영상 텍스처 계획 (Tier 1 완료, Tier 2a .skp 드레이프)
  sketchup_extension.md  SketchUp 확장 설치·사용·개발 가이드 (Phase B)

Dockerfile               프론트 빌드 + 파이썬 런타임 단일 이미지 (Cloud Run 배포)
.github/workflows/deploy.yml  main push 시 pytest → Cloud Run 자동 배포

tests/                   pytest 단위 테스트 (API 호출은 mock; test_api.py·test_integration_api.py 포함)
```

---

## MCP 도구 계약

### `preview_site(address, radius_m=250, floor_height_m=3.0)`

모델 생성 없이 건물 목록·층수·예상 규모 미리보기. "뭐가 들어갈까?" 사람 검토용.

- 건물별 `name`, `floors`, `height_m`, `footprint_area_m2`, `has_courtyard` 반환
- `summary`: 건물 수·층수 통계(max/avg)·중정 수·지적 수·지형 가용 여부
- 실제 `.skp`/`.3dm` 생성 없음 — `generate_site_model` 실행 전 검토 단계

### `check_site_data(address, radius_m=250)`

생성 전 선검사. "이 주소, 지금 만들 수 있나?" 답변.

- 주소→좌표, bbox → 건물(LT_C_SPBD) + 지적(LP_PA_CBND_BUBUN) + DEM 비축 여부 리포트
- `ok: true` = 건물이 1개 이상 존재

### `generate_site_model(address, radius_m, floor_height_m, outputs, layers, output_dir, missing_floors_policy, setback)`

주소 → .skp 코드(build_model 입력) 생성.

- `outputs.skp.code` → SketchUp MCP `build_model`에 그대로 전달
- `stats.origin_offset` → EPSG:5186 원점 오프셋, 반드시 보존 (좌표 복원용)
- **주의**: 엔진은 코드 문자열만 생성. 실제 SketchUp 호출은 오케스트레이터(Claude)가 수행.

**layers 파라미터:**

| 값 | 동작 |
| --- | --- |
| `{"buildings": true}` | 건물 매싱만 (기본값, Phase 2) |
| `{"buildings": true, "terrain": true}` | 지형 TIN + 건물 앉힘 (Phase 3B) |
| `{"buildings": true, "cadastral": true}` | 건물 + 대지 경계 폴리곤 (Phase 5) |
| `{"buildings": true, "terrain": true, "orthophoto": true}` | 지형에 정사영상 텍스처 (Tier 1, **.3dm 전용**) |

지형 활성화 시 추가 응답 필드:

- `outputs.skp.terrain_triangles`: 생성된 삼각형 수
- `stats.elev_range_m`: 클립 DEM 표고 범위 `[min, max]`
- DEM 타일 없거나 범위 밖이면 `ok: true` + `warnings`에 경고 → **건물만 생성됨** (조용한 fallback)

**정사영상(orthophoto) — Tier 1:**

- 지형 TIN에 위→아래 평면투영으로 정사영상을 드레이프. `terrain: true` + `outputs=["3dm"]` 필요
  (클라우드 MCP `.skp`는 샌드박스가 이미지 반입 차단 → 텍스처 불가. `.3dm`만 지원).
- 소스: `config.ORTHO_SOURCE` = `"vworld"`(기본, `VWORLD_KEY` 재사용) | `"ngii"`(공공누리 1유형,
  `NGII_KEY` 발급 후). 기술 동일 — `src/geo/ortho.py::TileSource`만 교체. NGII 타일 격자(3857 vs
  5179)는 키 발급 후 GetCapabilities로 확정 필요.
- 파이프라인: `bbox → WMTS 타일 다운로드 → 모자이크 → EPSG:5186 재투영(위도 왜곡 보정) → PNG →
  평면매핑 UV`. PNG는 `.3dm`과 같은 `output_dir`에 저장(같이 둬야 텍스처 참조 유효).
- 응답: `outputs.3dm.orthophoto = {image_path, missing_tiles, zoom}`,
  `provenance.orthophoto_src`(출처표시용) + `orthophoto_zoom`.
- 조용한 fallback: 키 없음/지형 미생성/타일 초과·실패 시 `warnings` 추가 후 건물·지형만 생성.
- 미착수: Tier 2a — SketchUp `.skp`용 컴패니언 Ruby 드레이프(반자동). 상세 `docs/orthophoto_texture_plan.md`.

### `generate_site_tiles(address, radius_m=500, tile_size_m=200.0, floor_height_m, layers, missing_floors_policy)`

대량 건물(반경 500m+, 밀집지역 수백~천 동) 시 `generate_site_model`의 단일 `code` 문자열이
`build_model` 호출 인자로 감당 못 할 만큼 커지는 문제(수백 KB) 해결용 — 백로그5.

- VWorld 조회 + `origin_offset` 산출은 **전체 반경 1회만** 수행(타일 경계 중복 조회·좌표
  불일치 방지). footprint(또는 지적 parcel) 중심점 기준으로 `tile_size_m` 격자에 배정 후
  타일마다 별도 `code`를 생성한다.
- 반환: `tiles: [{"tile_id", "tile_bbox_m", "code", "solids", "cadastral_parcels", "terrain_triangles"}, ...]`
- `stats.origin_offset`은 모든 타일에 공통 적용 — 반드시 보존.
- 오케스트레이터가 `tiles[]`를 순서대로 `build_model`에 호출해 조립한다.
- `layers={"terrain": true}` 시 지형 TIN도 타일 경계에 맞춰 분할(`src/tiles.py::_split_terrain_by_tile`).
  경계 정점은 타일마다 중복 생성되지만 각 타일이 독립 SketchUp 그룹이라 무해.
- `generate_site_model`과 달리 `outputs`/`output_dir`(.3dm) 파라미터 없음 — `.skp` 코드 분할 전용.

---

## 데이터 소스 (실측 검증됨)

| 소스 | API / 파일 | 주요 필드 |
| --- | --- | --- |
| 주소→좌표 | `api.vworld.kr/req/address` | `response.result.point.{x,y}` |
| 건물 footprint+층수 | `req/data?data=LT_C_SPBD` | `gro_flo_co`(층수), `geometry`(MultiPolygon) |
| 대지 경계(지적) | `req/data?data=LP_PA_CBND_BUBUN` | `pnu`(19자리), `geometry` |
| 지형 DEM | `geo_store/dem_*.tif` (오프라인 비축) | EPSG:5186, GeoTIFF float32 |

**VWorld API 공통 주의사항:**

- `INCORRECT_KEY` → `.env` 키 확인 (+ `VWORLD_DOMAIN`이 키 등록 도메인과 일치해야 함 — 배포 시 Cloud Run URL로 설정)
- `NOT_FOUND` → 정상 응답 (결과 없음), 예외 아님
- `gro_flo_co` 0/null 건물 존재 가능 → `floors_of()`가 `None` 반환, `default_floors=1` 적용
- **주소→좌표는 지번+도로명 모두 지원**: `geocode()`가 지번(PARCEL) 먼저 조회, `NOT_FOUND`면 도로명(ROAD)으로 재시도 (`src/geo/geocode.py::_ADDR_TYPES`)

---

## 좌표계 규칙

- **외부 API**: `EPSG:4326` (lon/lat)
- **내부 계산**: `EPSG:5186` (Korea 2000 중부원점, 미터)
- **SketchUp MCP**: 인치 (`×M2I=39.3701`)
- `pyproj.Transformer` 사용 시 반드시 `always_xy=True`
- `origin_offset`: 건물 군의 최소 bbox 좌하단. 로컬 좌표 = 5186 좌표 - offset → 수백 km 절댓값 제거

---

## 지형 아키텍처 (Phase 3)

### Phase 3A — 오프라인 DEM 굽기 `[완료]`

**소스**: 수치지형도Ver2.0 SHP (1:5,000, NGII)

- 등고선: `N3L_F0010000.shp` (LineString, 필드: `등고수치`)
- 표고점: `N3P_F0020000.shp` (Point, 필드: `수치`)

**이유**: 등고선을 런타임에 TIN으로 변환하면 "평평한 삼각형" 문제 발생 → 정규 격자로 오프라인 한 번 구워 해결.

**1:5,000 축척 선택 이유**: 전국 완전 커버리지(1:1,000은 도시 지역 미완전). 우리는 등고선+표고점만 사용하므로 다른 레이어 완전성 무관.

**보간법 (계단현상 대응)** — `bake_dem(method=...)`:

- `"clough"`(기본): **guarded CloughTocher**. CloughTocher C1 3차 보간(정점 gradient 추정)으로
  세 정점이 같은 등고선 위여도 곡면이 휘어 계단현상을 줄인다. 단 급경사부/슬리버 삼각형에서
  큰 오버슈트(스파이크·웅덩이)를 내므로, 안전한 `LinearNDInterpolator` 값에서 `±guard_m`(기본 3m)
  밖으로 벗어난 셀을 그 범위로 클립("튜브 클램프")한다. Delaunay는 두 보간기가 공유.
- `"linear"`: 평면 삼각보간만(과거 기본값). 오버슈트 없지만 등고선 사이가 평탄 삼각형이 돼 계단 발생.
- 실측(대전 도엽): quant_frac(5m 배수 몰림) 25.2%→22.3%, flat_frac 55.2%→48.5%, 스파이크·봉우리
  무손상(최고 190.4→190.5m). 완전 제거는 아닌 **부분 개선** — 더 강한 제거는 라플라스 harmonic
  인필/ANUDEM류 격자 솔버 필요(미착수).
- **진단**: `python scripts/dem_staircase.py <old.tif> <new.tif>` — quant_frac/flat_frac/봉우리 비교.

**실행 방법:**

```powershell
python -m src.terrain.contour_bake <shp_dir> `
    --cell 5 `
    --out geo_store/dem_daejeon_36710065_66.tif `
    --region "대전 서구(36710065+66)" `
    --sheets 36710065 36710066 `
    --method clough --guard 3
```

**원본 SHP 위치**: `C:\Users\20260102\Downloads\새 폴더\(B010)수치지도_36710065_..._` 및 `..._36710066_..._`
(각 폴더에 `N3L_F0010000.shp` 등고선 + `N3P_F0020000.shp` 표고점). `<shp_dir>`로 상위 "새 폴더"를
주면 rglob으로 양 도엽을 함께 읽는다(`--sheets`는 manifest 메타용일 뿐 필터 아님).

**현재 비축 파일:**

```text
geo_store/dem_daejeon_36710065_66.tif
  - 560행×900열, 5m 해상도, EPSG:5186
  - bounds(5186): left=231419 bottom=414155 right=235919 top=416955
  - 표고: 35.0~190.5m, NaN 0%, method=clough(guard 3m)
```

**표고점 필수**: `F0020000` 없으면 봉우리가 평면으로 처리됨. 항상 함께 bake할 것.

### Phase 3B — 런타임 지형 `[완료]`

DEM 타일 클립 → TIN 삼각망 → 건물 앉힘까지 파이프라인에 통합.

**사용 예시:**

```python
result = generate_site_model(
    "대전광역시 서구 괴정동 358",
    layers={"buildings": True, "terrain": True},
)
# result["outputs"]["skp"]["code"] → SketchUp MCP build_model 입력
# result["stats"]["elev_range_m"]  → [35.2, 112.7] (클립 DEM 표고 범위)
```

**지형 갱신 방법**: `dem_*.tif` 파일 교체 + `manifest.json` 해당 항목 수정 → 엔진 코드 무수정.

**DEM 범위 부족 시**: 인접 도엽 SHP 추가 후 `contour_bake` 재실행 (bounds 확장) → manifest 갱신.

---

## 구현 단계 현황

| Phase | 내용 | 상태 |
| --- | --- | --- |
| 0 | 프로젝트 스캐폴드, FastMCP 서버 기동 | ✅ 완료 |
| 1 | `check_site_data` (주소→좌표→건물/지적/지형 선검사) | ✅ 완료 |
| 2 | `generate_site_model` 건물 매싱 (footprint×층수→쿼드 솔리드→.skp) | ✅ 완료 |
| 3A | 오프라인 DEM 굽기 (`contour_bake.py`, 등고선SHP→GeoTIFF) | ✅ 완료 |
| 3B | 런타임 지형 (DEM 클립→TIN→드레이프, 건물 앉힘) | ✅ 완료 |
| 4 | `.3dm` 이중 출력 + origin_offset 복원 | ✅ 완료 |
| 5 | 지적 레이어 + 층수 누락 정책 + provenance 완성 | ✅ 완료 |
| 확장1 | 홀(중정) 처리 — `holes_m` + `add_face_inner_loop` | ✅ 완료 |
| 확장2 | `preview_site` — 건물 목록·규모 미리보기 (생성 없음) | ✅ 완료 |
| 확장3 | 이격면(setback) 실연동 — arch-law-diagnose | 🚧 블로커 (아래 참고) |
| 확장4 | `generate_site_tiles` — 대량건물 타일분할 | ✅ 완료 |
| 확장5 | 정사영상 텍스처 Tier 1 — 지형 TIN 드레이프 (.3dm) | ✅ 완료 (Tier 2a `.skp` 미착수) |

---

## .3dm 출력 아키텍처 (Phase 4)

`src/output/rhino.py` — `write_3dm(solids, terrain, path, offset, cadastral=None)`:

- **건물**: `rhino3dm.Extrusion` (닫힌 PolylineCurve → Z 돌출, 캡 포함)
- **지형**: `rhino3dm.Mesh` (삼각망). TerrainMesh.vertices는 인치(SketchUp)→ `/M2I` 미터 환산
- **지적**: `rhino3dm.PolylineCurve` at Z=0
- **레이어**: `buildings`(steel blue) / `buildings_unverified`(orange, policy=flag 시) / `terrain`(olive green) / `cadastral`(sandy yellow)
- **좌표계**: 로컬 미터 (BuildingSolid.footprint_m / base_z_m / height_m 그대로)
- **origin_offset**: 문서 `model.Strings["origin_offset_x/y"]` + 각 객체 `SetUserString` 이중 기록
- `write_3dm` 반환값: 저장된 절대 경로 문자열

**사용 예시:**

```python
result = generate_site_model(
    "대전광역시 서구 괴정동 358",
    outputs=["skp", "3dm"],
    layers={"buildings": True, "terrain": True},
)
# result["outputs"]["skp"]["code"]   → SketchUp MCP build_model 입력
# result["outputs"]["3dm"]["path"]   → 저장된 .3dm 절대 경로
# result["stats"]["origin_offset"]   → [ox, oy] EPSG:5186 (양쪽 출력에 동일 적용)
```

**좌표 복원 (Rhino에서):**

```python
# 로컬 미터 → EPSG:5186 절대
x_abs = x_local + origin_offset_x
y_abs = y_local + origin_offset_y
```

**output_dir**: `generate()` / `generate_site_model()` 에 `output_dir` 파라미터(기본: `"output/"`)
파일명: `{주소_안전화}.3dm` (특수문자 → `_`)

---

## Phase 5 추가 사항

### 지적 레이어 (`src/geometry/cadastral.py`)

`CadastralParcel(pnu, footprint_m)` — `features_to_parcels(features, offset)`.

- MultiPolygon → 가장 큰 외곽 링만 취득
- shapely 파싱 오류(꼭짓점 < 4 등) → 조용히 건너뜀
- `.skp`: Z=0 평면 폴리곤 그룹 (`CADASTRAL` 리터럴 + `_CADASTRAL_BUILD` 템플릿)
- `.3dm`: `cadastral` 레이어(황색) PolylineCurve at Z=0

### 층수 누락 정책 (`missing_floors_policy`)

`features_to_solids()` 의 `missing_policy` 파라미터 (`"default"` | `"skip"` | `"flag"`):

- `"default"`: 기본 1층 적용, `BuildingSolid.flagged=False`
- `"skip"`: `gro_flo_co` 누락 건물 solid 미생성
- `"flag"`: 기본 1층 적용, `BuildingSolid.flagged=True` → `.3dm` `buildings_unverified` 레이어(주황), `.skp` 이름 `[층수미확인]` 접미사

### provenance 완성

`generate()` 반환의 `provenance` 필드 (항상 포함):

- `building_src`, `floor_height_m`, `missing_floors_policy`, `radius_m`, `fetched_at`
- `cadastral_src` — `layers.cadastral=True` 시 추가
- `terrain_tile` — 지형 DEM 타일 파일명 (지형 활성화 시)
- `setback_analysis: "stub"` — `setback=True` 시 추가

### setback stub — 실연동 블로커 확인됨 (2026-07-01)

`generate()` / `generate_site_model()` 의 `setback: bool = False` 파라미터.
`True` 시 `warnings`에 "arch-law-diagnose 연동 예정 [목표]" 추가 + provenance에 `"stub"` 표기.
실제 분석 호출은 미구현 [목표].

`D:\APPS\arch-law-diagnose` 조사 결과 실연동은 현재 불가능:

- MCP 서버가 아니라 REST API(FastAPI, `POST /api/diagnose`, uvicorn:8000). 그쪽 자체
  CLAUDE.md에도 "MCP/API 도구화"가 `arch-law-graph` 병합 전까지 보류 상태로 명시됨.
- `DiagnoseRequest`는 설계된 건물 프로그램 데이터(`site_area`, `building_area`,
  `floor_area_above`, `floors_above`, `height`, `building_use` 등)를 요구하는데,
  arch-site-model은 기존 건물의 as-built footprint/층수만 가지고 있어 애초에 채울 데이터가 없음.
- 이격면 로직도 8개 카테고리 종합 진단 응답 안에 묻혀 있어 "이격 오프셋만" 뽑는 좁은 계약이 없음.
- **재개 조건**: arch-law-diagnose 쪽에서 설계 프로그램 불필요 + 이격만 반환하는 좁은 API/MCP 계약을
  노출할 때까지 보류.

---

## SketchUp MCP 코드 생성 규칙

`src/output/skp_mcp.py`가 생성하는 코드의 제약 (실측 검증):

- `import` 금지 — `GeometryInput`, `LoopInput`, `SUPoint3D`, `SUVector3D`, `Group`, `Camera`, `model`, `math` 등은 전역 제공됨
- 단위 = **인치** (`M2I = 39.3701` 으로 미터 변환)
- 좌표축: X=폭, Y=깊이, Z=높이 (Z=0 모델 원점 — 지형 없을 때 지면, 지형 있을 때 지형 아래)
- 옆면 = 수직 쿼드 (변마다 1면)
- 바닥면: 정점 역순 (하향), 천장면: 정순 (상향)
- 중정(홀): `BuildingSolid.holes_m` → `extrude_solid(holes_m=...)` → 바닥/천장 `add_face_inner_loop` + 내벽 쿼드. `.3dm`은 홀 링마다 별도 내벽 Extrusion(`{name}_hole{i}`) 추가.

---

## 테스트 규칙

- VWorld API 호출은 항상 mock (`tests/conftest.py`)
- 각 모듈마다 대응 `tests/test_*.py` 작성 — 실제 파일·API 없이 합성 데이터로 동작
- `tests/test_contour_bake.py`: 합성 SHP(동심원 등고선+봉우리 표고점)
- `tests/test_dem.py`: 합성 GeoTIFF(경사면)
- `tests/test_terrain_mesh.py`: 합성 DEMPatch(균일/경사/NaN 격자)
- `tests/test_seating.py`: 합성 DEMPatch(평지/경사지)
- `tests/test_rhino.py`: 합성 BuildingSolid/TerrainMesh → .3dm 검증 (Phase 4, 19개 테스트)
- `tests/test_cadastral.py`: 합성 GeoJSON 피처 → CadastralParcel 변환 (Phase 5, 8개 테스트)

---

## 자주 하는 실수 / 주의

1. **좌표 순서**: VWorld API 응답은 `x=경도, y=위도` 순서. `pyproj` `always_xy=True` 필수.
2. **gro_flo_co 0/null**: `floors_of()`가 `None` 반환 → 기본 1층 적용, `floors=None` 보존(추정 금지).
3. **등고수치/수치 필드명**: 수치지형도Ver2.0 SHP의 한국어 필드명. 영문 ID(`CONT`, `NUME`)와 구별.
4. **SketchUp 인치 단위**: 로컬 미터 좌표를 `×M2I` 없이 쓰면 모델이 극소 크기로 생성됨.
5. **origin_offset 보존**: `generate_site_model` 응답의 `stats.origin_offset`은 실제 위치 복원에 필수. 버리면 안 됨.
6. **PowerShell 한국어 인코딩**: 터미널에서 한국어 파일명/필드명 출력 시 깨짐 가능. `-Encoding utf8`로 파일 출력 후 확인.
7. **`layers.terrain` 조용한 fallback**: DEM 타일 없거나 사이트가 범위 밖이면 `ok: true`로 반환하되 지형은 생성 안 됨. 반드시 `warnings` 필드 확인.
8. **`clip_dem`에 4326 bbox 직접 금지**: `bbox_5186` 파라미터는 EPSG:5186이어야 함. pipeline 내부의 `_bbox_4326_to_5186()` 헬퍼로 변환할 것.
