# arch-site-model — 설계 사양서 (Claude Code 인계용)

> **목적**: 대지 주소 입력만으로 주변 지형·건물 3D 대지모델을 자동 생성하는 MCP 서버.
> KBS TopoMap의 수동 워크플로우를 "주소 딸깍 + API 취득"으로 대체.
> 본 문서는 이 세션에서 **실측·검증된 사실**만으로 작성됨. 추정은 `[목표]`로 명시.

---

## 0. TL;DR

```
입력:  대지 주소 + 반경(m)
처리:  주소→좌표(VWorld) + 건물/지적 API + 로컬 DEM 비축 → 좌표·층수·지형 계산
출력:  .skp (SketchUp MCP, 네이티브) + .3dm (rhino3dm, Rhino)
정체:  arch-site-context(인문·생활 맥락)와 분리된 별도 앱 = 물리 대지모델링
```

전 구간 데이터·출력 경로가 본 세션에서 검증 완료됨. 미검증 잔여 항목은 §9에 명시.

---

## 1. 배경 & 포지셔닝

### 1.1 KBS TopoMap이 푸는 문제 (참조 대상)
NGII 수치지도(.shp)를 SketchUp 3D로 변환. 등고선→지형, 건물경계→층수 매스, 도로→평면.
**한계**: ① 국토정보플랫폼 로그인→영역신청→문자→다운로드 **수동 4단계**, ② 레이어별 .shp를 하나씩 import, ③ SketchUp 종속(루비라 SketchUp 켜야 실행), ④ 결과물이 .skp에 갇힘.

### 1.2 경쟁자 분석 (arch-site-mcp by SeonJ)
| 배울 점 | 피할 점 |
|---|---|
| MCP 3-도구 분리(discover/validate/generate) | 입력이 **수동 ZIP 업로드** (딸깍 아님) |
| validate를 별도 단계로 (지저분한 정부데이터 방어) | Rhino 3DM에 갇힘, 렌더 직결 안 됨 |
| 법적 이격면을 law MCP와 연결 | "15분 변환" — 배치 느낌 |
| PyQt5 시각 QA 체크포인트 | 야심 버전이 VWorld 3D + CesiumJS 의존(보안제약) |
| rhino3dm 출력(라이선스 불필요) | 정사영상 텍스처 없음 |

### 1.3 우리 차별화
- **주소 딸깍**(경쟁자는 수동 ZIP) — 건물·도로·지적을 API로 자동 취득
- **이중 출력** .skp(SketchUp MCP) + .3dm(rhino3dm) — 경쟁자는 3dm only
- **"재현이지 상상이 아님"** — gro_flo_co(실제 층수)로 직접 돌출, AI 추정 0%
- **이격면**은 보유 중인 arch-law-diagnose로 [목표]
- **정사영상 텍스처** = 빈칸 선점 [목표]

---

## 2. 시스템 아키텍처

### 2.1 앱 분리 원칙
```
arch-site-context (터읽기)          arch-site-model (본 앱)
= 인문·생활 맥락                    = 물리 대지모델링
  KOSIS 인구·경제·사회              건물 footprint·층수·지형·이격
  Kakao 반경 시설                   DEM TIN, 쿼드 솔리드 매스
        │                                  │
        └──── point-to-point 연결 ─────────┘
              (공유: 주소·좌표·PNU·BBOX)
```
두 앱은 **좌표·PNU를 공유**하되 독립 배포. 오케스트레이션 허브 없이 직접 연결.

### 2.2 스택
```
FastMCP (MCP 서버)              ← 경쟁자도 채택, Claude 오케스트레이션
FastAPI (Python 3.11)          ← 기존 스택
geopandas / shapely            ← .shp·폴리곤 처리
rhino3dm                       ← .3dm 출력
SketchUp MCP (build_model)     ← .skp 출력 (외부 위임)
pyproj                         ← EPSG:5186 ↔ 4326 좌표변환
GCP Cloud Run + Secret Manager + GCS  ← 기존 배포 패턴
로컬/GCS DEM 비축 스토어        ← B안 (지형)
```

### 2.3 실행 모델 (백그라운드)
엔진은 **"무엇을 어디에 몇 m로"만 계산**. 실제 .skp 조립은 SketchUp MCP에 위임.
→ 내 PC에 SketchUp 안 켜도 됨(클라우드 세션). 서버 배치 가능. "내 PC에서만 됨" 문제 소멸.

---

## 3. 데이터 소스 명세 (★ 본 세션 실측 검증)

### 3.1 주소 → 좌표 `[검증됨]`
```
GET https://api.vworld.kr/req/address
  service=address  request=getcoord  version=2.0
  crs=epsg:4326  type=PARCEL  format=json
  key=<KEY>  domain=<DOMAIN>
→ response.result.point.{x:경도, y:위도}
```
실측: "대전광역시 서구 괴정동 358" → (127.37098, 36.33998)
주의: 주소는 깔끔한 지번만. "일원(...)" 같은 설명 제거.

