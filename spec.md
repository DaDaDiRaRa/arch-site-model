# arch-site-model — 기술 사양서

> 대지 주소 입력만으로 주변 지형·건물 3D 대지모델을 자동 생성. **MCP 서버 + 배포 웹앱 + SketchUp 확장**.
> 본 문서는 **구현 완료된 현재 상태**를 기술한다. 미구현 항목은 `[목표]`로 명시.

---

## 0. TL;DR

```
입력:  대지 주소(지번·도로명) + 반경(m)
처리:  주소→좌표(VWorld) + 건물/지적 API + 로컬 DEM·도로·수계 비축 → 좌표·층수·지형(적응형 TIN)·
       도로/보도/차선(통합표면)·수계 계산 + 자동 QA(검증)
출력:  .skp (SketchUp) + .3dm (rhino3dm, 정사영상 텍스처) + geometry JSON(브라우저 3D·확장, 정사영상 PNG)
소비:  ① MCP 도구(Claude)  ② 웹앱(FastAPI+React, SSAO·QA 핀)  ③ SketchUp 확장(.rbz — 건물+지형+도로+수계+정사영상)  — 엔진 공유
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
- **SketchUp 확장(.rbz)**: 데스크톱 Ruby가 `geometry`를 받아 네이티브 조립(건물=실제 face, 지형 mesh),
  정사영상 PNG를 받아 지형에 드레이프(B2). 대반경(>500m)은 타일을 하나씩 받아 점진 조립(§3.6).

→ 무거운 처리(VWorld/DEM/GDAL)는 중앙(엔진·백엔드)에 집중, 클라이언트는 얇게.

### 스택

| 컴포넌트 | 기술 |
|---|---|
| MCP 서버 | FastMCP (Python) |
| 웹 백엔드 | FastAPI + uvicorn (`src/api.py`, Cloud Run 배포) |
| 웹 프론트 | React + Vite + Tailwind (`frontend/`), 브라우저 3D = three.js (+ EffectComposer/GTAOPass SSAO, QA 결함 핀) |
| SketchUp 확장 | Ruby `.rbz` (`sketchup_ext/`, Sketchup::Http, 건물=add_face·정사영상=position_material) |
| 지오메트리 | shapely, pyproj |
| 지형 TIN | scipy (적응형 error-bounded TIN — 없으면 정규격자 fallback) |
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

**geomFilter BOX 10km² 한도**: VWorld data API의 `geomFilter=BOX`는 요청영역 10km² 이내만 허용
(반경 ~1.58km 상한). `VWorldClient.get_features`가 bbox>~9.5km²면 서브박스(≤9km², 한 변 ~3km)로
분할 조회 후 병합·중복제거(경계 피처는 인접 서브박스에 중복 → `id`/`bd_mgt_sn`/`pnu` 키로 dedup).
`count()`도 분할 대응. 상한 ~반경 15km(서브박스 100개). 호출자(pipeline/tiles/site_check/preview)
투명 적용 — 반경 2km+ 조회 가능.

### 2.3 대지 경계 (LP_PA_CBND_BUBUN)

```
data=LP_PA_CBND_BUBUN  (나머지 파라미터 동일)
```

| 필드 | 의미 |
|---|---|
| `pnu` | 필지고유번호 19자리 |
| geometry.type | MultiPolygon (대지 경계) |

### 2.4 지형 DEM (오프라인 비축)

NGII 수치지형도Ver2.0 SHP → 오프라인 굽기 → COG GeoTIFF. 타일은 공개 GCS 버킷에서 GDAL
`/vsicurl` 윈도우 range-read로 서빙(이미지에 타일 미포함) — 경로는 `config.DEM_TILE_BASE`가 로컬
`geo_store` ↔ 원격 GCS를 결정. `manifest.json`은 항상 로컬(git 추적)이고 타일만 원격 base로 해석.

```
geo_store/
  manifest.json       비축 DEM 타일 목록 (region/file/bounds/cell_m/updated) — 로컬/git
  road_manifest.json  비축 도로 GeoJSON 지역 목록 — 로컬/git (§2.5)
  water_manifest.json 비축 수계 GeoJSON 지역 목록 — 로컬/git (§2.6)
  dem_*.tif           GeoTIFF, EPSG:5186, float32 — git 미추적(.gitignore), GCS에서 서빙
  roads_*.geojson     도로/보도/중심선 벡터, EPSG:5186 — gitignore, ROAD_BASE로 GCS 서빙
  water_*.geojson     수계(하천·호소) 벡터, EPSG:5186 — gitignore, WATER_BASE로 GCS 서빙
```

한 질의가 여러 타일에 걸치면 겹치는 타일을 전부 병합(mosaic)해 사용한다(§7).

**소스 준비**: 시·도 SHP → bake. (a) 좌표대 재투영 — 동부원점(EPSG:5187) 지역 SHP는 베이크 시
내부 기준 5186으로 재투영(`read_contours(target_crs="EPSG:5186")`, 5186이면 no-op). (b) 도엽
중복제거 — 시·도 단위 다운로드는 경계 도엽을 각 구 폴더에 바이트 동일 복사하므로 `_find_shp`가
도엽 번호(`수치지도_<도엽>` 폴더명)로 dedup. (c) 거리제한 채움 — 볼록껍질 밖 nan 셀을 실데이터
200m 이내만 채우고 먼 셀은 nodata 유지(무제한 외삽 시 지역 경계에서 mosaic 오염). §4·§8 참고.

현재 비축: 6개 광역단체 120타일(10km 격자, 5m 해상도) — 대전 14·서울 15·부산 24·대구 36·
울산 20·세종 11. 공개 GCS `gs://arch-site-model-dem`(asia-northeast3, `/vsicurl` 서빙).

