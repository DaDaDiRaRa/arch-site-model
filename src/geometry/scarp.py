"""지형 단차 — 제방(마루+사면)과 절토/성토면(파단선 선명화)을 DEM에 심는다.

두 가지는 성격이 다르다.

**제방(levee)** — 지형에 **없다**. 실측(충남 396표본): 도면 제방고 중앙 2.0m인데 우리 DEM의
마루−양옆 표고차는 중앙 −0.67m(음수가 93.4%). 마루폭이 2.5~4m로 5m 격자보다 좁아 등고선이
그 자리를 지나가지 않기 때문이다. 그래서 **실측 제방고를 올려서 만든다**(올리기만 한다).

**절토/성토면(cut/fill)** — 표고차는 **이미 DEM에 있다**(양 끝이 모두 실제 지표면이다).
등고선이 망친 것은 그 표고차가 일어나는 **폭**(실제 약 7m를 20m 완경사로 번지게 함)과 엣지의
각짐이다. 그래서 높이를 만들지 않고 두 경계선 사이를 직선 사면으로 **조인다**. 표고는 선 바깥
실측 지반에서 읽으므로 추정이 0이다.

## 누적 하강을 구조적으로 막는다 — `ZTargets`

`pad.burn_pads`는 필지 경계마다 옹벽 버닝을 반복했다가 **같은 셀이 여러 번 내려가** 필지 안이
더 파였다(실측: 고저차 3.7m → 5.6m). 원인은 "내리는 클램프를 피처마다 순차 적용"이다.

여기서는 피처마다 **목표만 누적**하고(하한 `lo`는 `fmax`, 상한 `hi`는 `fmin`) 마지막에 **한 번**
적용한다. `fmax`/`fmin`은 멱등·교환법칙이라 결과가 **피처 순서와 중복 적용 횟수에 무관**하다.
누적 중 원본 격자를 건드리지 않으므로 모든 probe가 **원 DEM**을 읽는다는 성질도 따라온다
(`wall.burn_walls`는 진행 중인 배열을 읽어 순서 의존이 남아 있다).
"""

from __future__ import annotations

from dataclasses import dataclass

# --- 제방 ---
LEVEE_CROWN_W_M = 3.0        # 둑마루폭(실측 2.5~4m, 하천설계기준 ≥3m). 형상만 표준단면이고
LEVEE_SLOPE_RATIO = 2.0      # 사면 1:2. **높이는 실측(HEIG)** — provenance에 그대로 남긴다
LEVEE_SMOOTH_WIN_M = 30.0    # 마루 기준 지반고 종단 평활 창(노이즈만 죽인다)

# --- 절토/성토면 ---
SCARP_STEP_M = 2.0           # 상단선 샘플 간격
SCARP_PROBE_NEAR_M = 1.5     # 자연 사면 쪽 — 멀리 읽으면 경사지에서 과대평가된다
SCARP_PROBE_FAR_M = 6.0      # 인공 평탄면 쪽 — 뭉개진 전이부를 넘어가야 한다
SCARP_TOL_M = 0.3            # 이 안쪽은 손대지 않는다(이미 맞는 곳은 그대로)
SCARP_MIN_DROP_M = 0.5       # 이보다 낮은 단차는 짝짓기 오류·사면고 0 → 버린다

# `DIVI` 값 → (상단 probe 거리, 하단 probe 거리). 한쪽은 인공 평탄면, 다른 쪽은 자연 지반이다.
_PROBE = {
    "SJD001": (SCARP_PROBE_NEAR_M, SCARP_PROBE_FAR_M),   # 절토: 위가 자연 산지
    "절토":   (SCARP_PROBE_NEAR_M, SCARP_PROBE_FAR_M),
    "SJD002": (SCARP_PROBE_FAR_M, SCARP_PROBE_NEAR_M),   # 성토: 위가 인공 천단
    "성토":   (SCARP_PROBE_FAR_M, SCARP_PROBE_NEAR_M),
}


@dataclass
class ScarpFeature:
    """지형 단차 하나. 좌표=로컬 미터.

    kind="levee": `top`(마루선) + `height_m`(실측 제방고). `bot`은 없다 — 토우는 높이로 유도한다.
    kind="cut"|"fill": `top`(상단선) + `bot`(하단선) 짝. 높이는 DEM에서 읽는다.
    """

    kind: str
    top: list[tuple[float, float]]
    bot: list[tuple[float, float]] | None = None
    height_m: float | None = None
    divi: str = ""


