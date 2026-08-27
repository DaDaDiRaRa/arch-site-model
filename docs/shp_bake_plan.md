# SHP 베이크 실행 계획 — 지역 추가 절차서

> 수치지형도 SHP → DEM·도로·수계 → GCS 서빙까지, 지역 하나를 추가하는 전체 절차.
> 2026-08-25 작성 / **2026-08-27 소스를 연속수치지형도 [도 영역]으로 전환**(§1).
> 검증된 레시피(대전·서울·부산·대구·울산·세종 6개 광역단체 적용 완료).

---

## 0. 이 문서를 읽기 전에 알아야 할 것

**베이크는 지역당 1회다.** 등고선 지형은 사실상 변하지 않으므로 갱신 주기가 없다.
"주기적으로 다시 굽는 유지보수"가 아니라 "지역당 한 번 내는 입장료"에 가깝다.
대전을 2026-07에 구운 뒤 지금까지 손대지 않았고, 앞으로도 손댈 일이 없다.

**자동화할 수 없는 단계는 딱 하나 — SHP 다운로드(1단계)뿐이다.**
국토정보플랫폼은 벌크/API 다운로드를 제공하지 않아 사람이 포털에서 받아야 한다.
그 이후 2~6단계는 전부 명령 한 줄씩이다.

**왜 이 경로밖에 없는가** (2026-08-25 재확인, 상세 [[nationwide-dem-ngii-source]])

| 대안 | 판정 |
| --- | --- |
| 실시간 표고 API | 없음 — VWorld 3D Data Open API 2019 폐쇄 |
| VWorld 3D `.bil` 표고 타일 스크래핑 | **금지** — 제한 공간정보, 공간정보산업진흥원이 능동 단속(코드 삭제 요청 사례) |
| 무료 공개 DEM 다운로드 | 90m — 반경 250m 사이트에 셀 3~5개라 대지모델엔 무용. 단 전국·무료라 **미커버 지역 폴백 후보**로는 남아 있음(전국 한 파일본은 이미 EPSG:5186) |
| 전세계 공개 30m 타일(terrarium 등) 폴백 | 기술 검증은 됨(키 없이 fetch 실측 OK), **2026-08-25 사용자 결정으로 미도입** |
| NGII 안심구역 5m/1m DEM | 검토 중 — `docs/ngii_data_inquiry_plan.md` 참조. **1회성 반출 모델이라 SHP 대체 아님** |

---

## 1. SHP 확보 — 유일한 사람 일

**받을 것**: 대상 시도의 **연속수치지형도(SHP) [도 영역]** — 1:5,000, 무료, 로그인 필요
**받는 곳**: 국토정보플랫폼 → **국토정보맵 > 공간정보받기** → 행정구역에서 시/도 선택
  → 목록에서 `연속수치지형도(SHP파일)` 펼치기 → **(-)[도 영역]** (시군구 영역 아님)

**대상지형지물은 4개만 체크**: **지형(F) · 교통(A) · 수계(E) · 시설(C)**
건물(B)·식생(D)·경계(G)·주기(H)는 안 받는다 — 건물·지적은 VWorld 실시간이 층수까지 주고,
용도지역은 형제 앱 arch-law-graph 소관이라 중복이다.

| 카테고리 | 우리가 쓰는 것 |
| --- | --- |
| 지형 F | `F0010000` 등고선 · `F0020000` 표고점 → **DEM** / `F0040000` **옹벽**(높이 속성) · `F0030000` **절토·성토면** → 미사용(TODO) |
| 교통 A | `A0010000` 도로경계 · `A0020000` 중심선(차로수·도로폭) · `A0033320` 보도 |
| 수계 E | `E0010001` 하천경계 · `E0052114` 호소 등 |
| 시설 C | `C0050000` 제방(높이 0~12m) 등 → 미사용(보험용) |

**왜 도엽별이 아니라 [도 영역]인가** (2026-08-27 전환)

| | 도엽별 수치지도 Ver2.0 | **연속수치지형도 [도 영역]** |
| --- | --- | --- |
| 배포 단위 | 도엽(약 6.5 km²) | **시도 1개 = 1세트** |
| 경기도 기준 | 약 1,570개 | **1세트 (1.8 GB)** |
| 품질 | 기준 | **동급** (상관 0.999, RMS 1.5m 실측) |