### 2.5 도로·보도 (오프라인 비축, Phase R)

DEM과 동일 사정(실시간 API 없음, 로컬 SHP뿐) — 수치지도 A계열을 오프라인으로 지역 GeoJSON에 굽는다
(`src/terrain/road_bake.py`). 도로경계 `A0010000`(폴리곤, 노면), 도로중심선 `A0020000`(라인, 평탄화 척추
+ 실측 `도로폭`·`차로수`를 props에 `{"cl":1,"n","w"}`로 담음 — 다차선 마킹용), 보도 `A0033320`(폴리곤). 한
FeatureCollection에 도로(properties {})·중심선({"cl":1})·보도({"sw":1})로 담고 `road_manifest.json`에 등록.
좌표계·도엽 dedup·5187→5186 재투영은 contour_bake 헬퍼 재사용.

**갭 채움**: `synthesize_gap_roads`가 A0010000 경계 폴리곤이 빠뜨린 소로·골목을 A0020000 중심선의 실측
`도로폭`으로 버퍼링해 노면(`{"syn":1}`)으로 합성한다(실측 커버리지 89%→100%, `--no-fill-gaps`로 끔).

**서빙**: `road_manifest.json`만 git 추적, `roads_*.geojson`은 gitignore(DEM 타일과 동일 원칙). 배포는
공개 GCS에 올리고 `ROAD_BASE=gs://<버킷>/roads`(기본=로컬 `GEO_STORE`)로 서빙 — 앱이 HTTP fetch+캐시로
읽는다(`config.road_file_path`가 gs→https 변환, `road._read_geojson_text`가 fetch). DEM은 GDAL `/vsicurl`
윈도우 읽기지만 도로는 json+shapely 전량 파싱이라 HTTP fetch. 미배포 시 클라우드 도로는 조용히 생략(로컬
백엔드는 정상). 상세 `docs/deploy.md` §5.

입체(고가/교량 A0070000, 지하차도 A0090000, 터널 A0110020)는 미베이크 → 통합 표면에서 자동 제외.

### 2.6 수계 (오프라인 비축)

