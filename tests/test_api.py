"""FastAPI 백엔드 테스트 — 엔진은 mock(네트워크 없음). API 레이어만 검증.

pipeline.generate 를 몽키패치해 파일 생성/URL 구성/다운로드/경로탈출 방어를 확인한다.
"""

from pathlib import Path

from fastapi.testclient import TestClient

import src.api as api


def _client():
    return TestClient(api.app)


def _fake_generate_factory():
    """가짜 generate: 잡 폴더에 .3dm + 정사영상 PNG 생성 후 결과 dict 반환."""
    def fake_generate(address, **kw):
        odir = Path(kw["output_dir"])
        odir.mkdir(parents=True, exist_ok=True)
        (odir / "site.3dm").write_bytes(b"3dm-bytes")
        (odir / "site_ortho.png").write_bytes(b"png-bytes")
        return {
            "ok": True,
            "outputs": {
                "3dm": {
                    "path": str(odir / "site.3dm"),
                    "solids": 3,
                    "terrain_triangles": 100,
                    "orthophoto": {
                        "image_path": str(odir / "site_ortho.png"),
                        "missing_tiles": 0,
                        "zoom": 18,
                    },
                }
            },
            "stats": {"buildings": 3, "origin_offset": [1.0, 2.0]},
            "provenance": {"orthophoto_src": "VWorld Satellite"},
            "warnings": [],
        }
    return fake_generate


def test_docs_available():
    # 프론트 빌드 유무와 무관하게 API 문서는 항상 제공(앱 기동 확인)
    assert _client().get("/docs").status_code == 200


def test_health():
    r = _client().get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "ortho_source" in body


def test_generate_requires_address():
    r = _client().post("/api/generate", json={})
    assert r.status_code == 422  # pydantic 검증 실패


def test_generate_bad_radius():
    r = _client().post("/api/generate", json={"address": "X", "radius_m": 99999})
    assert r.status_code == 422  # radius 상한 초과


def test_generate_and_download(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_generate", _fake_generate_factory())
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path.resolve())

    r = _client().post("/api/generate", json={"address": "대전 서구", "outputs": ["3dm"]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "3dm" in body["files"]
    assert "ortho_png" in body["files"]
    assert body["provenance"]["orthophoto_src"] == "VWorld Satellite"

    # .3dm 다운로드
    r3dm = _client().get(body["files"]["3dm"])
    assert r3dm.status_code == 200
    assert r3dm.content == b"3dm-bytes"

    # 정사영상 PNG 다운로드
    rpng = _client().get(body["files"]["ortho_png"])
    assert rpng.status_code == 200
    assert rpng.content == b"png-bytes"


class _FakeBlob:
    def __init__(self, store, name):
        self._store, self.name = store, name

    def upload_from_filename(self, path):
        self._store[self.name] = Path(path).read_bytes()

    def download_to_filename(self, path):
        Path(path).write_bytes(self._store[self.name])


class _FakeBucket:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    def blob(self, name):
        return _FakeBlob(self.store, name)

    def list_blobs(self, prefix):
        return [_FakeBlob(self.store, n) for n in self.store if n.startswith(prefix)]


def test_download_from_other_instance_via_bucket(monkeypatch, tmp_path):
    # Cloud Run: 생성(인스턴스 A)과 다운로드(인스턴스 B)가 갈려도 공유 버킷으로 받아진다
    bucket = _FakeBucket()
    monkeypatch.setattr(api, "_generate", _fake_generate_factory())
    monkeypatch.setattr(api, "JOBS_GCS_BUCKET", "fake-bucket")
    monkeypatch.setattr(api, "_gcs_bucket", lambda: bucket)

    monkeypatch.setattr(api, "JOBS_DIR", (tmp_path / "instA").resolve())
    body = _client().post("/api/generate", json={"address": "대전 서구"}).json()
    assert any(n.endswith("site.3dm") for n in bucket.store)

    (tmp_path / "instB").mkdir()
    monkeypatch.setattr(api, "JOBS_DIR", (tmp_path / "instB").resolve())
    r3dm = _client().get(body["files"]["3dm"])
    assert r3dm.status_code == 200
    assert r3dm.content == b"3dm-bytes"
    assert _client().get(body["files"]["ortho_png"]).content == b"png-bytes"
    # 버킷에도 없는 잡은 여전히 404
    assert _client().get("/api/files/nojobxyz/3dm").status_code == 404


def test_generate_failure_maps_to_400(monkeypatch, tmp_path):
    def failing(address, **kw):
        return {"ok": False, "error": "반경 내 건물이 없습니다."}

    monkeypatch.setattr(api, "_generate", failing)
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path.resolve())
    r = _client().post("/api/generate", json={"address": "바다 한가운데"})
    assert r.status_code == 400
    assert "건물" in r.json()["detail"]


def test_file_download_bad_kind_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path.resolve())
    # 허용 종류키(3dm/ortho)가 아니면 400 (임의 파일명 접근 차단)
    r = _client().get("/api/files/abc/secret.txt")
    assert r.status_code == 400
    # 유효 요청이지만 없는 잡 → 404
    r3 = _client().get("/api/files/nojobxyz/3dm")
    assert r3.status_code == 404


