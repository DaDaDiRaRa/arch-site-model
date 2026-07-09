# arch-site-model — 신뢰성·활용 로드맵 (형제 앱 기능 경계 반영)

> 두 질문에 답하는 문서:
> 1. **임원·실무자가 봤을 때 이 산출물을 믿을 수 있는가?**
> 2. **더 나은 설계 결과물·설계제안서를 만들 때 어떻게 활용하는가?**
>
> 전제: `D:\APPS`는 쿤원(KUNWON)의 80+ 건축 AI 앱 스위트이며 `kw-ai-hub`가 카탈로그 허브다.
> 각 앱은 좁은 도메인 하나를 소유한다. **이 로드맵은 다른 앱과 중복되지 않는, arch-site-model
> 고유의 영역만 심화한다.** (2026-07-09 형제 앱 21종 조사 근거, §3)

---

## 1. 한 줄 결론

> **데이터 신뢰도는 국내에서 살 수 있는 어떤 상용 도구보다 높다. 그러나 현재 산출물은
> "믿을 수 있는 모델"이지 아직 "설득하는 제안"이 아니다.** 임원·심의를 통과시키려면
> ① *신뢰를 눈에 보이게* 만들고, ② 모델을 *공간 판단(측정·분석)* 으로 바꾸되,
> ③ **법령 판정·제안서 조립은 이미 그걸 소유한 형제 앱에 위임**해야 한다.

---

## 2. 신뢰성 평가 (임원·실무자 관점)

### 2.1 코드로 검증한 "진짜 믿을 만한" 부분

핵심 주장 **"재현이지 상상이 아님 · AI 추정 0%"** 은 사실이다(코드 실측 확인).

| 항목 | 근거 (코드) | 신뢰 등급 |
| --- | --- | --- |
| 층수 | `building.py::floors_of()`가 VWorld `gro_flo_co`만 읽고, 누락/0/null이면 추정 대신 `None`. 추정 로직이 코드에 **부재** | ★★★ 실측 |
| 건물 높이 | 높이 = 실제 층수 × 층고. 중간 가공 없음 | ★★☆ 실측 층수 × 가정 층고 |
| 지형 | 적응형 TIN이 수직오차 **±25cm 보장**(`TERRAIN_MAX_ERROR_M=0.25`), 실측 NGII 5m DEM | ★★★ 실측 |
| 계보(provenance) | 모든 산출물에 출처·시각·반경·정책 기록(`building_src`·`fetched_at`·`terrain_tile`·`orthophoto_src`…) | ★★★ 감사 가능 |
| 자동 QA | 8종 검사(급경사·부유·침몰·겹침·자기교차·슬리버·지형 스파이크·지형밖)가 KBS TopoMap "눈검사"를 코드로 대체 | ★★★ 자동 검증 |

디지털트윈 신뢰성 문헌의 결론과 정확히 일치: **"모델은 그 뒤의 데이터만큼만 믿을 수 있다 —
권위 있는 출처 + 계보 추적 + 검증 가능성"**. 우리는 이 요건을 이미 코드로 충족한다.

### 2.2 임원이 5분 안에 잡아낼 신뢰 약점 (가장 위험)

신뢰를 깨는 건 부정확이 아니라 **"추정을 사실처럼 보여주는 것"**. 현재 산출물엔 침묵하는 가정이 섞여 있다.

| 침묵하는 가정 | 문제 | 근거 |
| --- | --- | --- |
| `policy="default"` 층수 누락 건물 | 1층(3m) 추정인데 **검증 건물과 시각적으로 동일** | pipeline 정책 분기 |
| 정사영상 타일 실패 | **회색 이미지가 정상 위성사진처럼** 보임 | `missing_tiles` → 회색 |
| 건물 높이 = 층수 × 3m | **실측 높이 아님**(옥탑·경사지붕·층고편차 무시) | `DEFAULT_FLOOR_H_M=3.0` |
| 고가·교량 | DSM 부재로 **지면 높이로 잘못 렌더**(블로커) | `dem_dsm_strategy.md` |

