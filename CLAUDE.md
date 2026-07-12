# arch-site-model — Claude Code 인계 문서

> 대지 주소 입력만으로 주변 지형·건물 3D 대지모델을 자동 생성하는 MCP 서버 + 배포 웹앱.
> KBS TopoMap의 수동 워크플로우를 "주소 딸깍 + API 취득"으로 대체.

---

## 다음 작업 (TODO)

> 완료하면 해당 줄을 삭제한다(항상 "남은 일"만 남게 유지). 완료 기능 상세는 아래 "구현 단계 현황"·모듈 구조 참조.

- [ ] **전국 5m DEM 확장**: 6개 광역단체(대전·서울·부산·대구·울산·세종 120타일) GCS 라이브. 건물·지적은
      VWorld 실시간이라 이미 전국이고 남은 건 지형(DEM)뿐. 지역 추가 = 1:5,000 수치지형도 SHP 폴더
      확보(사람 손) → `bake_tiled`→`dem_to_cog`→`gcloud storage cp`→`manifest.json` 커밋(반복). 경기권
      등 대기(사용자가 SHP 폴더 채워 알림). 경기 화성(`2MAP5000_SHP`)은 로직 호환 확인됨. 인천·광주는
      자치구 개편 확정 후. 상세 [[nationwide-dem-ngii-source]].
- [ ] **도로 Phase R 후속(선택)**: 도로·보도·차선 + **통합 표면**(지형·도로·보도를 한 삼각화로 → 정점
      공유, 이음매·구멍·뜸 구조적 제거) **완료**. **대반경 타일경로(`generate_tile`)에 도로 추가 완료**
      (타일마다 도로/보도/중심선 클립→버닝→통합표면, `test_generate_tile_roads`) + **SketchUp 확장
      builder가 도로/보도/차선 렌더**(단일+타일, 데스크톱 실기 렌더 검증 대기). **경계 폴리곤 없는
      도로(소로·골목) 실측폭 합성 완료**: A0010000이 빠뜨린 도로를 A0020000 중심선의 실측 `도로폭`으로
      버퍼링해 노면 채움(`road_bake.synthesize_gap_roads`, 커버리지 89%→100% 실측). **차선 다차선 완료**:
      A0020000 실측 `차로수`·`도로폭`을 중심선 props(`{"cl":1,"n","w"}`)에 담아(road_bake) 런타임이 차로수
      만큼 평행 차선 구분선 생성(`road.clip_lane_markings`, 소로는 중심선 1개, 실측 65→96선), 확장은 차선을
      **노란 얇은 면 리본**으로 렌더(엣지색 전역모드 불필요). **정교화 3종 완료**(전부 백엔드 → 확장/F2/.3dm/.skp
      자동 반영): ⓐ **차선 대시**(구분선은 점선 `_dash_line` 칠3·공백5m, 중앙선 median은 실선 유지), ⓑ **보도 표현
      강화**(A0033320 보도가 A0010000 도로경계에 97% 겹쳐 도로우선이면 거의 컬링→**보도 우선으로 뒤집어** 인도가
      제대로 보임, 도로는 보도 몫만 빠짐, 실측 보도 삼각형 110→5637), ⓒ **도로 경계 샤프닝**(통합표면 경계 densify를
      내부 격자(`ROAD_CELL_M` 2.5m)보다 촘촘한 `ROAD_EDGE_CELL_M`(1m)로 → 경계가 곡선 정밀 추종). **클라우드 도로/수계 서빙 = 배포 완료**(DEM과 동일 원칙): `roads_*/water_*.geojson`(gitignore)을
      공개 GCS에 올리고 `ROAD_BASE`/`WATER_BASE=gs://<버킷>/{roads,water}`로 두면 앱이 HTTP로 fetch+캐시해
      읽는다(`config.road_file_path`/`water_file_path` gs→https 변환, `road._read_geojson_text` fetch, DEM은
      GDAL /vsicurl이라 도로만 HTTP). **대전·서울 배포됨**(`ROAD_BASE`/`WATER_BASE` Cloud Run 설정 완료).
      ⚠️ **메트로 도로는 반드시 타일링**: 지역 1파일이면 서울 311MB→요청당 3분+/2GB(런타임 전량 파싱). DEM처럼
      `road_bake --tile-km 2`(`bake_roads_tiled`, 2km 하드클립·공간분할)로 쪼개고 런타임 `find_road_files`가
      겹치는 타일만 읽는다(서울 247타일, 강남 250m 0.46s·400배↑). 수계는 소량(서울 5.79MB)이라 단일 파일 OK.
      **남은 건 타 광역시**(부산·대구·울산·세종) 같은 레시피 반복(SHP는 [[desktop-shp-source]]). 상세
      `docs/deploy.md` §5, [[road-tiling-metro-serving]]. 입체 데크(고가/
      교량 A0070000/A0090000/A0110020)는 DSM 블로커 — `구분` 필드로 분류만 됨(고가=T0 휴리스틱, 지하/터널=
      생략, 복층=QA 플래그). 상세 `docs/road_surface_plan.md`.
