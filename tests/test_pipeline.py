"""generate 파이프라인 — client 주입 + geocode 몽키패치 (오프라인)."""

import src.pipeline as pl
from src.geo.vworld import DATASET_CADASTRAL
from src.pipeline import generate
from tests.conftest import load_fixture


class FakeClient:
    def __init__(self, features):
        self._features = features

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        return self._features

    def count(self, dataset, bbox):
        return len(self._features)


class FakeClientMulti:
    """건물/지적 데이터셋별로 다른 피처를 반환하는 가짜 클라이언트."""

    def __init__(self, building_features, cadastral_features):
        self._building = building_features
        self._cadastral = cadastral_features

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        if dataset == DATASET_CADASTRAL:
            return self._cadastral
        return self._building

    def count(self, dataset, bbox):
        if dataset == DATASET_CADASTRAL:
            return len(self._cadastral)
        return len(self._building)


def _daejeon_features():
    return load_fixture("buildings_daejeon.json")["response"]["result"][
        "featureCollection"
    ]["features"]


def _cadastral_features():
    return load_fixture("cadastral_daejeon.json")["response"]["result"][
        "featureCollection"
    ]["features"]


# ---------------------------------------------------------------------------
# 기존 테스트
# ---------------------------------------------------------------------------

def test_generate_buildings(monkeypatch):
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        radius_m=250,
        floor_h_m=3.0,
        client=FakeClient(_daejeon_features()),
    )
    assert out["ok"] is True
    # 픽스처: 4개 건물(사각형), 층수 4/10/0/null → with_floors 2, 누락 2.
    assert out["stats"]["buildings"] == 4
    assert out["stats"]["solids"] == 4
    assert out["stats"]["with_floors"] == 2
    assert len(out["stats"]["origin_offset"]) == 2
    assert out["stats"]["origin_offset"][0] > 0   # 5186 원점(복원용) 저장됨
    assert isinstance(out["outputs"]["skp"]["code"], str)
    assert "def extrude_solid" in out["outputs"]["skp"]["code"]
    assert any("gro_flo_co" in w for w in out["warnings"])
    assert out["provenance"]["building_src"] == "VWorld LT_C_SPBD"


def test_generate_no_buildings(monkeypatch):
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.0, "lat": 36.0, "crs": "EPSG:4326"}
    )
    out = generate("빈 곳", client=FakeClient([]))
    assert out["ok"] is False
    assert "건물" in out["error"]


def test_generate_geocode_fail(monkeypatch):
    from src.geo.geocode import GeocodeError

    def boom(a):
        raise GeocodeError("주소 변환 실패")

    monkeypatch.setattr(pl, "geocode", boom)
    out = generate("이상한 주소")
    assert out["ok"] is False
    assert "error" in out


# ---------------------------------------------------------------------------
# Phase 5: provenance 완성
# ---------------------------------------------------------------------------

def test_provenance_fields(monkeypatch):
    """provenance에 radius_m, missing_floors_policy, fetched_at 포함."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        radius_m=150,
        missing_floors_policy="flag",
        client=FakeClient(_daejeon_features()),
    )
    assert out["provenance"]["radius_m"] == 150
    assert out["provenance"]["missing_floors_policy"] == "flag"
    assert "fetched_at" in out["provenance"]
    assert "T" in out["provenance"]["fetched_at"]   # ISO 8601


def test_provenance_cadastral_src(monkeypatch):
    """지적 레이어 활성화 시 provenance에 cadastral_src 포함."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        layers={"buildings": True, "cadastral": True},
        client=FakeClientMulti(_daejeon_features(), _cadastral_features()),
    )
    assert "cadastral_src" in out["provenance"]
    assert "LP_PA_CBND_BUBUN" in out["provenance"]["cadastral_src"]


def test_provenance_setback_stub(monkeypatch):
    """setback=True 시 provenance에 stub 표기 + warnings 포함."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        setback=True,
        client=FakeClient(_daejeon_features()),
    )
    assert out["provenance"].get("setback_analysis") == "stub"
    assert any("setback" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# Phase 5: missing_floors_policy — 3개 정책 동작 검증
# ---------------------------------------------------------------------------

def test_missing_policy_default(monkeypatch):
    """policy=default: 누락 건물 포함(층수 1 fallback), flagged=False."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        missing_floors_policy="default",
        client=FakeClient(_daejeon_features()),
    )
    # 픽스처: 4건물, with_floors=2, missing=2 → 4개 모두 생성
    assert out["stats"]["solids"] == 4
    assert out["stats"]["flagged"] == 0
    assert any("기본 1층" in w for w in out["warnings"])


