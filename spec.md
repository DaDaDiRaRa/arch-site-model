# arch-site-model — 기술 사양서

> 대지 주소 입력만으로 주변 지형·건물 3D 대지모델을 자동 생성. **MCP 서버 + 배포 웹앱 + SketchUp 확장**.
> 본 문서는 **구현 완료된 현재 상태**를 기술한다. 미구현 항목은 `[목표]`로 명시.

---

## 0. TL;DR

```
입력:  대지 주소(지번·도로명) + 반경(m)
처리:  주소→좌표(VWorld) + 건물/지적 API + 로컬 DEM 비축 → 좌표·층수·지형 계산
출력:  .skp (SketchUp) + .3dm (rhino3dm, 정사영상 텍스처) + geometry JSON(브라우저 3D)
소비:  ① MCP 도구(Claude)  ② 웹앱(FastAPI+React)  ③ SketchUp 확장(.rbz)  — 엔진 공유
원칙:  gro_flo_co(실제 층수)로 직접 돌출 — AI 추정 0%, "재현이지 상상이 아님"
```

---

## 1. 시스템 아키텍처

### 앱 분리

```
arch-site-context (인문·생활 맥락)     arch-site-model (물리 대지모델링, 본 앱)
  KOSIS 인구·경제                       건물 footprint·층수·지형·이격
  Kakao 반경 시설                       DEM TIN, 쿼드 솔리드 매스
       └─── 공유: 주소·좌표·PNU·BBOX ───┘
```

### 실행 모델

엔진(`pipeline.generate`)은 "무엇을 어디에 몇 m로"만 계산하고, 조립/렌더는 소비자에 위임한다:

- **MCP**: `.skp` 코드 문자열 생성 → SketchUp MCP `build_model`(외부)이 조립.
- **웹앱**: FastAPI가 엔진을 HTTP로 감싸 `.3dm`/정사영상 + `geometry` JSON 반환 → 브라우저(three.js) 미리보기·다운로드.
- **SketchUp 확장(.rbz)**: 데스크톱 Ruby가 `geometry`를 받아 네이티브 조립(+정사영상 드레이프는 B2 예정).

→ 무거운 처리(VWorld/DEM/GDAL)는 중앙(엔진·백엔드)에 집중, 클라이언트는 얇게.

### 스택

| 컴포넌트 | 기술 |
|---|---|
| MCP 서버 | FastMCP (Python) |
| 웹 백엔드 | FastAPI + uvicorn (`src/api.py`, Cloud Run 배포) |
| 웹 프론트 | React + Vite + Tailwind (`frontend/`), 브라우저 3D = three.js |
| SketchUp 확장 | Ruby `.rbz` (`sketchup_ext/`, Sketchup::Http + Geom::PolygonMesh) |
| 지오메트리 | shapely, pyproj |
| .skp 출력 | SketchUp MCP `build_model` (외부) |
| .3dm 출력 | rhino3dm 8.17.0 (정사영상 비트맵 텍스처 포함) |
| 지형 DEM | rasterio + scipy (offline bake, guarded CloughTocher 보간) |

---

## 2. 데이터 소스

모두 실측 검증됨.

### 2.1 주소 → 좌표

```
GET https://api.vworld.kr/req/address
  service=address  request=getcoord  version=2.0
  crs=epsg:4326  type=PARCEL|ROAD  format=json  key=<KEY>  domain=<DOMAIN>
→ response.result.point.{x: 경도, y: 위도}
```

**지번·도로명 모두 지원**: 지번(`type=PARCEL`)으로 먼저 조회하고 `NOT_FOUND`면 도로명(`type=ROAD`)으로
재시도한다(`geocode()`의 `_ADDR_TYPES` 루프). `domain`은 키 등록 도메인과 일치해야 함(빈 값이면 파라미터 제외).

### 2.2 건물 footprint + 층수 (LT_C_SPBD)

```
GET https://api.vworld.kr/req/data
  service=data  request=GetFeature  data=LT_C_SPBD
  key=<KEY>  domain=<DOMAIN>  format=json
  geomFilter=BOX(minx,miny,maxx,maxy)  geometry=true  crs=EPSG:4326  size=<N>
→ response.result.featureCollection.features[]
```

주요 필드:

| 필드 | 의미 | 용도 |
|---|---|---|
| `gro_flo_co` | 지상층수 | 높이 = gro_flo_co × 층고 |
| `bd_mgt_sn` | 건물관리번호 25자리 | 건축물대장 연결키 |
| `buld_nm` | 건물명 | 라벨 |
| geometry.type | MultiPolygon | footprint 돌출 |

`gro_flo_co` 0/null 건물 존재 가능 → `missing_floors_policy` 분기 처리.

### 2.3 대지 경계 (LP_PA_CBND_BUBUN)

```
data=LP_PA_CBND_BUBUN  (나머지 파라미터 동일)
```

| 필드 | 의미 |
|---|---|
| `pnu` | 필지고유번호 19자리 |
| geometry.type | MultiPolygon (대지 경계) |

### 2.4 지형 DEM (오프라인 비축)

NGII 수치지형도Ver2.0 SHP → 오프라인 굽기 → `geo_store/dem_*.tif`.

```
geo_store/
  manifest.json       비축 타일 목록 (region/file/bounds/cell_m/updated)
  dem_*.tif           GeoTIFF, EPSG:5186, float32
```

현재 비축: `dem_daejeon_36710065_66.tif` (대전 서구, 5m 해상도, 560행×900열).

---

## 3. MCP 도구 계약

### 3.1 `check_site_data(address, radius_m=250)`

생성 가능 여부 선검사. 실제 모델 생성 없음.

```jsonc
// 반환
{
  "ok": true,
  "address": "대전광역시 서구 괴정동 358",
  "coord": { "lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326" },
  "bbox": [minx, miny, maxx, maxy],
  "buildings": { "available": true, "count": 37, "with_floors": 34 },
  "cadastral": { "available": true, "count": 12 },
  "terrain": { "available": true, "tile": "dem_daejeon_36710065_66.tif" },
  "warnings": ["건물 3개는 gro_flo_co 누락/0 → 기본 높이 적용 예정"]
}
```

### 3.2 `generate_site_model(address, radius_m, floor_height_m, outputs, layers, output_dir, missing_floors_policy, setback)`

주소 → 3D 모델.

**파라미터:**

| 이름 | 기본값 | 설명 |
|---|---|---|
| `address` | (필수) | 대지 주소 |
| `radius_m` | `250` | 반경 (m) |
| `floor_height_m` | `3.0` | 기본 층고 (m) |
| `outputs` | `["skp"]` | `"skp"` / `"3dm"` 복수 선택 가능 |
| `layers` | `{"buildings": true}` | 활성화할 레이어 |
| `output_dir` | `"output/"` | .3dm 저장 디렉터리 |
| `missing_floors_policy` | `"default"` | 층수 누락 처리 정책 |
| `setback` | `false` | 이격면 분석 (stub) |

**layers 옵션:**

| 값 | 동작 |
|---|---|
| `{"buildings": true}` | 건물 매싱만 |
| `{"buildings": true, "terrain": true}` | 지형 TIN + 건물 앉힘 |
| `{"buildings": true, "cadastral": true}` | 건물 + 대지 경계 폴리곤 |
| `{..., "terrain": true, "orthophoto": true}` | 지형에 정사영상 텍스처 드레이프 (**`.3dm` 전용**, `outputs`에 `"3dm"` 필요) |

**missing_floors_policy:**

| 값 | 동작 |
|---|---|
| `"default"` | 1층(3m) 적용, `flagged=False` |
| `"skip"` | 층수 누락 건물 제외 |
| `"flag"` | 1층 적용 + `.3dm` `buildings_unverified` 레이어(주황) + `.skp` 이름 `[층수미확인]` 접미사 |

**반환 구조:**