**연속본의 3가지 차이 — 전부 대응 완료**
- 좌표계가 **EPSG:5179**(도엽별은 5186/5187) → `read_contours`가 자동 재투영
- 필드명이 영문 **`CONT`(등고선) / `NUME`(표고점)** → `_ELEV_FIELDS`에 명시
- 등고선의 약 17%가 **MultiLineString** → `_geom_coords`가 파트별로 편다

**보관 위치**: `D:\APPS\SHP\ctnu_도영역\<시도>\` 에 **zip 그대로**(압축 해제는 스크립트가 한다).
등고선은 `.z01~.z10 + .zip` 분할압축이라 **조각을 전부 같은 폴더에** 둬야 한다(Bandizip으로 해제).
폴더 규칙 요약은 `D:\APPS\SHP\_받는_방법.txt`.

⚠️ **zip 내부가 평면이라 반드시 개별 폴더로 해제**한다. 한 폴더에 몽땅 풀면 레이어 파일명이
전부 같아서(`N3L_F0010000.shp` 등) 서로 덮어쓴다.
⚠️ **제품을 섞지 말 것**. 도엽별·연속·시군구본이 한 트리에 섞이면 같은 등고선이 이중으로 들어간다.

## 2. DEM 굽기

```powershell
python -m src.terrain.contour_bake "<SHP폴더>" `
    --cell 5 --tile-km 10 --margin-m 300 --stream `
    --out geo_store/dem_<지역>.tif --region "<지역명>" `
    --method clough --guard 3
```

- **`--stream`은 도 영역 소스에 필수**. 타일마다 그 영역만 골라 읽는다(`read_contours(bbox=)`).
  전량 적재면 경기도가 정점 1억 개 = **12GB**를 먹는데, 스트리밍은 타일당 4초·메모리 고정이다.
  타일이 쓰는 점 집합은 전량 적재와 **동일**하므로 산출 DEM도 동일하다
  (`test_bake_tiled_stream_matches_full_load`가 픽셀 단위로 못박음).

- 산출: `dem_<지역>_r{r}c{c}.tif` 여러 장 + `manifest.json` 자동 갱신
- **시/구 폴더만 가리키면 된다** — `rglob("*.shp")` 재귀 + 도엽 중복제거 자동
- 자동 처리: 좌표대 재투영(5187 동부 → 5186 고정) · 도엽 중복제거 · 거리제한 채움(`fill_dist_m=200`)
- `--tile-km 10`은 대용량 지역 필수. 생략하면 단일 거대 tif가 나온다
- **이음매 걱정 불필요**: 타일마다 경계 밖 `--margin-m`(300m)까지 등고선을 함께 넣어 보간하고
  타일 원점이 공통 격자에 정렬된다. 실측(서울 인접 타일 겹침 열) **불일치 RMS 4mm·93.5%가 1mm 이내**

**보간법 선택**

| `--method` | 성격 |
| --- | --- |
| `clough`(기본) | guarded CloughTocher. 계단현상 부분 개선, 스파이크는 `--guard 3`(m) 튜브 클램프로 억제 |
| `solver` | 라플라스 조화 격자 솔버. **계단 완전 제거**(힐셰이드 검증), 오버슈트 구조적 불가. ~10× 느림 |
| `linear` | 평면 삼각보간. 계단 발생, 비교용 |

기본은 `clough`를 쓴다. `solver`는 opt-in이고, 전 타일 재베이크는 비용이 커서 아직 채택 안 함.

⚠️ `scripts/dem_staircase.py`의 quant/flat 지표는 **솔버 판단에 오도**한다(조밀 제약·완경사를
페널티로 계산). 솔버 품질은 반드시 힐셰이드로 눈으로 볼 것.

---

## 3. 도로 굽기

```powershell
python -m src.terrain.road_bake "<SHP폴더>" `
    --out geo_store/roads_<지역>.geojson --region "<지역명>" `
    --tile-km 2
```

⚠️ **광역시급은 `--tile-km 2` 필수.** 지역 1파일로 구우면 서울 기준 311MB가 나오고,
런타임이 요청마다 전량 파싱해 **요청당 3분+·2GB**를 먹는다. 2km 하드클립 타일링 시
서울 247타일 / 강남 250m 조회 0.46초(약 400배 개선).