도로와 동형(실시간 API 없음, 로컬 SHP뿐) — 수치지도 E계열 수계 면 SHP(`N3A_E0*` 하천경계·호소)을
오프라인으로 지역 GeoJSON(EPSG:5186)에 굽는다(`src/terrain/water_bake.py`, `road_bake`와 동형: 좌표대
재투영·도엽 dedup 헬퍼 재사용) → `water_manifest.json`에 등록. 런타임(`src/geometry/water.py`)이 bbox 클립.
`water_manifest.json`만 git 추적, `water_*.geojson`은 gitignore. 배포는 `WATER_BASE=gs://<버킷>/…`로 GCS
서빙(도로와 동일 원칙). 도로/보도는 지형에 드레이프하지만 **수면은 경계 둑 DEM 저백분위를 수면표고로
잡아 표고고정 평면**으로 만들고 지형을 물 아래로 버닝한다(§5.7).

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
| `radius_m` | `250` | 반경 (m). 2km+ 지원 — VWorld BOX 10km² 한도를 클라이언트가 bbox 분할·병합으로 우회(§2.2) |
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
| `{..., "terrain": true, "roads": true}` | 도로 노면(A0010000)·보도(A0033320)·차선(A0020000 차로수 기반 다차선) 추가. 지형 있으면 **통합 표면**(§5.6). 도로 벡터 비축(`road_manifest.json`) 필요 — 없으면 조용한 fallback |
| `{..., "terrain": true, "water": true}` | 수계(E계열 하천·호소) 추가 → 표고고정 평면 수면 + 지형 물 아래로 버닝(§5.7). 수계 벡터 비축(`water_manifest.json`) + 지형(DEM) 필요 — 없으면 조용한 fallback |
| `{..., "qa": true}` | 자동 QA(검증) 실행 → `result.qa = {findings, summary}`(건물 앉힘·겹침·footprint 유효성·지형 스파이크). 다른 레이어와 무관하게 켤 수 있음. 웹 UI가 결함 목록 + F2 3D가 수직 핀 표시(§3.7) |
| `{..., "terrain": true, "orthophoto": true}` | 지형에 정사영상 텍스처 드레이프. `.3dm`은 비트맵 UV 직접, 웹/데스크톱 확장은 `files.ortho_png` PNG를 받아 드레이프(§3.2 추가 필드) |
| `{..., "zoning": true}` | 사이트 용도지역 조회(형제 앱 arch-law-graph `/api/zoning`, `ZONING_BASE` 필요) → `result.zoning{zone_name, zone_key, sido, sigungu}`. 미설정/미도달 시 조용한 fallback |

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
  "qa": {                     // layers.qa=True 시 (§3.7)
    "findings": [{ "severity": "warn", "kind": "steep_site", "label": "급경사 대지", "message": "...", "at": [x, y], "name": "..." }],
    "summary": { "total": 3, "warnings": 1, "passed": false, "stamp": "경고 1건 — 검토 필요", "by_kind": { "steep_site": 1 } }
  },
  "trust_report": { /* A-1 — 항상 부착: buildings(실측/추정 %)·terrain·orthophoto·qa·meta·caveats(정직한 한계 고지) */ },
  "zoning": { /* layers.zoning 시: {zone_name, zone_key, sido, sigungu, address, src} 또는 null */ },
  "warnings": []
}
```

**추가 필드(옵션):**

- `trust_report` — **항상 부착**(A-1). 조립된 result 위 순수 뷰(`src/trust_report.py`): `buildings{total, measured,
  estimated, measured_pct}`·`terrain`·`orthophoto`·`qa`·`meta`·`caveats`(정직한 한계 고지). 웹 UI "신뢰도 리포트" 패널.
- `zoning` — `layers.zoning=True` + `ZONING_BASE`(형제 앱 arch-law-graph) 시. `{zone_name, zone_key, sido, sigungu,
  address, src}` 또는 `null`(미설정/미도달). 웹 UI 배지.
- `outputs.3dm.orthophoto` — `{image_path, missing_tiles, zoom}` (orthophoto 활성 시). PNG는 `.3dm`과 같은 폴더에 저장.
- `geometry` — `include_geometry=True`(웹 백엔드 `src/api.py`가 지정)일 때만 포함. 브라우저 3D 미리보기(F2)·SketchUp
  확장용 경량 지오메트리(로컬 미터): `buildings[{footprint, holes, base_z, height, flagged, verified}]`(A-2:
  `verified=false`=층수 추정), `terrain{vertices, triangles}`, `ortho_extent_m`(정사영상 로컬 범위). MCP 응답 비대화 방지로 기본 `False`(=`null`).
- **정사영상은 출력 포맷 무관 생성**: `orthophoto` 활성 시 백엔드는 `.3dm` 여부와 상관없이 모자이크 PNG를
  굽고 `geometry.ortho_extent_m`(로컬 미터 범위)와 웹 응답 `files.ortho_png`(다운로드 URL)를 반환한다.
  데스크톱 SketchUp 확장이 이 PNG를 받아 지형에 드레이프(B2 — 평면 top-down 투영, 각 face 양면 적용,
  "Shaded With Textures" 뷰 자동전환). VWorld 위성영상은 실영상 취득 검증됨(회색 placeholder 아님).

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

### 3.5 지형 LOD — 적응형 TIN

DEM 격자를 삼각망으로 바꾸는 방식을 `config.TERRAIN_MAX_ERROR_M`(기본 `0.25`m)로 분기한다.
`src/geometry/terrain_mesh.py::build_tin(dem, max_error_m)` 디스패처:

- `max_error_m > 0` → `adaptive_tin`: **오차 한계 적응형 TIN**. scipy Delaunay 위에서 greedy
  삽입(수직오차 최대 셀부터 정점 추가)으로 **모든 셀의 수직오차 ≤ `TERRAIN_MAX_ERROR_M`**를
  **목표로 한다**(정점 상한 ≈90%까지 삽입; 극단 급경사서 상한 도달 시 예외 가능). 평탄부는 큰 삼각형,
  복잡부는 조밀 삼각형 → 정확도 유지하며 삼각형 급감.
- `max_error_m == 0` → `grid_to_tin`: 정규격자(칸마다 2삼각형). 과거 기본 방식.

효과: 25cm 정확도에서 지형 삼각형 약 86% 감소(실측 신반포 250m: 19602→2467, 최대오차 0.25m).
대반경 지형이 훨씬 가벼워지면서 정확도는 유지 — 데스크톱 조립·브라우저 렌더 부담 완화.

**scipy는 프로덕션 의존성**(`requirements.txt`). pydelatin은 C 컴파일러 필요(설치 취약)라 순수
파이썬 scipy 구현을 택했다. scipy 미탑재 환경은 정규격자로 조용히 fallback한다.

### 3.6 대반경 타일 순차조립 — `src/tiles_stream.py`

데스크톱 SketchUp은 수만 엔티티를 **한 번에** 조립하면 멈춘다(반경 1–2km = 수천 동). 확장은
반경 >500m일 때 타일을 하나씩 받아 진행바+취소 버튼과 함께 점진 조립해 매 스텝의 페이로드·
메모리·조립량을 유한하게 유지한다(1km 동작 검증). `generate_site_tiles`(§3.4, `.skp` 코드 분할)와
달리 확장용 **geometry JSON** 스트리밍 계약이다.

- `tile_plan(address, radius_m, tile_size_m)` → 고정 `origin_offset` + 타일 bbox 격자만 반환
  (지오메트리 없는 작은 JSON). 확장이 이 목록으로 타일 수를 알고 순차 호출한다.
- `generate_tile(bbox_4326, bbox_5186, offset, layers)` → **한 타일**의 geometry JSON. 건물은
  **중심점이 그 타일에 드는 것만** 포함(경계 중복 없음), 지형은 타일별 클립. **도로/보도/차선**도
  타일마다 클립→버닝→통합표면으로 포함(`test_generate_tile_roads`), **정사영상**도 타일마다 자기 영역만
  풀해상도(zoom 18)로 만들어 `ortho={extent_local_m, image_b64}`(base64 PNG, `tiles_stream._ortho_b64`)로
  담아 확장이 타일 지형에 드레이프(`test_generate_tile_orthophoto`).
- 지형 타일은 작은 겹침 여백(`_TERRAIN_OVERLAP_CELLS = 2` DEM 셀 ≈ 각 변 10m)으로 클립해 인접
  타일이 ~15–20m 겹치므로 **타일 사이 이음새/틈 없음**(같은 DEM 값 → 표면 일치).
- API: `POST /api/tile_plan`, `POST /api/generate_tile`.

**`.3dm`(Rhino)은 타일 불필요**: `.3dm`은 서버에서 파일로 한 번에 생성되고 Rhino는 SketchUp보다
대용량 mesh를 훨씬 잘 다룬다 → 단일 `/api/generate` 호출로 반경 확장이 그대로 스케일된다.

### 3.7 자동 QA — `src/qa.py`

KBS TopoMap의 "사람이 눈으로 검사 → 코드 수정" 단계를 자동화한다. `layers.qa=True`면 파이프라인이
`run_qa(solids, dem, terrain_mesh, m2i)`를 호출해 생성물 결함을 검사하고 `result.qa = {findings, summary}`를
반환한다(웹 응답 `qa`로 그대로 노출). 다른 레이어와 독립적으로 켤 수 있다.

검사 항목:

- **건물 앉힘**: DEM 격자를 **직접 샘플**해(nan=지형밖 감지) 급경사·부유(공중 뜸)·침몰(과다 묻힘)·지형밖 탐지.
- **건물 겹침**: shapely `STRtree`로 footprint 상호 교차(중복 건물) 검출.
- **footprint 유효성**: 자기교차·슬리버(면적 대비 과다 세장) 폴리곤.
- **지형 스파이크**: 정점 고도 vs 이웃 중앙값 편차(베이크 아티팩트).

각 finding은 `{severity(warn/info), kind, label(심의어), message, at:[x,y], name}`이고 `summary`는
`{total, warnings, passed, stamp, by_kind}`(`passed`=경고 0건, `stamp`="검수 통과…"). 웹 UI가 결함 목록 패널을
띄우고, F2 브라우저 3D·SketchUp 확장이 결함 위치에 **수직 핀**(경고=빨강/info=주황)을 세워 눈에 띄게 한다.

---

## 4. 좌표계 규칙

| 단계 | 좌표계 | 단위 |
|---|---|---|
| 외부 API 응답 | EPSG:4326 (lon/lat) | 도(°) |
| 내부 계산 | EPSG:5186 (Korea 2000 중부원점) | 미터 |
| SketchUp MCP | SketchUp 로컬 | 인치 (`× M2I = 39.3701`) |
| Rhino .3dm | 로컬 미터 | 미터 |

**좌표대(벨트)**: 한국 TM 좌표대가 여럿 — EPSG:5186(중부원점: 서울·대전·광주·세종),
EPSG:5187(동부원점: 부산·대구·울산). 내부 기준은 5186 고정. 동부원점(5187) 지역 SHP는 베이크
시 5186으로 재투영해 굽는다(§2.4·§8) — z(표고)는 수평 재투영에 무영향. 미적용 시 동부원점
지역 지형이 ~2° 어긋남.

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

`build_tin(dem, max_error_m)`이 `TERRAIN_MAX_ERROR_M` 기준으로 분기(§3.5):

- 적응형(`>0`, 기본): 오차 한계 적응형 TIN — 평탄부 큰 삼각형·복잡부 조밀, 수직오차 ≤ 한계 목표(대부분 충족, 극단 급경사 예외).
- 정규격자(`0`): 칸마다 대각 교차(`(ix+iy)%2`) 2삼각형.

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

중심 1점이 아닌 최저 꼭짓점 기준 → 경사지에서 건물 뜨는 현상 방지. DEM 내부 NaN 구멍(채움 한계)에 걸린
꼭짓점은 `dem.sample()`이 `None`으로 걸러 제외한다(그 값을 0.0으로 오인해 건물이 지형 아래로 침몰하는 것 방지).

### 5.5 대지 경계

```python
@dataclass
class CadastralParcel:
    pnu: str
    footprint_m: list[tuple[float, float]]  # 로컬 미터