임원이 이 중 **하나만 발견해도 전체를 불신**한다. 반대로, 먼저 정직하게 라벨링하면
"이 팀은 자기 데이터의 한계를 안다"는 신뢰가 생긴다. → **트랙 A의 근거.**

### 2.3 경쟁 위치

```text
Cadmapper                       arch-site-model                Autodesk Forma
(OSM 추정높이·30m SRTM,   ←──   VWorld 실측 footprint·        ──→  권위 GIS 데이터 +
 건물 누락 다수)                 실제 층수·5m DEM·지적/도로/수계       일조·바람·조망 분석 내장
데이터 신뢰: 낮음                데이터 신뢰: 최상                     데이터 + 분석: 최상
```

- **데이터 권위성**: Cadmapper를 압도(핵심 방어 우위).
- **격차**: Forma는 데이터에 더해 *분석·판단*을 판다. 우리는 아직 모델만 준다. → **트랙 B의 근거.**
- 단, 우리는 Forma가 아니라 **쿤원 스위트의 3D 공간 계층**이다. 분석을 혼자 다 하는 게 아니라
  형제 앱에 공간 데이터를 먹인다(§3·§4).

---

## 3. 형제 앱 기능 경계 지도 (중복 회피 — 이 로드맵의 핵심 제약)

`D:\APPS` 조사 결과. **핵심 통찰: arch-site-model은 스위트에서 유일하게 "권위 있는 3D 공간
기하"를 생산하는 앱이다.** 따라서 고유 자리 = 3D 기하가 있어야만 가능한 일(측정·공간분석)을
심화하고, 나머지는 각 도메인 소유 앱에 위임·연동한다.

| 형제 앱 | 소유 도메인 | 우리와의 관계 |
| --- | --- | --- |
| **arch-site-context** (터읽기) | 인간·생활 맥락(인구·시설·수급진단·부동산·날씨). 3D/지형/조망/법규 전부 스코프 밖 | **무관·병렬** — 둘 다 설계자에게 데이터를 주지만 물리 vs 생활로 분리 |
| **arch-law-diagnose** | 대지 법규 판정 엔진 — **정북일조 사선제한(§61)·건폐율·용적률** pass/fail. 표 기반 REST, 정북 이격거리·인접지 용도를 **사람이 손으로 입력** | **상보·연동** — 판정은 diagnose 소유. 우리는 3D에서 그 입력값을 *자동 측정*해 먹인다 |
| **arch-law-graph** | 건축 법령 지식원(42,000+ 조문 검색·RAG). 계산 없음 | **무관** — 법령 해석 전담 |
| **law-qa** | 자연어 법규 QA(RAG). 계산 없음 | **무관** |
| **elevation-renderer** | AI **2D 입면도** 생성(사진/주소 → 입면 이미지) | **상보** — 2D 입면 도면 ≠ 3D 맥락 속 스카이라인 시뮬레이션 |
| **competition_comparison** | 설계공모 제안서 분석·비교 + **"수주 제안서" 생성** | **연동(침범 주의)** — 제안서 *조립*은 이 앱 소유. 우리는 *공간 타당성 데이터*만 먹인다 |
| **sketch-2-render** / **floorplan-renderer** / **Floor Plan-3d-visualizer** | 스케치→렌더 / 평면도 텍스처 / 평면도→3D 아이소 | **무관** — 건물 내부·2D 이미지 도메인 |
| **kw-ai-hub** | 80+ 앱 카탈로그 허브(네비게이션·메타) | **등록 대상** — 우리 앱을 여기 등록해 발견 가능하게 |
| **kbs_topomap 3.9.2** | 레거시 수동 SketchUp 지형 모델링 툴 | **대체 대상** — arch-site-model이 자동화하는 원본 |

### 명시적 비목표 (Non-goals — 각 도메인 소유 앱에 위임)

