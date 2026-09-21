"""도시계획 레이어 — 지구단위계획구역·도시계획시설(도로·교통·공원녹지·광장 등) 경계선.

VWorld 데이터 API의 도시계획정보(UPIS) 레이어를 bbox로 받아, 경계선을 로컬 미터로 바꾸고
지형(DEM)에 드레이프한다. **판정은 하지 않는다** — 용도지역·행위제한 판단은 형제 앱
arch-law-graph 소관이고, 여기서는 "어디에 무엇이 결정돼 있는가"의 선형과 이름만 모델에 싣는다.

데이터셋 ID는 2026-09-21 실측(충남 아산 신인농공단지 일대)으로 확인했다:
  LT_C_UPISUQ161 지구단위계획(dgm_nm "신인농공단지 지구단위계획구역"),
  LT_C_UPISUQ151 도시계획도로(dgm_nm "소로3류"), 152 교통시설, 153 공간시설(광장·공원·녹지·공공공지),
  154 유통공급, 155 공공문화체육, 156 방재, 157 보건위생, 158 환경기초, 159 기타기반시설.
용도지역(LT_C_UQ11x)은 zoning 레이어(arch-law-graph)가 맡으므로 여기 넣지 않는다.

경계선은 **실제 결정선만** 남긴다: 폴리곤 경계를 사이트 사각형과 교차시켜(선 ∩ 사각형), 영역 밖으로
뻗은 큰 구역(지구단위계획구역 등)을 잘라도 사각형 테두리에 가짜 선이 생기지 않는다.
"""

from __future__ import annotations

from shapely.geometry import LineString, MultiLineString, box, shape
from shapely.ops import transform

from src.geo.crs import to_5186

# (데이터셋, 키, 표시 이름, RGB) — 키는 .3dm 레이어명·뷰어 범례·.dae 노드명에 쓴다.
PLANNING_LAYERS: list[tuple[str, str, str, tuple[int, int, int]]] = [
    ("LT_C_UPISUQ161", "district_plan", "지구단위계획구역", (230, 50, 180)),
    ("LT_C_UPISUQ151", "plan_road", "도시계획도로", (220, 60, 40)),
    ("LT_C_UPISUQ152", "transport", "교통시설", (40, 90, 200)),
    ("LT_C_UPISUQ153", "open_space", "공간시설(공원·녹지·광장)", (34, 160, 60)),
    ("LT_C_UPISUQ154", "supply", "유통공급시설", (150, 110, 60)),
    ("LT_C_UPISUQ155", "public", "공공문화체육시설", (20, 180, 200)),
    ("LT_C_UPISUQ156", "disaster", "방재시설", (90, 90, 160)),
    ("LT_C_UPISUQ157", "health", "보건위생시설", (200, 120, 160)),
    ("LT_C_UPISUQ158", "env", "환경기초시설", (120, 150, 60)),
    ("LT_C_UPISUQ159", "other_infra", "기타기반시설", (130, 130, 130)),
]
LABELS = {k: label for _, k, label, _ in PLANNING_LAYERS}
COLORS = {k: rgb for _, k, _, rgb in PLANNING_LAYERS}

# 드레이프 간격(m) — 경계선이 지형 굴곡을 따라가도록 정점을 이 간격으로 촘촘히.
DRAPE_STEP_M = 5.0


def _name(props: dict) -> str:
    for k in ("dgm_nm", "mls_nam", "lcl_nam", "atr_nam", "uname"):
        v = (props or {}).get(k)
        if v:
            return str(v)
    return ""


def _lines_local(geom: dict, offset: tuple[float, float], site_local) -> list[list[tuple[float, float]]]:
    """GeoJSON(4326) 폴리곤 → 사이트 사각형 안의 경계선(로컬 미터) 목록."""
    try:
        g = shape(geom)
    except Exception:  # noqa: BLE001 — 깨진 지오메트리는 건너뜀
        return []
    ox, oy = offset

    def _to_local(x, y, z=None):
        X, Y = to_5186(x, y)
        return X - ox, Y - oy

    g = transform(lambda xs, ys, zs=None: tuple(zip(*[_to_local(x, y) for x, y in zip(xs, ys)])), g)
    try:
        cut = g.boundary.intersection(site_local)
    except Exception:  # noqa: BLE001
        return []
    parts = []
    if isinstance(cut, LineString):
        parts = [cut]
    elif isinstance(cut, MultiLineString):
        parts = list(cut.geoms)
    elif hasattr(cut, "geoms"):
        parts = [p for p in cut.geoms if isinstance(p, LineString)]
    out = []
    for ln in parts:
        if ln.length < 0.5:
            continue
        out.append(_densify(list(ln.coords)))
    return out


def _densify(coords) -> list[tuple[float, float]]:
    """변마다 DRAPE_STEP_M 간격으로 점을 넣되 **원래 꼭짓점은 그대로 둔다**.

    선 전체를 등분하면(interpolate) 꼭짓점이 빠져 모서리가 깎인다 — 길이가 120.0 vs 119.9999로
    갈리면 등분 수가 달라져 모서리 점이 사라지는 게 CI(리눅스)에서 드러났다(2026-09-21).
    """
    pts = [(float(c[0]), float(c[1])) for c in coords]
    out = [pts[0]]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        seg = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        n = max(1, int(seg // DRAPE_STEP_M))
        out += [(x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n) for k in range(1, n + 1)]
    return out


def fetch_planning(client, bbox_4326, bbox_local, offset, dem=None, warnings=None) -> list[dict]:
    """사이트 bbox의 도시계획 경계선 → [{"cat","label","name","line":[[x,y,z],...]}].

    bbox_local: 사이트 사각형(로컬 미터, (x0,y0,x1,y1)). dem이 있으면 z를 지형에 드레이프(+0.2m
    띄워 지형과 겹쳐 깜빡이지 않게), 없으면 0. 한 레이어 실패는 경고만 남기고 계속한다.
    """
    from src.geo.vworld import VWorldError

    site = box(*bbox_local)
    out: list[dict] = []
    for dataset, key, label, _ in PLANNING_LAYERS:
        try:
            feats = client.get_features(dataset, bbox_4326, geometry=True)
        except VWorldError as e:
            if warnings is not None:
                warnings.append(f"도시계획 {label} 취득 실패 (계속 진행): {e}")
            continue
        for f in feats:
            name = _name(f.get("properties") or {})
            for line in _lines_local(f.get("geometry"), offset, site):
                if dem is not None:
                    pts = [[round(x, 2), round(y, 2), round(dem.elev_at(x, y) + 0.2, 2)] for x, y in line]
                else:
                    pts = [[round(x, 2), round(y, 2), 0.0] for x, y in line]
                out.append({"cat": key, "label": label, "name": name, "line": pts})
    return out