```

MultiPolygon → 가장 큰 외곽 링만. shapely 파싱 오류(꼭짓점 < 4) → 건너뜀.

### 5.6 도로·보도·차선 + 통합 표면 (Phase R)

도로 벡터는 실시간 API가 없어(로컬 SHP뿐) 지형 DEM처럼 **오프라인 비축**한다 — `src/terrain/road_bake.py`
가 수치지도 A0010000(도로경계 폴리곤)·A0020000(중심선)·A0033320(보도)을 지역 GeoJSON(EPSG:5186)으로
굽고 `road_manifest.json`에 등록. 런타임(`src/geometry/road.py`)은 json+shapely로 bbox 클립한다.

파이프라인(`layers.roads`, 지형 있을 때):

1. **버닝** `burn_roads` — 도로 폴리곤을 DEM 격자에 래스터화해 **footprint 셀 = 중심선 종단 평활 표고**로
   세팅(지형 절토/성토), footprint 밖 `ROAD_SKIRT_M` 밴드는 자연표고로 선형 블렌딩(비탈). 셀 z는 k-최근접
   중심선 **IDW**(교차부 계단 완화), **자기지면 ±`ROAD_MAX_DEV_M`(2m) 클램프**(종단평활 과다로 도로가
   솟는 것 방지). 터널/지하(A0110020·A0090000)는 미베이크라 자동 제외.
2. **통합 표면** `build_unified_surface` — 지형 DEM 점(도로/보도 밖) + 도로/보도 경계·내부 샘플점을
   **한 번의 Delaunay**로 삼각화 → 삼각형을 중심점 재질로 분류 → 재색인해 (TerrainMesh, RoadMesh road,
   RoadMesh sidewalk) **3메시로 분리**. 모든 메시가 **같은 정점 위치를 공유**하므로 경계 100% 일치 →
   이음매·구멍·뜸·z-fighting이 구조적으로 불가능. 도로 정점엔 크라운(횡단구배 `ROAD_CROWN_PCT` 2%) 적용.
   - **보도 우선**: A0033320 보도가 A0010000 도로경계에 대부분 겹쳐(실측 97%) 도로>보도 우선이면 인도가
     거의 컬링되므로 **보도 우선으로 뒤집었다**(도로는 보도 몫만 빠짐, 실측 보도 삼각형 110→5637).
   - **경계 샤프닝**: 통합표면 경계 densify를 내부 격자(`ROAD_CELL_M` 2.5m)보다 촘촘한
     `ROAD_EDGE_CELL_M`(1m)로 → 경계가 곡선을 정밀 추종.

3. **차선(다차선)** `clip_lane_markings` — 중심선 props의 실측 `차로수`·`도로폭`으로 차로수만큼 평행 차선
   구분선을 `offset_curve`로 생성(소로는 중심선 1개, 실측 65→96선). 중앙선(median)은 실선, 나머지 구분선은
   점선(`_dash_line` 칠 3m·공백 5m). 노면 위 드레이프 폴리라인 → **F2 뷰어(노란 라인)·SketchUp 확장(노란 얇은 면
   리본)에서만** 렌더. `.3dm`(rhino)·클라우드 `.skp`(MCP)에는 차선 미포함 — 노면·보도·수계만 들어간다.

(지형 없음/DEM 없음이면 도로·보도는 개별 드레이프 메시로 폴백. `carve_terrain`/`build_terrain_conformed`는
통합표면 이전 세대의 폴백·API로 유지.)

### 5.7 수계 (water)

`layers.water`(지형/DEM 필요). 도로가 지형에 **드레이프**되는 것과 달리 수면은 **표고고정 평면**이다
(`src/geometry/water.py`):

1. `clip_water` — E계열 폴리곤을 bbox 클립·로컬 미터 변환(도로 헬퍼 재사용).
2. `water_surface_z` — 각 수면의 경계 둑 DEM 고도 **저백분위**를 수면표고로 잡는다(둑 위 잡티 배제).
3. `burn_water` — 지형 DEM을 수면표고 **아래로 평탄화**(하상이 수면 위로 튀는 것 방지).
4. `build_water_mesh` — 표고고정 평면 수면 메시(도로처럼 드레이프하지 않음). F2(파랑)·`.3dm`(`water` 레이어)·
   `.skp`·확장이 렌더.

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
`write_3dm(solids, terrain, path, offset, cadastral=None, roads=None, sidewalks=None, water=None, ortho_image=None, ortho_extent_m=None) -> str`

| 레이어 | 색상 | 객체 타입 |
|---|---|---|
| `buildings` | steel blue | `rhino3dm.Extrusion` (캡 포함) |
| `buildings_unverified` | orange | `rhino3dm.Extrusion` (**층수 추정 건물** — flag 정책뿐 아니라 default 정책의 추정도, A-2) |
| `terrain` | olive green | `rhino3dm.Mesh` (정사영상 시 planar UV + 비트맵 텍스처 머티리얼) |
| `cadastral` | sandy yellow | `rhino3dm.PolylineCurve` at Z=0 |
| `roads` | asphalt gray | `rhino3dm.Mesh` (도로 노면, Phase R) |
| `sidewalks` | concrete beige | `rhino3dm.Mesh` (보도, Phase R) |
| `water` | river blue | `rhino3dm.Mesh` (표고고정 평면 수면, §5.7) |

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
4. 지형 취득   manifest → find_tiles(겹치는 타일 전부) → clip_dem_mosaic(다중 타일 병합,
              DEM_TILE_BASE로 로컬/GCS) → grid_to_tin (layers.terrain 시)
5. 건물 앉힘   seat_building (min-vertex, 지형 활성 시)
6. 지적 취득   LP_PA_CBND_BUBUN → features_to_parcels (layers.cadastral 시)
6.3 도로/보도   road.clip_* → burn_roads → build_unified_surface(통합표면) + clip_lane_markings(차선)
              (layers.roads 시, road_manifest/GeoJSON 비축 필요 — 로컬 또는 ROAD_BASE GCS)
6.4 수계       water.clip_water → water_surface_z → burn_water → build_water_mesh (layers.water 시)
6.5 정사영상   ortho.build_mosaic (layers.orthophoto 시, 출력 포맷 무관 — .3dm은 텍스처로,
              데스크톱 확장은 files.ortho_png로 소비) → PNG + 로컬 범위(ortho_extent_m)
7. SKP 코드 생성  build_skp_code(solids, terrain, cadastral, roads, sidewalks, water)
8. .3dm 저장  write_3dm(solids, terrain, path, offset, cadastral, roads, sidewalks, water, ortho_image, ortho_extent_m)
9. geometry JSON  _build_geometry(solids, terrain, ortho, roads, sidewalks, lanes, water) (include_geometry=True 시 — 웹/확장용)
9.5 자동 QA   run_qa(solids, dem, terrain_mesh) → result.qa (layers.qa 시, §3.7)
10. provenance + origin_offset 기록
```