- [ ] **DEM/DSM 이원화(고가/교량 데크 실측) — 블로커**: 지면=DEM, 공중 구조물=DSM 원리는 유효하나
      고해상도 DSM 민간 취득 불가(2026-07-08 확정: NGII 라이다=공문/기관 한정, 지자체 DSM=₩10M+"민간
      제공 불가", 무료 글로벌=30m라 데크·건물에 무용). 기관 접근/데이터 협약 생기면 승격(정사영상·setback과
      같은 블로커 대기). 상세 `docs/dem_dsm_strategy.md`, [[dsm-acquisition-blocker]].
- [ ] **Phase B — SketchUp 확장(.rbz)**: B1(지형+건물)·대반경 타일 순차조립·지형 LOD·**B2 정사영상
      드레이프**(단일+타일 경로 모두, `Face#position_material` 위→아래 평면투영·양면) 모두 **코드 완성·테스트
      통과**(pytest 51 green: ortho/pipeline/tiles_stream, UV 정합 손검증 완료, .rbz 최신 재빌드). 남은 건
      **데스크톱 실기 렌더 검증뿐**(헤드리스 없어 개발자 무인 확인 불가 — 사용자 루프): (a) 1~2km 타일모드
      실기 테스트(1km 이음매 없이 검증됨), (b) B2 정사영상이 지형에 실제로 입혀지는지 SketchUp에서 확인
      (Ruby Console `[ortho]` 로그가 실패 원인 출력). 상세 `docs/sketchup_extension.md`.
- [ ] **지형 계단현상 격자 솔버 — 구현 완료(opt-in)**: 라플라스 조화 격자 솔버 `--method solver`
      (`contour_bake.bake_dem`: 등고선 셀을 Dirichlet 제약으로 고정 + 나머지를 ∇²z=0 red-black SOR로 완화,
      `_grid_relax`). TIN 삼각화(평평한 삼각형=테라스)를 안 거치고 등고선에서 확산 → **계단현상 육안 제거
      확인(힐셰이드)**, 조화함수라 오버슈트도 없음. 등고선을 반 셀로 조밀화(`read_contours densify_m`)해
      정점 사이 beading 제거. guarded clough(기본)보다 ~10× 느려 **opt-in — 기본은 clough 유지**. 남은 선택 —
      전 타일 재베이크로 기본 채택(비용 큼, 사용자 결정) / 급경사 절토면 잔여 아티팩트는 biharmonic(thin-plate)
      개선 여지. ⚠️ `dem_staircase`의 quant/flat 지표는 솔버 판단에 오도(조밀 제약·완경사를 페널티) — 힐셰이드로 볼 것.
- [ ] **NGII 정사영상 소스**(보류): 서버사이드 키 접근 막힘(브라우저 전용 키 정황) + EPSG:5179 타일 구현
      필요. 키 서버사이드 접근이 풀리면 5179 `TileSource` 추가만. [[orthophoto-texture-blocker]].
- [ ] **이격면(setback) 실연동**(블로커): arch-law-diagnose가 좁은 API 계약(이격만 반환) 노출할 때까지 보류.

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
base_z/height/flagged/verified + 지형 vertices/triangles + ortho_extent)가 포함되고, `Viewer3D.tsx`가
three.js로 지형 mesh+건물 돌출을 렌더(+정사영상 평면 드레이프). `pipeline.generate(include_geometry=True)`
일 때만 직렬화(MCP 응답 비대화 방지 — 기본 False). rhino3dm/WASM 미사용(생성 Extrusion에 렌더
메시가 없어 3DMLoader가 건물을 못 그림 → geometry JSON 직접 렌더로 결정).

**`src/config.py` 주요 설정값:**

- `VWORLD_KEY`: `VWORLD_TEST_KEY` 우선, 없으면 `VWORLD_KEY`
- `M2I = 39.3701`: 미터→인치 (SketchUp MCP는 인치 단위)
- `DEFAULT_FLOOR_H_M = 3.0`: 기본 층고
- `TERRAIN_MAX_ERROR_M = 0.25`: 지형 TIN 방식. >0=오차 한계 적응형 TIN(그 수직오차[m]를 **목표로**
  정점 삽입 — 대부분 충족하나 극단 급경사서 정점 상한(≈90%) 도달 시 예외 가능. 평지는 큰 삼각형·복잡한 곳만
  촘촘 → 삼각형 대폭 감소로 넓은 반경도 가벼움), 0=균일 격자.
  실측(신반포 250m): 0.25m에서 삼각형 86% 감소(19602→2467), 실측 최대오차 0.25m 준수.
  `terrain_mesh.build_tin`→`adaptive_tin`(scipy greedy insertion)