- 자동 포함: 갭 채움(경계 폴리곤 없는 소로·골목을 실측 `도로폭`으로 버퍼링 → `{"syn":1}`).
  끄려면 `--no-fill-gaps`
- 중심선 props에 `차로수`·`도로폭`이 실려 런타임이 다차선 마킹을 생성한다

---

## 4. 수계 굽기

```powershell
python -m src.terrain.water_bake "<SHP폴더>" `
    --out geo_store/water_<지역>.geojson --region "<지역명>"
```

수계는 소량이라(서울 5.79MB) **타일링 불필요** — 단일 파일로 OK.
수면 표고가 DEM에서 나오므로 **DEM을 먼저 구워야** 얹힌다.

---

## 5. COG 변환 + GCS 업로드

```powershell
# DEM → COG 변환 (--bucket 주면 업로드 명령까지 출력해준다)
python scripts/dem_to_cog.py geo_store --out cog_out --bucket arch-site-model-dem --prefix dem
gcloud storage cp cog_out/*.tif gs://arch-site-model-dem/dem/

# 도로·수계 GeoJSON (COG 변환 없이 그대로)
gcloud storage cp geo_store/roads_<지역>*.geojson gs://<버킷>/roads/
gcloud storage cp geo_store/water_<지역>.geojson  gs://<버킷>/water/
```

공개 버킷이라 `/vsicurl` 익명 읽기가 되고 인증·서비스계정이 필요 없다.
서빙 설정(`DEM_TILE_BASE`/`ROAD_BASE`/`WATER_BASE`)은 이미 Cloud Run에 들어가 있어 손댈 일이 없다.

---

## 6. manifest 커밋 → 자동 배포

```powershell
git add geo_store/manifest.json geo_store/road_manifest.json geo_store/water_manifest.json
git commit -m "지형 비축 추가: <지역명>"
git push
```

**타일은 GCS, manifest만 git.** `geo_store/*.tif`·`*.geojson`은 gitignore다.
main push 시 GitHub Actions가 pytest 통과 후 Cloud Run에 자동 배포한다. **엔진 코드는 무수정.**

---

## 현재 비축 현황 (2026-08-25)

| 종류 | 커버리지 |
| --- | --- |
| DEM (`manifest.json`) | 120타일 — 대전 14 · 서울 15 · 부산 24 · 대구 36 · 울산 20 · 세종 11 |
| 도로 (`road_manifest.json`) | 248 — 서울 247타일(2km) + 대전 서구 1 |
| 수계 (`water_manifest.json`) | 2 — 서울 + 대전 서구 |

**남은 일**: 도로·수계는 부산·대구·울산·세종이 DEM만 있고 미비축(같은 레시피 반복).
신규 지역은 경기권 대기 중. 인천·광주는 자치구 개편 확정 후.

건물·지적은 VWorld 실시간이라 **이미 전국**이다. 비축이 필요한 건 지형·도로·수계뿐이다.

---

## 착수 전 확인 사항

- [ ] 대상 시도 결정 (실제 프로젝트가 있는 곳 우선이 합리적)
- [ ] **연속수치지형도 [도 영역]** 확보 → `D:\APPS\SHP\ctnu_도영역\<시도>\`에 zip 그대로
- [ ] 대상지형지물 **지형·교통·수계·시설 4개**만 체크했는지
- [ ] 분할압축 조각(`.z01~`)이 전부 같은 폴더에 있는지 (`.irx`가 남아 있으면 전송 미완료 → 재다운로드)
- [ ] `pip install -r requirements-dev.txt` (geopandas·scipy — 베이크 전용, 런타임엔 불필요)
- [ ] DEM은 `--stream`, 광역시급 도로는 `--tile-km 2` 잊지 말 것

---

## 관련 문서

- `docs/ngii_data_inquiry_plan.md` — NGII 안심구역 문의·신청 계획(이 절차를 대체하지는 않음)
- `docs/deploy.md` §5 — 클라우드 도로/수계 서빙 설정
- `docs/road_surface_plan.md` — 도로 노면 설계·한계
- 메모리: [[ngii-continuous-map-route]] · [[nationwide-dem-ngii-source]] · [[desktop-shp-source]] · [[road-tiling-metro-serving]]
