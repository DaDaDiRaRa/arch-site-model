"""좌표변환 유틸 — EPSG:4326(WGS84) ↔ EPSG:5186(중부원점) + 원점 오프셋.

EPSG:5186 (Korea 2000 / Central Belt 2010)은 좌표값이 100만 단위라
SketchUp 정밀도가 붕괴한다(사양서 §6.1). 작업영역 최소좌표를 빼서
원점을 (0,0) 근처로 이동하고, 그 offset을 출력에 저장해 실제 위치를 복원한다.

좌표 규약: 경위도는 (lon, lat) = (경도, 위도) 순서. pyproj Transformer를
`always_xy=True`로 생성해 항상 (x=동/경도, y=북/위도) 순서를 사용한다.
"""

from pyproj import Transformer

# 모듈 로드 시 1회 생성(재사용). always_xy=True → (lon,lat)/(x,y) 순서 고정.
_TO_5186 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
_TO_4326 = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)


def to_5186(lon: float, lat: float) -> tuple[float, float]:
    """경위도(EPSG:4326) → 평면직각(EPSG:5186, meter). 반환 (x, y)."""
    x, y = _TO_5186.transform(lon, lat)
    return (x, y)


def to_4326(x: float, y: float) -> tuple[float, float]:
    """평면직각(EPSG:5186) → 경위도(EPSG:4326). 반환 (lon, lat)."""
    lon, lat = _TO_4326.transform(x, y)
    return (lon, lat)


def origin_offset(coords_5186: list[tuple[float, float]]) -> tuple[float, float]:
    """좌표 목록의 최소 (x, y)를 원점 오프셋으로 반환.

    이 값을 모든 좌표에서 빼면 작업영역이 (0,0) 근처로 이동한다.
    출력에 반드시 저장해야 실제 위치/정사영상 정합을 복원할 수 있다(사양서 §6.1).
    """
    if not coords_5186:
        raise ValueError("origin_offset: 빈 좌표 목록")
    xs = [c[0] for c in coords_5186]
    ys = [c[1] for c in coords_5186]
    return (min(xs), min(ys))


def apply_offset(
    coords: list[tuple[float, float]], offset: tuple[float, float]
) -> list[tuple[float, float]]:
    """모든 좌표에서 offset을 빼 원점을 이동. 반환은 새 리스트."""
    ox, oy = offset
    return [(x - ox, y - oy) for (x, y) in coords]