### 3.2 건물 footprint + 층수 `[검증됨]` ★핵심
```
GET https://api.vworld.kr/req/data
  service=data  request=GetFeature  data=LT_C_SPBD
  key=<KEY>  domain=<DOMAIN>  format=json
  geomFilter=BOX(minx,miny,maxx,maxy)  geometry=true  crs=EPSG:4326  size=<N>
→ response.result.featureCollection.features[]
```
**검증된 속성 필드** (실측 그대로):
| 필드 | 의미 | 용도 |
|---|---|---|
| `gro_flo_co` | **지상층수** (예: 4) | ★ 높이 = gro_flo_co × 층고 |
| `bd_mgt_sn` | 건물관리번호 (25자리) | 건축물대장 연결키 |
| `buld_nm` | 건물명 | 라벨 |
| `rd_nm` | 도로명 | 라벨 |
| `sido`/`sigungu`/`gu` | 행정구역 | 메타 |
| geometry.type | **MultiPolygon** | footprint 돌출 |

→ **별도 건축물대장 API 불필요.** 이 한 번에 footprint+층수 확보.
주의: `gro_flo_co`가 0/null인 건물 존재 가능 → §6.4 분기 처리.

### 3.3 대지 경계 (지적) `[검증됨]`
```
data=LP_PA_CBND_BUBUN  (나머지 파라미터 동일)
```
**검증된 속성**:
| 필드 | 의미 |
|---|---|
| `pnu` | 필지고유번호 19자리 |
| `addr` | 지번주소 |
| `jibun`/`bonbun`/`bubun` | 지번 |
| `jiga` | 개별공시지가 |
| geometry.type | MultiPolygon (대지 경계) |
→ 이격면 계산의 입력(대지 폴리곤) 확보.

### 3.4 지형 (DEM) `[목표 — B안]`
**제약**: NGII 표고/3D는 보안통제 대상. 깨끗한 bbox API 없음(공공데이터포털 등고선=파일데이터, VWorld 3D API 보안규정으로 중지).
**해법 (B안)**: 관심지역 DEM/등고선을 **사전 1회 다운로드 → 로컬/GCS 비축** → 요청마다 bbox 클립.
```
geo_store/
  manifest.json   { 지역, 파일, 좌표계(EPSG:5186), 갱신일, 도엽 }
  dem_*.tif       (또는 등고선 .shp)
```
지형 바뀌면 **파일 교체 + manifest 한 줄 수정** → 엔진 무수정.
**우선순위: DEM 격자** (등고선보다 안정적, "평평한 삼각형" 문제 없음). §6.5 참조.

---

## 4. MCP 도구 계약

도구 구성: **MVP 2개 + 확장 1개**.

### 4.1 `check_site_data` `[MVP]`
> "이 주소, 지금 만들 수 있나?" — 생성 전 취득 가능성 선검사.

```jsonc
// INPUT
{ "address": "대전광역시 서구 괴정동 358", "radius_m": 250 }

// OUTPUT
{
  "ok": true,
  "address": "대전광역시 서구 괴정동 358",
  "coord": { "lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326" },
  "bbox": [minx, miny, maxx, maxy],
  "buildings": { "available": true, "count": 37, "with_floors": 34 },
  "cadastral": { "available": true, "count": 12 },
  "terrain":   { "available": true, "source": "DEM", "tile": "dem_daejeon_seogu_2026Q2.tif" },
  "warnings": ["건물 3개는 gro_flo_co 누락 → 기본 높이 적용 예정"]
}
```
역할: 경쟁자의 `validate_zip` 자리. ZIP 검증 대신 **API 취득 가능 + 지형 비축 여부** 검사.
무작정 generate 돌렸다 "그 지역 DEM 없음"으로 실패하는 것 방지.

### 4.2 `generate_site_model` `[MVP]`
> "딸깍 → 3D"

```jsonc
// INPUT
{
  "address": "대전광역시 서구 괴정동 358",
  "radius_m": 250,
  "floor_height_m": 3.0,          // 층고 (기본 3.0)
  "outputs": ["skp", "3dm"],      // 둘 다 / 하나
  "layers": {                     // 선택 (기본 전부)
    "buildings": true, "terrain": true, "cadastral": true, "roads": false
  },
  "setback": false                // 이격면 [목표]
}

// OUTPUT
{
  "ok": true,
  "files": { "skp": "<url_or_path>", "3dm": "<path>" },
  "stats": {
    "buildings": 37, "terrain_triangles": 448,
    "origin_offset": [ox, oy],    // EPSG:5186 원점 오프셋 (복원용) — 필수 저장
    "elev_range_m": [6.0, 21.5]
  },
  "provenance": {                 // 정현님 출처표기 원칙
    "building_src": "VWorld LT_C_SPBD",
    "cadastral_src": "VWorld LP_PA_CBND_BUBUN",
    "terrain_src": "dem_daejeon_seogu_2026Q2.tif",
    "fetched_at": "2026-06-30T..."
  }
}
```