- ❌ **법령 판정**(정북일조 pass/fail·건폐율·용적률) → `arch-law-diagnose`
- ❌ **법령 해석·조문**(무엇이 규정인가) → `arch-law-graph` / `law-qa`
- ❌ **설계제안서 조립·비교**(제안서 문서 생성) → `competition_comparison`
- ❌ **인간·생활 맥락**(인구·시설·수급) → `arch-site-context`
- ❌ **2D 입면도 생성** → `elevation-renderer`

> arch-site-model은 위 어느 것도 *재현*하지 않는다. 대신 그들이 못 가진 **3D 공간 기하·측정값**을
> 생산해 각 앱에 공급하는 스위트의 "공간 substrate"가 된다.

---

## 4. 로드맵 — 3트랙 (경계 반영 + 실행계획)

> 노력: **S**(≤1주) / **M**(1–3주) / **L**(3주+).  임팩트: **상 / 중 / 하**.
> 각 항목의 *산출물(done)* = 완료 판정 기준, *의존성* = 착수 전 선행과제.

### 트랙 A · 신뢰를 "눈에 보이게" — 100% 내부, 무충돌 — ✅ **완료 (A-1·A-2·A-3)**

> 정확도를 올리는 게 아니라 이미 있는 신뢰 정보를 표면으로 끌어올리는 것. 데이터가 이미 다 있어 값쌌다.

**A-1. 데이터 신뢰도 리포트 (플래그십)** — `노력 S · 임팩트 상` — ✅ **백엔드 구현 완료**
- *산출물(done)*: `generate()` 결과에 `result["trust_report"]`(구조화 dict) 부착 ✅ · `tests/test_trust_report.py`
  8개 통과 ✅ · `/api/generate` 응답 노출 ✅ · **웹 패널 렌더 ✅**(App.tsx `TrustPanel`, 빌드 검증).
  [남은 소비자] 인쇄용 1페이지(HTML) · `.3dm`/`.skp` 노트 임베드.
- *구현*: `src/trust_report.py::build_trust_report(result) -> dict` — **이미 조립된 파이프라인 결과
  (provenance+qa+stats+outputs) 위의 순수 뷰 함수.** 신규 데이터 취득 0. 상세 필드·목업 → §4-A1.
- ~~의존성 ①~~ **불필요 확인**: "실측 vs 추정" 층수는 이미 있는 `stats.with_floors`(gro_flo_co 있는
  건물)와 `stats.buildings`(총 건물)로 계산 — `BuildingSolid` 수정 없이 됨. (A-2 시각화에서 per-solid
  tier가 필요하면 그때 `floors_source` 추가 검토.)

**A-2. 불확실성 가시화** — `노력 M · 임팩트 상` — ✅ **완료**
- *산출물(done)*: `BuildingSolid.floors_source`(measured|default) 신설 → **추정 건물이 default 정책에서도
  시각 구분**(핵심: 침묵하는 추정 제거). 뷰어 색(verified=램프 / 추정=주황) ✅ · `.3dm` buildings_unverified
  레이어를 추정 전체로 확장 ✅ · 확장 builder.rb 색/레이어 ✅ · 정사영상 결측 타일 체커 패턴("영상 없음") ✅.
- *구현*: `building.py`+`pipeline`(geometry `verified`)·`rhino.py`·`builder.rb`·`ortho.py`(`_nodata_tile`).
  `tests/test_pipeline.py` +1(default에서 추정 2동 `verified=False` 검증). 전체 303 green + 프론트 빌드 ✓.
  (`.skp` 이름 접미사·`stats.flagged`는 정책 의미 보존. 확장 색은 데스크톱 실기 렌더 검증 대기.)

**A-3. QA 실무언어 + "검수 통과" 스탬프** — `노력 S · 임팩트 중` — ✅ **완료**
- *산출물(done)*: finding마다 심의어 `label`(예: `terrain_spike`→"지형 돌출·웅덩이") ✅ ·
  `qa.summary`에 `passed: bool` + `stamp` ✅ · 웹 QA 패널이 스탬프·라벨 렌더(내부 코드명 숨김) ✅.
- *구현*: `qa.py` `KIND_LABELS` 맵 + summary 확장 · `tests/test_qa.py` +3 · App.tsx QA 패널. 전체 302 green.