def test_safe_component_rejects_traversal():
    from src.api import _safe_component

    assert _safe_component("abc123DEF") is True
    assert _safe_component("가나다") is True
    assert not _safe_component("..")
    assert not _safe_component(".")
    assert not _safe_component("a/b")       # 슬래시 불가
    assert not _safe_component("../etc")
    assert not _safe_component("")


def test_generate_tile_rejects_oversized_bbox():
    """미인증 자원증폭 방지: bbox span이 상한 초과면 generate_tile 호출 전 400."""
    huge = 10_000.0  # 한 변 10km > _MAX_TILE_SPAN_M(3000)
    r = _client().post("/api/generate_tile", json={
        "bbox_4326": [127.0, 37.0, 127.1, 37.1],
        "bbox_5186": [200000.0, 400000.0, 200000.0 + huge, 400000.0 + huge],
        "origin_offset": [200000.0, 400000.0],
    })
    assert r.status_code == 400
    assert "span" in r.json()["detail"]


def test_generate_tile_rejects_degenerate_bbox():
    """span ≤ 0(퇴화 bbox)도 400."""
    r = _client().post("/api/generate_tile", json={
        "bbox_4326": [127.0, 37.0, 127.1, 37.1],
        "bbox_5186": [200000.0, 400000.0, 200000.0, 400500.0],  # sx=0
        "origin_offset": [200000.0, 400000.0],
    })
    assert r.status_code == 400


def test_generate_area_validation():
    # 주소도 영역도 없으면 422
    assert _client().post("/api/generate", json={"address": ""}).status_code == 422
    # 한국 밖 / 순서 뒤집힘 / 너무 큼(>4km) → 422
    assert _client().post("/api/generate", json={"bbox_4326": [0, 0, 1, 1]}).status_code == 422
    assert _client().post("/api/generate", json={"bbox_4326": [127.1, 36.1, 127.0, 36.0]}).status_code == 422
    assert _client().post("/api/generate", json={"bbox_4326": [127.0, 36.0, 127.1, 36.1]}).status_code == 422


def test_generate_area_passes_bbox_and_serves_package(monkeypatch, tmp_path):
    seen = {}

    def fake(address, **kw):
        seen.update(kw)
        odir = Path(kw["output_dir"])
        odir.mkdir(parents=True, exist_ok=True)
        (odir / "s_package.zip").write_bytes(b"zip-bytes")
        return {"ok": True, "outputs": {"dae": {"zip": str(odir / "s_package.zip")}}, "stats": {}, "warnings": []}

    monkeypatch.setattr(api, "_generate", fake)
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path.resolve())
    body = _client().post(
        "/api/generate", json={"bbox_4326": [127.368, 36.338, 127.374, 36.342], "outputs": ["dae"]}
    ).json()
    assert seen["bbox_4326"] == (127.368, 36.338, 127.374, 36.342)
    assert _client().get(body["files"]["package"]).content == b"zip-bytes"


def test_basemap_rejects_bad_tiles():
    assert _client().get("/api/basemap/evil/10/1/1").status_code == 400
    assert _client().get("/api/basemap/base/3/1/1").status_code == 400      # 줌 범위 밖
    assert _client().get("/api/basemap/base/10/5000/1").status_code == 400  # 타일 범위 밖


def test_extension_rbz_injects_site_url():
    import io
    import zipfile

    r = _client().get("/api/extension.rbz", headers={"host": "asm.example.run.app", "x-forwarded-proto": "https"})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "arch_site_model.rb" in names and "arch_site_model/import_softener.rb" in names
    settings = z.read("arch_site_model/settings.rb").decode("utf-8")
    assert 'DEFAULT_BACKEND = "https://asm.example.run.app".freeze' in settings


def test_generate_response_includes_coord(monkeypatch, tmp_path):
    # SketchUp 확장이 모델 위치(그림자)로 쓰는 대지 중심 위경도
    def fake(address, **kw):
        return {"ok": True, "coord": {"lon": 127.1, "lat": 37.4}, "outputs": {}, "stats": {}, "warnings": []}

    monkeypatch.setattr(api, "_generate", fake)
    monkeypatch.setattr(api, "JOBS_DIR", tmp_path.resolve())
    body = _client().post("/api/generate", json={"address": "x"}).json()
    assert body["coord"] == {"lon": 127.1, "lat": 37.4}


def test_frontend_html_not_cached_assets_immutable():
    # 배포 뒤 예전 첫 화면이 보이지 않게 HTML은 no-cache, 해시 붙은 assets는 장기 캐시
    import pytest
    from starlette.testclient import TestClient as _TC

    if not api._FRONTEND_DIST.is_dir():
        pytest.skip("frontend/dist 없음(CI는 프론트 빌드 안 함)")
    c = _TC(api.app)
    assert c.get("/").headers.get("cache-control") == "no-cache"
    assert c.get("/guide.html").headers.get("cache-control") == "no-cache"
    js = next((api._FRONTEND_DIST / "assets").glob("*.js")).name
    assert "immutable" in c.get(f"/assets/{js}").headers.get("cache-control", "")
