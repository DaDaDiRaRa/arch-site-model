# arch-site-model

> 대지 주소 입력만으로 주변 지형·건물 3D 대지모델을 자동 생성. **MCP 서버 + 배포 웹앱 + SketchUp 확장**.

**입력**: 대지 주소(지번·도로명) + 반경(m)  
**출력**: `.skp` (SketchUp, 정사영상 텍스처 포함) · `.3dm` (Rhino, 정사영상 텍스처 포함) · 브라우저 3D 미리보기  
**소비 경로**: ① MCP 도구(Claude) · ② 웹앱(주소→다운로드 + 3D 미리보기) · ③ SketchUp 확장(.rbz) — 엔진·백엔드는 공유  
**핵심**: `gro_flo_co`(실제 층수) 직접 사용 — AI 추정 0%, "재현이지 상상이 아님"

---

## 1. 환경 세팅

### 사전 요건

- Python 3.11 (본 환경: `C:\Users\20260102\AppData\Local\Programs\Python\Python311\python.exe`)
- Git

### 설치

```powershell
# 1. 가상환경 생성 및 활성화 (Windows PowerShell)
$py = "C:\Users\20260102\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m venv .venv
.venv\Scripts\Activate.ps1

# macOS/Linux: python3.11 -m venv .venv && source .venv/bin/activate

# 2. 의존성 설치
python -m pip install -U pip
python -m pip install -r requirements.txt
```

### 환경변수 설정

```powershell
Copy-Item .env.example .env
# → .env 를 열어 아래 항목 채우기
```

| 변수 | 설명 |
| --- | --- |
| `VWORLD_TEST_KEY` | VWorld 개발(테스트) 키 — **운영 키보다 우선** |
| `VWORLD_KEY` | VWorld 운영 키 |
| `VWORLD_DOMAIN` | 키 발급 시 등록 도메인 (기타 개발자 키는 불필요) |
| `DEM_TILE_BASE` | (선택) DEM 타일 읽기 위치. 기본=로컬 `geo_store`. GCS 서빙 시 `/vsicurl/https://storage.googleapis.com/<버킷>/<프리픽스>` — 로컬 개발엔 불필요 |
| `TERRAIN_MAX_ERROR_M` | (선택) 지형 TIN 적응형 단순화 오차 한도(m, 기본 `0.25`). `>0` = 오차 유계 적응형 TIN(평탄부는 큰 삼각형, 복잡부는 조밀 삼각형 — 수직오차 ≤ 이 값 보장). `0` = 균일 5m 격자 |

> **키 발급**: [VWorld 공간정보 오픈플랫폼](https://www.vworld.kr) → 개발자 → 인증키 발급

---

## 2. 실행 방법

세 가지 소비 경로가 있고 **엔진(`pipeline`)·백엔드는 공유**합니다.

### (A) MCP 서버 — Claude 연동

```powershell
python -m src.server
```

stdio 모드로 기동됩니다. Claude Desktop 설정(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "arch-site-model": {
      "command": "C:/절대경로/arch-site-model/.venv/Scripts/python.exe",
      "args": ["-m", "src.server"],
      "cwd": "C:/절대경로/arch-site-model"
    }
  }
}
```

### (B) 웹앱 — 브라우저에서 주소 입력 → 3D 미리보기 + 다운로드

```powershell
# 백엔드 (반드시 저장소 루트에서 — 상대경로 geo_store 때문)
uvicorn src.api:app --port 8000            # http://localhost:8000/docs

# 프론트 개발 서버 (React, /api → :8000 프록시)
cd frontend; npm install; npm run dev       # http://localhost:5173

