"""도시계획 경계선 — 사이트 밖으로 뻗은 구역은 실제 결정선만 남고 사각형 테두리 가짜선이 없어야 한다."""

from src.geo.crs import to_4326
from src.geo.planning import fetch_planning
from src.geo.vworld import VWorldError

OFFSET = (197000.0, 459500.0)


def _poly_4326(x0, y0, x1, y1):
    """로컬 미터 사각형 → GeoJSON(4326) 폴리곤."""
    ox, oy = OFFSET
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return {"type": "Polygon", "coordinates": [[list(to_4326(x + ox, y + oy)) for x, y in ring]]}


class _Client:
    def __init__(self, by_ds, fail=()):
        self.by_ds, self.fail = by_ds, fail

    def get_features(self, dataset, bbox, geometry=True):
        if dataset in self.fail:
            raise VWorldError("boom")
        return self.by_ds.get(dataset, [])


def test_only_real_boundary_inside_site():
    # 사이트 0~100m. 지구단위계획구역은 -50~60 (서·남쪽으로 넘침) → 사이트 안 결정선은 x=60, y=60 두 변뿐
    feat = {"properties": {"dgm_nm": "테스트 지구단위계획구역"}, "geometry": _poly_4326(-50, -50, 60, 60)}
    out = fetch_planning(_Client({"LT_C_UPISUQ161": [feat]}), None, (0, 0, 100, 100), OFFSET)
    assert out and all(o["cat"] == "district_plan" and o["name"] == "테스트 지구단위계획구역" for o in out)
    pts = [p for o in out for p in o["line"]]
    # 사각형 테두리(x=0 또는 y=0) 위를 달리는 선이 없어야 한다 — 끝점 외엔 전부 x≈60 또는 y≈60
    assert all(abs(x - 60) < 0.05 or abs(y - 60) < 0.05 for x, y, _ in pts)
    total = sum(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        for o in out for a, b in zip(o["line"], o["line"][1:])
    )
    assert abs(total - 120.0) < 0.01   # 60 + 60 — 모서리(60,60)가 깎이지 않아야 한다
    assert any(abs(x - 60) < 1e-6 and abs(y - 60) < 1e-6 for x, y, _ in pts)


def test_drape_and_failure_is_warning():
    class _Dem:
        def elev_at(self, x, y):
            return 42.0

    feat = {"properties": {"dgm_nm": "소로3류"}, "geometry": _poly_4326(10, 10, 20, 20)}
    warnings = []
    out = fetch_planning(
        _Client({"LT_C_UPISUQ151": [feat]}, fail={"LT_C_UPISUQ161"}),
        None, (0, 0, 100, 100), OFFSET, dem=_Dem(), warnings=warnings,
    )
    assert out and all(p[2] == 42.2 for o in out for p in o["line"])   # 지형 + 0.2m
    assert any("지구단위계획구역" in w for w in warnings)