```jsonc
{
  "ok": true,
  "outputs": {
    "skp": {
      "code": "...",          // SketchUp MCP build_model 입력 Python 코드
      "solids": 12,
      "cadastral_parcels": 5  // 지적 비활성 시 0
    },
    "3dm": {
      "path": "C:/.../output/대전광역시_서구_괴정동_358.3dm",
      "solids": 12
    }
  },
  "stats": {
    "buildings": 12,          // 취득 건물 수
    "solids": 12,             // 생성된 BuildingSolid 수 (skip 정책 시 감소)
    "with_floors": 9,
    "flagged": 0,             // missing_floors_policy=flag 시 해당 건물 수
    "cadastral_parcels": 5,
    "terrain_triangles": 448, // 지형 활성 시
    "elev_range_m": [35.2, 112.7],  // 지형 활성 시
    "origin_offset": [233142.5, 415678.2]  // EPSG:5186, 반드시 보존
  },
  "provenance": {
    "building_src": "VWorld LT_C_SPBD",
    "floor_height_m": 3.0,
    "missing_floors_policy": "default",
    "radius_m": 250,
    "fetched_at": "2026-07-01T03:15:00+00:00",
    "cadastral_src": "VWorld LP_PA_CBND_BUBUN",  // cadastral 활성 시
    "terrain_tile": "dem_daejeon_36710065_66.tif",  // 지형 활성 시
    "setback_analysis": "stub"  // setback=True 시
  },
  "warnings": []
}
```

**추가 필드(옵션):**

- `outputs.3dm.orthophoto` — `{image_path, missing_tiles, zoom}` (orthophoto 활성 시). PNG는 `.3dm`과 같은 폴더에 저장.
- `geometry` — `include_geometry=True`(웹 백엔드 `src/api.py`가 지정)일 때만 포함. 브라우저 3D 미리보기(F2)·SketchUp
  확장용 경량 지오메트리(로컬 미터): `buildings[{footprint, holes, base_z, height, flagged}]`, `terrain{vertices, triangles}`,
  `ortho_extent_m`. MCP 응답 비대화 방지로 기본 `False`(=`null`).

### 3.3 `preview_site(address, radius_m=250, floor_height_m=3.0)`

모델 생성 없이 건물 목록·층수·예상 규모 미리보기. 실제 `.skp`/`.3dm` 파일은 생성하지 않는다.

- 건물별 `name`, `floors`, `height_m`, `footprint_area_m2`, `has_courtyard` 반환
- `summary`: 건물 수, 층수 통계(max/avg), 중정 수, 지적 수, 지형 가용 여부

### 3.4 `generate_site_tiles(address, radius_m=500, tile_size_m=200.0, floor_height_m, layers, missing_floors_policy)`

대량 건물(반경 500m+) 시 `generate_site_model`의 단일 `code` 문자열이 `build_model` 호출
인자로 감당 못 할 만큼 커지는 문제(수백 KB, 대략 건물 1000동당 ~250KB) 해결용.

- VWorld 조회 + `origin_offset` 산출은 전체 반경 1회만 수행 — 타일 경계에서 중복 조회나
  좌표 불일치가 생기지 않는다.
- footprint(또는 지적 parcel) 중심점 기준으로 `tile_size_m` 격자에 배정 후 타일마다 별도
  `code`를 생성한다. `layers={"terrain": true}`면 지형 TIN도 같은 격자로 분할된다.
- `.3dm`(`outputs`/`output_dir`) 미지원 — `.skp` 코드 분할 전용.

```jsonc
{
  "ok": true,
  "tile_size_m": 200.0,
  "tiles": [
    {
      "tile_id": "3_-1",
      "tile_bbox_m": [600.0, -200.0, 800.0, 0.0],
      "code": "...",
      "solids": 42,
      "cadastral_parcels": 18,
      "terrain_triangles": 220
    }
  ],
  "stats": {
    "buildings": 620, "solids": 620, "with_floors": 590,
    "tile_count": 14, "cadastral_parcels": 210,
    "origin_offset": [233142.5, 415678.2]
  },
  "warnings": []
}
```

오케스트레이터는 `tiles[]`를 순서대로 `build_model`에 호출해 조립한다. `stats.origin_offset`은
모든 타일에 공통 적용되므로 반드시 보존한다.

---

## 4. 좌표계 규칙

| 단계 | 좌표계 | 단위 |
|---|---|---|
| 외부 API 응답 | EPSG:4326 (lon/lat) | 도(°) |
| 내부 계산 | EPSG:5186 (Korea 2000 중부원점) | 미터 |
| SketchUp MCP | SketchUp 로컬 | 인치 (`× M2I = 39.3701`) |
| Rhino .3dm | 로컬 미터 | 미터 |