DEM 범위 밖 → `ok: true` + `warnings` 추가 + 건물만 생성 (조용한 fallback). DEM 타일을 열 수 없어도
(로컬 파일 없음/GCS 미도달) 마찬가지로 건물만 생성 + 경고. 정사영상도 키 없음/타일 실패/지형 미생성
시 경고 후 생략(조용한 fallback).

---

## 8. 모듈 구조

```
src/
  server.py              FastMCP 서버 (도구 4개: check_site_data, generate_site_model,
                          preview_site, generate_site_tiles)
  pipeline.py            generate() 오케스트레이션 (include_geometry 시 geometry JSON 포함)
  api.py                 FastAPI 웹 백엔드 (/api/generate → geometry + .3dm/정사영상(ortho_png) 다운로드,
                          /api/tile_plan·/api/generate_tile 대반경 타일 스트리밍, frontend/dist 서빙)
  tiles.py               generate_tiles() — 대량건물 .skp 코드 타일분할
  tiles_stream.py        tile_plan()/generate_tile() — 확장용 geometry JSON 타일 순차조립(대반경, §3.6).
                          타일별 도로/보도/차선 통합표면 + 정사영상 base64(_ortho_b64)
  site_check.py          check_site_data 핵심 로직
  preview.py             preview_site 핵심 로직
  qa.py                  run_qa() — 자동 QA/검증(§3.7): 건물 앉힘·겹침·footprint 유효성·지형 스파이크 → {findings(label 심의어 포함), summary(total/warnings/passed/stamp/by_kind)}
  trust_report.py        build_trust_report() — 데이터 신뢰도 리포트(A-1): 조립된 result 위 순수 뷰 → result.trust_report(항상 부착, 실측/추정 %·지형·정사영상·QA·한계 고지)
  config.py              환경변수 (VWORLD_KEY, M2I=39.3701, DEFAULT_FLOOR_H_M=3.0, TERRAIN_MAX_ERROR_M=0.25)
                          + DEM_TILE_BASE + dem_tile_path(로컬↔GCS /vsicurl 타일 경로)
                          + ROAD_BASE/WATER_BASE(도로·수계 GCS 서빙) + road_file_path(gs→https)
                          + ROAD_CELL_M/ROAD_EDGE_CELL_M/ROAD_SKIRT_M/ROAD_MAX_DEV_M/ROAD_CROWN_PCT/WATER_CELL_M
  geo/
    geocode.py           주소 → 좌표
    bbox.py              반경 → BBOX (4326)
    crs.py               4326 ↔ 5186 변환 + origin_offset + apply_offset
    vworld.py            VWorldClient (페이지네이션 내장 + bbox 분할: 10km² 한도 우회, 반경 2km+)
    ortho.py             정사영상 WMTS 타일 다운로드 + 모자이크 + EPSG:5186 재투영 (Tier 1)
    zoning.py            용도지역 조회 — 형제 앱 arch-law-graph GET /api/zoning 연동(ZONING_BASE, 조용한 fallback)
  geometry/
    building.py          BuildingSolid + features_to_solids + floors_of
    terrain_mesh.py      TerrainMesh + build_tin(디스패처) / adaptive_tin(오차한계 적응형) / grid_to_tin(정규격자)
    seating.py           seat_building (min-vertex)
    cadastral.py         CadastralParcel + features_to_parcels
    road.py              도로/보도/차선 런타임 (Phase R). clip_roads/sidewalks/centerlines + burn_roads
                          + build_unified_surface(통합표면, 보도우선·경계 샤프닝) + clip_lane_markings(다차선)
                          + _read_geojson_text(로컬/HTTP fetch+캐시 — 클라우드 도로 서빙) + apply_crown
    water.py             수계 런타임(§5.7). clip_water + water_surface_z(경계 저백분위) + burn_water
                          + build_water_mesh(표고고정 평면). road.py 헬퍼 재사용
  output/
    skp_mcp.py           build_skp_code → SketchUp MCP Python 코드 문자열 (roads/sidewalks/water 포함)
    rhino.py             write_3dm → .3dm 파일 (roads/sidewalks/water 레이어)
  terrain/
    store.py             load_manifest + find_tiles(겹치는 타일 전부·고해상도 우선) / find_tile(대표 1개)
                          + find_road_file(도로 GeoJSON) / find_water_file(수계 GeoJSON)
    contour_bake.py      등고선 SHP → DEM(.tif) 오프라인 굽기 (CLI)
                          + bake_tiled(대용량 지역 타일 배치 베이크, --tile-km/--margin-m)
                          + 좌표대 재투영(5187→5186)·도엽 중복제거·거리제한 채움(fill_dist_m)
                          + method: clough(기본)/linear/solver(라플라스 조화 격자 솔버 _grid_relax, opt-in §2.4)
    road_bake.py         A0010000 도로경계·A0020000 중심선(실측 도로폭·차로수)·A0033320 보도 SHP → 지역
                          GeoJSON(EPSG:5186) 오프라인 굽기 (Phase R) + road_manifest.json 갱신
                          + synthesize_gap_roads(경계 폴리곤 없는 소로를 실측 도로폭으로 버퍼해 합성)
    water_bake.py        E계열 수계 면(N3A_E0* 하천경계·호소) SHP → 지역 GeoJSON(EPSG:5186) 오프라인 굽기
                          + water_manifest.json 갱신 (road_bake 동형)
    dem.py               clip_dem + clip_dem_mosaic(다중 타일 rasterio.merge 병합, 로컬/vsicurl) + DEMPatch (표고 보간)
```