def test_missing_policy_skip(monkeypatch):
    """policy=skip: 층수 누락 건물 제외 → solids 감소."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        missing_floors_policy="skip",
        client=FakeClient(_daejeon_features()),
    )
    # 픽스처: 층수 유효 2개(4층/10층), 누락 2개(0/null) → skip 후 2개
    assert out["stats"]["solids"] == 2
    assert out["stats"]["flagged"] == 0
    assert any("제외됨" in w for w in out["warnings"])


def test_missing_policy_flag(monkeypatch):
    """policy=flag: 누락 건물 포함하되 flagged=True + skp 접미사 포함."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        missing_floors_policy="flag",
        client=FakeClient(_daejeon_features()),
    )
    assert out["stats"]["solids"] == 4
    assert out["stats"]["flagged"] == 2   # 누락 2개
    assert "[층수미확인]" in out["outputs"]["skp"]["code"]
    assert any("buildings_unverified" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# Phase 5: 지적 레이어
# ---------------------------------------------------------------------------

def test_cadastral_layer(monkeypatch):
    """layers.cadastral=True → stats.cadastral_parcels > 0."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        layers={"buildings": True, "cadastral": True},
        client=FakeClientMulti(_daejeon_features(), _cadastral_features()),
    )
    assert out["ok"] is True
    assert out["stats"]["cadastral_parcels"] == 2
    assert out["outputs"]["skp"]["cadastral_parcels"] == 2
    assert "CADASTRAL" in out["outputs"]["skp"]["code"]


def test_cadastral_skp_code_has_pnu(monkeypatch):
    """지적 활성화 시 skp 코드에 pnu 값이 포함되어야 한다."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        layers={"buildings": True, "cadastral": True},
        client=FakeClientMulti(_daejeon_features(), _cadastral_features()),
    )
    code = out["outputs"]["skp"]["code"]
    assert "3017010800103580000" in code


def test_cadastral_disabled_by_default(monkeypatch):
    """layers.cadastral 미지정 시 CADASTRAL 블록 생성되지 않음."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        client=FakeClient(_daejeon_features()),
    )
    assert "CADASTRAL" not in out["outputs"]["skp"]["code"]
    assert out["stats"]["cadastral_parcels"] == 0


def test_skp_output_cadastral_parcels_key(monkeypatch):
    """outputs.skp에 cadastral_parcels 키가 항상 존재한다."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate(
        "대전광역시 서구 괴정동 358",
        client=FakeClient(_daejeon_features()),
    )
    assert "cadastral_parcels" in out["outputs"]["skp"]


# ---------------------------------------------------------------------------
# 정사영상 텍스처 (Tier 1) — mock 타일 페처
# ---------------------------------------------------------------------------