class ZTargets:
    """셀별 표고 하한/상한 누적기. `lo`는 "이 이상", `hi`는 "이 이하". NaN = 제약 없음."""

    def __init__(self, shape):
        import numpy as np

        self.lo = np.full(shape, np.nan, dtype=float)
        self.hi = np.full(shape, np.nan, dtype=float)

    def raise_to(self, rows, cols, z) -> None:
        import numpy as np

        self.lo[rows, cols] = np.fmax(self.lo[rows, cols], z)

    def lower_to(self, rows, cols, z) -> None:
        import numpy as np

        self.hi[rows, cols] = np.fmin(self.hi[rows, cols], z)

    def conflicts(self) -> int:
        """`lo > hi`인 셀 수 — 제방 마루가 절토 토우를 지나는 등. 침묵시키지 않는다."""
        import numpy as np

        both = ~np.isnan(self.lo) & ~np.isnan(self.hi)
        return int((self.lo[both] > self.hi[both]).sum())

    def apply(self, grid, protect=None):
        """원 격자에 한 번에 적용. 모순이면 **실측 높이(lo)가 이긴다**."""
        import numpy as np

        out = grid.astype(float).copy()
        m_hi = ~np.isnan(self.hi)
        if protect is not None:
            m_hi &= ~protect
        out[m_hi] = np.fmin(out[m_hi], self.hi[m_hi])
        m_lo = ~np.isnan(self.lo)
        if protect is not None:
            m_lo &= ~protect
        out[m_lo] = np.fmax(out[m_lo], self.lo[m_lo])   # lo를 나중에 → 모순 시 lo 우선
        return out


# --- 입력 -------------------------------------------------------------------


def clip_scarps(geojson_path, bbox_5186, offset) -> list[ScarpFeature]:
    """단차 GeoJSON을 bbox 클립 → 로컬 미터 `ScarpFeature` 목록.

    스키마: `{"k": "levee"|"cut"|"fill", "u": "top"|"bot", "h": 제방고|생략,
             "p": 짝 id|생략, "d": DIVI|생략}`. 사면은 `p`로 상·하단을 묶는다.
    """
    from shapely.geometry import box, shape

    from src.geometry.road import _iter_lines, _load_features

    clip = box(*bbox_5186)
    ox, oy = offset
    tops: dict = {}
    bots: dict = {}
    out: list[ScarpFeature] = []
    for f in _load_features(geojson_path):
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("LineString", "MultiLineString"):
            continue
        try:
            g = shape(geom)
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty or not g.intersects(clip):
            continue
        p = f.get("properties") or {}
        kind = str(p.get("k") or "")
        for ls in _iter_lines(g.intersection(clip)):
            pts = [(float(x) - ox, float(y) - oy) for x, y in ls.coords]
            if len(pts) < 2:
                continue
            if kind == "levee":
                try:
                    h = float(p.get("h"))
                except (TypeError, ValueError):
                    continue
                if h > 0:
                    out.append(ScarpFeature(kind="levee", top=pts, height_m=h))
            elif kind in ("cut", "fill"):
                key = (kind, p.get("p"), str(p.get("d") or ""))
                (tops if p.get("u") == "top" else bots).setdefault(key, []).append(pts)
    # 짝 맞추기 — 같은 짝 id의 상단·하단을 붙인다(클립으로 한쪽만 남으면 버린다).
    for key, tlist in tops.items():
        blist = bots.get(key)
        if not blist:
            continue
        kind, _pid, divi = key
        for t in tlist:
            out.append(ScarpFeature(kind=kind, top=t, bot=blist[0], divi=divi))
    return out


# --- 버닝 -------------------------------------------------------------------


def _densify(points, step: float):
    out = []
    for i in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        n = max(1, int(d / step))
        for k in range(n):
            t = k / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    out.append(tuple(points[-1]))
    return out


def _smooth(vals, win: int):
    """종단 이동평균. **가장자리는 끝값으로 패딩**한다.

    `np.convolve(..., mode="same")`은 양 끝을 **0으로** 패딩한다 — 그러면 선 끝부분의 기준
    지반고가 0 쪽으로 끌려가고, `z_base + 실측 제방고`가 실제 지면보다 낮아져 `raise_to`가
    아무 일도 하지 않는다(실측 2026-10-01: 마루 상승량이 도면고의 13%, p10은 0%였다).
    """
    import numpy as np

    a = np.asarray(vals, dtype=float)
    if win <= 1 or a.size < 3:
        return a
    w = min(win, a.size)
    pad = w // 2
    k = np.ones(w) / w
    sm = np.convolve(np.pad(a, pad, mode="edge"), k, mode="same")
    return sm[pad:pad + a.size]