- `ORTHO_SOURCE = "vworld"`: 정사영상 소스 (`"vworld"` 기본, `VWORLD_KEY` 재사용 | `"ngii"` 공공누리) — [[orthophoto-texture-blocker]]
- `NGII_KEY = ""`: NGII 정사영상 키 (발급 후 `ORTHO_SOURCE=ngii`와 함께 사용)
- `ORTHO_ZOOM = 18`: 정사영상 WMTS 줌 레벨
- `GEO_STORE = Path("geo_store")`: manifest.json + 로컬 베이크 산출물 디렉터리
- `DEM_TILE_BASE`: DEM **타일** 읽기 위치. 기본=로컬 `GEO_STORE`. `/vsigs/<버킷>/<프리픽스>`(또는
  `gs://…`, 자동으로 `/vsigs` 변환) 지정 시 GCS COG 윈도우 읽기. manifest는 항상 로컬, 타일만 원격.
  경로 해석은 `config.dem_tile_path(file)`. 업로드 준비는 `scripts/dem_to_cog.py`(COG 변환)
- `ZONING_BASE = ""`: 용도지역 조회용 형제 앱 **arch-law-graph** base URL. 설정 시 `layers.zoning`이
  `GET /api/zoning?address=`로 사이트 용도지역을 받아 `result.zoning`에 담음(미설정/미도달 시 조용히 생략).
- `scrub_secrets(text)`: 응답/로그에서 VWorld/NGII 키 값 마스킹(에러 URL의 `key=` 유출 방지).

---

## 모듈 구조