### 4.3 `preview_site` `[확장 — 나중]`
> "만들기 전에 뭐가 들어가나" — 생성 없이 건물목록·층수·대지·예상규모만.
> 사람 검토 흐름(경쟁자 PyQt5 QA 역할)이 필요해지면 추가.

---

## 5. 출력 포맷

| 포맷 | 생성 방식 | 용도 | 상태 |
|---|---|---|---|
| **.skp** | SketchUp MCP `build_model` | SketchUp 네이티브 (딸깍) | `[검증됨]` |
| **.3dm** | rhino3dm 라이브러리 | Rhino / 계산설계 | `[목표]` |
| .obj/.gltf | trimesh 등 | 렌더 직결 | `[목표·옵션]` |

엔진은 좌표·층수·메시를 계산 → 포맷별 어댑터로 분기. OBJ 중간단계 불필요(MCP 직접 .skp).
**SketchUp MCP 비용 주의**: 무료 월 30개 .skp, 초과 시 유료 entitlement(SketchUp Go $19.99/mo~).

---

## 6. 지오메트리 생성 로직 (★ 본 세션 검증된 코드 기반)

### 6.1 좌표계 처리 `[검증 필요]`
EPSG:5186(중부원점)은 100만 단위 → SketchUp 정밀도 붕괴.
**필수**: 작업영역 최소좌표를 빼서 원점을 (0,0) 근처로 이동. **offset 값을 출력에 저장**(실제 위치 복원·정사영상 정합용).
단위: SketchUp MCP는 **인치**. `M2I = 39.3701`.

### 6.2 건물 쿼드 솔리드 돌출 `[검증됨]`
삼각형 아님 — **변마다 수직 쿼드 1개** = 푸시풀 효과. 직사각형 6면, L자 8면 확인.
```python
def extrude_solid(fp_m, base_z_m, height_m):
    fp = [(x*M2I, y*M2I) for (x,y) in fp_m]; bz=base_z_m*M2I; h=height_m*M2I; n=len(fp)
    g = GeometryInput()
    g.set_vertices([SUPoint3D(x,y,bz) for x,y in fp] + [SUPoint3D(x,y,bz+h) for x,y in fp])
    lp=LoopInput()                                   # 바닥(하향)
    for i in range(n-1,-1,-1): lp.add_vertex_index(i)
    _,g=g.add_face(lp)
    lp=LoopInput()                                   # 천장(상향)
    for i in range(n): lp.add_vertex_index(n+i)
    _,g=g.add_face(lp)
    for i in range(n):                               # 옆면 = 쿼드
        j=(i+1)%n; lp=LoopInput()
        for vi in [i,j,n+j,n+i]: lp.add_vertex_index(vi)
        _,g=g.add_face(lp)
    return g
```
주의: MultiPolygon은 폴리곤별로 분리 처리. 내부 홀(중정)은 inner loop로 [목표].

### 6.3 건물 높이 `[검증됨]`
```
height_m = gro_flo_co * floor_height_m   # 층수 × 층고. AI 추정 없음.
```

### 6.4 층수 누락 분기 `[목표]`
```
gro_flo_co 있음 → 층수×층고
gro_flo_co 0/null → 옵션: ① 기본높이(예: 1층) ② 스킵 ③ 플래그만 (사용자 선택)
                    절대 임의 추정 금지. "확인 불가" 명시.
```

### 6.5 지형 TIN `[검증됨 — DEM 격자]`
DEM 격자 → 정규격자 삼각망(칸마다 대각 방향 교차) → 모든 내부 edge softening.
```python
# 격자 정점 생성 → 칸마다 삼각형 2개 (방향 (ix+iy)%2로 교차)
# fill 후: for e in edges: if len(e.get_faces())==2: e.set_soft(True); e.set_smooth(True)
```
검증: 16×14 격자, 448 삼각형, 표고 6~21.5m 언덕 정상 생성.
등고선(.shp) 방식 시 "평평한 삼각형" 보정 필요 → **DEM 우선 권장**.

### 6.6 건물-지형 정합 `[검증됨 + 개선필요]`
검증: footprint 중심 고도를 읽어 base에 앉힘(건물별 base z 상이 확인).
**개선 [목표]**: 중심 1점은 경사지에서 한쪽이 뜨거나 묻힘.
```
권장: base_z = min(각 footprint 꼭짓점의 DEM 고도) - 묻힘여유(0.5m)
     급경사 시 옹벽/성토 표면 생성 [목표]
```

