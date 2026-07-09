"""일조·그림자 분석 (로드맵 B-3) — 순수 계산, 외부 API 불필요.

위경도+일시로 태양 고도·방위를 계산하고(`sun_position`), 건물 footprint×높이를 태양 반대
방향으로 투영해 지면 그림자 폴리곤을 만든다(`building_shadow`). `shadows_for_day`는 하루
시간대별 그림자(모든 건물 합집합)를 로컬 미터 폴리곤으로 반환 → geometry와 동일 좌표라
뷰어/확장이 그대로 오버레이. 스위트 미소유 영역(Autodesk Forma의 일조/그림자에 대응).

좌표: 로컬 미터(x≈동, y≈북). 방위는 진북 기준 시계방향(E=90, S=180, W=270). 격자북≠진북
(자오선 수렴)은 근린 규모에서 무시. 그림자 지면은 각 건물 base 평면(평지 가정).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


def _julian_day(dt: datetime) -> float:
    """그레고리력 datetime → 율리우스일(UT). naive는 UTC로 간주."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    y, m = dt.year, dt.month
    day = dt.day + (dt.hour + dt.minute / 60 + dt.second / 3600) / 24
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + day + b - 1524.5


def sun_position(lat_deg: float, lon_deg: float, dt: datetime) -> tuple[float, float]:
    """(고도°, 방위°). 방위는 진북 기준 시계방향(E=90, S=180, W=270). 고도<0 = 야간.

    저차 천문식(태양 겉보기 위치, 정확도 ~0.01°) — 일조/그림자 스터디에 충분.
    """
    n = _julian_day(dt) - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360                      # 평균 황경
    g = math.radians((357.528 + 0.9856003 * n) % 360)        # 평균 근점이각
    lam = math.radians((L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)  # 황경
    eps = math.radians(23.439 - 4e-7 * n)                    # 황도경사
    decl = math.asin(math.sin(eps) * math.sin(lam))          # 적위
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))  # 적경
    gmst = (280.46061837 + 360.98564736629 * n) % 360        # 그리니치 항성시
    lmst = math.radians((gmst + lon_deg) % 360)
    ha = lmst - ra                                           # 시각(hour angle)
    latr = math.radians(lat_deg)
    alt = math.asin(math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(ha))
    az_south = math.atan2(math.sin(ha), math.cos(ha) * math.sin(latr) - math.tan(decl) * math.cos(latr))
    return math.degrees(alt), (math.degrees(az_south) + 180.0) % 360.0


def shadow_offset(height_m: float, alt_deg: float, az_deg: float) -> tuple[float, float] | None:
    """건물 상단이 지면에 지는 그림자 변위(동, 북 미터). 태양이 지평 근처/아래면 None."""
    if alt_deg <= 1.0:                                       # 지평 근처: 그림자 무한대 → 생략
        return None
    length = height_m / math.tan(math.radians(alt_deg))
    az = math.radians(az_deg)
    return (-math.sin(az) * length, -math.cos(az) * length)  # 태양 반대방향


def building_shadow(footprint_m, height_m, alt_deg, az_deg):
    """단일 건물(수직 돌출)의 지면 그림자 폴리곤(shapely). 야간/무효면 None.

    그림자 = footprint ⊕ [0, offset] (민코프스키 합) = footprint ∪ (이동 footprint) ∪ 모서리 스윕.
    오목 footprint도 정확. 지면은 평지 가정.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    off = shadow_offset(height_m, alt_deg, az_deg)
    if off is None or len(footprint_m) < 3:
        return None
    dx, dy = off
    base = Polygon(footprint_m)
    if not base.is_valid or base.area <= 0:
        return None
    parts = [base, Polygon([(x + dx, y + dy) for x, y in footprint_m])]
    n = len(footprint_m)
    for i in range(n):
        x0, y0 = footprint_m[i]
        x1, y1 = footprint_m[(i + 1) % n]
        quad = Polygon([(x0, y0), (x1, y1), (x1 + dx, y1 + dy), (x0 + dx, y0 + dy)])
        if quad.is_valid and quad.area > 0:
            parts.append(quad)
    return unary_union(parts)


def _polys_to_rings(geom):
    """Polygon/MultiPolygon/Collection → 외곽 링 목록([[x,y],...], 홀 무시, 2자리 반올림)."""
    rings = []
    for g in getattr(geom, "geoms", [geom]):
        if g.is_empty or g.geom_type != "Polygon":
            continue
        rings.append([[round(x, 2), round(y, 2)] for x, y in g.exterior.coords])
    return rings


def shadows_for_day(solids, lat_deg, lon_deg, on_date, hours, tz_offset_h: float = 9.0) -> list[dict]:
    """하루 시간대별 그림자(모든 건물 합집합) → [{time, sun_alt, sun_az, polygons}]. 로컬 미터.

    on_date: datetime.date. hours: 현지시 정수 목록. tz_offset_h: 현지시−UTC(한국 +9).
    polygons: 폴리곤 외곽 링 목록(야간/무그림자면 빈 목록).
    """
    from shapely.ops import unary_union

    out: list[dict] = []
    for h in hours:
        dt_local = datetime(on_date.year, on_date.month, on_date.day, int(h), 0, 0)
        dt_utc = dt_local - timedelta(hours=tz_offset_h)     # naive = UTC
        alt, az = sun_position(lat_deg, lon_deg, dt_utc)
        entry = {"time": f"{int(h):02d}:00", "sun_alt": round(alt, 2),
                 "sun_az": round(az, 2), "polygons": []}
        if alt > 1.0:
            shads = [s for s in
                     (building_shadow(b.footprint_m, b.height_m, alt, az) for b in solids)
                     if s is not None]
            if shads:
                entry["polygons"] = _polys_to_rings(unary_union(shads))
        out.append(entry)
    return out
