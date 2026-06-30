# arch-site-model — Phase 상세 실행 문서 (Claude Code용)

> 본 문서는 `arch-site-model_설계사양서.md`의 §10을 **실행 가능한 작업 명세**로 푼 것.
> 각 Phase는 클로드 코드에 **그대로 던질 수 있는** 단위. Phase 단위로 순서대로 진행.
> 원칙: ① 검증된 것만 구현 ② 사람 검토 가능한 출력 ③ 팀원이 작성자 없이 실행 가능 = 완료.
> 단계별 프롬프트로 쪼개서 진행할 것 (한 번에 큰 프롬프트 X).

---

## 전체 파일 구조 (최종 목표)

```
arch-site-model/
├── .env                          # VWORLD_KEY(운영), VWORLD_TEST_KEY(실측), VWORLD_DOMAIN
├── .env.example
├── requirements.txt
├── README.md
├── pyproject.toml
│
├── src/
│   ├── __init__.py
│   ├── config.py                 # 환경변수 로드, 상수(M2I, 기본 층고 등)
│   │
│   ├── geo/                       # 지리 데이터 취득·변환
│   │   ├── __init__.py
│   │   ├── geocode.py            # 주소 → 좌표
│   │   ├── vworld.py             # VWorld 데이터 API 클라이언트
│   │   ├── bbox.py              # 좌표+반경 → BBOX, 좌표계 변환
│   │   └── crs.py               # EPSG 변환, origin offset
│   │
│   ├── terrain/                   # 지형 비축·클립
│   │   ├── __init__.py
│   │   ├── contour_bake.py      # [오프라인] 수치지형도 등고선+표고점 → DEM(.tif) 굽기
│   │   ├── store.py             # DEM 비축 스토어 (manifest 관리)
│   │   └── dem.py               # DEM 클립 → 격자 표고
│   │
│   ├── geometry/                  # 지오메트리 생성 (출력 포맷 무관)
│   │   ├── __init__.py
│   │   ├── building.py          # footprint + 층수 → 솔리드 데이터
│   │   ├── terrain_mesh.py      # 격자 → TIN 데이터
│   │   └── seating.py           # 건물-지형 정합
│   │
│   ├── output/                    # 포맷별 어댑터
│   │   ├── __init__.py
│   │   ├── skp_mcp.py           # SketchUp MCP build_model 코드 생성
│   │   └── rhino.py             # rhino3dm → .3dm
│   │
│   ├── pipeline.py               # 전체 파이프라인 오케스트레이션
│   └── server.py                # FastMCP 서버 (도구 정의)
│
├── geo_store/                     # 지형 비축 (gitignore, GCS 동기화)
│   ├── manifest.json
│   └── *.tif
│
└── tests/
    ├── test_geocode.py
    ├── test_vworld.py
    ├── test_geometry.py
    └── fixtures/                 # 실측 응답 샘플 (대전 KT 등)
```

---

## 의존성 (requirements.txt)

```
fastmcp
fastapi
uvicorn
requests
python-dotenv
shapely
geopandas         # 수치지형도 SHP(등고선·표고점) 읽기
pyproj
rhino3dm
rasterio          # DEM(.tif) 읽기/클립/쓰기
numpy
scipy             # 등고선+표고점 → DEM 격자 보간 (Phase 3 비축)
```
설치: `C:\Users\20260102\AppData\Local\Programs\Python\Python311\python.exe -m pip install -r requirements.txt`

---

# Phase 0 — 스캐폴드 & 환경

**목표**: 프로젝트 뼈대 + 환경변수 + 좌표변환 유틸. 이후 모든 Phase의 토대.

