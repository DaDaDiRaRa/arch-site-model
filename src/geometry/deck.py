"""교량 데크 — 도로가 강을 건널 때 지형을 따라 내려가지 않게 한다.

## 왜 필요한가

우리 지형은 등고선(5m 간격)에서 구운 DEM이라 **교량 데크가 없다**. 그 위에 도로를 구우면
다음이 일어난다(실측 기준선 `docs/bridge_baseline.json` — 수면 3m 내 도로 정점 6,135개 중
4,779개가 수면 아래, 이촌동은 4,184 중 4,133):

1. `road.burn_roads`가 데크 셀을 중심선 최근접 표고로 굽는다. 그 중심선 z 자체가 DEM
   샘플이라 강 중간에서는 **하천 바닥**이다.
2. `water.burn_water`가 수계 폴리곤 내부 모든 셀을 수면 표고로 덮어써 1을 지운다.
3. `road.build_unified_surface`가 도로 정점 z를 그 DEM에서 다시 읽는다.
4. 크라운이 도로 정점만 조금 더 낮춘다.

## 방식 — 데크는 DEM에 넣지 않는다

지형은 강·골짜기 그대로 두고(버닝에서 데크 발자국을 제외) **데크만 DEM 위로 뜬다**.
`build_unified_surface`는 평면 Delaunay라 한 (x,y)에 z가 하나뿐이므로, 데크 정점 z를
지형과 공유하면 강 양안이 데크 높이로 솟는다 — 그래서 데크는 별도 정점 배열을 쓴다.

**데크 표고는 발명하지 않는다.** 교량 양단(아붓먼트, 육상부) 노면 표고를 실측으로 읽고
그 사이를 중심선 호장(arc-length) 선형으로 잇는다 = 두 실측점 사이 단조 보간. `A0070000`에
종단 정보가 없으므로 중간 수직곡선을 넣으면 그게 발명이다. 평면 곡률은 중심선을 따라가므로
곡선·사교 교량도 자동으로 맞는다(폴리곤 양끝 2점 평면 보간은 현(弦)을 따라가 틀린다).

## 데이터원 2단계 — 같은 코드 경로

- **수계 프록시**(재베이크 0): 도로 ∩ 수계 = 교량. 실측상 도로·중심선·보도가 수계를
  건너는 지점은 **100% 교량 폴리곤이 덮는다**(8/8, 8/8, 10/10) → 가짜 교량을 만들지 않는다.
  단 하천 폴리곤 **안을 따라 달리는 강변 제방도로**를 램프 교량으로 오인할 수 있어
  직진성·최대 경간 가드를 둔다.
- **교량 레이어**(`A0070000` 등): `clip_decks`로 실측 폴리곤을 읽는다. 이후 처리는 동일.

터널·지하차도는 `hidden`으로 분리한다 — 지형은 건드리지 않고 그 구간 노면만 생략한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 이 값들은 config에서 주입받는다(테스트가 인자로 덮어쓸 수 있게 기본값도 둔다).
DECK_SAMPLE_M = 5.0            # 중심선 샘플 간격
DECK_ANCHOR_SPAN_M = 10.0      # 아붓먼트 표고를 읽을 육상 구간 길이(여러 샘플의 중앙값)
DECK_MARGIN_M = 4.0            # 수계 프록시: 물가선이 아니라 둑 위에서 노면과 만나게
DECK_MAX_SPAN_M = 400.0        # 이보다 긴 연속 구간은 교량으로 보지 않는다
DECK_MIN_STRAIGHTNESS = 0.8    # 현/호장 — 사행하는 강변도로를 걸러낸다
DECK_MIN_CLEARANCE_M = 0.30    # 노면이 수면보다 이만큼은 높아야 한다(물리 제약)

HIDDEN_KINDS = frozenset({"tunnel", "underpass"})


@dataclass
class DeckFeature:
    """교량·터널 발자국 하나. 좌표=로컬 미터, rings[0]=외곽, 이후=구멍."""

    rings: list[list[tuple[float, float]]]
    kind: str = "bridge"                 # bridge | viaduct | tunnel | underpass
    clearance_m: float | None = None     # A0090000/A0110020의 HEIG(통과높이). 데크고 아님
    name: str = ""
    source: str = "layer"                # layer | water_proxy


@dataclass
class DeckProfile:
    """한 노선이 한 데크를 한 번 건너는 구간의 종단."""

    xy: list[tuple[float, float]]
    z: list[float]
    span_m: float
    flags: list[str] = field(default_factory=list)


@dataclass
class DeckSurface:
    """풀린 종단 + (x,y)→z 조회. 종단이 안 풀린 데크는 담지 않는다."""

    decks: list[DeckFeature] = field(default_factory=list)
    hidden: list[DeckFeature] = field(default_factory=list)
    profiles: list[DeckProfile] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    dropped: int = 0                     # 종단을 못 푼 데크 수(경고용)
    _tree: object = None
    _z: object = None

    def __post_init__(self):
        self._build()

    def _build(self) -> None:
        pts, zs = [], []
        for p in self.profiles:
            pts.extend(p.xy)
            zs.extend(p.z)
        if not pts:
            self._tree, self._z = None, None
            return
        import numpy as np
        from scipy.spatial import cKDTree

        self._tree = cKDTree(np.asarray(pts, dtype=float))
        self._z = np.asarray(zs, dtype=float)

    def z_at(self, x: float, y: float) -> float | None:
        """데크 종단 표고. 종단이 없으면 None(호출자가 지형 z로 폴백)."""
        if self._tree is None:
            return None
        _, i = self._tree.query([float(x), float(y)])
        return float(self._z[int(i)])

    def polygon(self):
        """활성 데크(노면을 띄울 발자국) union 또는 None."""
        return _union(self.decks)

    def hidden_polygon(self):
        """터널·지하차도 union 또는 None — 노면을 **생략**할 발자국."""
        return _union(self.hidden)

    def exclude_polys(self) -> list:
        """`burn_roads`가 지형을 건드리지 말아야 할 폴리곤 — 데크 + 터널."""
        return [p for p in (_polys(self.decks) + _polys(self.hidden))]

    def empty(self) -> bool:
        return not self.profiles and not self.hidden


# --- 기하 헬퍼 ---------------------------------------------------------------


def _poly(rings):
    from shapely.geometry import Polygon

    if not rings or len(rings[0]) < 3:
        return None
    try:
        p = Polygon(rings[0], [r for r in rings[1:] if len(r) >= 3] or None)
    except Exception:  # noqa: BLE001 — 망가진 링은 버린다
        return None
    if not p.is_valid:
        p = p.buffer(0)
    if p.is_empty or p.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return p


def _polys(feats) -> list:
    out = []
    for f in feats:
        p = _poly(f.rings)
        if p is not None:
            out.append(p)
    return out


def _union(feats):
    from shapely.ops import unary_union

    ps = _polys(feats)
    return unary_union(ps) if ps else None


def _contains(geom, xs, ys):
    """벡터 포함 판정 — 점별 Point 루프는 1~2km 반경에서 느리다."""
    import numpy as np

    try:
        from shapely import contains_xy

        return np.asarray(contains_xy(geom, np.asarray(xs), np.asarray(ys)), dtype=bool)
    except ImportError:  # shapely 1.x 폴백
        from shapely.geometry import Point

        return np.asarray([geom.contains(Point(x, y)) for x, y in zip(xs, ys)], dtype=bool)


def _runs(mask):
    """True가 연속된 구간의 (시작, 끝+1) 목록."""
    out = []
    i = 0
    n = len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return out


def _in_dem(dem, x: float, y: float) -> bool:
    """DEM 격자 안인가. `dem._sample`이 범위 밖을 **가장자리로 클램프**하므로 명시 검사가 필수다
    — 없으면 DEM 밖 200m 지점이 조용히 가장자리 표고를 돌려준다."""
    import math

    grid = getattr(dem, "grid", None)
    tf = getattr(dem, "transform", None)
    if grid is None or tf is None or tf.a == 0 or tf.e == 0:
        return False
    ox, oy = dem.offset
    c = (x + ox - tf.c) / tf.a
    r = (y + oy - tf.f) / tf.e
    rows, cols = grid.shape
    if not (0 <= r < rows and 0 <= c < cols):
        return False
    v = float(grid[int(r), int(c)])
    return math.isfinite(v)


# --- 소스 -------------------------------------------------------------------


def clip_decks(geojson_path, bbox_5186, offset) -> list[DeckFeature]:
    """데크 GeoJSON(교량·터널 폴리곤)을 bbox로 골라 로컬 미터로.

    ⚠️ **교집합으로 자르지 않는다.** 폴리곤의 양 끝이 종단 z를 정의하므로 타일·bbox 경계에서
    자르면 그 정보가 파괴된다. `intersects`만 보고 **원 폴리곤을 그대로** 돌려준다(베이크도
    같은 이유로 하드클립하지 않고 닿는 타일마다 통째로 복제한다 → `"i"`로 중복 제거).
    """
    from shapely.geometry import box, shape

    from src.geometry.road import _load_features

    clip = box(*bbox_5186)
    ox, oy = offset
    seen: set = set()
    out: list[DeckFeature] = []
    for f in _load_features(geojson_path):
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            g = shape(geom)
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty or not g.intersects(clip):
            continue
        props = f.get("properties") or {}
        fid = props.get("i")
        if fid is not None:
            if fid in seen:          # 여러 타일에 복제된 같은 교량
                continue
            seen.add(fid)
        for part in getattr(g, "geoms", [g]):
            if part.geom_type != "Polygon":
                continue
            rings = [[(float(x) - ox, float(y) - oy) for x, y in part.exterior.coords]]
            rings += [[(float(x) - ox, float(y) - oy) for x, y in r.coords]
                      for r in part.interiors]
            cl = props.get("h")
            out.append(DeckFeature(
                rings=rings, kind=str(props.get("k") or "bridge"),
                clearance_m=float(cl) if isinstance(cl, (int, float)) else None,
                name=str(props.get("n") or ""), source="layer",
            ))
    return out


def decks_from_water(
    water_features, road_features, *, margin_m: float = DECK_MARGIN_M,
) -> list[DeckFeature]:
    """도로 ∩ 수계 = 교량 후보(재베이크 0 경로).

    실측상 도로가 수계를 건너는 지점은 100% 교량이므로 가짜 교량을 만들지 않는다. 경간·직진성
    가드는 `solve_decks`가 **중심선 구간**에 걸어서, 하천 폴리곤 안을 사행하는 강변 제방도로는
    종단이 풀리지 않아 자동으로 버려진다.
    """
    from shapely.ops import unary_union

    if not water_features or not road_features:
        return []
    wps = [p for p in (_poly(getattr(f, "rings", None)) for f in water_features) if p]
    rps = [p for p in (_poly(getattr(f, "rings", None)) for f in road_features) if p]
    if not wps or not rps:
        return []
    inter = unary_union(rps).intersection(unary_union(wps).buffer(margin_m))
    if inter.is_empty:
        return []
    out: list[DeckFeature] = []
    for part in getattr(inter, "geoms", [inter]):
        if part.geom_type != "Polygon" or part.area <= 0:
            continue
        rings = [list(part.exterior.coords)] + [list(r.coords) for r in part.interiors]
        out.append(DeckFeature(rings=rings, kind="bridge", source="water_proxy"))
    return out


# --- 종단 풀이 ---------------------------------------------------------------


def _boundary_cross(geom, p_out, p_in, steps: int = 8):
    """geom 경계와 만나는 점(선분 p_out→p_in 위)을 이분법으로. 이음매를 정확히 맞추기 위함."""
    from shapely.geometry import Point

    ax, ay = p_out
    bx, by = p_in
    for _ in range(steps):
        mx, my = (ax + bx) / 2, (ay + by) / 2
        if geom.contains(Point(mx, my)):
            bx, by = mx, my
        else:
            ax, ay = mx, my
    return ((ax + bx) / 2, (ay + by) / 2)


def _anchor_z(dem, pts, arcs, idxs, deck_u, water_u, span_m: float):
    """육상부 여러 샘플의 DEM 표고 중앙값 = 아붓먼트 노면 표고. 없으면 None.

    단일 샘플은 DEM 노이즈 한 점에 종단 전체가 끌린다. 채택 조건 3개: 데크 밖 + 수계 밖 +
    **DEM 범위 안**(`_in_dem` — `dem._sample`의 가장자리 클램프 때문).
    """
    import numpy as np
    from shapely.geometry import Point

    zs = []
    base = None
    for i in idxs:
        if base is None:
            base = arcs[i]
        if abs(arcs[i] - base) > span_m:
            break
        x, y = pts[i]
        if deck_u is not None and deck_u.contains(Point(x, y)):
            continue
        if water_u is not None and water_u.contains(Point(x, y)):
            continue
        if not _in_dem(dem, x, y):
            continue
        zs.append(float(dem.elev_at(x, y)))
    return float(np.median(zs)) if zs else None


def solve_decks(
    decks: list[DeckFeature], centerlines, dem, water_features=None, water_zs=None, *,
    sample_m: float = DECK_SAMPLE_M, anchor_span_m: float = DECK_ANCHOR_SPAN_M,
    max_span_m: float = DECK_MAX_SPAN_M, min_straightness: float = DECK_MIN_STRAIGHTNESS,
    min_clearance_m: float = DECK_MIN_CLEARANCE_M,
) -> DeckSurface:
    """데크 발자국 + 중심선 + DEM → 종단이 풀린 `DeckSurface`.

    한 노선이 한 데크를 한 번 건너는 구간(run)마다 종단을 푼다. 양 끝 아붓먼트 표고를
    실측으로 읽고 호장 선형으로 잇는다. 풀리지 않은 구간은 **버린다** — 오늘 동작보다
    나빠질 수 없게(회귀 구조적 불가).

    고가차도(viaduct)는 비활성이다 — `HEIG`가 통과높이여서 데크고를 모르고, 아붓먼트가 지면이라
    2점 보간이 물리적으로 틀리다. 데이터만 두고 쓰지 않는다(DSM이 생기면 승격).
    """
    import numpy as np
    from shapely.geometry import Point
    from shapely.ops import unary_union

    hidden = [d for d in decks if d.kind in HIDDEN_KINDS]
    active = [d for d in decks if d.kind not in HIDDEN_KINDS and d.kind != "viaduct"]
    if dem is None or not active or not centerlines:
        return DeckSurface(decks=[], profiles=[], hidden=hidden)

    deck_u = _union(active)
    if deck_u is None:
        return DeckSurface(decks=[], profiles=[], hidden=hidden)

    water_u = None
    wz_polys: list = []
    if water_features:
        wps = [_poly(getattr(f, "rings", None)) for f in water_features]
        wz_polys = list(zip(wps, list(water_zs or [])))
        ok = [p for p in wps if p is not None]
        water_u = unary_union(ok) if ok else None

    from src.geometry.road import _densify_line

    profiles: list[DeckProfile] = []
    flags: list[str] = []
    for line in centerlines:
        pts = _densify_line(line, sample_m)
        if not pts or len(pts) < 2:
            continue
        P = np.asarray(pts, dtype=float)
        arcs = np.concatenate([[0.0], np.cumsum(
            np.hypot(np.diff(P[:, 0]), np.diff(P[:, 1])))])
        for i0, i1 in _runs(_contains(deck_u, P[:, 0], P[:, 1])):
            span = float(arcs[i1 - 1] - arcs[i0])
            chord = float(np.hypot(*(P[i1 - 1] - P[i0])))
            if span > max_span_m:
                flags.append("span_too_long")
                continue
            if span > 1e-6 and chord / span < min_straightness:
                flags.append("not_straight")       # 하천 안을 사행하는 강변도로
                continue
            zl = _anchor_z(dem, pts, arcs, range(i0 - 1, -1, -1), deck_u, water_u,
                           anchor_span_m)
            zr = _anchor_z(dem, pts, arcs, range(i1, len(pts)), deck_u, water_u,
                           anchor_span_m)
            run_flags: list[str] = []
            if zl is None and zr is None:
                continue
            if zl is None or zr is None:
                # 한쪽만 풀림 — 중심선이 질의 영역 경계에서 잘린 경우만 수평 데크로 허용.
                clipped = (zl is None and i0 == 0) or (zr is None and i1 == len(pts))
                if not clipped:
                    continue
                zl = zr = zl if zl is not None else zr
                run_flags.append("single_anchor")

            # 데크 경계 교점을 종단 양 끝에 정확히 배치 → 아붓먼트 이음매 단차 0.
            xy = [(float(P[i][0]), float(P[i][1])) for i in range(i0, i1)]
            st = [float(arcs[i]) for i in range(i0, i1)]
            if i0 > 0:
                xy.insert(0, _boundary_cross(deck_u, pts[i0 - 1], pts[i0]))
                st.insert(0, float(arcs[i0]))
            if i1 < len(pts):
                xy.append(_boundary_cross(deck_u, pts[i1], pts[i1 - 1]))
                st.append(float(arcs[i1 - 1]))
            s0, s1 = st[0], st[-1]
            L = (s1 - s0) or 1.0
            z = [zl + (zr - zl) * ((s - s0) / L) for s in st]

            # 물리 제약: 노면이 수면 아래일 수는 없다. 종단을 통째로 들어올리고 표시한다.
            wz = None
            mid = Point(*xy[len(xy) // 2])
            for p, w in wz_polys:
                if p is not None and p.distance(mid) <= sample_m:
                    wz = w if wz is None else max(wz, w)
            if wz is not None and min(z) < wz + min_clearance_m:
                z = [v + ((wz + min_clearance_m) - min(z)) for v in z]
                run_flags.append("lifted_to_clear_water")

            profiles.append(DeckProfile(xy=xy, z=z, span_m=span, flags=run_flags))
            flags.extend(run_flags)

    solved = active if profiles else []
    return DeckSurface(decks=solved, profiles=profiles, hidden=hidden,
                       flags=sorted(set(flags)),
                       dropped=0 if profiles else len(active))


def split_centerlines(centerlines, geom) -> list:
    """중심선에서 geom(데크·터널) 안 구간을 **제거**한다.

    `burn_roads`의 IDW는 k=8·30m 반경이라 강 중간 중심선 샘플(=하천 바닥 z)이 아붓먼트
    노면까지 끌어내린다. 그래서 데크 메시보다 **이 제거가 먼저** 필요하다.
    """
    if geom is None or not centerlines:
        return centerlines
    from shapely.geometry import LineString

    from src.geometry.road import _iter_lines

    out = []
    for coords in centerlines:
        if len(coords) < 2:
            continue
        try:
            rest = LineString(coords).difference(geom)
        except Exception:  # noqa: BLE001
            out.append(coords)
            continue
        if rest.is_empty:
            continue
        for ls in _iter_lines(rest):
            c = [(float(x), float(y)) for x, y in ls.coords]
            if len(c) >= 2:
                out.append(c)
    return out


def decks_to_geometry(surface: DeckSurface) -> list[dict]:
    """뷰어·.3dm용 데크 종단선 — [{"points": [[x,y,z], ...], "span_m", "flags"}]."""
    return [
        {"points": [[round(x, 2), round(y, 2), round(z, 2)]
                    for (x, y), z in zip(p.xy, p.z)],
         "span_m": round(p.span_m, 1), "flags": p.flags}
        for p in surface.profiles
    ]
