# arch-site-model

대지 주소 입력만으로 주변 **지형·건물 3D 대지모델**을 자동 생성하는 MCP 서버.
주소 → 좌표(VWorld) → 건물/지적 API + 로컬 DEM 비축 → 좌표·층수·지형 계산 →
`.skp`(SketchUp MCP) / `.3dm`(rhino3dm) 출력.

> 전체 사양은 [arch-site-model_설계사양서.md](arch-site-model_설계사양서.md) 참조.

## 현재 상태

- **Phase 0** ✅ 스캐폴드 + 환경변수 + 좌표변환 유틸
- **Phase 1** ✅ `check_site_data` — 주소→좌표→건물/지적 취득 가능성 + 지형 비축 확인
- **Phase 2** ✅ `generate_site_model` (건물) — LT_C_SPBD footprint × 층수 → 쿼드 솔리드 → `.skp`

(이후 Phase: 3 지형 → 4 `.3dm`/오프셋 → 5 지적/이격면)

### `generate_site_model` (Phase 2: 건물)

```powershell
python -c "from src.pipeline import generate; o=generate('대전광역시 서구 괴정동 358', 120); print(o['stats']); open('skp_code.py','w',encoding='utf-8').write(o['outputs']['skp']['code'])"
```

엔진은 **SketchUp MCP `build_model` 에 넣을 Python 코드 문자열**을 `outputs.skp.code` 로 반환한다
(실제 `.skp` 조립은 오케스트레이터가 MCP로 수행 — 사양서 §4 권장 방식). `stats.origin_offset`
(EPSG:5186 원점)은 실제 위치 복원용으로 반드시 보존한다.

검증(실측): 괴정동 358 / 120m → 건물 7동, 면수 = footprint 변수 + 2 (직사각형 6 / L자 8),
높이 = gro_flo_co × 층고. SketchUp MCP `.skp` 생성 1건 확인.

### `check_site_data` 사용 예

```powershell
python -c "from src.site_check import check_site_data; import json; print(json.dumps(check_site_data('대전광역시 서구 괴정동 358', 250), ensure_ascii=False, indent=2))"
```

→ `buildings.count`/`with_floors`, `cadastral`, `terrain.available`, `warnings` 리포트.
MCP 도구로도 등록됨(`src/server.py`). 실측 검증은 `pytest tests/test_integration_api.py`
(키 없으면 자동 skip).

## 요구사항

- Python **3.11**
  - Windows 본 환경 풀경로: `C:\Users\20260102\AppData\Local\Programs\Python\Python311\python.exe`
- VWorld API 키 ([발급](https://www.vworld.kr))

## 환경 세팅

```powershell
# 1) 저장소 루트(d:\APPS\arch-site-model)에서 가상환경 생성
$py = "C:\Users\20260102\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 의존성 설치
python -m pip install -U pip
python -m pip install -r requirements.txt

# 3) 환경변수 파일 작성 (.env 는 git 추적 안 됨)
Copy-Item .env.example .env
#   → .env 를 열어 VWORLD_TEST_KEY / VWORLD_DOMAIN 채우기
```

macOS/Linux:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install -U pip && python -m pip install -r requirements.txt
cp .env.example .env   # 편집
```

## 환경변수

| 변수 | 설명 |
|---|---|
| `VWORLD_TEST_KEY` | VWorld 테스트(개발) 키 — **운영 키보다 우선** |
| `VWORLD_KEY` | VWorld 운영 키 (테스트 키 비었을 때 사용) |
| `VWORLD_DOMAIN` | 키 발급 시 등록한 도메인 |
| `GEO_STORE` | (선택) 지형 비축 경로. 기본 `geo_store/` |

키 선택 로직은 [src/config.py](src/config.py): `VWORLD_TEST_KEY or VWORLD_KEY`.

## 동작 확인

좌표변환 유틸 (Phase 0 완료기준):

```powershell
python -c "from src.geo.crs import to_5186; print(to_5186(127.371, 36.340))"
# → 대전 EPSG:5186 평면좌표 (x, y) 출력
```

테스트 (대전 좌표 왕복변환 오차 < 0.01m):

```powershell
python -m pytest tests/test_crs.py -v
```

## 디렉터리 구조

```
src/
  config.py        환경변수·단위 상수 (M2I, 기본 층고, geo_store)
  server.py        FastMCP 서버 진입점 (도구는 Phase 1+ 등록)
  geo/crs.py       좌표변환: to_5186 / to_4326 / origin_offset / apply_offset
  data/            VWorld·DEM 취득         (Phase 1+)
  geometry/        돌출·TIN·정합           (Phase 2+)
  output/          .skp / .3dm 어댑터       (Phase 2+/4+)
tests/
  test_crs.py      좌표변환 검증
geo_store/         DEM 비축 (git 미추적, 사양서 §3.4)
```