**작업 항목**
1. 위 파일 구조 생성 (빈 모듈 + `__init__.py`)
2. `src/config.py`:
   ```python
   # 환경변수: VWORLD_TEST_KEY 우선, 없으면 VWORLD_KEY
   VWORLD_KEY = os.environ.get("VWORLD_TEST_KEY") or os.environ.get("VWORLD_KEY")
   VWORLD_DOMAIN = os.environ.get("VWORLD_DOMAIN", "")
   M2I = 39.3701              # meter → inch
   DEFAULT_FLOOR_H_M = 3.0
   GEO_STORE = Path("geo_store")
   ```
3. `src/geo/crs.py`:
   ```python
   def to_5186(lon, lat) -> tuple[float, float]      # EPSG:4326 → 5186
   def to_4326(x, y) -> tuple[float, float]          # 역변환
   def origin_offset(coords_5186: list) -> tuple      # 최소좌표 = offset
   def apply_offset(coords, offset) -> list           # 원점 이동
   ```
4. `.env.example` 작성, `.gitignore`에 `.env`, `geo_store/`, `*.skp`

**의존성**: pyproj
**완료 기준**: `from src.geo.crs import to_5186; to_5186(127.371, 36.340)` 가 5186 좌표 반환. 팀원이 README만 보고 환경 세팅 가능.
**검증**: `pytest tests/test_crs.py` — 대전 좌표 왕복변환 오차 < 0.01m.

---

# Phase 1 — check_site_data (취득 가능성 선검사)

**목표**: 주소 → 좌표 → 건물/지적 취득 가능 + 지형 비축 여부 확인. 생성 없음.

**작업 항목**
1. `src/geo/geocode.py`:
   ```python
   def geocode(address: str) -> dict
   # → {"lon","lat","crs":"EPSG:4326"} | raises GeocodeError
   # VWorld req/address, type=PARCEL, domain 포함
   ```
2. `src/geo/bbox.py`:
   ```python
   def bbox_from_point(lon, lat, radius_m) -> tuple   # (minx,miny,maxx,maxy) 4326
   def to_geomfilter_box(bbox) -> str                 # "BOX(...)"
   ```
3. `src/geo/vworld.py`:
   ```python
   class VWorldClient:
       def __init__(self, key, domain)
       def get_features(self, dataset, bbox, size=1000, page=1) -> list[dict]
       # req/data, GetFeature, geometry=true. 페이지네이션 내장.
       def count(self, dataset, bbox) -> int          # geometry=false로 개수만
   ```
   상수: `DATASET_BUILDING="LT_C_SPBD"`, `DATASET_CADASTRAL="LP_PA_CBND_BUBUN"`
4. `src/terrain/store.py`:
   ```python
   def load_manifest() -> list[dict]                  # geo_store/manifest.json
   def find_tile(bbox) -> dict | None                 # bbox 포함하는 DEM 타일
   ```
5. `src/server.py` — FastMCP 도구 `check_site_data` 등록 (사양서 §4.1 스키마)

**의존성**: requests, shapely(bbox 포함판정)
**완료 기준**: `check_site_data({"address":"대전광역시 서구 괴정동 358","radius_m":250})` →
`buildings.count`, `with_floors`, `cadastral.available`, `terrain.available` 반환.
**검증**:
- `tests/fixtures/`에 실측 응답(대전 KT) 저장 → 오프라인 단위테스트
- 실 API 통합테스트 1건 (VWORLD_TEST_KEY)
- gro_flo_co 누락 건물 카운트가 warnings에 반영되는지

**주의**: domain 파라미터 필수(없으면 INCORRECT_KEY). 주소는 지번만(설명문구 제거).

---

# Phase 2 — generate_site_model (건물만)

**목표**: 건물 footprint + 층수 → 쿼드 솔리드 → .skp. 지형·지적 제외, 건물만.