**origin_offset**: 건물 군의 EPSG:5186 최소 bbox 좌하단.
- 로컬 좌표 = 5186 좌표 − offset (수백 km 절댓값 제거)
- 복원: `x_abs = x_local + offset[0]`
- `pyproj.Transformer` 반드시 `always_xy=True`

---

## 5. 지오메트리 생성 로직

### 5.1 건물 쿼드 솔리드

변마다 수직 쿼드 1개. 삼각형 분할 없음.

```python
def extrude_solid(fp_m, base_z_m, height_m):
    fp = [(x*M2I, y*M2I) for (x,y) in fp_m]
    bz = base_z_m * M2I; h = height_m * M2I; n = len(fp)
    g = GeometryInput()
    g.set_vertices([SUPoint3D(x,y,bz) for x,y in fp] + [SUPoint3D(x,y,bz+h) for x,y in fp])
    # 바닥(정점 역순=하향), 천장(정순=상향), 옆면(쿼드)
    ...
```

규칙: `import` 금지, 인치 단위, X=폭/Y=깊이/Z=높이, 바닥=역순/천장=정순.

MultiPolygon → 폴리곤별 분리 처리. 홀(중정)은 구현됨(확장1): `holes_m` → `.skp`는 바닥/천장
`add_face_inner_loop` + 내벽 쿼드, `.3dm`은 홀 링마다 별도 내벽 Extrusion.

### 5.2 건물 높이

```
height_m = gro_flo_co × floor_height_m
```

AI 추정 없음. 누락 시 `missing_floors_policy` 적용.

### 5.3 지형 TIN

DEM 격자 → 정규격자 삼각망 (칸마다 대각 방향 교차, `(ix+iy)%2`).
내부 edge softening 적용. 등고선 직접 TIN의 "평평한 삼각형" 문제 원천 차단.

```python
@dataclass
class TerrainMesh:
    vertices: list[tuple]   # (x, y, z) 인치
    triangles: list[tuple]  # 정점 인덱스 3개
```

TerrainMesh.vertices는 인치 단위 (SketchUp용). Rhino 출력 시 `/M2I` 변환 필요.

### 5.4 건물-지형 정합

```
base_z = min(각 footprint 꼭짓점의 DEM 고도) − 0.5m(묻힘여유)
```

중심 1점이 아닌 최저 꼭짓점 기준 → 경사지에서 건물 뜨는 현상 방지.

### 5.5 대지 경계

```python
@dataclass
class CadastralParcel:
    pnu: str
    footprint_m: list[tuple[float, float]]  # 로컬 미터
```

MultiPolygon → 가장 큰 외곽 링만. shapely 파싱 오류(꼭짓점 < 4) → 건너뜀.

---

## 6. 출력 포맷

### 6.1 SketchUp (.skp)

`src/output/skp_mcp.py`가 Python 코드 문자열 생성 → Claude 오케스트레이터가 `build_model` 호출.

- 건물: `extrude_solid` 쿼드 솔리드
- 지형: 격자 삼각망 + softening
- 지적: Z=0 평면 폴리곤 그룹 (`CADASTRAL` 섹션)
- flagged 건물: 이름에 `[층수미확인]` 접미사

### 6.2 Rhino 3D (.3dm)

`src/output/rhino.py` —
`write_3dm(solids, terrain, path, offset, cadastral=None, ortho_image=None, ortho_extent_m=None) -> str`

| 레이어 | 색상 | 객체 타입 |
|---|---|---|
| `buildings` | steel blue | `rhino3dm.Extrusion` (캡 포함) |
| `buildings_unverified` | orange | `rhino3dm.Extrusion` (policy=flag 시) |
| `terrain` | olive green | `rhino3dm.Mesh` (정사영상 시 planar UV + 비트맵 텍스처 머티리얼) |
| `cadastral` | sandy yellow | `rhino3dm.PolylineCurve` at Z=0 |

- origin_offset: `model.Strings["origin_offset_x/y"]` + 각 객체 `SetUserString` 이중 기록
- 좌표계: 로컬 미터 (SketchUp 인치 변환 없음)
- 파일명: `{주소_안전화}.3dm` (특수문자 → `_`, 최대 60자)
- 정사영상(Tier 1): `ortho_image`+`ortho_extent_m` 지정 시 지형 메시에 `CreatePlaneMapping`으로 위→아래
  평면 UV 부여 + 비트맵 텍스처 머티리얼(파일명 참조 — PNG를 `.3dm`과 같은 폴더에 둬야 유효).

