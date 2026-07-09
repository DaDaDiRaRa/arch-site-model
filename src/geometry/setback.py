"""정북일조 사선 봉투(buildable envelope) 작도 (로드맵 B-1', 재설계).

건축법 시행령 §86 ① 정북일조 사선제한을 대상 대지(subject parcel)에 **기하로 작도**한다 —
북측 대지경계에서 거리 d에 따른 최대 높이 h_max(d)의 사선면(참고용 봉투). *법적 판정*(특정
설계안 pass/fail)은 arch-law-diagnose 소유이며 여기서 하지 않는다(경계 유지). 규칙 값은
arch-law-diagnose(height.py)와 동일하게 맞춘다 — 법 해석은 법령 클러스터가 단일 소스.

좌표: 로컬 미터(x≈동, y≈북, geometry 동일). 진북은 EPSG:5186 격자북과 다르므로(자오선 수렴)
`true_north_local`로 실제 진북 벡터를 구해 사선 방향에 쓴다. 봉투는 주거지역에만 적용(zone 확인은 호출측).
"""

from __future__ import annotations

import math

from src.geo.crs import to_5186

# 건축법 시행령 §86 ① — arch-law-diagnose height.py와 동일 값(법 해석 단일 소스).
SETBACK_THRESHOLD_M = 10.0   # 이 높이 이하 부분은 기본 이격, 초과 부분은 높이의 1/2 이격
SETBACK_BASE_M = 1.5         # 기본 이격거리(m). > threshold 부분의 봉투 사선은 h = 2·d


def true_north_local(lon: float, lat: float) -> tuple[float, float]:
    """사이트(lon,lat)의 진북 단위벡터를 로컬 5186 격자 좌표로(offset 무관 — 방향만).

    진북 = 위도만 미세 증가시킨 점의 5186 방향. 격자북(+Y)과의 차이 = 자오선 수렴각.
    """
    x0, y0 = to_5186(lon, lat)
    x1, y1 = to_5186(lon, lat + 0.0015)   # 약 165m 북(진북)
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    if n == 0:
        return (0.0, 1.0)
    return (dx / n, dy / n)


def north_azimuth_deg(north_dir: tuple[float, float]) -> float:
    """진북 벡터의 격자 기준 방위(도). 0=격자북(+Y)과 일치, 부호=수렴각 방향."""
    nx, ny = north_dir
    return math.degrees(math.atan2(nx, ny))


def find_subject_parcel(parcels, point_xy):
    """point_xy(로컬 미터)를 포함하는 지적 필지를 subject로 반환. 없으면 1m 내 최근접, 그래도 없으면 None."""
    if not parcels:
        return None
    try:
        from shapely.geometry import Point, Polygon
    except Exception:  # noqa: BLE001
        return None
    pt = Point(point_xy)
    near = None
    for p in parcels:
        ring = getattr(p, "footprint_m", None)
        if not ring or len(ring) < 3:
            continue
        try:
            poly = Polygon(ring)
        except Exception:  # noqa: BLE001
            continue
        if not poly.is_valid:
            continue
        if poly.contains(pt):
            return p
        if near is None and poly.distance(pt) < 1.0:  # 경계 위/근접 대비
            near = p
    return near


def setback_max_height(d: float,
                       threshold_m: float = SETBACK_THRESHOLD_M,
                       base_m: float = SETBACK_BASE_M) -> float:
    """북측 경계에서 거리 d(m)에 허용되는 최대 높이(정북일조 봉투 단면)."""
    if d < base_m:
        return 0.0
    if d <= threshold_m / 2.0:
        return threshold_m
    return 2.0 * d


def build_setback_envelope(footprint_m, north_dir, cell_m: float = 1.5,
                           threshold_m: float = SETBACK_THRESHOLD_M,
                           base_m: float = SETBACK_BASE_M):
    """대상 필지 위 정북일조 봉투 상단면 메시 → {"vertices":[[x,y,z]], "triangles":[[a,b,c]]}.

    북측 기준선 = 필지 정점의 진북 투영 최댓값(북단 접선). 내부 점 거리
    d = max_proj − (점·진북) → z = setback_max_height(d). 필지 내부 격자 셀만 삼각화.
    필지 무효/샘플 부족 시 None.
    """
    try:
        from shapely.geometry import Point, Polygon
    except Exception:  # noqa: BLE001
        return None
    if not footprint_m or len(footprint_m) < 3:
        return None
    poly = Polygon(footprint_m)
    if not poly.is_valid or poly.area <= 0:
        return None
    nx, ny = north_dir
    max_proj = max(x * nx + y * ny for x, y in footprint_m)
    minx, miny, maxx, maxy = poly.bounds
    cols = max(1, int((maxx - minx) / cell_m) + 1)
    rows = max(1, int((maxy - miny) / cell_m) + 1)

    idx: dict[tuple[int, int], int] = {}
    verts: list[list[float]] = []
    for r in range(rows + 1):
        for c in range(cols + 1):
            x = minx + c * cell_m
            y = miny + r * cell_m
            if not poly.contains(Point(x, y)):
                continue
            d = max_proj - (x * nx + y * ny)
            z = setback_max_height(d, threshold_m, base_m)
            idx[(r, c)] = len(verts)
            verts.append([round(x, 2), round(y, 2), round(z, 2)])

    tris: list[list[int]] = []
    for r in range(rows):
        for c in range(cols):
            a = idx.get((r, c))
            b = idx.get((r, c + 1))
            cc = idx.get((r + 1, c))
            dd = idx.get((r + 1, c + 1))
            if None not in (a, b, cc, dd):
                tris.append([a, b, cc])
                tris.append([b, dd, cc])
    if not tris:
        return None
    return {"vertices": verts, "triangles": tris}