그 외: `frontend/`(React 웹앱 + three.js 브라우저 미리보기 — SSAO(EffectComposer/GTAOPass)·QA 결함 수직 핀),
`sketchup_ext/`(SketchUp `.rbz` 확장 + `build_rbz.py` — 건물=실제 face(벽 수직쿼드·상면 n각형 add_face,
pushpull/삼각메시 미사용)·지형 mesh(경계선 soft+smooth)·정사영상 드레이프·도로/보도 메시·차선 리본·수계 평면·QA 핀),
`scripts/dem_staircase.py`(지형 계단현상 진단), `scripts/dem_to_cog.py`(DEM→COG 변환·GCS 업로드),
`docs/`(`deploy.md`·`sketchup_extension.md`·`orthophoto_texture_plan.md`·`road_surface_plan.md`·`dem_dsm_strategy.md`).

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
| 확장5 | 정사영상 텍스처 — 지형 planar UV 드레이프 (.3dm=Rhino / .skp=데스크톱 확장 B2, 단일+타일 경로) | ✅ 코드·테스트 (데스크톱 실기 렌더 검증 대기) |
| 웹앱 | FastAPI 백엔드 + React UI + 브라우저 3D 미리보기(F2) + Cloud Run 배포 | ✅ |
| 확장(Phase B1) | SketchUp `.rbz` — 지형+건물 조립(건물=실제 face) | ✅ |
| 확장(Phase B2) | SketchUp 확장 정사영상 드레이프(PNG→position_material, 양면·Shaded 자동전환, 단발+타일별) | ✅ 코드 (데스크톱 실기 렌더 검증 대기) |
| 지형 LOD | 적응형 error-bounded TIN(scipy) — 25cm에서 삼각형 ~86%↓ | ✅ |
| 대반경 조립 | `tiles_stream`(tile_plan/generate_tile) — 확장 타일 순차조립(>500m, 1km 검증). 타일별 도로/정사영상 포함 | ✅ (1km 검증, 2km 대기) |
| 지형 개선 | 계단현상 완화 — guarded CloughTocher 재bake | ✅ |
| geocode | 지번+도로명(PARCEL→ROAD) 지원 | ✅ |
| 전국 DEM 확장 | 다중 타일 mosaic + 배치 베이크(bake_tiled) + GCS COG 서빙(/vsicurl) | ✅ 프로덕션 |
| 전국 DEM(다지역) | 6개 광역단체 120타일 + 좌표대 재투영(5187→5186) + 도엽 중복제거 + 거리제한 채움 + bbox 분할(반경 2km+) | ✅ 프로덕션 |
| F2 뷰어 표현 | 높이별 색·건물 외곽선·그림자·뷰모드·지적/도로/보도/차선·레이어 토글·SSAO(GTAO 음영)·QA 결함 핀 | ✅ |
| 도로 R1~R3 (Phase R) | 노면(A0010000)·보도(A0033320) → F2/.3dm/.skp/확장 · 차선(A0020000 차로수 기반 **다차선**, 중앙선 실선/구분선 점선) → **F2(라인)·확장(노란 면)만**. 지형정합=버닝(절토/성토·스커트·IDW·클램프)+크라운 | ✅ |
| 통합 표면 | 지형·도로·보도를 1번 Delaunay(정점 공유) → 이음매·구멍·뜸·z-fighting 구조적 제거 | ✅ |
| 도로 R4 정교화 | 실측폭 갭채움(소로·골목 `synthesize_gap_roads`)·경계 샤프닝(`ROAD_EDGE_CELL_M`)·보도우선(도로겹침 컬링 방지)·타일경로/확장 렌더·**클라우드 서빙**(`ROAD_BASE`=GCS) | ✅ |
| 수계 | E계열 하천·호소 → 표고고정 평면 수면 + 지형 물아래 버닝 (`water_bake`·`water.py`, F2/.3dm/.skp/확장, `WATER_BASE` 서빙) | ✅ |
| 지형 솔버 | 계단현상 라플라스 조화 격자 솔버 `--method solver`(등고선 Dirichlet 제약+∇²z=0 완화, 오버슈트 없음, 힐셰이드 검증) | ✅ opt-in (기본 clough, ~10× 느림) |
| 자동 QA | 건물 앉힘(급경사/부유/침몰/지형밖)·겹침·footprint 유효성·지형 스파이크 → findings, 웹 패널+F2/확장 3D 핀 | ✅ (KBS "눈검사→코드") |
| DEM/DSM 이원화 | 지면=DEM, 공중 구조물=DSM 원리(고가/교량 데크 실측). 고해상도 DSM 민간 취득 불가 | 🚧 블로커 |