```text
src/
  server.py              FastMCP 서버 진입점 (MCP 도구 4개 등록, Claude 연동)
  api.py                 FastAPI 백엔드 (배포용 HTTP API — /api/generate, /api/tile_plan+/api/generate_tile(대반경 타일 순차조립), 파일 다운로드, frontend/dist 서빙)
  pipeline.py            generate_site_model 파이프라인
  tiles.py               generate_site_tiles — 대량건물 타일분할 .skp 코드 (백로그5)
  tiles_stream.py        tile_plan + generate_tile — SketchUp 확장 대반경(1~2km) 순차조립용 타일별 geometry JSON (계획→타일별 fetch, centroid 중복제거, 타일별 정사영상·도로/보도/차선 통합표면)
  site_check.py          check_site_data 핵심 로직
  preview.py             preview_site 핵심 로직
  qa.py                  생성물 자동 QA (검증 자동화) — 건물 앉힘(급경사/부유/침몰/지형밖)·건물 겹침(중복)·footprint 유효성(자기교차/슬리버)·지형 스파이크 검사 → findings{severity(warn/info),kind,label(심의어),message,at,name} + summary{total,warnings,passed,stamp,by_kind}. layers.qa=True 시 pipeline이 호출, result.qa로 반환. 웹 UI가 결함 목록 + F2 3D가 결함 위치에 수직 핀 표시(KBS "눈검사→코드" 대체)
  trust_report.py        데이터 신뢰도 리포트 (A-1) — 조립된 result(provenance+stats+qa+outputs) 위 순수 뷰 build_trust_report → result.trust_report{buildings(실측/추정 %)·terrain·orthophoto·qa·meta·caveats(정직한 한계 고지)}. 신규 취득 0, 항상 부착
  config.py              전역 설정·환경변수 (+ dem_tile_path: DEM 타일 로컬↔GCS /vsicurl 경로 해석 + ZONING_BASE(arch-law-graph 용도지역) + scrub_secrets 키 마스킹)
  geo/
    geocode.py           주소 → 좌표 (VWorld address API)
    bbox.py              반경 → bbox (EPSG:4326)
    crs.py               EPSG:4326 ↔ 5186 변환 + origin_offset
    vworld.py            VWorld data API 클라이언트 (페이지네이션 + bbox 분할: 10km²/쿼리 한도 우회 → 반경 2km+)
    ortho.py             정사영상 WMTS 타일수학 + TileSource + 모자이크(재투영→PNG, Tier 1)
    zoning.py            용도지역 조회 — 형제 앱 arch-law-graph GET /api/zoning 연동(경계 존중: zoning=법령 클러스터 소유). ZONING_BASE 미설정/미도달 시 None(조용한 fallback)
  geometry/
    building.py          LT_C_SPBD features → BuildingSolid (쿼드 솔리드, 홀 포함)
    terrain_mesh.py      DEMPatch → TerrainMesh (TIN 삼각망, Phase 3B). grid_to_tin(균일) + adaptive_tin(오차 한계 적응형, scipy greedy insertion) + adaptive_select/pixel_to_local_m(통합표면용 분리) + build_tin(디스패처, config.TERRAIN_MAX_ERROR_M)
    seating.py           BuildingSolid + DEMPatch → base_z 앉힘 (Phase 3B)
    cadastral.py         LP_PA_CBND_BUBUN features → CadastralParcel (Phase 5)
    road.py              도로/보도 런타임 (Phase R). clip_roads/clip_sidewalks/clip_centerlines/clip_lane_markings(GeoJSON→로컬미터, json+shapely — _load_features로 단일 경로 또는 겹치는 타일 리스트 병합 수용, 메트로 타일 서빙) + burn_roads(도로를 DEM에 소각: footprint 절토/성토·스커트·IDW교차블렌딩·자기지면 클램프) + build_unified_surface(★지형·도로·보도를 1번 Delaunay로 삼각화→재질별 3메시, 정점공유로 이음매0. 보도우선(도로겹침 컬링 방지)·경계 edge_cell 샤프닝) + clip_lane_markings(중심선 props 차로수·도로폭→평행 차선 구분선, offset_curve, 구분선은 _dash_line 점선·중앙선 실선)/drape_centerlines(차선 드레이프) + _read_geojson_text(로컬/HTTP fetch+캐시 — 클라우드 도로 서빙) + apply_crown + build_road_mesh/carve_terrain/build_terrain_conformed(폴백·구버전)
    water.py             수계 런타임 (수계). clip_water(E계열 폴리곤→로컬미터) + water_surface_z(경계 둑 DEM 저백분위=수면표고) + burn_water(지형을 물 아래로 평탄화) + build_water_mesh(★표고고정 평면 수면, road와 달리 드레이프 아님). road.py 헬퍼 재사용
  output/
    skp_mcp.py           BuildingSolid(+TerrainMesh+Cadastral+RoadMesh road/sidewalk) → SketchUp MCP 코드 문자열
    rhino.py             BuildingSolid(+TerrainMesh+Cadastral+RoadMesh road/sidewalk) → .3dm (Phase 4-R)
  terrain/
    store.py             manifest.json/road_manifest.json 조회 (find_tiles/find_road_files(겹치는 도로 타일 전부)/find_road_file(대표 1개)/find_water_file)
    contour_bake.py      수치지형도 등고선 SHP → DEM(.tif) 오프라인 굽기 (Phase 3A) + bake_tiled(대용량 지역 타일 배치) + 좌표대 재투영(5187→5186)·도엽 중복제거·거리제한 채움(fill_dist_m) + method: clough(기본)/linear/solver(라플라스 조화 격자 솔버 _grid_relax — 계단 완전제거, opt-in)
    road_bake.py         수치지도 A0010000 도로경계·A0020000 중심선(+실측 `도로폭`·`차로수`)·A0033320 보도 SHP → 지역 GeoJSON(EPSG:5186) 오프라인 굽기 (Phase R) + road_manifest.json 갱신 (contour_bake 헬퍼 재사용) + synthesize_gap_roads(경계 폴리곤 없는 도로를 실측 도로폭으로 버퍼해 노면 합성 {"syn":1}, --no-fill-gaps로 끔) + 중심선 props에 도로폭/차로수 담음(다차선 마킹용) + bake_roads_tiled(--tile-km: 메트로용 2km 하드클립 타일링, STRtree 후보추출+타일박스 교집합, 갭채움 union은 타일 내부로 한정 → 단일파일 311MB/요청당 3분+ 회피, 서울 247타일)
    water_bake.py        수치지도 E계열 수계 면(N3A_E0* 하천경계·호소) SHP → 지역 GeoJSON(EPSG:5186) 오프라인 굽기 + water_manifest.json 갱신 (road_bake 동형)
    dem.py               DEM 타일 클립 + 표고 보간 (Phase 3B) + clip_dem_mosaic(다중 타일 rasterio.merge 병합)

geo_store/
  manifest.json          비축 DEM 타일 목록 (git 추적)
  road_manifest.json     비축 도로 GeoJSON 지역 목록 (git 추적)
  water_manifest.json    비축 수계 GeoJSON 지역 목록 (git 추적)
  water_*.geojson        수계(하천·호소) 지역 벡터 (EPSG:5186, gitignore — water_bake 생성, ROAD_BASE처럼 WATER_BASE로 GCS 서빙)
  dem_*.tif              GeoTIFF DEM 파일 (EPSG:5186, gitignore — GCS 서빙)
  roads_*.geojson        도로/보도/중심선 지역 벡터 (EPSG:5186, gitignore — road_bake 생성). 배포는 공개 GCS에
                         올리고 ROAD_BASE=gs://…/roads로 서빙(앱이 HTTP fetch, config.road_file_path/road._read_geojson_text). docs/deploy.md §5

frontend/                React + Vite + Tailwind 웹 UI (주소 입력 → /api/generate 호출 → .3dm/정사영상 다운로드)
  src/App.tsx            메인 폼·결과 화면
  src/Viewer3D.tsx       브라우저 3D 미리보기 (three.js — 지형 mesh+건물 돌출+정사영상 드레이프 + 지적/도로/보도/차선 + 높이색 그라디언트·외곽선·그림자·뷰모드·레이어 토글·SSAO(EffectComposer+GTAOPass, 음영 토글)·QA 결함 수직 핀(경고=빨강/info=주황)) [F2]
  dist/                  빌드 산출물 (FastAPI가 루트에서 서빙)

sketchup_ext/            SketchUp 확장(.rbz) — 주소→백엔드 geometry JSON→SketchUp 조립 (Phase B) [B1: 지형+건물]
  arch_site_model.rb     로더(SketchupExtension 등록)
  arch_site_model/       main(메뉴·HtmlDialog)·api_client(Sketchup::Http)·builder(지형mesh+건물돌출+정사영상 드레이프+도로/보도 메시·차선·수계 평면·QA 결함 핀)·settings·dialog.html
  build_rbz.py           확장 폴더 → dist/arch_site_model.rbz 패키징

docs/
  deploy.md              배포 가이드 (로컬 실행·도커·Cloud Run·인증·NGII 전환)
  orthophoto_texture_plan.md  정사영상 텍스처 계획 (Tier 1 완료, Tier 2a .skp 드레이프)
  sketchup_extension.md  SketchUp 확장 설치·사용·개발 가이드 (Phase B)
  kbs_topomap_reference.md  KBS TopoMap(원본 수동 워크플로우) 기능 대조표 + 무인화 로드맵 (도로/수계/자동QA)
  road_surface_plan.md   도로 노면 자동화 (Phase R) 설계·진행 — R1~R3 + 통합 표면, KBS 대조, 한계
  dem_dsm_strategy.md    DEM+DSM 이원화 전략 (지면=DEM, 공중 구조물=DSM) — DSM 취득 블로커 확정

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
| `{"buildings": true, "terrain": true, "roads": true}` | 지형 + 도로 노면(A0010000 DEM 드레이프 메시, Phase R). 도로는 `road_manifest.json`/GeoJSON 비축 필요 — 없으면 조용히 생략+warnings |
| `{"buildings": true, "terrain": true, "water": true}` | 지형 + 수계(E계열 하천·호소 → 표고고정 평면 수면 + 지형 물 아래로 버닝). `water_manifest.json`/GeoJSON 비축 필요, 지형(DEM) 필요 — 없으면 조용히 생략+warnings |
| `{..., "qa": true}` | 자동 QA(검증) 실행 → `result.qa = {findings, summary}` (건물 앉힘·겹침·지형 스파이크). 다른 레이어와 무관하게 켤 수 있음. 웹 UI가 결함 목록 표시 |
| `{"buildings": true, "terrain": true, "orthophoto": true}` | 지형에 정사영상 텍스처 (.3dm=Rhino 텍스처 / .skp=데스크톱 확장 B2 드레이프) |
| `{"buildings": true, "zoning": true}` | 사이트 용도지역 조회 (arch-law-graph `/api/zoning`, `ZONING_BASE` 필요) → `result.zoning{zone_name, zone_key, sido, sigungu}`. 웹 배지 표시. 미설정/미도달 시 조용히 생략 |

**신뢰도 리포트(A-1)**: `result.trust_report`가 **항상** 부착된다 — 건물 실측/추정 층수 비율·지형 출처/정확도·
정사영상·QA 요약·정직한 한계 고지(층수×3m 가정·DSM 제외 등). 이미 있는 데이터의 순수 뷰(`src/trust_report.py`).
웹 UI가 "데이터 신뢰도 리포트" 패널로 소비. **불확실성 가시화(A-2)**: 추정 층수 건물은 `geometry.verified=false`로
뷰어에서 주황, `.3dm`은 `buildings_unverified` 레이어(default 정책 추정도 포함).

지형 활성화 시 추가 응답 필드:

- `outputs.skp.terrain_triangles`: 생성된 삼각형 수
- `stats.elev_range_m`: 클립 DEM 표고 범위 `[min, max]`
- DEM 타일 없거나 범위 밖이면 `ok: true` + `warnings`에 경고 → **건물만 생성됨** (조용한 fallback)

**정사영상(orthophoto):**

- 지형 TIN에 위→아래 평면투영으로 정사영상을 드레이프. `terrain: true` 필요(지형 없으면 조용히 생략).
- **소비자 2종**:
  - **.3dm (Rhino)**: `write_3dm`이 지형 메시에 비트맵 텍스처 입힘(평면매핑 UV). `outputs=["3dm"]`.
  - **.skp 데스크톱 확장 (B2)**: 백엔드는 **출력 포맷과 무관하게** mosaic PNG를 만들고
    `geometry.ortho_extent_m`(로컬 미터 extent) + `files.ortho_png`(다운로드 URL)을 응답에 담는다.
    데스크톱 SketchUp 확장이 PNG를 다운로드해 지형 삼각형마다 `Face#position_material`로 **양면**
    평면투영 드레이프(+ "텍스처" 뷰모드 자동 전환). **클라우드 MCP `.skp` 코드**는 여전히 텍스처
    불가(샌드박스 이미지 차단) — 이건 데스크톱 확장 전용 경로다.
