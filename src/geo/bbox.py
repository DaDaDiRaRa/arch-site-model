"""점 + 반경 → BBOX (EPSG:4326) 및 VWorld geomFilter 문자열.

VWorld data API의 geomFilter는 EPSG:4326 BOX(minx,miny,maxx,maxy)를 받는다(사양서 §3.2).
반경(m)을 위경도 차분으로 환산: 위도는 ~111320 m/도, 경도는 cos(위도) 보정.
반경 수백 m 규모에서 충분히 정확하다.
"""

import math

_M_PER_DEG_LAT = 111_320.0  # 위도 1도당 거리 (근사)


def bbox_from_point(
    lon: float, lat: float, radius_m: float
) -> tuple[float, float, float, float]:
    """중심점(EPSG:4326) + 반경(m) → (minx, miny, maxx, maxy) 정사각 bbox."""
    if radius_m <= 0:
        raise ValueError(f"radius_m must be > 0, got {radius_m}")
    dlat = radius_m / _M_PER_DEG_LAT
    dlon = radius_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def to_geomfilter_box(bbox: tuple[float, float, float, float]) -> str:
    """bbox → VWorld geomFilter 문자열 "BOX(minx,miny,maxx,maxy)"."""
    minx, miny, maxx, maxy = bbox
    return f"BOX({minx},{miny},{maxx},{maxy})"
