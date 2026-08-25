# SHP 베이크 실행 계획 — 지역 추가 절차서

> 수치지형도 SHP → DEM·도로·수계 → GCS 서빙까지, 지역 하나를 추가하는 전체 절차.
> 2026-08-25 작성. 검증된 레시피(대전·서울·부산·대구·울산·세종 6개 광역단체 적용 완료).

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
| 무료 공개 DEM 다운로드 | 90m — 반경 250m 사이트에 셀 3~5개, 사실상 평면이라 무용 |
| 전세계 공개 30m 타일(terrarium 등) 폴백 | 기술 검증은 됨(키 없이 fetch 실측 OK), **2026-08-25 사용자 결정으로 미도입** |
| NGII 안심구역 5m/1m DEM | 검토 중 — `docs/ngii_data_inquiry_plan.md` 참조. **1회성 반출 모델이라 SHP 대체 아님** |

---

## 1. SHP 확보 — 유일한 사람 일 ⚠️ 현재 블로커

**받을 것**: 대상 지역 1:5,000 수치지형도Ver2.0 (무료, 로그인 필요)
**받는 곳**: 국토정보플랫폼

**폴더 구조**: `<지역>/<구>/(B010)수치지도_<도엽>_2025_*/`
한 도엽 폴더 안에 **DEM·도로·수계 레이어가 전부** 들어있다 — 같은 폴더로 3종을 다 굽는다.

| 용도 | 레이어 파일 |
| --- | --- |
| DEM | `N3L_F0010000`(등고선) + `N3P_F0020000`(표고점) — **표고점 없으면 봉우리가 평면이 된다** |
| 도로 | `N3A_A0010000`(도로경계) + `N3L_A0020000`(중심선) + `N3A_A0033320`(보도) |
| 수계 | `N3A_E00*`(하천경계 `E0010001`·호소 `E0052114` 등) |

**⚠️ 2026-08-25 현재 상태**: 기존 재고 폴더(`C:\Users\20260102\Desktop\shp`, ~1,600도엽)가
바탕화면·OneDrive 어디에도 없다. 새 지역을 굽기 전에 **SHP를 다시 받거나 옮긴 위치를 확인**해야 한다.

**규모 감각**: 시도별 도엽 수 = 서울 269 · 부산 277 · 울산 251 · 세종 108 · 인천 309.

**주의**: 과거 재고에서 폴더명 `대전광역시`에 실제로는 대구 자치구(군위·달서·수성·달성)가
들어있던 사례가 있다. 폴더명을 믿지 말고 도엽 번호로 확인할 것.

---

## 2. DEM 굽기

```powershell
python -m src.terrain.contour_bake "<SHP폴더>" `
    --cell 5 --tile-km 10 --margin-m 300 `
    --out geo_store/dem_<지역>.tif --region "<지역명>" `
    --method clough --guard 3
```

- 산출: `dem_<지역>_r{r}c{c}.tif` 여러 장 + `manifest.json` 자동 갱신
- **시/구 폴더만 가리키면 된다** — `rglob("*.shp")` 재귀 + 도엽 중복제거 자동
- 자동 처리: 좌표대 재투영(5187 동부 → 5186 고정) · 도엽 중복제거 · 거리제한 채움(`fill_dist_m=200`)
- `--tile-km 10`은 대용량 지역 필수. 생략하면 단일 거대 tif가 나온다

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

- [ ] 대상 지역 결정 (실제 프로젝트가 있는 곳 우선이 합리적)
- [ ] SHP 폴더 확보 — **현재 재고 없음**, 국토정보플랫폼에서 재다운로드 필요
- [ ] `pip install -r requirements-dev.txt` (geopandas·scipy — 베이크 전용, 런타임엔 불필요)
- [ ] 광역시급이면 도로 `--tile-km 2` 잊지 말 것

---

## 관련 문서

- `docs/ngii_data_inquiry_plan.md` — NGII 안심구역 문의·신청 계획(이 절차를 대체하지는 않음)
- `docs/deploy.md` §5 — 클라우드 도로/수계 서빙 설정
- `docs/road_surface_plan.md` — 도로 노면 설계·한계
- 메모리: [[nationwide-dem-ngii-source]] · [[desktop-shp-source]] · [[road-tiling-metro-serving]]