---

## 7. 처리 파이프라인 (종합)

```
[generate_site_model]
  1. 주소 → 좌표 (VWorld geocoder)
  2. 좌표 + radius → BBOX (4326) / 5186 변환 + origin offset 산출
  3. 건물 취득   LT_C_SPBD → footprint(MultiPolygon) + gro_flo_co
  4. 지적 취득   LP_PA_CBND_BUBUN → 대지 경계 + pnu
  5. 지형 취득   로컬 DEM 비축에서 bbox 클립
  6. 좌표 정규화 모든 좌표 -= origin offset, m→inch
  7. 지오메트리:
       건물 → extrude_solid (층수×층고)
       지형 → DEM 격자 TIN + softening
       건물을 지형 고도에 앉힘 (min-vertex 방식)
       (이격면 → arch-law-diagnose 호출) [목표]
  8. 출력:
       skp → SketchUp MCP build_model
       3dm → rhino3dm
  9. provenance + origin_offset 기록
```

---

## 8. 부록 — NGII 레이어 코드 (KBS 분석 산출)
KBS TopoMap에서 추출한 **674개 NGII 지형지물 코드** 매핑 테이블 보유 (`ngii_layer_codes.json/csv`).
8개 대분류: A교통 / B건물 / C시설 / D식생 / E수계 / F지형(등고선) / G경계 / H주기.
파일명 규칙: `N3A_A0010000` = N국가기본도 / 3축척(1:5000) / A면(또는 L선·P점) / 코드.
레이어 필터·확장 시 재사용.

---

## 9. 검증 상태표 (검증됨 vs 목표)

| 항목 | 상태 |
|---|---|
| 주소 → 좌표 (VWorld) | ✅ 검증됨 (대전 실측) |
| 건물 footprint + 층수 (LT_C_SPBD/gro_flo_co) | ✅ 검증됨 (실측) |
| 대지 경계 + PNU (LP_PA_CBND_BUBUN) | ✅ 검증됨 (실측) |
| 쿼드 솔리드 돌출 (extrude_solid) | ✅ 검증됨 (MCP) |
| 층수 → 높이 매스 | ✅ 검증됨 (MCP) |
| DEM 격자 → 지형 TIN | ✅ 검증됨 (MCP) |
| 건물-지형 정합 (중심점) | ✅ 검증됨 / ⚠ min-vertex로 개선 필요 |
| SketchUp MCP → .skp 출력 | ✅ 검증됨 (클라우드) |
| EPSG:5186 origin offset | 🎯 목표 (로직 확정, 미구현) |
| .3dm (rhino3dm) 출력 | 🎯 목표 |
| 지형 DEM 비축·클립 (B안) | 🎯 목표 (경로 확정) |
| 층수 누락 분기 | 🎯 목표 |
| MultiPolygon/중정 홀 처리 | 🎯 목표 |
| 대량 건물(수백 개) MCP 성능 | 🎯 미검증 — 타일분할 필요 가능 |
| 이격면 (arch-law-diagnose 연동) | 🎯 목표 |
| 정사영상 텍스처 | 🎯 목표 (차별화) |

---

## 10. 개발 단계 (MVP 정의)

```
Phase 0  스캐폴드: FastMCP 서버 + .env(VWORLD_TEST_KEY 분리) + 좌표변환
Phase 1  check_site_data: 주소→좌표→건물/지적 취득 가능성 + 지형 비축 확인
Phase 2  generate_site_model (건물만): LT_C_SPBD → extrude_solid → .skp
Phase 3  + 지형: DEM 비축 클립 → TIN → 건물-지형 정합(min-vertex)
Phase 4  + .3dm 이중 출력 + origin offset 복원
Phase 5  + 대지/이격면, 층수누락 분기, provenance
─────── 이후 ───────
확장     preview_site / 정사영상 텍스처 / 대량건물 타일분할
```
완료 기준(정현님 원칙): **각 Phase는 팀원이 작성자 없이 실행 가능**해야 완료.

---

## 11. Claude Code 인계 메모
- 개발은 Claude Code(VS Code)에서. 본 문서가 단일 사양 기준.
- `.env`: `VWORLD_TEST_KEY`(실측) / `VWORLD_KEY`(운영) 분리 유지.
- Python 311 풀경로 사용(`C:\Users\20260102\AppData\Local\Programs\Python\Python311\python.exe`).
- 첨부 자산: `ngii_layer_codes.json/csv`, `test_building_api_v3.py`(API 호출 레퍼런스).
- SketchUp MCP 코드 규칙: import 금지, 인치 단위, 좌표 X=폭/Y=깊이/Z=높이, 쿼드 옆면.