**작업 항목**
1. `src/geometry/building.py`:
   ```python
   @dataclass
   class BuildingSolid:
       name: str
       footprint_m: list[tuple[float,float]]   # 단일 폴리곤 (로컬좌표)
       base_z_m: float
       height_m: float
       floors: int | None
       attrs: dict                              # bd_mgt_sn, buld_nm 등

   def features_to_solids(features, floor_h_m, offset) -> list[BuildingSolid]
   # MultiPolygon → 폴리곤별 분리. gro_flo_co → height. offset 적용.
   def floors_of(props) -> int | None           # gro_flo_co 파싱 (0/null 처리)
   ```
2. `src/output/skp_mcp.py`:
   ```python
   def build_skp_code(solids, terrain=None, camera=True) -> str
   # SketchUp MCP build_model에 넣을 Python 코드 문자열 생성.
   # extrude_solid() 헬퍼 포함 (사양서 §6.2 검증된 코드).
   # 규칙: import 금지, 인치, X=폭/Y=깊이/Z=높이, 옆면=쿼드.
   def extrude_solid_snippet() -> str           # 재사용 헬퍼 텍스트
   ```
   ※ MCP 호출 자체는 Claude(오케스트레이터)가 수행. 엔진은 **코드 문자열을 생성**해 반환하거나, MCP 클라이언트로 직접 호출(환경에 따라). 1차는 코드+좌표 데이터를 반환하는 방식 권장.
3. `src/pipeline.py`:
   ```python
   def generate(address, radius_m, floor_h_m, outputs, layers) -> dict
   # Phase2: layers={buildings:True} 만. 나머지 stub.
   ```
4. `src/server.py` — `generate_site_model` 도구 등록 (사양서 §4.2)

**의존성**: shapely(MultiPolygon 분해)
**완료 기준**: 대전 주소 → 건물 N개가 층수대로 돌출된 데이터/`.skp` 코드 생성. 직사각형 6면, L자 8면 확인.
**검증**:
- `extrude_solid` 출력 면수 = 폴리곤 변수 + 2 (천장/바닥)
- gro_flo_co=4 → height = 4 × floor_h_m
- 실제 SketchUp MCP로 .skp 생성 1건 (눈으로 확인)

**참조**: 본 세션 검증된 `extrude_solid()` (사양서 §6.2)

---

# Phase 3 — 지형 추가 (등고선 SHP → 비축 DEM → 격자 TIN + 정합)

**배경 결정 (KBS 방식 채택 + 자동화)**
지형 소스는 **수치지형도의 등고선 + 표고점**(KBS TopoMap이 검증한 정밀 경로 — `Kbs_Cont_Terrain`).
공개DEM은 90m로 너무 거칠고(250m 사이트가 3×3칸), 5m DEM은 보안통제(신청). 반면 수치지형도
등고선은 1:5,000 주곡선 5m 간격 + 표고점으로 **무료·고정밀**.
단 KBS는 등고선 TIN의 **"평평한 삼각형"을 사람이 수동 보정** → 우리는 헤드리스 자동화라 불가.
**해법**: 등고선 처리를 **런타임이 아니라 비축 단계에서** 정규 DEM 격자로 한 번 구워둔다.
런타임은 깨끗한 격자만 소비 → 평평삼각형 원천 차단 + KBS급 정밀 소스. (90m 공개DEM은 폴백.)

```
[비축 1회 — 오프라인]  수치지형도 SHP(등고선 F0010000 + 표고점 F0020000)
                        → 보간 → geo_store/dem_*.tif   (5m→1m 해상도 파라미터)
[런타임 — 자동, 딸깍]  dem_*.tif bbox 클립 → 격자 TIN → 건물 드레이프(min-vertex)
```
> 5m→1m 정밀화 = **굽는 단계 cell_m 파라미터**일 뿐. 런타임·엔진 무수정. (측정정확도 증가가
> 아니라 표면 평활화 = 계단/평면 아티팩트 감소가 실익.)

## Phase 3A — 오프라인 비축 도구 (등고선 → DEM 굽기)

