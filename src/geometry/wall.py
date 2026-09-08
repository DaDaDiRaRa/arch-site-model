"""옹벽 런타임 지오메트리 — DEM에 **수직 단차**를 심는다.

등고선으로 만든 DEM은 옹벽 자리를 완만한 비탈로 뭉갠다. 5m 격자에 2m 옹벽이면 등고선이 그 자리를
안 지나거나 한 줄만 스쳐서, 실제로는 뚝 떨어지는 레벨차가 비스듬하게 나온다. 수치지형도
F0040000이 주는 **상단선 + 실측 높이**로 그 단차를 되살린다(wall_bake 참조).

방식은 "만들어내기"가 아니라 **조이기(clamp)**다. 옹벽 선 양옆 DEM을 실제로 읽어 어느 쪽이 위인지
정하고, 좁은 복도 안에서만
    윗면 z >= (상단 표고) - tol,   아랫면 z <= (상단 표고) - 높이
로 눌러 경사를 단차로 세운다. 없는 지형을 지어내지 않고 이미 있는 값의 범위를 좁히는 것이라
등고선이 이미 정확한 곳에서는 거의 변화가 없다.

도로 버닝(road.burn_roads)과 같은 자리(지형 확정 직후, TIN 만들기 전)에서 돈다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 단차를 세우는 복도 반폭(m). 이 밖은 원래 지형 그대로 둔다.
WALL_CORRIDOR_M = 3.0
# 위/아래 판정을 위해 선 양옆으로 떨어져 DEM을 읽는 거리(m). 멀수록 판정이 안정적.
WALL_PROBE_M = 4.0
# 상단 표고를 읽는 거리(m). 옹벽 상단 지반은 벽 **바로 뒤**라 가까이서 읽는다.
# (판정용 probe 거리로 읽으면 경사지에서 상단을 실제보다 높게 잡는다.)
WALL_TOP_NEAR_M = 1.5
# 상단면 허용 오차(m) — 이보다 낮은 윗면만 끌어올린다(미세 요철은 건드리지 않음).
WALL_TOP_TOL_M = 0.3
# 선을 따라 샘플하는 간격(m).
WALL_STEP_M = 2.0


@dataclass
class WallFeature:
    """옹벽 상단선 하나. 좌표=로컬 미터(offset 적용), height_m=실측 높이."""

    points: list[tuple[float, float]]
    height_m: float


def clip_walls(geojson_path, bbox_5186, offset) -> list[WallFeature]:
    """지역 GeoJSON 옹벽 선을 bbox로 클립 → 로컬 미터 WallFeature.

    geojson_path는 단일 경로 또는 겹치는 타일 경로 리스트(도로·수계와 동일).
    """
    from shapely.geometry import box, shape

    from src.geometry.road import _load_features

    feats = _load_features(geojson_path)
    if not feats:
        return []
    clip = box(*bbox_5186)
    ox, oy = offset
    out: list[WallFeature] = []
    for f in feats:
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("LineString", "MultiLineString"):
            continue
        try:
            h = float((f.get("properties") or {}).get("h", 0.0))
        except (TypeError, ValueError):
            continue
        if h <= 0:
            continue
        try:
            g = shape(geom)
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty or not g.intersects(clip):
            continue
        piece = g.intersection(clip)
        parts = piece.geoms if hasattr(piece, "geoms") else [piece]
        for p in parts:
            if p.is_empty or p.geom_type != "LineString":
                continue
            pts = [(x - ox, y - oy) for x, y, *_ in p.coords]
            if len(pts) >= 2:
                out.append(WallFeature(points=pts, height_m=h))
    return out


def _densify(points: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    """선을 step 간격 이하로 조밀화(단차를 셀마다 걸기 위해)."""
    if step <= 0 or len(points) < 2:
        return points
    out: list[tuple[float, float]] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        out.append((x0, y0))
        d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        n = int(d // step)
        for i in range(1, n + 1):
            t = i / (n + 1)
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    out.append(points[-1])
    return out


def burn_walls(dem, walls: list[WallFeature],
               corridor_m: float = WALL_CORRIDOR_M,
               probe_m: float = WALL_PROBE_M,
               protect_footprints=None):
    """옹벽 선을 따라 DEM을 **단차로 조인다**. 새 DEMPatch 반환(원본 불변).

    선의 각 샘플점에서 법선 방향 ±probe_m 지점의 DEM을 읽어 위/아래를 정하고, 복도 안의 셀을
    위/아래 소속으로 나눠 상단 표고 z_top 기준으로 클램프한다. 옹벽 없거나 DEM 없으면 원본 그대로.

    protect_footprints: 건물 footprint(로컬 미터 링) 목록. **그 안의 셀은 건드리지 않는다.**
    파이프라인은 건물을 버닝 전 지면에 앉히므로(설계상 도로 버닝의 영향을 안 받게), 옹벽이 건물
    아래 지면을 내리면 건물이 허공에 뜬다(QA building_float). 옹벽은 대개 건물 경계선을 따라가
    실제로 이 일이 생긴다. 건물이 올라앉은 platform은 원래 표고 그대로 두는 게 물리적으로도 맞다.
    """
    grid = getattr(dem, "grid", None)
    if grid is None or grid.size == 0 or not walls:
        return dem

    import numpy as np

    from src.terrain.dem import DEMPatch

    rows, cols = grid.shape
    tf = dem.transform
    inv = ~tf
    ox, oy = dem.offset
    new = grid.astype(float).copy()
    cell = abs(tf.a) or 1.0
    # 파라미터를 **격자 해상도에 맞춰 스케일**한다. 5m 격자에 3m 복도를 쓰면 셀 하나도 못 덮어
    # 단차가 거의 안 선다(실측: 바뀐 셀 0.5%). 거리들은 최소 셀 크기 기준으로 끌어올린다.
    corridor = max(corridor_m, 1.2 * cell)
    probe = max(probe_m, 1.5 * cell)
    near = max(WALL_TOP_NEAR_M, 0.6 * cell)
    side_eps = 0.25 * cell            # 선에 걸친 셀은 어느 쪽도 아닌 것으로 둔다
    rad = max(1, int(round(corridor / cell)) + 1)

    protect = None
    if protect_footprints:
        from rasterio.features import rasterize
        from shapely.geometry import Polygon

        polys = []
        for ring in protect_footprints:
            if ring is None or len(ring) < 3:
                continue
            try:
                poly = Polygon([(x + ox, y + oy) for x, y in ring])
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not poly.is_empty:
                    polys.append((poly, 1))
            except Exception:  # noqa: BLE001
                continue
        if polys:
            protect = rasterize(polys, out_shape=(rows, cols), transform=tf, fill=0,
                                all_touched=True).astype(bool)

    def sample(xl: float, yl: float):
        c, r = inv * (xl + ox, yl + oy)
        ci, ri = int(round(c - 0.5)), int(round(r - 0.5))
        if 0 <= ri < rows and 0 <= ci < cols:
            v = new[ri, ci]
            return None if np.isnan(v) else float(v)
        return None

    for w in walls:
        pts = _densify(w.points, WALL_STEP_M)
        for i, (x, y) in enumerate(pts):
            # 진행 방향 → 법선
            j = i + 1 if i + 1 < len(pts) else i - 1
            if j < 0:
                continue
            dx, dy = pts[j][0] - x, pts[j][1] - y
            if i + 1 >= len(pts):
                dx, dy = -dx, -dy
            n = (dx * dx + dy * dy) ** 0.5
            if n == 0:
                continue
            nx, ny = -dy / n, dx / n            # 좌법선

            za = sample(x + nx * probe, y + ny * probe)
            zb = sample(x - nx * probe, y - ny * probe)
            if za is None or zb is None or abs(za - zb) < 1e-6:
                continue
            # 높은 쪽이 옹벽 위. 상단 표고는 그쪽 지반.
            hi_sign = 1.0 if za > zb else -1.0
            # 상단 표고는 벽 바로 뒤(가까이)에서. 없으면 판정용 probe 값으로 폴백.
            z_near = sample(x + nx * near * hi_sign, y + ny * near * hi_sign)
            z_top = z_near if z_near is not None else max(za, zb)
            z_bot = z_top - w.height_m

            c, r = inv * (x + ox, y + oy)
            ci, ri = int(round(c - 0.5)), int(round(r - 0.5))
            for rr in range(max(0, ri - rad), min(rows, ri + rad + 1)):
                for cc in range(max(0, ci - rad), min(cols, ci + rad + 1)):
                    v = new[rr, cc]
                    if np.isnan(v) or (protect is not None and protect[rr, cc]):
                        continue
                    px, py = tf * (cc + 0.5, rr + 0.5)
                    ux, uy = px - ox - x, py - oy - y
                    if (ux * ux + uy * uy) > corridor * corridor:
                        continue
                    side = (ux * nx + uy * ny) * hi_sign     # >0 = 윗면
                    if side > side_eps:                       # 윗면: 상단 아래로 처지지 않게
                        if v < z_top - WALL_TOP_TOL_M:
                            new[rr, cc] = z_top - WALL_TOP_TOL_M
                    elif side < -side_eps:                    # 아랫면: 하단 위로 뜨지 않게
                        if v > z_bot:
                            new[rr, cc] = z_bot
    return DEMPatch(grid=new.astype(np.float32), transform=tf, offset=dem.offset)


def walls_to_geometry(walls: list[WallFeature], dem) -> list[dict]:
    """뷰어·출력용 옹벽 선(상단 표고 드레이프) — [{"points":[[x,y,z]…],"h":높이}, …]."""
    out: list[dict] = []
    for w in walls:
        pts = []
        for x, y in w.points:
            z = dem.sample(x, y) if dem is not None else None
            pts.append([round(x, 2), round(y, 2), round(z if z is not None else 0.0, 2)])
        if len(pts) >= 2:
            out.append({"points": pts, "h": round(w.height_m, 2)})
    return out
