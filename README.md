# arch-site-model

> 대지 주소 입력만으로 주변 지형·건물 3D 대지모델을 자동 생성하는 MCP 서버.

**입력**: 대지 주소 + 반경(m)  
**출력**: `.skp` (SketchUp MCP) · `.3dm` (Rhino 3D)  
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

> **키 발급**: [VWorld 공간정보 오픈플랫폼](https://www.vworld.kr) → 개발자 → 인증키 발급

---

## 2. MCP 서버 실행

```powershell
python -m src.server
```

서버가 stdio 모드로 기동됩니다. Claude Desktop에서 아래 MCP 설정으로 연결하세요.

### Claude Desktop 연결 (`claude_desktop_config.json`)

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
| `radius_m` | `250` | 반경 (m) |
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

**현재 비축**: `geo_store/dem_daejeon_36710065_66.tif` (대전 서구 일부, 5m 해상도)

새 지역 추가:

1. [국토지리정보원](https://map.ngii.go.kr) 수치지형도Ver2.0 SHP 다운로드 (1:5,000 도엽)
2. `N3L_F0010000.shp`(등고선) + `N3P_F0020000.shp`(표고점) 준비

```powershell
python -m src.terrain.contour_bake <shp_dir> `
    --cell 5 `
    --out geo_store/dem_신지역.tif `
    --region "지역명" `
    --sheets 도엽번호1 도엽번호2
```

1. `geo_store/manifest.json` 업데이트 (`bounds`, `file` 항목 추가)

---

## 6. 테스트 실행

```powershell
# 전체 단위 테스트 (오프라인, API 미호출) — 145개
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