### 트랙 B · 공간 측정·분석 (우리만 가능 → 형제 앱에 먹임)

> 3D 기하가 있어야만 가능한 일. 법령 판정은 하지 않고, 측정·시뮬레이션 결과를 소유 앱에 넘긴다.

**B-1(재조정). 정북일조 사선 "봉투(envelope)" 작도** — `노력 L · 임팩트 상` — 🔀 **재설계(정찰 2026-07-09)**
- ⚠️ *정찰 결과*: diagnose는 **좁은 계약 없음** — `POST /api/diagnose`에 필수 8필드(address·building_use·
  site_area·building_area·floor_area_above·floors_above·height·floors_below = **설계 프로그램**)를 요구해
  현황만 있는 우리가 못 채움. "measure→feed diagnose"는 **폐기**(setback 블로커 유지).
- *재설계 산출물*: **정북일조 사선 봉투(buildable envelope)를 우리가 기하로 직접 작도** — subject parcel에
  공개규칙(`h≤10m→1.5m`, `>10m→h/2`, 주거지역만) 적용. 용도지역은 arch-law-graph `/api/zoning`으로.
  *판정*(특정 설계안 pass/fail)만 diagnose 유보 → 경계 유지(우리는 봉투=기하, diagnose는 verdict).
- *진행*: **① arch-law-graph 용도지역 연동 ✅ 완료**(`src/geo/zoning.py`·`layers.zoning`·조용한 fallback·
  `ZONING_BASE`, 테스트 6+2, 웹 배지). 남은 선행: ② subject-parcel 지정 프리미티브, ③ 진북 보정(격자북≠진북),
  ④ 봉투 작도+뷰어/.3dm 렌더.

**B-3. 일조·그림자 분석 export** — `노력 M · 임팩트 중상` — ✅ **완료 (뷰어 포함)**
- *산출물(done)*: `src/solar.py`(sun_position 저차 천문식 + building_shadow 민코프스키 그림자 +
  shadows_for_day) ✅ · `layers.shadows=True` → `result["shadows"]`(동지 09~15시, 로컬 미터) ✅ ·
  `/api/generate` 노출 ✅ · **F2 뷰어 "일조분석" 토글 + 시각 슬라이더**(그림자 폴리곤을 지면 표고에
  반투명 렌더, 정오 기본) ✅ · `tests/test_solar.py` 9 + 파이프라인 1. [남은 선택] 일조시간(누적) 맵.
- *구현*: 태양 저차 천문식(외부 API 0) + footprint×높이 태양반대 투영. `Viewer3D.tsx` buildShadowOverlay.
  전체 313 green + 프론트 빌드 ✓. (진북≈격자북, 그림자 지면 평지 가정 — 슬라이더 옆 날짜 표기.)

**B-2. 조망·스카이라인 3D 시뮬레이션 (경관심의)** — `노력 L · 임팩트 상 · 의존성: 제안 매스 입력`
- *산출물(done)*: 핵심 조망점 카메라 렌더 + 스카이라인 종/횡단면 컷 + before/after 세트.
- *경계*: `elevation-renderer`(2D 입면도)와 **상보** — 우리는 3D 맥락 속 스카이라인.
- *의존성*: "제안 매스" 입력 프리미티브 필요(현재는 현황만 모델링). B-1의 subject-parcel과 공유.

**밀도 지표**: 건폐율/용적률 *판정*은 `arch-law-diagnose` 소유 → 우리는 **주변 밀도 맥락 측정**만.

### 트랙 C · 정확도 심화 (외부 블로커 대기) — `착수 불가`

- DSM(실측 높이·고가 데크), 식생 — 기관 접근/데이터 협약 생기면 승격. 지금 갈아넣을 곳 아님.

---

## 4-A1. 상세 스펙 — 데이터 신뢰도 리포트 (플래그십)

**필드 정의** (전부 이미 파이프라인 결과에 존재 — 신규 취득 없음):