---

## 10. 확장 로드맵

| 항목 | 내용 | 상태 |
|---|---|---|
| 이격면 실연동 | arch-law-diagnose 조닝 파라미터 → 이격 오프셋 | 🚧 블로커: arch-law-diagnose가 REST-only + 설계 프로그램 입력을 요구, 좁은 API 계약 없음 (§9 확장3 참고) |
| 정사영상 Tier 2a | SketchUp 데스크톱 확장 정사영상 드레이프 (`Face#position_material`) | ✅ (Phase B2) — `docs/orthophoto_texture_plan.md` §5 |
| 정사영상 타일 | 대반경 타일별(per-tile) 정사영상 드레이프 | ✅ `tiles_stream._ortho_b64`(타일마다 zoom 18 base64) → 확장 드레이프 (test_generate_tile_orthophoto) |
| NGII 정사영상 소스 | VWorld → NGII(공공누리 1유형) 전환 | 보류: 서버사이드 키 접근 + EPSG:5179 타일 구현 필요 |
| 지형 계단현상 완전제거 | 라플라스 조화 격자 솔버 `--method solver`(`_grid_relax` red-black SOR) | ✅ opt-in(§2.4·§9): 계단 육안 제거·오버슈트 없음, 기본 clough보다 ~10× 느림. 남은 선택 — 전 타일 재베이크로 기본 채택(비용) / 급경사면 biharmonic 개선 |

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
12. **DEM 타일은 GCS 서빙**: 타일은 `DEM_TILE_BASE=/vsicurl/…`(원격)에서 읽고 `manifest.json`은
    로컬(git). 타일 열기 실패 시 지형 생략(조용한 fallback)이므로 `warnings` 확인.