# 프로덕션: 프론트 빌드 → FastAPI가 dist를 루트에서 서빙
cd frontend; npm run build; cd ..; uvicorn src.api:app --port 8000
```

`/api/generate` 는 `.3dm`/정사영상 다운로드 URL + `geometry` JSON(three.js **브라우저 3D 미리보기**)을 반환.
도커/Cloud Run 배포·인증은 **`docs/deploy.md`**.

### (C) SketchUp 확장 (.rbz) — 데스크톱에서 조립

```powershell
python sketchup_ext/build_rbz.py --backend-url <Cloud Run URL>   # 배포용(URL 박힘)
python sketchup_ext/build_rbz.py                                  # 개발용(localhost)
```

SketchUp 확장 관리자에서 `sketchup_ext/dist/arch_site_model.rbz` 설치 → **Extensions > 대지모델 생성**.
확장이 백엔드 `geometry`를 받아 지형·건물을 조립(B1). 건물은 깔끔한 실면(벽=쿼드, 상판=n각형)으로
조립되어(push/pull·삼각메시 아님) 밀집지역 렉과 삼각분할 선이 없고, 지형 타일 경계선도 제거됩니다(soft edge).
상세 **`docs/sketchup_extension.md`**.

**대반경(1~2km) 타일 순차조립**: 반경 500m 초과 시 확장이 타일을 하나씩 받아(`/api/tile_plan` →
`/api/generate_tile`) 진행바·취소 버튼과 함께 점진 조립합니다 — 수만 개 엔티티를 한 번에 만들며 데스크톱이
멈추거나 죽던 문제를 회피(1km 검증). 인접 타일은 살짝 겹쳐 이음새가 보이지 않습니다. **Rhino(.3dm) 모드는
타일링 불필요** — `.3dm`은 서버에서 생성되고 Rhino가 대형 모델을 잘 다루므로 반경만큼 그대로 확장됩니다.

**정사영상 텍스처**: "정사영상" 체크 시 확장이 항공/위성 정사영상을 지형에 드레이프합니다(백엔드가
`geometry.ortho_extent_m` + `files.ortho_png` 반환). 단발(≤500m) 정사영상만 지원 — 대반경 타일 정사영상은
미구현. (클라우드 MCP `.skp` 코드는 여전히 텍스처 불가 — 데스크톱 확장만 가능.)

---

## 3. 도구 사용 예시

### 미리보기 — `preview_site`

생성 전 건물 목록·층수·예상 규모를 사람이 검토합니다.

```python
preview_site("대전광역시 서구 괴정동 358", radius_m=250)
```

**반환 예시:**

```json
{
  "ok": true,
  "summary": {
    "buildings": 12, "with_floors": 9, "missing_floors": 3,
    "max_floors": 15, "avg_floors": 4.2, "courtyards": 1,
    "cadastral_parcels": 5,
    "terrain": {"available": true, "tile": "dem_daejeon_36710065_66.tif"}
  },
  "buildings": [
    {"name": "괴정동주공", "floors": 15, "height_m": 45.0, "footprint_area_m2": 820.5, "has_courtyard": false},
    {"name": "미상", "floors": null, "height_m": null, "footprint_area_m2": 145.2, "has_courtyard": false}
  ],
  "warnings": ["층수 미확인 건물 3개 — 기본 3.0m 적용 예정"]
}
```

---

### 사전 검사 — `check_site_data`

생성 가능 여부를 먼저 확인합니다.

```python
check_site_data("대전광역시 서구 괴정동 358", radius_m=250)
```

**반환 예시:**

```json
{
  "ok": true,
  "buildings": {"count": 12, "with_floors": 9},
  "cadastral": {"available": true, "count": 5},
  "terrain": {"available": true, "tile": "dem_daejeon_36710065_66.tif"},
  "warnings": ["건물 3개는 gro_flo_co 누락/0 → 기본 높이 적용 예정"]
}
```

---

### 모델 생성 — `generate_site_model`

**파라미터 요약:**

| 파라미터 | 기본값 | 설명 |
| --- | --- | --- |
| `address` | (필수) | 대지 주소 |
| `radius_m` | `250` | 반경 (m) — **2km 이상도 지원**(VWorld 박스당 10km² 한도를 클라이언트가 bbox 분할로 자동 우회, 상한 ~반경 15km) |
| `floor_height_m` | `3.0` | 기본 층고 (m) |
| `outputs` | `["skp"]` | `"skp"` · `"3dm"` 선택 |
| `layers` | `{"buildings": true}` | 레이어 활성화 |
| `output_dir` | `"output/"` | .3dm 저장 경로 |
| `missing_floors_policy` | `"default"` | 층수 누락 처리 정책 |
| `setback` | `false` | 이격면 분석 (stub) |

#### 건물만 (기본)

```python
generate_site_model("대전광역시 서구 괴정동 358")
```

#### 건물 + 지형 TIN + 지적 경계 + .3dm 동시 출력 (전체)

```python
generate_site_model(
  "대전광역시 서구 괴정동 358",
  radius_m=300,
  outputs=["skp", "3dm"],
  layers={"buildings": true, "terrain": true, "cadastral": true},
  output_dir="output/"
)
```

응답 구조:

```json
{
  "ok": true,
  "outputs": {
    "skp": {"code": "...", "solids": 12, "cadastral_parcels": 5},
    "3dm": {"path": "C:/.../output/대전광역시_서구_괴정동_358.3dm", "solids": 12}
  },
  "stats": {
    "buildings": 12,
    "solids": 12,
    "with_floors": 9,
    "flagged": 0,
    "cadastral_parcels": 5,
    "origin_offset": [233142.5, 415678.2]
  },
  "provenance": {
    "building_src": "VWorld LT_C_SPBD",
    "cadastral_src": "VWorld LP_PA_CBND_BUBUN",
    "radius_m": 300,
    "missing_floors_policy": "default",
    "fetched_at": "2026-07-01T03:15:00+00:00"
  }
}
```

#### 정사영상 텍스처

지형 TIN에 정사영상을 위→아래 평면투영으로 드레이프합니다. **이제 `.3dm`(Rhino)와 `.skp`(SketchUp 데스크톱
확장) 모두 지원** — `.3dm`은 `layers`에 `orthophoto: true` + `outputs`에 `"3dm"`, SketchUp 데스크톱 확장은
"정사영상" 체크로 활성화합니다(백엔드가 정사영상 PNG를 함께 서빙). 소스는 `ORTHO_SOURCE`(기본 `vworld`,
위성 영상 실측 검증). 키 없음/타일 실패 시 경고 후 건물·지형만 생성(조용한 fallback). 클라우드 MCP `.skp`
코드는 여전히 텍스처 불가(데스크톱 확장만 가능), 대반경 타일 정사영상은 미구현(단발 ≤500m만).

```python
generate_site_model(
  "대전광역시 서구 괴정동 358",
  outputs=["3dm"],
  layers={"buildings": True, "terrain": True, "orthophoto": True},
)
```

#### 층수 누락 정책

| `missing_floors_policy` | 동작 |
| --- | --- |
| `"default"` (기본) | 1층(3m) 적용, 경고 추가 |
| `"skip"` | 층수 누락 건물 제외 |
| `"flag"` | 1층 적용 + `.3dm`의 `buildings_unverified` 레이어, `.skp`명 `[층수미확인]` 접미사 |

```python
generate_site_model(
  "대전광역시 서구 괴정동 358",
  missing_floors_policy="flag"
)
```

---

## 4. 출력 확인

### SketchUp (.skp)

응답의 `outputs.skp.code`를 SketchUp MCP `build_model`에 전달합니다:

```python
# Claude(오케스트레이터)가 자동 수행
build_model(code=result["outputs"]["skp"]["code"])
```

### Rhino 3D (.3dm)

`outputs["3dm"]["path"]` 경로의 파일을 Rhino에서 열면 레이어가 분리되어 있습니다:

| 레이어 | 색상 | 내용 |
| --- | --- | --- |
| `buildings` | 파란색 | 층수 확인된 건물 Extrusion |
| `buildings_unverified` | 주황색 | 층수 미확인 (policy=flag 시) |
| `terrain` | 올리브 | 지형 TIN Mesh |
| `cadastral` | 황색 | 대지 경계 PolylineCurve |

### 실제 위치 복원

`stats.origin_offset`으로 EPSG:5186 절대 좌표 복원:

```python
x_abs = x_local + origin_offset[0]   # → EPSG:5186 X (m)
y_abs = y_local + origin_offset[1]   # → EPSG:5186 Y (m)
```

`.3dm` 파일에는 `model.Strings["origin_offset_x/y"]`와 각 객체 `UserString`에도 이중 기록되어 있습니다.

---

## 5. 지형 DEM 추가/갱신

**현재 비축**: **6개 광역단체** 커버 — 대전 14타일 · 서울 15 · 부산 24 · 대구 36 · 울산 20 · 세종 11
= **총 120타일**, 10km 격자·5m 해상도. 타일은 공개 GCS 버킷 `gs://arch-site-model-dem`(asia-northeast3)에
COG로 있고, 배포된 Cloud Run 앱이 GDAL `/vsicurl` 윈도우 범위읽기로 **요청 시점에** 읽습니다(컨테이너
이미지에 타일 미포함). `manifest.json`만 로컬/깃에 남고 큰 타일은 `DEM_TILE_BASE`로 원격 해석됩니다.
타일을 못 열면(로컬 부재/GCS 불가) 파이프라인은 경고와 함께 건물만 생성으로 조용히 폴백합니다.