- 소스: `config.ORTHO_SOURCE` = `"vworld"`(기본, `VWORLD_KEY` 재사용, 위성 실취득 검증됨) |
  `"ngii"`(공공누리 1유형, `NGII_KEY` 발급 후). 기술 동일 — `src/geo/ortho.py::TileSource`만 교체.
- 파이프라인: `bbox → WMTS 타일 다운로드 → 모자이크 → EPSG:5186 재투영(위도 왜곡 보정) → PNG`.
  PNG는 `output_dir`(잡 폴더)에 저장 — .3dm은 같은 폴더 참조, 확장은 `/api/files/<job>/ortho`로 다운로드.
- 응답: `outputs.3dm.orthophoto = {image_path, missing_tiles, zoom}`, `geometry.ortho_extent_m`,
  `files.ortho_png`, `provenance.orthophoto_src`(출처표시용) + `orthophoto_zoom`.
- 조용한 fallback: 키 없음/지형 미생성/타일 초과·실패 시 `warnings` 추가 후 건물·지형만 생성.
- 대반경 타일 경로(`/api/generate_tile`)도 정사영상 **구현됨**: 타일마다 자기 영역만 풀해상도(zoom 18)로
  만들어 `ortho={extent_local_m, image_b64(base64 PNG)}`로 반환(`tiles_stream._ortho_b64`) → 확장이 타일
  지형에 드레이프. 타일별 자기 이미지라 단발과 같은 선명도. 상세 `docs/orthophoto_texture_plan.md`.

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
| 지형 DEM | `geo_store/manifest.json`(로컬) + GCS COG `gs://arch-site-model-dem`(/vsicurl) | EPSG:5186, float32, 다중타일 mosaic |
| 수계(하천·호소) | 수치지도 E계열 면 SHP(`N3A_E0*`) → `water_bake` → `water_manifest.json`/GeoJSON | 하천경계 `E0010001`, 호소 `E0052114` 등. 표고고정 평면 수면 |