**작업 항목**
1. `src/terrain/contour_bake.py`:
   ```python
   def read_contours(shp_dir) -> tuple
   # 수치지형도 SHP에서 등고선(F0010000: 라인+표고) + 표고점(F0020000: 점+표고) 추출.
   # 좌표계 EPSG:5186. geopandas/fiona 로 읽고 표고 속성 필드 자동 탐지.
   def bake_dem(contours, spots, cell_m=5.0, bounds=None, method="linear") -> (array, transform)
   # 등고선 정점 + 표고점을 정규격자에 보간. cell_m=5.0(기본)/1.0(정밀).
   # ★ 표고점 필수 포함 — 없으면 최고 등고선 위 봉우리가 평면으로 뜸.
   # method: linear|cubic|natural(자연근방). 평평삼각형은 격자 보간으로 해소.
   def write_dem_tif(path, array, transform, crs="EPSG:5186") -> None   # geo_store/dem_*.tif
   ```
   CLI: `python -m src.terrain.contour_bake <shp_dir> --cell 5 --out geo_store/dem_daejeon_seogu.tif`
2. `manifest.json` 갱신: region/file/source="CONTOUR_BAKE"/cell_m/updated.

**완료 기준 (3A)**: 수치지형도 SHP → `geo_store/dem_*.tif` 생성. 5m·1m 둘 다 구워짐.
**검증 (3A)**:
- 합성 등고선(동심원 언덕 + 정점 표고점)으로 bake → DEM 표고가 입력 등고값과 일치(±보간오차).
- 표고점 뺀 경우 봉우리가 평면으로 뜨는지 → 표고점 넣으면 해소됨 확인.
- cell_m=1 시 메시가 5m 대비 매끈해지는지.

## Phase 3B — 런타임 지형 (클립 → TIN → 정합)

**작업 항목**
3. `src/terrain/dem.py`:
   ```python
   def clip_dem(tile_path, bbox) -> np.ndarray    # rasterio windowed read + CRS 정합
   def elev_at(x_m, y_m) -> float                  # 보간 표고 (EPSG:5186 로컬좌표)
   ```
4. `src/geometry/terrain_mesh.py`:
   ```python
   @dataclass
   class TerrainMesh:
       vertices: list[tuple]      # (x,y,z) inch
       triangles: list[tuple]     # 정점 인덱스 3개
   def grid_to_tin(grid, offset) -> TerrainMesh
   # 정규격자 삼각망, 칸마다 대각 교차 (사양서 §6.5 검증코드)
   ```
5. `src/geometry/seating.py`:
   ```python
   def seat_building(solid, dem) -> float
   # base_z = min(footprint 각 꼭짓점의 DEM 고도) - 묻힘여유(0.5m)
   # ★ 사양서 §6.6 개선판: 중심 1점 아님, 최저 꼭짓점 기준 (경사지 뜸 방지)
   ```
6. `src/output/skp_mcp.py` 확장: 지형 메시 + 내부 edge softening 코드 추가.
7. `src/pipeline.py`: layers.terrain 처리 + **DEM 범위 밖 영역 경고**.

**의존성**: geopandas(또는 fiona+shapely) SHP 읽기, scipy(보간), rasterio, numpy
**완료 기준 (3B)**: 주소 → 지형 메시 + 건물이 각자 지형고도에 앉은 .skp. base_z가 위치별 상이.
**검증 (3B)**:
- 클립 DEM 표고범위 = 메시 z범위 일치.
- 경사지 건물이 안 뜨고 안 묻히는지 (min-vertex 효과).
- DEM 격자라 평평삼각형 없음.

**주의**:
- 수치지형도 SHP는 **EPSG:5186(중부원점)** — 건물과 동일 좌표계(정합 깔끔).
- 비축은 1회. 지형 갱신/정밀화 = `dem_*.tif` 교체 + `manifest.json` 한 줄 (엔진 무수정).
- DEM 클립이 사이트보다 작으면(도엽 경계) 경고 → 인접 도엽 SHP 추가 후 재bake.

