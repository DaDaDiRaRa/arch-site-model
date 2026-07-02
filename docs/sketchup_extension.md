# SketchUp 확장 (Phase B) — 설치·사용·개발

주소를 입력하면 백엔드에서 지형·건물을 받아 **데스크톱 SketchUp 안에서 직접 조립**하는 Ruby 확장.
F1(웹)·F2(브라우저 뷰어)와 **같은 백엔드 `/api/generate`의 geometry JSON을 재사용**한다.

- **B1(현재)**: 지형(TIN mesh) + 건물(돌출) 조립. 텍스처 없음(단색).
- **B2(예정)**: 정사영상 텍스처 드레이프(`Face#position_material`).
- 대상: **SketchUp 2021+** (HtmlDialog, `Sketchup::Http`, `Geom::PolygonMesh`).

---

## 1. 빌드 (.rbz 생성)

```bash
# 개발용 (기본 백엔드 = http://localhost:8000)
python sketchup_ext/build_rbz.py

# 배포용 (기본 백엔드 = Cloud Run URL 주입 → 팀원은 URL 입력 불필요)
python sketchup_ext/build_rbz.py --backend-url https://arch-site-model-xxxx.run.app
# → sketchup_ext/dist/arch_site_model.rbz
```

`--backend-url` 은 zip에 들어가는 `settings.rb` 의 `DEFAULT_BACKEND` 한 줄만 그 URL로
치환한다(디스크 소스는 localhost 유지). 배포 흐름: **① Cloud Run 배포로 URL 획득 → ②
`--backend-url <URL>` 로 .rbz 생성 → ③ 팀 배포**. 팀원은 설치만 하면 URL 입력 없이 바로 작동
(다이얼로그의 URL 필드는 override 용으로 남음). Cloud Run 서비스 URL은 재배포해도 안 바뀌므로
1회 주입으로 충분.

## 2. 설치 (사용자, 1회)

1. SketchUp > **확장 관리자(Extension Manager)** > **설치(Install Extension)**.
2. `arch_site_model.rbz` 선택 → 설치. (서명 안 됨 경고 시 허용)
3. 메뉴 **Extensions > 대지모델 생성…** 확인.

## 3. 백엔드 실행 (개발/로컬)

확장은 백엔드가 있어야 동작한다. **반드시 저장소 루트에서** 실행(상대경로 `geo_store` 때문):

```powershell
uvicorn src.api:app --port 8000
# 또는 GEO_STORE를 절대경로로: GEO_STORE=<repo>/geo_store uvicorn src.api:app --port 8000
```

배포 백엔드(Cloud Run)를 쓰면 확장 다이얼로그의 **백엔드 설정 > 백엔드 URL**에 그 URL을 넣는다
(설정은 저장됨).

## 4. 사용

1. **Extensions > 대지모델 생성…** 실행.
2. 주소·반경 입력, 지형 체크, (필요 시) 백엔드 URL 확인.
3. **모델 생성** → 백엔드 조회 + 조립(최대 ~30초) → 지형·건물이 현재 모델에 생성됨.
4. 결과는 태그로 분리: `terrain` / `buildings` / `buildings_unverified`(층수 미확인). 되돌리기 1회로 제거.

---

## 5. 동작 방식

```text
SketchUp 확장  ──HTTP POST /api/generate──▶  백엔드(FastAPI)
   (Sketchup::Http)   {address, radius_m, layers, outputs:["skp"]}
        ◀── geometry(로컬 미터: 건물 footprint/base_z/height/flagged + 지형 verts/tris) + warnings
   Builder: 미터→인치(×39.3701)
     - 지형: Geom::PolygonMesh → add_faces_from_mesh (소프트 엣지)
     - 건물: footprint 면 + pushpull(위로) + 홀 처리 + 착색
     - 태그/그룹 분리 + zoom_extents
```

파일:

- `arch_site_model.rb` — 로더(SketchupExtension 등록)
- `arch_site_model/main.rb` — 메뉴·HtmlDialog·오케스트레이션
- `arch_site_model/api_client.rb` — 백엔드 HTTP(Sketchup::Http, 비동기)
- `arch_site_model/builder.rb` — geometry → 엔티티(지형/건물)
- `arch_site_model/settings.rb` — 백엔드 URL 영구 저장
- `arch_site_model/dialog.html` — 입력 UI

---

## 6. 자동화 경계 / 한계

- **자동(설치 후 매번)**: 주소 입력 → 버튼 1회 → 지형·건물 조립까지 완전 자동.
- **수동/1회성**: `.rbz` 설치 + 백엔드 URL 설정, SketchUp 실행(헤드리스 없음).
- **한계**:
  - 헤드리스 SketchUp이 없어 개발자가 무인 검증 불가 → 설치·실행은 사용자 테스트 루프.
  - 지형 삼각형이 많으면(큰 반경) SketchUp이 느려질 수 있음.
  - 배포 백엔드에 인증(토큰/IAP)을 붙이면 확장도 헤더 인증을 추가해야 함(B1 미구현).
  - 텍스처는 B2에서. 현재는 단색.

---

## 7. 다음 (B2 — 텍스처)

`layers.orthophoto=true` + `outputs=["3dm"]`로 호출하면 백엔드가 정사영상 PNG를 만들고
`files.ortho_png` URL + `geometry.ortho_extent_m`를 반환한다. 확장이 PNG를 다운로드해
`model.materials.add` + `material.texture=` 후 지형 면에 `Face#position_material`로 위→아래
평면 투영. 상세 설계: `docs/orthophoto_texture_plan.md` §5(Tier 2a).