def _solid_png8(rgb=(20, 180, 90)) -> bytes:
    """8x8 단색 PNG(합성 타일)."""
    import struct
    import zlib

    r, g, b = rgb
    raw = bytearray()
    for _ in range(8):
        raw.append(0)
        raw += bytes((r, g, b)) * 8

    def _chunk(typ, data):
        return (
            struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def _patch_small_ortho(monkeypatch):
    """소형 타일 소스 + 낮은 zoom으로 테스트 가속."""
    from src.geo.ortho import TileSource

    small = TileSource(name="t", url_template="http://t/{z}/{x}/{y}.png", tile_size=8)
    monkeypatch.setattr(pl, "_resolve_ortho_source", lambda: (small, "KEY", "TestOrtho"))
    monkeypatch.setattr(pl.config, "ORTHO_ZOOM", 16)


def _patch_synth_dem(monkeypatch):
    """실 DEM 타일 없이 합성 지형을 주입한다.

    geo_store/*.tif 는 gitignore(GCS가 서빙)라 CI 체크아웃엔 없다 → 실 타일에
    의존하면 지형이 조용히 생략돼 테스트가 깨진다(테스트 규칙: 합성 데이터로 동작).
    파이프라인이 지형 경로를 함수-로컬 import(`from src.terrain.store import
    find_tiles`, `from src.terrain.dem import clip_dem_mosaic`)로 부르므로
    소스 모듈 속성을 패치하면 호출 시점에 반영된다.
    """
    import numpy as np
    from rasterio.transform import from_bounds as _tf_from_bounds

    import src.terrain.dem as dem_mod
    import src.terrain.store as store_mod
    from src.terrain.dem import DEMPatch

    monkeypatch.setattr(
        store_mod,
        "find_tiles",
        lambda bbox, manifest=None: [
            {"file": "synthetic_dem.tif", "region": "합성", "cell_m": 5.0}
        ],
    )

    def _fake_mosaic(tile_paths, bbox_5186, offset):
        minx, miny, maxx, maxy = bbox_5186
        rows = cols = 24
        tf = _tf_from_bounds(minx, miny, maxx, maxy, cols, rows)
        # 완만한 남서→북동 경사(50~60m) — z_range·TIN·seating 모두 유효값
        cx = np.linspace(0.0, 1.0, cols, dtype=np.float32)[None, :]
        ry = np.linspace(0.0, 1.0, rows, dtype=np.float32)[:, None]
        grid = (50.0 + 5.0 * (cx + ry)).astype(np.float32)
        return DEMPatch(grid=grid, transform=tf, offset=offset)

    monkeypatch.setattr(dem_mod, "clip_dem_mosaic", _fake_mosaic)


def test_generate_orthophoto_textures_terrain(monkeypatch, tmp_path):
    """orthophoto=True → .3dm 지형 메시에 텍스처 좌표 + provenance 기록."""
    import rhino3dm

    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    _patch_small_ortho(monkeypatch)
    _patch_synth_dem(monkeypatch)
    out = generate(
        "대전광역시 서구 괴정동 358",
        client=FakeClient(_daejeon_features()),
        outputs=["3dm"],
        layers={"buildings": True, "terrain": True, "orthophoto": True},
        output_dir=str(tmp_path),
        ortho_fetch=lambda url: _solid_png8(),
    )
    assert out["ok"] is True
    o3 = out["outputs"]["3dm"]["orthophoto"]
    assert o3 is not None
    assert o3["missing_tiles"] == 0
    from pathlib import Path
    assert Path(o3["image_path"]).exists()          # PNG가 .3dm 옆에 저장됨
    assert out["provenance"]["orthophoto_src"] == "TestOrtho"

    m = rhino3dm.File3dm.Read(out["outputs"]["3dm"]["path"])
    meshes = [o for o in m.Objects if type(o.Geometry).__name__ == "Mesh"]
    assert meshes, "지형 메시가 있어야 함"
    assert len(meshes[0].Geometry.TextureCoordinates) > 0


def test_orthophoto_without_terrain_warns_and_skips(monkeypatch, tmp_path):
    """지형 없이 orthophoto만 → 경고 + orthophoto None(조용한 fallback)."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    _patch_small_ortho(monkeypatch)
    out = generate(
        "대전광역시 서구 괴정동 358",
        client=FakeClient(_daejeon_features()),
        outputs=["3dm"],
        layers={"buildings": True, "orthophoto": True},  # terrain 없음
        output_dir=str(tmp_path),
        ortho_fetch=lambda url: _solid_png8(),
    )
    assert out["ok"] is True
    assert out["outputs"]["3dm"]["orthophoto"] is None
    assert any("지형" in w and "정사영상" in w for w in out["warnings"])


def test_orthophoto_skp_only_generates_for_extension(monkeypatch, tmp_path):
    """outputs=skp만 + orthophoto → 정사영상 PNG 생성 + geometry.ortho_extent_m (B2:
    데스크톱 확장이 PNG를 받아 지형에 드레이프. 더 이상 .3dm 전용 아님)."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    _patch_small_ortho(monkeypatch)
    _patch_synth_dem(monkeypatch)
    out = generate(
        "대전광역시 서구 괴정동 358",
        client=FakeClient(_daejeon_features()),
        outputs=["skp"],
        layers={"buildings": True, "terrain": True, "orthophoto": True},
        output_dir=str(tmp_path),
        ortho_fetch=lambda url: _solid_png8(),
        include_geometry=True,
    )
    assert out["ok"] is True
    # 정사영상이 생성돼 확장이 쓸 extent가 geometry에 담긴다.
    assert out["geometry"]["ortho_extent_m"] is not None
    # PNG가 output_dir에 저장됨(확장이 /api/files/.../ortho로 다운로드).
    from pathlib import Path
    assert list(Path(tmp_path).glob("*_ortho.png"))
    # ".3dm 전용" 경고는 더 이상 없어야 한다.
    assert not any(".3dm" in w and "정사영상" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# 브라우저 3D 미리보기용 geometry JSON (F2)
# ---------------------------------------------------------------------------

def test_generate_include_geometry(monkeypatch):
    """include_geometry=True → 건물+지형 지오메트리가 JSON 직렬화 가능한 형태로 포함."""
    import json

    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    _patch_synth_dem(monkeypatch)
    out = generate(
        "대전광역시 서구 괴정동 358",
        client=FakeClient(_daejeon_features()),
        layers={"buildings": True, "terrain": True},
        include_geometry=True,
    )
    g = out["geometry"]
    assert g is not None
    # 건물: footprint/base_z/height/flagged
    assert len(g["buildings"]) == 4
    b0 = g["buildings"][0]
    assert len(b0["footprint"]) >= 3
    assert {"base_z", "height", "flagged"} <= b0.keys()
    # 지형: vertices/triangles, 인덱스는 순수 int (numpy.int32 혼입 방지)
    assert g["terrain"] is not None
    assert g["terrain"]["vertices"] and g["terrain"]["triangles"]
    assert all(type(i) is int for i in g["terrain"]["triangles"][0])
    # 전체 직렬화 가능 — numpy 타입이 섞이면 여기서 TypeError (회귀 가드)
    json.dumps(g)


def test_generate_geometry_omitted_by_default(monkeypatch):
    """기본(include_geometry=False)은 geometry=None — MCP 응답 비대화 방지."""
    monkeypatch.setattr(
        pl, "geocode", lambda a: {"lon": 127.37098, "lat": 36.33998, "crs": "EPSG:4326"}
    )
    out = generate("대전광역시 서구 괴정동 358", client=FakeClient(_daejeon_features()))
    assert out["geometry"] is None