> **지형 TIN — 적응형 LOD**: 런타임 지형 삼각망은 `TERRAIN_MAX_ERROR_M`(기본 0.25m) 오차 한도의
> 적응형 TIN으로 생성됩니다 — 평탄부는 큰 삼각형, 복잡부는 조밀 삼각형으로 수직오차를 보장하면서
> 삼각형 수를 크게 줄입니다(신반포 250m: 19,602→2,467, 약 86% 감소). 대반경일수록 모델이 가벼워지며
> 정확도 손실은 없습니다. (`0`으로 두면 균일 5m 격자.) `scipy`가 런타임 의존성으로 추가되었습니다.

새 지역 추가(반복 루프):

1. [국토지리정보원](https://map.ngii.go.kr)에서 대상 지역 수치지형도Ver2.0 SHP(1:5,000)를 아무 로컬
   폴더에 다운로드 — `N3L_F0010000.shp`(등고선) + `N3P_F0020000.shp`(표고점). **폴더 경로만 주면 됩니다**:
   (a) **동부원점(EPSG:5187) 지역**(부산·대구·울산 등)도 그대로 — `contour_bake`가 SHP를 읽을 때 파이프라인
   기준인 EPSG:5186으로 자동 재투영합니다(5186이면 no-op, 안 하면 지형이 ~2° 어긋남). (b) 시·도 단위로
   받아 경계 도엽이 여러 구 폴더에 중복 복사돼도 도엽 번호로 자동 중복제거됩니다. 사용자 추가 작업 없음.
1. 배치 베이크 — `--tile-km`로 지역을 격자 타일로 굽습니다(등고선·표고점 1회 읽고 타일별 서브셋
   보간, `--margin-m` 여유로 이음새 연속, 전역 격자 픽셀정합). `dem_<지역>_r{r}c{c}.tif` 타일이
   생성되고 `manifest.json`이 타일별로 자동 갱신됩니다.

```powershell
python -m src.terrain.contour_bake "<지역 폴더>" `
    --out geo_store/dem_<지역>.tif `
    --tile-km 10 --margin-m 300 `
    --method clough --guard 3
```

> `--method clough`(기본) = guarded CloughTocher 보간(계단현상 완화, 오버슈트는 linear±`guard`m로 클램프).
> `--method linear`로 옛 평면삼각 보간 폴백 가능. 계단 지표 진단: `python scripts/dem_staircase.py <tif>`.

1. COG 변환 — 베이크된 GeoTIFF를 Cloud-Optimized GeoTIFF(내부 타일링)로:

```powershell
python scripts/dem_to_cog.py geo_store --out cog_out --glob "dem_<지역>*.tif"
```

1. GCS 업로드:

```powershell
gcloud storage cp cog_out/dem_<지역>*.tif gs://arch-site-model-dem/dem/
```

1. 갱신된 `geo_store/manifest.json`을 commit + push → GitHub Actions가 재배포 → 새 지역이 GCS에서
   라이브 서빙됩니다. **깃에는 `manifest.json`만 들어갑니다(타일은 GCS로).**

---

## 6. 테스트 실행

```powershell
# 단위 테스트 (오프라인, API mock) — 전체 약 200개
python -m pytest tests/ --ignore=tests/test_integration_api.py -v

# 실제 VWorld API 연동 테스트 (키 필요)
python -m pytest tests/test_integration_api.py -v
```

---

## 구현 현황

| Phase | 내용 | 상태 |
| --- | --- | --- |
| 0 | 프로젝트 스캐폴드, MCP 서버 기동 | ✅ |
| 1 | `check_site_data` 선검사 | ✅ |
| 2 | `generate_site_model` 건물 매싱 → .skp | ✅ |
| 3A | 오프라인 DEM 굽기 (등고선SHP → GeoTIFF) | ✅ |
| 3B | 런타임 지형 TIN + 건물 앉힘 | ✅ |
| 4 | `.3dm` 이중 출력 + origin_offset 보존 | ✅ |
| 5 | 지적 레이어 + 층수 누락 정책 + provenance | ✅ |
| 확장1 | 홀(중정) 처리 — `holes_m` + 내벽 생성 | ✅ |
| 확장2 | `preview_site` — 건물 목록·규모 미리보기 | ✅ |
| 확장3 | 이격면(setback) 실연동 — arch-law-diagnose | 🚧 블로커 |
| 확장4 | `generate_site_tiles` — 대량건물 타일분할 | ✅ |
| 확장5 | 정사영상 텍스처 — 지형 드레이프 (.3dm + .skp 데스크톱 확장) | ✅ (단발 ≤500m, 대반경 타일 정사영상 미구현) |
| 웹앱 | FastAPI 백엔드 + React UI + 브라우저 3D 미리보기(F2) + Cloud Run 배포 | ✅ |
| 확장(Phase B) | SketchUp `.rbz` — 지형+건물 조립(B1, 깔끔한 실면) + 정사영상 드레이프 | ✅ |
| 대반경 조립 | SketchUp 확장 타일 순차조립(반경>500m, `/api/tile_plan`·`/api/generate_tile`, 진행바·취소) — `src/tiles_stream.py` | ✅ (1km 검증, .3dm은 타일링 불필요) |
| 지형 LOD | 적응형 TIN(`TERRAIN_MAX_ERROR_M`, 오차 유계 삼각형 ~86% 감소) | ✅ |
| 지형 개선 | 계단현상 완화 — guarded CloughTocher 재bake | ✅ |
| geocode | 지번+도로명(PARCEL→ROAD) 지원 | ✅ |
| 전국 DEM 확장 | 6개 광역단체 120타일 GCS COG 서빙(/vsicurl) + 다중 타일 mosaic + 배치 베이크(bake_tiled) + 좌표대 재투영(5187→5186) + 도엽 중복제거 + bbox 분할(반경 2km+) | ✅ 프로덕션 |
