# 정사영상 텍스처 — 조사 종합 및 구현 계획

> 2026-07-01 심층 조사 결과 종합. 로드맵 확장6(정사영상 텍스처)의 구현 설계 문서.
> 표기: `[검증]` = 코드/문서로 실증 · `[유력]` = 정황상 강함, 미확정 · `[미확인]` = 확인 필요.

---

**구현 상태 (2026-07-08 갱신)**: Tier 1(`.3dm`)·Tier 2a(SketchUp) **모두 구현·테스트 완료**. 실제로는
Tier 2a의 "컴패니언 `.rb` 수동 load"를 넘어 **확장 버튼 1회로 다운로드+드레이프까지 자동화**했다(단일
`/api/generate` PNG 경로 + 대반경 `/api/generate_tile` base64 타일 경로 둘 다). 코드: `src/geo/ortho.py`,
`pipeline.generate`, `tiles_stream._ortho_b64`, 확장 `builder.rb::drape_ortho`. 테스트: pytest 51 green
(`test_ortho`/`test_pipeline`/`test_tiles_stream`). 남은 것은 **데스크톱 SketchUp 실기 렌더 검증뿐**(헤드리스
없어 무인 확인 불가). 아래 §5는 착수 시점의 설계 기록으로, 실제 구현은 자동화 경로를 택했다. 사용·검증
절차는 `docs/sketchup_extension.md` §7.

---

## 0. 한 줄 결론

지형 TIN에 정사영상을 자동으로 입히는 것은 **`.3dm`(Rhino) 출력으로는 완전 자동화가 지금 가능**하고
(빌딩 블록 로컬 검증 완료), **`.skp`(SketchUp)로는 "클릭 1회" 반자동까지만** 가능하다(무인 자동화는
SketchUp 쪽 제약으로 불가).

---

## 1. 실행 환경별 텍스처 가능성 `[검증]`

| 환경 | 이미지 텍스처 | 왜 |
|---|---|---|
| 클라우드 MCP `build_model` | ❌ | 네임스페이스에 `Texture`/`Image`/`ImageRep`/`SUPoint2D`/`face_set_front_material(uv)` **있음**. 그러나 샌드박스가 import·파일시스템·네트워크를 전부 차단 → 외부 정사영상을 안으로 들일 방법이 없음 |
| 데스크톱 SketchUp 네이티브 Ruby | ✅ | `Sketchup::Http`(공식 다운로드 모듈), `materials.add`+`material.texture=`, `Face#position_material`(2/4/6/8점, 투영). KBS `Kbs_FastDrape`가 쓰던 계열 |
| `.3dm` (rhino3dm, 우리 파이썬) | ✅ | 아래 §4에서 로컬 실증 |

핵심: **막힌 건 "SketchUp에 텍스처 API가 없어서"가 아니라 "클라우드 샌드박스에 이미지를 못 넣어서".**
데스크톱 SketchUp이나 우리 파이썬 파이프라인에는 제약이 없다.

---

## 2. 정사영상 소스 결정 매트릭스 `[핵심 분기]`

| 소스 | 자동화 | 라이선스 | 좌표계 | 품질 | 비고 |
|---|---|---|---|---|---|
| **NGII 영상지도 WMTS** (data.go.kr 15059358) | ✅ WMTS 타일 | ✅ **공공누리 1유형**(출처표시 시 상업·변형 허용) `[유력]` | 미명시, 5179/3857 추정 `[미확인]` | 상 | 우리 DEM과 동일 기관 → 정합성 유리. "제3자 권리 포함" 레이어 주의 |
| VWorld WMTS Satellite | ✅ 타일(3857) | ⚠️ 상업 이용 제한 정황 `[미확인]` | EPSG:3857 `[검증]` | 상 | 공식 약관 페이지 방화벽으로 원문 미확보 → 재확인 필수 |
| NGII 국토정보플랫폼 정사영상 원본 | ❌ 대용량 전송 프로그램 수동 | ✅ 공공누리 1유형 | 국내 좌표계, TIFF | 최상 | 자동화 불가. 최고 품질이 필요한 단발 케이스용 |
| VWorld 옛 3D API (bil+obj+dds) | — | — | — | — | **2019 폐쇄. 사용 불가** `[검증]` |

**1순위 추천: NGII 영상지도 WMTS.** 자동화 가능 + 상업 이용 가능(공공누리 1유형) + DEM과 동일 출처.
착수 전 `GetCapabilities`로 (a) tileMatrixSet 좌표계, (b) site 스케일(반경 250m) 해상도(줌 레벨),
(c) "제3자 권리" 해당 여부만 확정하면 됨. 이 세 가지가 유일한 미확인 게이트.

---

## 3. 좌표·UV 설계 `[검증]` (rhino3dm 프로토타입으로 실증)

정사영상은 "위에서 수직으로 내려본 이미지" → **평면 투영(planar mapping)**이 정확한 모델.

- `rhino3dm.TextureMapping.CreatePlaneMapping(plane=WorldXY, dx=Interval(x0,x1), dy=Interval(y0,y1), dz=Interval(0,1))`
- `mesh.SetTextureCoordinates(tm, Transform.Identity, False)` → 정점별 UV 자동 생성
- 로컬 실증 결과(월드→UV): (x0,y0)→(0,0), (x1,y1)→(1,1), 중점→(0.5,0.5). **표고 Z는 UV에 무영향**
  (정사영상 특성과 정확히 일치).
