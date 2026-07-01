"""generate_site_tiles — 대량건물 타일분할 (백로그5)."""

import pytest

import src.tiles as tl
from src.geo.vworld import DATASET_CADASTRAL
from src.geometry.terrain_mesh import TerrainMesh
from src.tiles import _split_terrain_by_tile, generate_tiles


class FakeClient:
    def __init__(self, features):
        self._features = features

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        return self._features

    def count(self, dataset, bbox):
        return len(self._features)


class FakeClientMulti:
    def __init__(self, building_features, cadastral_features):
        self._building = building_features
        self._cadastral = cadastral_features

    def get_features(self, dataset, bbox, size=1000, page=1, geometry=True):
        if dataset == DATASET_CADASTRAL:
            return self._cadastral
        return self._building

    def count(self, dataset, bbox):
        return len(self._building)


def _square(lon0, lat0, side_deg=0.0002):
    return [[
        [lon0, lat0],
        [lon0 + side_deg, lat0],
        [lon0 + side_deg, lat0 + side_deg],
        [lon0, lat0 + side_deg],
        [lon0, lat0],
    ]]


def _feature(idx, lon0, lat0, floors):
    return {
        "type": "Feature",
        "properties": {"gro_flo_co": str(floors), "buld_nm": f"BLDG{idx}"},
        "geometry": {"type": "MultiPolygon", "coordinates": [_square(lon0, lat0)]},
    }


def _spread_features(n=6):
    """경도 방향으로 ~85m 간격으로 흩어진 n개 건물(EPSG:5186 기준 tile_size_m=50이면
    서로 다른 타일에 배정됨)."""
    return [_feature(i, 127.3700 + i * 0.001, 36.3400, floors=(i % 5) + 1) for i in range(n)]


def _cadastral_features(n=6):
    feats = []
    for i in range(n):
        lon0 = 127.3700 + i * 0.001
        feats.append({
            "type": "Feature",
            "properties": {"pnu": f"301701080010{i:04d}0000"},
            "geometry": {"type": "MultiPolygon", "coordinates": [_square(lon0, 36.3400, 0.0003)]},
        })
    return feats


def _fake_geocode(monkeypatch):
    monkeypatch.setattr(
        tl, "geocode", lambda a: {"lon": 127.3705, "lat": 36.3400, "crs": "EPSG:4326"}
    )


# --- 기본 동작 ---

def test_multiple_tiles(monkeypatch):
    _fake_geocode(monkeypatch)
    out = generate_tiles(
        "대전광역시 서구 괴정동 358",
        radius_m=500,
        tile_size_m=50.0,
        client=FakeClient(_spread_features(6)),
    )
    assert out["ok"] is True
    assert out["stats"]["buildings"] == 6
    assert out["stats"]["solids"] == 6
    assert out["stats"]["tile_count"] > 1
    # 타일별 solids 합 == 전체 solids
    assert sum(t["solids"] for t in out["tiles"]) == 6
    for t in out["tiles"]:
        assert "def extrude_solid" in t["code"]
        assert t["solids"] > 0   # 빈 타일은 만들지 않음


def test_single_tile_when_area_small(monkeypatch):
    _fake_geocode(monkeypatch)
    out = generate_tiles(
        "대전광역시 서구 괴정동 358",
        tile_size_m=5000.0,   # 넓은 격자 → 전부 한 타일
        client=FakeClient(_spread_features(6)),
    )
    assert out["stats"]["tile_count"] == 1
    assert out["tiles"][0]["solids"] == 6


def test_origin_offset_shared_across_tiles(monkeypatch):
    """origin_offset은 전체 반경에서 1회만 계산 — stats에 단일 값으로 보존."""
    _fake_geocode(monkeypatch)
    out = generate_tiles(
        "대전광역시 서구 괴정동 358",
        tile_size_m=50.0,
        client=FakeClient(_spread_features(6)),
    )
    assert len(out["stats"]["origin_offset"]) == 2
    assert out["stats"]["origin_offset"][0] > 0


def test_no_buildings(monkeypatch):
    _fake_geocode(monkeypatch)
    out = generate_tiles("빈 곳", client=FakeClient([]))
    assert out["ok"] is False
    assert "건물" in out["error"]


def test_geocode_fail(monkeypatch):
    from src.geo.geocode import GeocodeError

    def boom(a):
        raise GeocodeError("주소 변환 실패")

    monkeypatch.setattr(tl, "geocode", boom)
    out = generate_tiles("이상한 주소")
    assert out["ok"] is False


def test_invalid_tile_size():
    with pytest.raises(ValueError):
        generate_tiles("아무 주소", tile_size_m=0, client=FakeClient([]))


# --- missing_floors_policy 전달 ---

def test_missing_policy_skip_reduces_solids(monkeypatch):
    _fake_geocode(monkeypatch)
    feats = _spread_features(4)
    feats[0]["properties"]["gro_flo_co"] = "0"   # 누락 처리 대상
    out = generate_tiles(
        "대전광역시 서구 괴정동 358",
        tile_size_m=50.0,
        missing_floors_policy="skip",
        client=FakeClient(feats),
    )
    assert out["stats"]["solids"] == 3
    assert sum(t["solids"] for t in out["tiles"]) == 3


# --- 지적 레이어 ---

def test_cadastral_split_across_tiles(monkeypatch):
    _fake_geocode(monkeypatch)
    out = generate_tiles(
        "대전광역시 서구 괴정동 358",
        tile_size_m=50.0,
        layers={"buildings": True, "cadastral": True},
        client=FakeClientMulti(_spread_features(6), _cadastral_features(6)),
    )
    assert out["ok"] is True
    assert out["stats"]["cadastral_parcels"] == 6
    assert sum(t["cadastral_parcels"] for t in out["tiles"]) == 6
    assert any("CADASTRAL" in t["code"] for t in out["tiles"])


# --- 지형 분할 (순수 함수 단위 테스트) ---

def test_split_terrain_by_tile():
    # 삼각형 3개: (0,0,0)/(0,60,0)/(0,120,0) 근방 — x=0 고정, y만 벌려 다른 타일로.
    verts = [
        (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0),      # tile (0,0) 부근
        (0.0, 2600.0, 0.0), (10.0, 2600.0, 0.0), (0.0, 2610.0, 0.0),  # tile 멀리 (y=2600in≈66m local... )
    ]
    # M2I 배율 고려: 로컬 미터 * M2I = 인치. tile_size_m=50 기준 확실히 분리되도록 y 큰 폭 사용.
    import src.config as config
    verts = [
        (0.0, 0.0, 0.0),
        (5.0 * config.M2I, 0.0, 0.0),
        (0.0, 5.0 * config.M2I, 0.0),
        (0.0, 500.0 * config.M2I, 0.0),
        (5.0 * config.M2I, 500.0 * config.M2I, 0.0),
        (0.0, 505.0 * config.M2I, 0.0),
    ]
    mesh = TerrainMesh(vertices=verts, triangles=[(0, 1, 2), (3, 4, 5)])
    buckets = _split_terrain_by_tile(mesh, tile_size_m=50.0)
    assert len(buckets) == 2
    for key, sub in buckets.items():
        assert len(sub.triangles) == 1
        assert len(sub.vertices) == 3