---

# Phase 4 — .3dm 이중 출력 + origin offset 복원

**목표**: 같은 지오메트리를 .3dm으로도. 실제 좌표 복원 정보 보존.

**작업 항목**
1. `src/output/rhino.py`:
   ```python
   def write_3dm(solids, terrain, path, offset) -> str
   # rhino3dm: 건물=Extrusion/Brep, 지형=Mesh.
   # 레이어 분리(건물/지형/지적). offset을 UserText/Notes에 기록.
   ```
2. `src/pipeline.py`: outputs=["skp","3dm"] 분기. 동일 지오메트리 데이터 → 두 어댑터.
3. origin_offset을 generate 출력 stats에 포함 (사양서 §4.2)

**의존성**: rhino3dm
**완료 기준**: outputs 선택대로 .skp/.3dm 생성. .3dm을 Rhino에서 열면 레이어 분리됨. offset으로 실제 위치 복원 가능.
**검증**:
- .skp와 .3dm의 건물 개수·높이 일치
- offset 적용/역적용 왕복 일치

---

# Phase 5 — 대지/이격면 + 누락분기 + provenance

**목표**: 대지 경계 출력, 층수 누락 정책, 출처표기 완성.

**작업 항목**
1. 지적(LP_PA_CBND_BUBUN) → 대지 경계 폴리곤 출력 레이어 추가
2. `floors_of` 누락 정책 옵션화: 기본높이 / 스킵 / 플래그 (사양서 §6.4)
3. provenance 블록 완성: 데이터 출처·취득시각·DEM 타일 (사양서 §4.2)
4. (이격면) arch-law-diagnose 연동은 **stub만** — `setback` 플래그 받되 호출은 [목표] 주석
5. README: 팀원 실행 가이드 (환경세팅 → 도구 호출 → 출력 확인)

**완료 기준**: provenance가 모든 출력에 동반. 층수 누락 건물이 정책대로 처리. 팀원이 README만으로 전 과정 실행.
**검증**: gro_flo_co 누락 건물에 대해 3개 정책이 각각 동작.

---

# 확장 (MVP 이후)

| 항목 | 내용 |
|---|---|
| preview_site | 생성 없이 건물목록·층수·규모 미리보기 (사람 검토) |
| 정사영상 텍스처 | DEM과 같은 좌표계 정사영상 → TIN UV 매핑 (차별화) |
| 대량건물 타일분할 | 반경 500m+ 수백 건물 시 MCP 세션 분할 (미검증) |
| 이격면 실연동 | arch-law-diagnose 조닝 파라미터 → 이격 오프셋 표면 |
| MultiPolygon 중정 | 건물 내부 홀 inner loop 처리 |

---

# Phase별 의존 관계

```
Phase 0 (스캐폴드·좌표)
   └→ Phase 1 (check_site_data) ── geo/ 완성
        └→ Phase 2 (건물 생성) ── geometry/building, output/skp
             └→ Phase 3 (지형) ── terrain/contour_bake(오프라인)·dem, geometry/terrain_mesh, seating
                  └→ Phase 4 (.3dm) ── output/rhino
                       └→ Phase 5 (대지·provenance)
```
각 Phase는 이전 Phase 완료를 전제. 순서 엄수.

---

# 클로드 코드 프롬프트 가이드

각 Phase를 더 작은 프롬프트로 쪼갤 것. 예: Phase 1을
1. "geocode.py 만들어줘 — VWorld req/address, 사양서 §3.1 그대로"
2. "vworld.py 클라이언트 — get_features + 페이지네이션"
3. "bbox.py — 반경→BBOX"
4. "check_site_data 도구로 조립 + fixtures 테스트"
처럼 4개 프롬프트로.

검증 우선: 각 모듈은 `tests/fixtures/`의 실측 응답으로 **오프라인 단위테스트** 먼저, 실 API는 통합테스트 1건.