---

## 7. 파이프라인 (`src/pipeline.py`)

```
generate(address, radius_m, floor_h_m, outputs, layers, output_dir,
         missing_floors_policy, setback, client)

1. 주소 → 좌표 (VWorld geocode)
2. 좌표 + radius → BBOX (4326) / 5186 변환 + origin_offset 산출
3. 건물 취득   LT_C_SPBD → features_to_solids (missing_policy 적용)
4. 지형 취득   manifest → find_tile → clip_dem → grid_to_tin (layers.terrain 시)
5. 건물 앉힘   seat_building (min-vertex, 지형 활성 시)
6. 지적 취득   LP_PA_CBND_BUBUN → features_to_parcels (layers.cadastral 시)
6.5 정사영상   ortho.build_mosaic (layers.orthophoto + outputs=3dm 시) → PNG + 로컬 범위
7. SKP 코드 생성  build_skp_code(solids, terrain, cadastral)
8. .3dm 저장  write_3dm(solids, terrain, path, offset, cadastral, ortho_image, ortho_extent_m)
9. geometry JSON  _build_geometry(solids, terrain, ortho) (include_geometry=True 시 — 웹/확장용)
10. provenance + origin_offset 기록
```

DEM 범위 밖 → `ok: true` + `warnings` 추가 + 건물만 생성 (조용한 fallback). 정사영상도 키 없음/타일
실패/지형 미생성 시 경고 후 생략(조용한 fallback).

---

## 8. 모듈 구조

```
src/
  server.py              FastMCP 서버 (도구 4개: check_site_data, generate_site_model,
                          preview_site, generate_site_tiles)
  pipeline.py            generate() 오케스트레이션 (include_geometry 시 geometry JSON 포함)
  api.py                 FastAPI 웹 백엔드 (/api/generate → geometry + .3dm/정사영상 다운로드, frontend/dist 서빙)
  tiles.py               generate_tiles() — 대량건물 타일분할
  site_check.py          check_site_data 핵심 로직
  preview.py             preview_site 핵심 로직
  config.py              환경변수 (VWORLD_KEY, M2I=39.3701, DEFAULT_FLOOR_H_M=3.0)
  geo/
    geocode.py           주소 → 좌표
    bbox.py              반경 → BBOX (4326)
    crs.py               4326 ↔ 5186 변환 + origin_offset + apply_offset
    vworld.py            VWorldClient (페이지네이션 내장)
    ortho.py             정사영상 WMTS 타일 다운로드 + 모자이크 + EPSG:5186 재투영 (Tier 1)
  geometry/
    building.py          BuildingSolid + features_to_solids + floors_of
    terrain_mesh.py      TerrainMesh + grid_to_tin
    seating.py           seat_building (min-vertex)
    cadastral.py         CadastralParcel + features_to_parcels
  output/
    skp_mcp.py           build_skp_code → SketchUp MCP Python 코드 문자열
    rhino.py             write_3dm → .3dm 파일
  terrain/
    store.py             load_manifest + find_tile
    contour_bake.py      등고선 SHP → DEM(.tif) 오프라인 굽기 (CLI)
    dem.py               clip_dem + DEMPatch (표고 보간)
```

그 외: `frontend/`(React 웹앱 + three.js 브라우저 미리보기), `sketchup_ext/`(SketchUp `.rbz` 확장 + `build_rbz.py`),
`scripts/dem_staircase.py`(지형 계단현상 진단), `docs/`(`deploy.md`·`sketchup_extension.md`·`orthophoto_texture_plan.md`).

---

