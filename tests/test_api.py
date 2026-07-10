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