| 필드 | 값 예시 | 출처 (코드/데이터) |
| --- | --- | --- |
| 건물 총수 | 142 | `len(solids)` |
| 실측 층수 수·% | 128 (90%) | `stats.with_floors` / `stats.buildings` |
| 추정 층수 수 | 14 | `stats.buildings − stats.with_floors` |
| 지형 출처 | dem_daejeon_36710065_66.tif | `provenance.terrain_tile` |
| 지형 수직정확도 | ±0.25m | `config.TERRAIN_MAX_ERROR_M` |
| 표고 범위 | 35.2–112.7m | `stats.elev_range_m` |
| TIN 삼각형 수 | 2,467 | `outputs.skp.terrain_triangles` |
| 정사영상 출처 | VWorld 위성 | `provenance.orthophoto_src` |
| 정사영상 zoom·결측 | 18 · 0타일 | `provenance.orthophoto_zoom`, `…orthophoto.missing_tiles` |
| QA 요약 | 경고 3 · info 1 | `result.qa.summary` |
| 취득 시각·반경 | 2026-07-09 · 250m | `provenance.fetched_at`, `radius_m` |
| 좌표계 | EPSG:5186 | 고정 |
| 한계 고지 | (목업 하단) | 정적 + 조건부(DSM 제외 등) |

**인쇄용 1페이지 목업**:

```text
┌────────────────────────────────────────────────────────────┐
│  대지모델 데이터 신뢰도 리포트                              │
│  대전광역시 서구 괴정동 358 · 반경 250m · 2026-07-09 12:04  │
├────────────────────────────────────────────────────────────┤
│  건물   142동  │ 실측 층수 128동 (90%)  ▓▓▓▓▓▓▓▓▓░         │
│                │ 추정 층수  14동 (10%)  → 지도에 주황 표시   │
│  지형   NGII 수치지형도 5m · 수직오차 ±0.25m 보장           │
│         표고 35.2–112.7m · 적응형 TIN 2,467 삼각형          │
│  정사영상 VWorld 위성 · zoom18 · 결측 0타일                 │
│  검수(QA) 경고 3 · info 1  → 위치 3D 핀                     │
│         · 급경사 앉힘 2 · 건물 겹침 1                       │
│  출처   건물 VWorld LT_C_SPBD · 지적 LP_PA_CBND_BUBUN       │
│         취득 2026-07-09 12:04 KST · 좌표 EPSG:5186          │
├────────────────────────────────────────────────────────────┤
│  ⚠ 이 모델은 기존 현황의 재현입니다. 건물 높이는 실제       │
│    층수 × 3.0m 층고 가정이며 실측 높이가 아닙니다.          │
│    고가·교량은 DSM 부재로 제외되었습니다.                   │
└────────────────────────────────────────────────────────────┘
```

> 하단 "정직한 한계 고지"가 역설적으로 신뢰의 핵심 — 임원이 스스로 발견할 약점을 먼저 명시한다.

**소비자**: (a) 웹 패널, (b) 인쇄 1페이지(HTML→PDF), (c) `.3dm`/`.skp` 노트 임베드.
엔진은 데이터만 만들고 각 소비자는 렌더만 한다(정사영상·QA와 동일 원칙).

---

## 5. 우선순위 & 착수 순서

**임팩트 × 노력 매트릭스**:

```text
          노력 S          노력 M          노력 L
 임팩트 상 │ A-1  ★1     │ A-2  ★2       │ B-1 ★4 · B-2
 임팩트 중 │ A-3  ★3     │ B-3  ★5       │
 차단됨    │                             │ C (DSM·식생)
```

**착수 순서(★)**:
1. **A-1** 데이터 신뢰도 리포트 — 최저 노력·최고 임팩트. "믿을 만한가?"에 직접 답, 트랙 B의 전제.
   (선행: 의존성 ① `floors_source` 필드 — 반나절)
2. **A-2** 불확실성 가시화 — 추정이 사실처럼 보이는 위험 제거. A-1 tier 정의 재사용.
3. **A-3** QA 실무언어·스탬프 — 값싼 마감.
4. **B-1** 정북 이격거리 측정 → diagnose — 규제 수요·스위트 연동 모범·setback 해소.
   (선행 3종: subject-parcel · 진북 보정 · diagnose 계약 검증 — 여기서부터 진짜 개발)
