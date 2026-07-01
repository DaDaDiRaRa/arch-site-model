# arch-site-model — Claude Code 인계 문서

> 대지 주소 입력만으로 주변 지형·건물 3D 대지모델을 자동 생성하는 MCP 서버 + 배포 웹앱.
> KBS TopoMap의 수동 워크플로우를 "주소 딸깍 + API 취득"으로 대체.

---

## 다음 작업 (TODO)

> 완료하면 해당 줄을 삭제한다(항상 "남은 일"만 남게 유지).

- [ ] **F2 — 브라우저 3D 미리보기**: `frontend/`에 rhino3dm.js(WASM)+three.js로 생성된 `.3dm`을
      화면에서 바로 렌더(지형+건물+정사영상 텍스처). 다운로드 없이 결과 확인.
- [ ] **Phase B — SketchUp 확장(.rbz)**: `.skp`용. 확장이 백엔드(`/api/generate` 또는 신규
      지오메트리 JSON 엔드포인트) 호출 → 데스크톱 SketchUp에서 조립 + 정사영상 드레이프
      (`Face#position_material`). 팀 대부분이 SketchUp이라 실사용 가치 큼. 데스크톱 GUI라
      사용자 테스트 루프 필요. 상세 `docs/orthophoto_texture_plan.md`.
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

# 3. MCP 서버 실행
python -m src.server

# 4. 테스트 실행
python -m pytest tests/ -v
```

**`src/config.py` 주요 설정값:**

- `VWORLD_KEY`: `VWORLD_TEST_KEY` 우선, 없으면 `VWORLD_KEY`
- `M2I = 39.3701`: 미터→인치 (SketchUp MCP는 인치 단위)
- `DEFAULT_FLOOR_H_M = 3.0`: 기본 층고
- `GEO_STORE = Path("geo_store")`: DEM 비축 디렉터리

---

## 모듈 구조

```text
src/
  server.py              FastMCP 서버 진입점 (MCP 도구 4개 등록)
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
    store.py             manifest.json 조회 (find_tile)
    contour_bake.py      수치지형도 등고선 SHP → DEM(.tif) 오프라인 굽기 (Phase 3A)
    dem.py               DEM 타일 클립 + 표고 보간 (Phase 3B)

geo_store/
  manifest.json          비축 DEM 타일 목록
  dem_*.tif              GeoTIFF DEM 파일 (EPSG:5186)

tests/                   pytest 단위 테스트 (API 호출은 mock)
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

- `INCORRECT_KEY` → `.env` 키 확인
- `NOT_FOUND` → 정상 응답 (결과 없음), 예외 아님
- `gro_flo_co` 0/null 건물 존재 가능 → `floors_of()`가 `None` 반환, `default_floors=1` 적용

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

**실행 방법:**

```powershell
python -m src.terrain.contour_bake <shp_dir> `
    --cell 5 `
    --out geo_store/dem_daejeon_36710065_66.tif `
    --region "대전 서구(36710065+66)" `
    --sheets 36710065 36710066
```

**현재 비축 파일:**

```text
geo_store/dem_daejeon_36710065_66.tif
  - 560행×900열, 5m 해상도, EPSG:5186
  - bounds(5186): left=231419 bottom=414155 right=235919 top=416955
  - 표고: 35.0~190.4m, NaN 0%
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