13. **동부원점(5187) 지역 소스 CRS**: 베이크가 SHP를 5186으로 재투영하니(§2.4·§4) 부산·대구·울산
    등 소스 CRS가 섞여도 됨. 내부·타일은 항상 5186.
14. **반경 2km+ 자동 처리**: VWorld BOX 10km² 한도는 클라이언트가 bbox 분할로 우회(§2.2) —
    큰 반경도 예전 10km² 오류 없이 조회된다.
15. **정사영상 텍스처 경로 구분**: `.3dm`(Rhino)은 비트맵 UV 직접, 데스크톱 SketchUp 확장은 PNG를
    받아 드레이프(B2). 반면 **클라우드 SketchUp MCP `.skp` 코드 경로는 여전히 텍스처 불가**(샌드박스가
    이미지 반입 차단, 단색만) — 정사영상 드레이프는 PNG를 내려받는 데스크톱 확장·`.3dm`에서만 된다.
16. **적응형 TIN scipy 의존**: `TERRAIN_MAX_ERROR_M>0`(기본)은 scipy 필요. 미탑재 시 정규격자로
    조용히 fallback(삼각형 급증) — 대반경에서 확장·렌더가 무거워지면 scipy 설치 확인.
17. **도로·수계 조용한 fallback**: `layers.roads`/`layers.water`는 `road_manifest.json`/`water_manifest.json`
    비축(또는 `ROAD_BASE`/`WATER_BASE` GCS)이 있어야 하고, 수계는 지형(DEM)까지 필요. 없으면 `ok:true`로
    반환하되 해당 레이어 생략 + `warnings`. 클라우드는 GeoJSON을 GCS에 올려야 도로/수계가 나온다.
18. **`--method solver` 지표 오도**: `dem_staircase`의 quant/flat 수치는 솔버 판단에 **오도**(조밀 등고선
    제약·완경사를 페널티로 계산). 솔버 품질은 반드시 **힐셰이드**로 눈으로 판단한다(§2.4).

---

## 12. 테스트

```powershell
# 단위 테스트 (오프라인, API 미호출)
python -m pytest tests/ --ignore=tests/test_integration_api.py -v

# 실 API 연동 테스트 (VWORLD_TEST_KEY 필요)
python -m pytest tests/test_integration_api.py -v
```

모든 VWorld API 호출은 mock (`tests/conftest.py`, `FakeClient`/`FakeClientMulti`).
합성 데이터로만 동작 — 실제 키·파일 없이 전체 스위트 실행 가능. 현재 **318개 통과**
(`test_road`/`test_water`/`test_qa`/`test_trust_report`/`test_zoning`/`test_dem` 등 포함).