**VWorld API 공통 주의사항:**

- `INCORRECT_KEY` → `.env` 키 확인 (+ `VWORLD_DOMAIN`이 키 등록 도메인과 일치해야 함 — 배포 시 Cloud Run URL로 설정)
- `NOT_FOUND` → 정상 응답 (결과 없음), 예외 아님
- `gro_flo_co` 0/null 건물 존재 가능 → `floors_of()`가 `None` 반환, `default_floors=1` 적용
- **주소→좌표는 지번+도로명 모두 지원**: `geocode()`가 지번(PARCEL) 먼저 조회, `NOT_FOUND`면 도로명(ROAD)으로 재시도 (`src/geo/geocode.py::_ADDR_TYPES`)
- **반경 2km+ 는 bbox 분할로 자동 처리**: geomFilter BOX는 요청영역 10km² 이내만 허용(반경 ~1.58km 상한). `get_features`/`count`가 큰 bbox를 ≤9km² 서브박스로 나눠 조회 후 병합·중복제거(경계 피처) → 반경 2km+ 가능(상한 ~15km). 예전 "16km² 초과" 오류 사라짐.

---

## 좌표계 규칙

- **외부 API**: `EPSG:4326` (lon/lat)
- **내부 계산**: `EPSG:5186` (Korea 2000 중부원점, 미터) — **전국 고정**. 한국 TM 좌표대가 여럿이지만
  (5186 중부: 서울·대전·광주·세종 / 5187 동부: 부산·대구·울산) 파이프라인은 5186만 쓴다. 동부원점
  (5187) 지역 SHP도 `contour_bake`가 읽을 때 5186으로 재투영해 통일(안 하면 지형이 ~2° 어긋남).
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
- `"solver"`(opt-in, `--solver-iters` 기본 400): **라플라스 조화 격자 솔버**(`_grid_relax`, red-black SOR).
  등고선 정점(반 셀로 조밀화)+표고점 셀을 Dirichlet 제약으로 고정하고 나머지 셀을 ∇²z=0로 완화한다.
  TIN 삼각화를 아예 안 거쳐(평평한 삼각형=테라스 없음) 등고선에서 확산 → **계단현상 육안 제거**(힐셰이드
  확인), 조화함수 최대원리로 **오버슈트 구조적 불가**. clough보다 ~10× 느림 → opt-in(기본 clough).