def _sampler(grid, tf, offset):
    """원 격자에서 읽는 샘플러 — 누적 중에는 절대 수정된 배열을 읽지 않는다."""
    import math

    inv = ~tf
    ox, oy = offset
    rows, cols = grid.shape

    def sample(x, y):
        c, r = inv * (x + ox, y + oy)
        ci, ri = int(c), int(r)
        if 0 <= ri < rows and 0 <= ci < cols:
            v = float(grid[ri, ci])
            return None if math.isnan(v) else v
        return None

    return sample


def _cells_near(tf, offset, rows, cols, x, y, radius):
    """(x,y) 반경 안 셀의 (row, col, 셀중심 로컬좌표) — 작은 창만 훑는다."""
    inv = ~tf
    ox, oy = offset
    cell = abs(tf.a) or 1.0
    c, r = inv * (x + ox, y + oy)
    ci, ri = int(c), int(r)
    rad = max(1, int(radius / cell) + 1)
    for rr in range(max(0, ri - rad), min(rows, ri + rad + 1)):
        for cc in range(max(0, ci - rad), min(cols, ci + rad + 1)):
            px, py = tf * (cc + 0.5, rr + 0.5)
            yield rr, cc, px - ox, py - oy


def _burn_levee(f: ScarpFeature, tg: ZTargets, sample, tf, offset, shape) -> None:
    """마루 + 양측 사면을 **올리기만** 해서 만든다.

    마루선 위 DEM을 기준 지반고로 쓴다 — 실측이 근거다(마루 자리 DEM은 주변 지반과 2cm
    차이뿐이므로 `선 위 DEM + 실측 제방고`가 물리적으로 맞다). 종단 평활로 노이즈만 죽인다.
    """
    import numpy as np

    rows, cols = shape
    cell = abs(tf.a) or 1.0
    pts = _densify(f.top, SCARP_STEP_M)
    base = [sample(x, y) for x, y in pts]
    if all(v is None for v in base):
        return
    filled = [v for v in base if v is not None]
    med = float(np.median(filled))
    zb = _smooth([v if v is not None else med for v in base],
                 max(1, int(LEVEE_SMOOTH_WIN_M / SCARP_STEP_M)))
    h = float(f.height_m or 0.0)
    # 마루 반폭을 **격자 해상도에 맞춰 올린다**(옹벽 `burn_walls`와 같은 스케일링).
    # 셀 중심이 마루선에서 최대 반 셀(5m 격자면 2.5m) 벗어나면 사면 구간에 떨어져 목표가
    # 낮아진다 — 실측(2026-10-01): 그대로 두면 마루 상승량이 도면고의 75%에 머물렀다.
    # 모델 마루폭이 실제(3m)보다 넓어지는 대가로 **높이를 제대로 담는다**. 설계 검토에
    # 결정적인 것은 마루 폭이 아니라 레벨차다.
    half = max(LEVEE_CROWN_W_M / 2.0, 0.6 * cell)
    toe_r = max(half + LEVEE_SLOPE_RATIO * h, 1.2 * cell)
    for (x, y), z0 in zip(pts, zb):
        z_crown = z0 + h
        for rr, cc, px, py in _cells_near(tf, offset, rows, cols, x, y, toe_r):
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d > toe_r:
                continue
            target = z_crown if d <= half else z_crown - (d - half) / LEVEE_SLOPE_RATIO
            tg.raise_to(rr, cc, target)