- x0,y0,x1,y1 = 정사영상 모자이크의 EPSG:5186 실제 범위. 우리 `origin_offset` 로컬 좌표계와 동일 기준
  이면 그대로, 아니면 offset만 보정.

**중요 단순화**: 이미지를 5186으로 재투영(resample)하지 않아도 됨. 타일을 그 CRS 그대로 두고, TIN
정점을 그 CRS로 변환해 UV만 계산하면 리샘플 아티팩트 없이 더 선명하다. (재투영 vs 노-리샘플 중
구현 시 선택.)

---

## 4. Tier 1 — 완전 자동, `.3dm` 출력 `[데이터레벨 검증]`

파이프라인에 필요한 라이브러리 전부 설치 확인: `requests`, `rasterio 1.4.4`, `pyproj 3.7.2`,
`numpy`, `rhino3dm 8.17.0`. (`PIL` 미설치 — 필요 시 rasterio로 대체.)

로컬 프로토타입으로 실증한 것:
- 평면 매핑 UV 생성 정확 (§3)
- `.3dm` 쓰기 → 재읽기: 텍스처 좌표(정점수 일치)·머티리얼 비트맵 참조 모두 보존
- `Material.SetBitmapTexture(path)`: 유효 이미지면 True, stub이면 False지만 참조는 기록됨

**남은 미검증**: Rhino에서 실제 드레이프 렌더 왕복(시각 확인). API·데이터는 모두 통과 —
표준 planar TextureMapping을 쓰므로 신뢰도 높음. 프로토타입 1회로 확정.

**구현 단계:**
1. `src/geo/ortho.py` (신규): bbox(4326/5186) → WMTS 타일 범위 계산 → 타일 다운로드 →
   모자이크 → (선택) EPSG:5186 정렬 → 단일 이미지 + 실제 지리 범위 반환.
2. `src/output/rhino.py::write_3dm`: `terrain` 있고 `ortho` 옵션 시, TerrainMesh에 planar
   mapping UV 부여 + `cadastral`/`buildings`와 별도 `terrain` 머티리얼에 `SetBitmapTexture`.
3. 파이프라인/서버: `layers={"terrain": true, "orthophoto": true}` 플래그 추가.
4. 출처표시(공공누리) 문자열을 provenance + (가능하면) 모델 텍스트로 기록.

---

## 5. Tier 2a — 반자동(클릭 1회), SketchUp `[빌딩블록 검증]`

"어려운 자동화는 파이썬이, 마지막 얹기만 SketchUp이."

1. 파이썬: §4-1과 동일하게 정사영상 1장 스티칭 + 로컬 저장 + 지리 범위 산출.
2. 파이썬: 컴패니언 `.rb` 스크립트 생성 — 이미지 경로·범위·`origin_offset` 임베드.
3. 사용자: 데스크톱 SketchUp에서 생성된 모델 열고 Ruby 콘솔 `load "companion.rb"` 1회.
4. `.rb` 동작: 로컬 이미지 로드 → `model.materials.add` + `material.texture=` →
   TIN 면들에 `Face#position_material(mat, pts, projection_vector)` 하향 투영.
   (SketchUp 수동 정석 "Projected texture" 기법의 스크립트판.)

빌딩블록 검증: `Sketchup::Http`·`materials.add`·`position_material` 모두 공식 API 존재.
SketchUp Ruby는 이미지 합성 라이브러리가 없으므로 **스티칭은 반드시 파이썬 쪽에서** 수행(위 1번).

대안 2b(`.dae` 임포트): 파이썬이 텍스처 입힌 COLLADA 생성 → File>Import. .dae는 SketchUp
내장 임포트가 텍스처 유지. 단 "텍스처 안 보임" 사례 있어 finicky + COLLADA 작성 비용 → 후순위.
(OBJ는 SketchUp 네이티브 임포트 없음 → 제외.)

---

## 6. Tier 3 — 완전 자동 + `.skp` 텍스처: 불가 `[검증]`

- 클라우드 MCP: 샌드박스 이미지 반입 차단.
- 데스크톱 SketchUp: 공식 헤드리스/CLI 없음 → 무인 구동 불가.
- 텍스처 지원 순수 파이썬 `.skp` 라이터 없음.
→ SketchUp MCP가 이미지 반입을 허용하거나 헤드리스가 생기기 전까지 대기.

---

## 7. 착수 전 닫아야 할 미확인 게이트

1. **소스 확정 + 라이선스**: NGII WMTS `GetCapabilities`로 좌표계·줌해상도·제3자권리 확인.
   상업적 사용 맥락이면 공공누리 1유형 출처표시 문구 확정. (VWorld로 갈 경우 약관 원문 재확인.)
2. **Rhino 렌더 왕복**: Tier 1 프로토타입 .3dm을 실제 Rhino/뷰어로 열어 드레이프 확인.
3. **origin_offset 정합**: 정사영상 지리 범위와 우리 로컬 좌표 기준 일치 확인(오프셋 보정 지점).

---

## 8. 권장 순서

1. 소스 결정(§2, 사용자 판단 필요 — 상업/비상업 + 품질/자동화 우선순위).
2. 결정된 소스로 `GetCapabilities` 확인(게이트 1).
3. Tier 1 프로토타입: 실제 site 1곳으로 타일→모자이크→텍스처 .3dm → Rhino 확인(게이트 2·3).
4. 검증되면 `src/geo/ortho.py` + `write_3dm` 확장으로 정식화.
5. `.skp`도 필요하면 Tier 2a 컴패니언 Ruby 추가.