## 9. 구현 상태

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 프로젝트 스캐폴드, FastMCP 서버 기동 | ✅ |
| 1 | `check_site_data` 선검사 | ✅ |
| 2 | `generate_site_model` 건물 매싱 → .skp | ✅ |
| 3A | 오프라인 DEM 굽기 (contour_bake.py) | ✅ |
| 3B | 런타임 지형 TIN + 건물 앉힘 | ✅ |
| 4 | `.3dm` 이중 출력 + origin_offset 보존 | ✅ |
| 5 | 지적 레이어 + 층수 누락 정책 + provenance | ✅ |
| 확장1 | 홀(중정) 처리 — `holes_m` + inner loop | ✅ |
| 확장2 | `preview_site` — 생성 없이 건물목록·규모 미리보기 | ✅ |
| 확장3 | 이격면 실연동 (arch-law-diagnose) | 🚧 블로커 |
| 확장4 | `generate_site_tiles` — 대량건물 타일분할 | ✅ |
| 확장5 | 정사영상 텍스처 Tier 1 — 지형 planar UV 드레이프 (.3dm) | ✅ |
| 웹앱 | FastAPI 백엔드 + React UI + 브라우저 3D 미리보기(F2) + Cloud Run 배포 | ✅ |
| 확장(Phase B) | SketchUp `.rbz` — 지형+건물 조립(B1) | ✅ (B2 정사영상 드레이프 예정) |
| 지형 개선 | 계단현상 완화 — guarded CloughTocher 재bake | ✅ |
| geocode | 지번+도로명(PARCEL→ROAD) 지원 | ✅ |

---

## 10. 확장 로드맵

| 항목 | 내용 | 상태 |
|---|---|---|
| 이격면 실연동 | arch-law-diagnose 조닝 파라미터 → 이격 오프셋 | 🚧 블로커: arch-law-diagnose가 REST-only + 설계 프로그램 입력을 요구, 좁은 API 계약 없음 (§9 확장3 참고) |
| 정사영상 Tier 2a | SketchUp 확장 정사영상 드레이프 (`Face#position_material`) | 미착수 (Phase B2) — `docs/orthophoto_texture_plan.md` §5 |
| NGII 정사영상 소스 | VWorld → NGII(공공누리 1유형) 전환 | 보류: 서버사이드 키 접근 + EPSG:5179 타일 구현 필요 |
| 지형 계단현상 완전제거 | 라플라스 harmonic 인필/ANUDEM류 격자 솔버 | 선택: 1차(guarded CloughTocher) 완료, 부분개선 한계 돌파용 |

---

## 11. 자주 하는 실수

1. **좌표 순서**: VWorld `x=경도, y=위도`. pyproj `always_xy=True` 필수.
2. **gro_flo_co 0/null**: `floors_of()` → `None`. 추정 금지. `missing_floors_policy` 정책 적용.
3. **SketchUp 인치 단위**: `×M2I` 없으면 극소 모델 생성.
4. **origin_offset 보존**: `stats.origin_offset` 버리면 절대 위치 복원 불가.
5. **TerrainMesh 단위**: vertices는 인치. Rhino 출력 시 `/M2I` 변환 필요.
6. **cadastral 기본 비활성**: `layers={"cadastral": true}` 명시 필요. 기본값은 건물만.
7. **DEM bbox 좌표계**: `clip_dem`의 `bbox_5186`은 EPSG:5186. `_bbox_4326_to_5186()` 헬퍼 사용.
8. **등고수치/수치 필드명**: 수치지형도Ver2.0 한국어 필드명. 영문 ID(`CONT`, `NUME`)와 구별.
9. **PowerShell 한국어**: `-Encoding utf8`로 파일 출력 후 확인.
10. **DEM fallback**: DEM 타일 없거나 범위 밖이면 지형 생략 + `warnings` 추가. 반드시 확인.
11. **타일별 origin_offset 오해 금지**: `generate_site_tiles`의 `stats.origin_offset`은 전체 반경
    기준 단일 값 — 타일마다 다른 원점을 쓰지 않는다. 타일별로 재계산하면 좌표가 어긋난다.

---

## 12. 테스트

```powershell
# 단위 테스트 (오프라인, API 미호출)
python -m pytest tests/ --ignore=tests/test_integration_api.py -v

# 실 API 연동 테스트 (VWORLD_TEST_KEY 필요)
python -m pytest tests/test_integration_api.py -v
```

모든 VWorld API 호출은 mock (`tests/conftest.py`, `FakeClient`/`FakeClientMulti`).
합성 데이터로만 동작 — 실제 키·파일 없이 전체 스위트 실행 가능.