def _burn_slope(f: ScarpFeature, tg: ZTargets, sample, tf, offset, shape) -> None:
    """상단선–하단선 사이를 직선 사면(ruled surface)으로 조인다.

    표고는 **선 바깥** 실측 지반에서 읽는다(선 위에서 읽으면 등고선이 뭉개 놓은 값을 그대로
    쓰게 된다). 하강 방향은 **짝에서** 얻는다 — DEM probe로 정하면 이미 뭉개진 DEM에 판정을
    맡기는 순환이 된다. `DIVI`에 따라 인공 평탄면 쪽은 멀리, 자연 지반 쪽은 가까이 읽는다.
    """
    import numpy as np
    from shapely.geometry import LineString, Point

    rows, cols = shape
    bot = LineString(f.bot)
    near_t, near_b = _PROBE.get(f.divi, (SCARP_PROBE_NEAR_M, SCARP_PROBE_NEAR_M))

    T, B, ZT, ZB = [], [], [], []
    for x, y in _densify(f.top, SCARP_STEP_M):
        q = bot.interpolate(bot.project(Point(x, y)))
        ux, uy = q.x - x, q.y - y
        L = (ux * ux + uy * uy) ** 0.5
        if L < 0.5:                       # 두 선이 맞닿는 끝부분 — 사면고 0
            continue
        ux, uy = ux / L, uy / L
        zt = sample(x - ux * near_t, y - uy * near_t)     # 상단 **바깥**(고지대 쪽)
        zb = sample(q.x + ux * near_b, q.y + uy * near_b)  # 하단 **바깥**(저지대 쪽)
        if zt is None or zb is None or zt - zb < SCARP_MIN_DROP_M:
            continue                      # 뒤집힌 짝·사면고 0 → 버린다(지형을 거꾸로 세우지 않게)
        T.append((x, y)); B.append((q.x, q.y)); ZT.append(zt); ZB.append(zb)
    if not T:
        return

    from scipy.spatial import cKDTree

    tree = cKDTree(np.asarray(T, dtype=float))
    Lm = [((b[0] - t[0]) ** 2 + (b[1] - t[1]) ** 2) ** 0.5 for t, b in zip(T, B)]
    reach = max(Lm) + max(near_t, near_b)
    for (x, y), L in zip(T, Lm):
        for rr, cc, px, py in _cells_near(tf, offset, rows, cols, x, y, reach):
            i = int(tree.query([px, py])[1])
            tx, ty = T[i]
            bx, by = B[i]
            li = Lm[i] or 1.0
            ux, uy = (bx - tx) / li, (by - ty) / li
            t = ((px - tx) * ux + (py - ty) * uy) / li     # 0=상단, 1=하단
            if t < 0:
                if -t * li <= near_t:
                    tg.raise_to(rr, cc, ZT[i] - SCARP_TOL_M)   # 고지대 쪽은 처지지 않게
            elif t > 1:
                if (t - 1) * li <= near_b:
                    tg.lower_to(rr, cc, ZB[i] + SCARP_TOL_M)   # 저지대 쪽은 뜨지 않게
            else:
                z = ZT[i] + (ZB[i] - ZT[i]) * t
                tg.raise_to(rr, cc, z - SCARP_TOL_M)
                tg.lower_to(rr, cc, z + SCARP_TOL_M)


def burn_scarps(dem, scarps: list[ScarpFeature], protect_footprints=None):
    """제방·절토/성토면을 DEM에 심는다. 새 DEMPatch 반환(원본 불변).

    건물 발자국은 보호한다 — 건물은 버닝 **전** 지면에 앉으므로, 지반을 내리면 뜨고
    (`wall.burn_walls`와 같은 이유) **올리면 묻힌다**. 절토/성토선은 정의상 조성 대지 경계를
    따라가 건물과 공존율이 높다.
    """
    import numpy as np

    grid = getattr(dem, "grid", None)
    if grid is None or grid.size == 0 or not scarps:
        return dem
    from src.terrain.dem import DEMPatch

    tf, shape = dem.transform, grid.shape
    sample = _sampler(grid, tf, dem.offset)
    tg = ZTargets(shape)
    for f in scarps:
        try:
            if f.kind == "levee":
                _burn_levee(f, tg, sample, tf, dem.offset, shape)
            elif f.bot:
                _burn_slope(f, tg, sample, tf, dem.offset, shape)
        except Exception:  # noqa: BLE001 — 한 피처 실패가 전체를 막지 않게
            continue

    protect = None
    if protect_footprints:
        from rasterio.features import rasterize
        from shapely.geometry import Polygon

        ox, oy = dem.offset
        polys = []
        for ring in protect_footprints:
            if len(ring) < 3:
                continue
            p = Polygon([(x + ox, y + oy) for x, y in ring])
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                polys.append((p, 1))
        if polys:
            protect = rasterize(polys, out_shape=shape, transform=tf, fill=0,
                                all_touched=True).astype(bool)

    new = tg.apply(grid, protect=protect)
    new[np.isnan(grid.astype(float))] = np.nan       # nodata 구멍은 메우지 않는다
    return DEMPatch(grid=new.astype(np.float32), transform=tf, offset=dem.offset)


def scarps_to_geometry(scarps: list[ScarpFeature], dem) -> list[dict]:
    """뷰어·.3dm용 단차선(지형 드레이프) — [{"kind","points":[[x,y,z]],"h"}]."""
    out = []
    for f in scarps:
        pts = [[round(x, 2), round(y, 2),
                round(float(dem.elev_at(x, y)) if dem is not None else 0.0, 2)]
               for x, y in f.top]
        out.append({"kind": f.kind, "points": pts,
                    "h": round(f.height_m, 2) if f.height_m else None})
    return out