5. **B-3** 일조·그림자 export — 미소유·마찰 0.
6. **B-2** 조망·스카이라인 — 심의 직결. B-1의 subject-parcel·제안매스 프리미티브 공유 후.

> 갈림길: A트랙(3개, 전부 S~M·무의존)을 **한 스프린트로 묶어** 신뢰 기반을 먼저 완성한 뒤
> B트랙(공간분석, 프리미티브 신설 필요)으로 넘어가는 걸 권장.

---

## 6. 통합 계약 (스위트 안에서 누가 뭘 주고받나)

| 방향 | 내용 | 계약 |
| --- | --- | --- |
| arch-site-model ← **arch-law-graph** | 사이트 용도지역(zone_name·zone_key) | `GET /api/zoning?address=` — ✅ 연동(`ZONING_BASE`, 조용한 fallback) |
| ~~→ arch-law-diagnose~~ | ~~정북일조 입력값~~ | ⚠️ 좁은 계약 없음(필수 8필드=설계 프로그램) → 봉투는 자체 작도 |
| arch-site-model → **competition_comparison** | 공간 타당성 데이터 레이어 | 그림자 영향·스카이라인 저촉·경사도 (제안서 *조립*은 그쪽) |
| arch-site-model ↔ **elevation-renderer** | 3D 맥락 배경 / 2D 입면 상호참조 | 스카이라인 컨텍스트 제공 |
| arch-site-model → **kw-ai-hub** | 앱 등록(발견 가능성) | 카탈로그 메타 등록 |
| arch-site-context ∥ arch-site-model | 병렬 — 생활맥락 + 물리맥락 | 둘 다 설계자 입력 |

---

## 부록 · 근거

- **코드 실측**: `src/qa.py`(8종 검사·임계값), `src/geometry/building.py::floors_of()`,
  `src/pipeline.py`(provenance dict), `src/config.py`(`TERRAIN_MAX_ERROR_M=0.25`,
  `DEFAULT_FLOOR_H_M=3.0`), `docs/dem_dsm_strategy.md`(DSM 블로커).
- **형제 앱 조사**: `D:\APPS` 21종 중 arch-site-context / arch-law-diagnose·graph / law-qa /
  elevation-renderer / sketch-2-render / floorplan-renderer / Floor Plan-3d-visualizer /
  competition_comparison / kw-ai-hub / kbs_topomap 의 CLAUDE.md·README 조사(2026-07-09).
- **외부 리서치**:
  - 건축 심의·설계공모 제출물 기준 — [서울시 건축 설계공모 지침서](https://project.seoul.go.kr/downloadFile.do?fileSeq=127171), [건축심의 가이드라인(NAACC)](https://naacc.go.kr/file/flexer_doc/file/archi/20160609/16E3F6AF643F4BE299E49C7677954F10.hwp.files/Sections1.html)
  - 경관심의 조망 시뮬레이션·스카이라인 단면 — [경관심의 조망 시뮬레이션 실무](https://kmong.com/gig/660670)
  - 정북일조 사선제한 — [건축법 시행령 제86조](https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B1%B4%EC%B6%95%EB%B2%95%EC%8B%9C%ED%96%89%EB%A0%B9/%EC%A0%9C86%EC%A1%B0), [정북일조권 사선제한 해설](https://landvalueup.hankyung.com/valueupguide-20240320-1200/)
  - 경쟁 도구 — [Autodesk Forma](https://www.autodesk.com/products/forma/overview), [ArcGIS for Forma](https://www.esri.com/en-us/arcgis/products/arcgis-for-autodesk-forma/overview), [Cadmapper](https://cadmapper.com/)
  - 데이터 신뢰·계보 — [Esri: Data Governance for Digital Twins](https://www.esri.com/arcgis-blog/products/arcgis-online/3d-gis/securing-the-invisible-why-digital-twin-security-starts-with-data-governance)