- 실측(대전 도엽): clough는 quant 25.2%→22.3%, flat 55.2%→48.5%로 **부분 개선**(스파이크·봉우리 무손상).
  solver는 **힐셰이드로 계단 확실 제거**되나 quant/flat 지표는 오히려 오르는데(조밀 등고선 제약이 정확히
  5m 배수 + 완경사) 이는 **지표의 함정**이지 열화가 아님 — 솔버 품질은 반드시 힐셰이드로 판단.
- **진단**: `python scripts/dem_staircase.py <old.tif> <new.tif>` — quant/flat/봉우리(단, 솔버엔 오도 주의).

**실행 방법:**

```powershell
python -m src.terrain.contour_bake <shp_dir> `
    --cell 5 `
    --out geo_store/dem_daejeon_36710065_66.tif `
    --region "대전 서구(36710065+66)" `
    --sheets 36710065 36710066 `
    --method clough --guard 3
```

**대용량 지역(전국 확장 기본 경로)**: `--tile-km 10 --margin-m 300` 추가 시 `bake_tiled`가 등고선을
1회 읽고 타일 배치로 굽는다(`dem_<지역>_r{r}c{c}.tif` 여러 개 + manifest 자동 갱신). 이후
`scripts/dem_to_cog.py`로 COG 변환 → `gcloud storage cp … gs://arch-site-model-dem/dem/` 업로드.

**원본 SHP 위치**: `C:\Users\20260102\Downloads\새 폴더\(B010)수치지도_36710065_..._` 및 `..._36710066_..._`
(각 폴더에 `N3L_F0010000.shp` 등고선 + `N3P_F0020000.shp` 표고점). `<shp_dir>`로 상위 "새 폴더"를
주면 rglob으로 양 도엽을 함께 읽는다(`--sheets`는 manifest 메타용일 뿐 필터 아님).

**현재 비축 (6개 광역단체):** 대전·서울·부산·대구·울산·세종 = **120타일**(대전14·서울15·부산24·
대구36·울산20·세종11). 10km 격자·5m·EPSG:5186·method=clough guard 3m·거리제한 채움 fill_dist 200m.
부산·대구·울산은 동부원점(5187) 원본을 5186으로 재투영. **git 미추적**(`geo_store/*.tif` gitignore)
→ 공개 GCS `gs://arch-site-model-dem/dem/`에 COG로 서빙(`/vsicurl`), `manifest.json`만 git 추적.
지역 추가는 폴더 경로 → `bake_tiled`(재투영·중복제거·거리채움 자동) → `dem_to_cog` → 업로드 → manifest 커밋.

**표고점 필수**: `F0020000` 없으면 봉우리가 평면으로 처리됨. 항상 함께 bake할 것.

### Phase 3B — 런타임 지형 `[완료]`

DEM 타일 클립 → TIN 삼각망 → 건물 앉힘까지 파이프라인에 통합.

**TIN 방식 — `terrain_mesh.build_tin(dem, config.TERRAIN_MAX_ERROR_M)`:**

- `TERRAIN_MAX_ERROR_M > 0`(기본 0.25) → **오차 한계 적응형 TIN**(`adaptive_tin`, scipy greedy
  insertion). 평지는 큰 삼각형·복잡한 곳만 촘촘 → 지정 수직오차(25cm)를 **목표로**(정점 상한 ≈90%까지;
  극단 급경사선 상한 도달로 예외 가능) 삼각형을 최소화. 실측(신반포 250m): 삼각형 86%↓(19602→2467),
  실측 최대오차 0.25m. 넓은 반경도 가벼움.
- `= 0` → 균일 격자(`grid_to_tin`, 어디든 5m마다 삼각형).
- pydelatin은 C 컴파일러를 요구해 설치가 취약 → scipy(이미 런타임 의존성)만으로 순수 파이썬 구현.
  scipy 없거나 실패 시 균일 격자로 안전 폴백. **`requirements.txt`에 scipy 추가됨**(배포 적응형).

**사용 예시:**

```python
result = generate_site_model(
    "대전광역시 서구 괴정동 358",
    layers={"buildings": True, "terrain": True},
)
# result["outputs"]["skp"]["code"] → SketchUp MCP build_model 입력
# result["stats"]["elev_range_m"]  → [35.2, 112.7] (클립 DEM 표고 범위)
```

**지형 갱신/추가 방법** (전국 확장 루프): 지역 SHP → `contour_bake … --tile-km 10 --margin-m 300`
(대용량은 `bake_tiled` 타일 배치) → `scripts/dem_to_cog.py`로 COG 변환 →
`gcloud storage cp … gs://arch-site-model-dem/dem/` 업로드 → `manifest.json` 커밋·푸시(엔진 무수정).
**타일은 GCS, manifest만 git.** 질의가 여러 타일에 걸치면 `find_tiles`+`clip_dem_mosaic`가 병합.

