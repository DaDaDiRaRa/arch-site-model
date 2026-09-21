"""Phase 5: CadastralParcel 단위 테스트."""

import pytest

from src.geometry.cadastral import CadastralParcel, features_to_parcels

OFFSET = (200_000.0, 400_000.0)


def _feat(pnu: str, coords: list) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [coords]},
        "properties": {"pnu": pnu},
    }


def _feat_mp(pnu: str, rings: list) -> dict:
    """MultiPolygon 피처."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [[ring] for ring in rings],
        },
        "properties": {"pnu": pnu},
    }


# ---------------------------------------------------------------------------
# 기본 변환
# ---------------------------------------------------------------------------

def test_basic_polygon():
    """사각형 Polygon → CadastralParcel 1개, pnu 보존."""
    feat = _feat(
        "3017010800103580000",
        [[127.37, 36.33], [127.38, 36.33], [127.38, 36.34], [127.37, 36.34], [127.37, 36.33]],
    )
    parcels = features_to_parcels([feat], OFFSET)
    assert len(parcels) == 1
    assert parcels[0].pnu == "3017010800103580000"
    assert len(parcels[0].footprint_m) == 4  # 닫힘점 제거


def test_footprint_is_local_meters():
    """footprint_m 좌표는 EPSG:5186 - offset 로컬 미터여야 한다."""
    feat = _feat(
        "0000",
        [[127.37, 36.33], [127.38, 36.33], [127.38, 36.34], [127.37, 36.34], [127.37, 36.33]],
    )
    parcels = features_to_parcels([feat], OFFSET)
    # 로컬 좌표: 5186 절댓값에서 offset 뺀 값
    xs = [x for x, _ in parcels[0].footprint_m]
    ys = [y for _, y in parcels[0].footprint_m]
    # offset=(200000, 400000) 적용 후 수백m 수준이어야 함
    assert all(abs(x) < 50_000 for x in xs), "X 오프셋이 적용되지 않음"
    assert all(abs(y) < 50_000 for y in ys), "Y 오프셋이 적용되지 않음"


def test_multipolygon_takes_largest():
    """MultiPolygon → 가장 큰 폴리곤 1개만 취득."""
    small = [[127.370, 36.330], [127.371, 36.330], [127.371, 36.331], [127.370, 36.331], [127.370, 36.330]]
    large = [[127.380, 36.330], [127.390, 36.330], [127.390, 36.340], [127.380, 36.340], [127.380, 36.330]]
    feat = _feat_mp("mp_test", [small, large])
    parcels = features_to_parcels([feat], OFFSET)
    assert len(parcels) == 1
    # 큰 폴리곤은 꼭짓점이 많고 좌표 범위가 더 넓음
    xs = [x for x, _ in parcels[0].footprint_m]
    assert max(xs) - min(xs) > 100  # 대략 1km 정도 차이


def test_bub_cd_fallback():
    """pnu 없을 때 bub_cd로 대체."""
    feat = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [
            [[127.37, 36.33], [127.38, 36.33], [127.38, 36.34], [127.37, 36.34], [127.37, 36.33]]
        ]},
        "properties": {"bub_cd": "3017010800"},
    }
    parcels = features_to_parcels([feat], OFFSET)
    assert parcels[0].pnu == "3017010800"


def test_empty_features():
    """빈 피처 목록 → 빈 결과."""
    assert features_to_parcels([], OFFSET) == []


def test_degenerate_geometry_skipped():
    """꼭짓점 < 3 폴리곤은 건너뜀."""
    feat = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[127.37, 36.33], [127.37, 36.33]]]},
        "properties": {"pnu": "deg"},
    }
    assert features_to_parcels([feat], OFFSET) == []


def test_none_geometry_skipped():
    """geometry=None 피처는 건너뜀."""
    feat = {"type": "Feature", "geometry": None, "properties": {"pnu": "null_geom"}}
    assert features_to_parcels([feat], OFFSET) == []


def test_multiple_features():
    """피처 여러 개 → 동수의 파셀."""
    coords = [[127.37, 36.33], [127.38, 36.33], [127.38, 36.34], [127.37, 36.34], [127.37, 36.33]]
    feats = [_feat(f"pnu_{i}", coords) for i in range(3)]
    parcels = features_to_parcels(feats, OFFSET)
    assert len(parcels) == 3
    assert [p.pnu for p in parcels] == ["pnu_0", "pnu_1", "pnu_2"]


def test_clip_parcels_cuts_long_parcel_and_keeps_corners():
    from src.geometry.cadastral import CadastralParcel, clip_parcels

    # 모델 범위 0~100m, 도로 필지는 x -500~2000 (수 km 뻗음) → 범위 안으로 잘린다
    road = CadastralParcel(pnu="R", footprint_m=[(-500, 40), (2000, 40), (2000, 60), (-500, 60)])
    out = clip_parcels([road, CadastralParcel(pnu="FAR", footprint_m=[(900, 900), (950, 900), (950, 950)])],
                       (0, 0, 100, 100), step_m=5.0)
    assert [p.pnu for p in out] == ["R"]                    # 범위 밖 필지는 사라짐
    xs = [x for x, _ in out[0].footprint_m]
    ys = [y for _, y in out[0].footprint_m]
    assert min(xs) >= 0 and max(xs) <= 100
    for corner in [(0, 40), (100, 40), (100, 60), (0, 60)]:  # 모서리 보존
        assert any(abs(x - corner[0]) < 1e-6 and abs(y - corner[1]) < 1e-6 for x, y in out[0].footprint_m)
    assert len(out[0].footprint_m) >= 2 * (100 // 5)       # 변 조밀화(지형 드레이프용)
    assert min(ys) >= 40