**저장/서빙**: 배포는 `DEM_TILE_BASE=/vsicurl/…`로 Cloud Run이 GCS COG를 윈도우 읽기(이미지에 타일 없음).
로컬 개발은 `DEM_TILE_BASE` 미설정(로컬 `geo_store`) 또는 `/vsicurl/…`로 GCS 직접. 타일 열기 실패 시
지형 생략(조용한 fallback) → `warnings` 확인.

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
| 확장5 | 정사영상 텍스처 — 지형 TIN 드레이프 (.3dm=Rhino, .skp=데스크톱 확장 B2, 단일+타일 경로) | ✅ 코드완성·테스트 (데스크톱 실기 렌더 검증 대기) |
| 확장6 | 전국 DEM 확장 — 다중 타일 mosaic(`find_tiles`/`clip_dem_mosaic`) + 배치 베이크(`bake_tiled`) + GCS COG 서빙(`/vsicurl`) | ✅ 프로덕션 (지역 추가는 반복) |
| 확장7 | 지형 LOD — 오차 한계 적응형 TIN(`adaptive_tin`, scipy greedy insertion, 삼각형 86%↓·25cm) | ✅ 완료 |
| 확장8 | 대반경 타일 순차조립 — `/api/tile_plan`+`/api/generate_tile`, 확장이 타일별 fetch+조립(진행바/취소), 이음매 겹침 | ✅ 완료 (1km 검증, 2km 대기) |
| F2 | 뷰어 표현 — 높이별 색·건물 외곽선·그림자·뷰모드·지적/도로/보도/차선 표시·레이어 토글·SSAO(GTAO 음영) | ✅ 완료 |
| R1 | 도로 노면 — A0010000 폴리곤 → DEM 드레이프 메시(외곽선+면) → F2/.3dm/.skp 3출력 | ✅ 완료 |
| R2 | 도로 지형정합 — 버닝(절토/성토·스커트·IDW 교차블렌딩·자기지면 클램프) + 크라운(횡단구배) | ✅ 완료 |
| R3 | 보도(A0033320)·차선(A0020000) — 차로수 기반 **다차선**(중앙선 실선/구분선 점선, 노란 면 리본) | ✅ 완료 |
| R★ | 통합 표면 — 지형·도로·보도를 1번 Delaunay로(정점 공유) → 이음매·구멍·뜸·z-fighting 구조적 제거 | ✅ 완료 |
| R4 | 도로 정교화 — 실측폭 갭채움(소로/골목)·경계 샤프닝·보도우선(도로겹침 컬링 방지)·타일경로/확장 렌더·**클라우드 서빙**(`WATER_BASE`처럼 `ROAD_BASE`=GCS) | ✅ 완료 |
| R5 도로 타일링 | 메트로 도로 = 2km 하드클립 타일(`road_bake --tile-km 2`→`bake_roads_tiled`) + 런타임 `find_road_files` 다파일 클립(`_load_features`). 단일 311MB/요청당 3분+ → 서울 247타일/0.46s(400배↑). 대전·서울 GCS 배포 | ✅ (서울, 타 광역시 반복) |
| 수계 | E계열 하천·호소 → 표고고정 평면 수면 + 지형 물아래 버닝 (`water_bake`·`water.py`, F2/.3dm/.skp/확장) | ✅ 완료 (클라우드 서빙 `WATER_BASE`) |
| 지형솔버 | 계단현상 라플라스 조화 격자 솔버 `--method solver`(등고선 Dirichlet 제약+∇²z=0 완화, 오버슈트 없음, 힐셰이드 검증) | ✅ opt-in (기본 clough, ~10× 느림) |
| QA | 자동 검증 — 건물 앉힘(급경사/부유/침몰/지형밖)·겹침·footprint 유효성·지형 스파이크 → findings, 웹 패널+F2/확장 3D 핀 | ✅ 완료 (KBS "눈검사→코드") |

---

## .3dm 출력 아키텍처 (Phase 4)

`src/output/rhino.py` — `write_3dm(solids, terrain, path, offset, cadastral=None)`:

- **건물**: `rhino3dm.Extrusion` (닫힌 PolylineCurve → Z 돌출, 캡 포함)
- **지형**: `rhino3dm.Mesh` (삼각망). TerrainMesh.vertices는 인치(SketchUp)→ `/M2I` 미터 환산
- **지적**: `rhino3dm.PolylineCurve` at Z=0
- **레이어**: `buildings`(steel blue) / `buildings_unverified`(orange, **층수 추정 건물 = 실측 아님**; A-2에서 flag 정책뿐 아니라 default 정책의 추정 건물도 포함) / `terrain`(olive green) / `cadastral`(sandy yellow)
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
