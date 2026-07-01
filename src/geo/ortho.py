"""정사영상 타일 소스 — Web Mercator(EPSG:3857) 슬리피맵 타일 수학 + 프로바이더 설정.

정사영상(위성/항공)은 국내 공공 소스가 모두 **WMTS 타일 피라미드**로 제공된다
(단일 bbox 이미지 API는 정사영상 레이어엔 없음). 타일은 Web Mercator 격자라
좌표계는 EPSG:3857 고정, 우리 DEM/TIN(EPSG:5186)과는 다르므로 다운로드 후
모자이크→UV 매핑 단계에서 정합한다(그 단계는 별도, 여기는 타일 산출까지만).

프로바이더별 URL 형식이 달라 `TileSource`로 추상화한다:
- **NGII 영상지도 WMTS** (data.go.kr 15059358): 공공누리 1유형(출처표시 시 상업 허용).
  우리 DEM과 동일 기관 → 1순위. 정확한 layer/tileMatrixSet은 GetCapabilities로 확정 필요.
- **VWorld Satellite WMTS**: REST `/{z}/{y}/{x}` (row 먼저 주의), EPSG:3857, 상업 이용 제한 정황.

좌표 규약: (lon, lat) = (경도, 위도). 타일 인덱스는 좌상단 원점(표준 XYZ/구글 호환).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Web Mercator 유효 위도 한계(±85.0511°) — 이 밖은 타일 격자에 표현 불가.
_MAX_LAT = 85.05112878

# Web Mercator(EPSG:3857) 원점 이동량 = π·R. 타일 격자의 절반 폭(m).
_ORIGIN_SHIFT = math.pi * 6378137.0


def deg2tile(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """경위도(EPSG:4326) → 타일 좌표(부동소수, 좌상단 원점).

    정수부가 타일 인덱스, 소수부가 타일 내 위치. 표준 슬리피맵 공식.
    """
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"lon out of range: {lon}")
    lat = max(-_MAX_LAT, min(_MAX_LAT, lat))  # 극지방 클램프(격자 밖 방지)
    n = 2.0**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return (x, y)


def tile2deg(x: float, y: float, zoom: int) -> tuple[float, float]:
    """타일 좌표(부동소수) → 경위도(EPSG:4326). 타일 좌상단 코너의 (lon, lat).

    `deg2tile`의 역변환. 정수 (x, y)를 주면 그 타일의 북서(NW) 코너를 반환한다.
    """
    n = 2.0**zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return (lon, lat)


def tiles_for_bbox(
    bbox: tuple[float, float, float, float], zoom: int
) -> tuple[int, int, int, int]:
    """bbox(minlon, minlat, maxlon, maxlat) → 포함 타일 인덱스 범위.

    반환 (x_min, y_min, x_max, y_max) — 모두 포함(inclusive) 정수 인덱스.
    위도는 북쪽이 y 작은 값이므로 maxlat이 y_min에 대응한다.
    """
    minlon, minlat, maxlon, maxlat = bbox
    if minlon > maxlon or minlat > maxlat:
        raise ValueError(f"invalid bbox: {bbox}")
    x0, y0 = deg2tile(minlon, maxlat, zoom)  # 좌상단(북서)
    x1, y1 = deg2tile(maxlon, minlat, zoom)  # 우하단(남동)
    n = int(2.0**zoom)
    x_min = max(0, int(math.floor(x0)))
    y_min = max(0, int(math.floor(y0)))
    x_max = min(n - 1, int(math.floor(x1)))
    y_max = min(n - 1, int(math.floor(y1)))
    return (x_min, y_min, x_max, y_max)


def tile_extent_4326(
    x_min: int, y_min: int, x_max: int, y_max: int, zoom: int
) -> tuple[float, float, float, float]:
    """타일 인덱스 범위 → 그 타일 묶음이 덮는 실제 bbox(EPSG:4326).

    모자이크 이미지의 지리 범위. UV 매핑 시 이 범위를 기준으로 정규화한다.
    반환 (minlon, minlat, maxlon, maxlat).
    """
    # 좌상단 타일의 NW 코너, 우하단 타일의 SE 코너(=다음 타일의 NW).
    nw_lon, nw_lat = tile2deg(x_min, y_min, zoom)
    se_lon, se_lat = tile2deg(x_max + 1, y_max + 1, zoom)
    return (nw_lon, se_lat, se_lon, nw_lat)


def mosaic_bounds_3857(
    x_min: int, y_min: int, x_max: int, y_max: int, zoom: int
) -> tuple[float, float, float, float]:
    """타일 인덱스 범위 → 모자이크의 EPSG:3857(Web Mercator) 미터 bbox.

    타일을 붙인 이미지에 지리참조를 부여하기 위한 값(재투영 전 단계).
    반환 (minx, miny, maxx, maxy) 미터.
    """
    span = 2.0 * _ORIGIN_SHIFT / (2.0**zoom)  # 타일 1장의 변 길이(m)
    minx = -_ORIGIN_SHIFT + x_min * span
    maxx = -_ORIGIN_SHIFT + (x_max + 1) * span
    maxy = _ORIGIN_SHIFT - y_min * span       # y는 위가 큰 값
    miny = _ORIGIN_SHIFT - (y_max + 1) * span
    return (minx, miny, maxx, maxy)


@dataclass(frozen=True)
class TileSource:
    """정사영상 타일 프로바이더 설정.

    url_template의 치환 필드: `{key}` `{z}` `{x}` `{y}` `{matrixset}` `{layer}`.
    프로바이더마다 순서/이름이 달라 템플릿 문자열로 흡수한다(예: VWorld는 `/{z}/{y}/{x}`).
    """

    name: str
    url_template: str
    layer: str = ""
    matrixset: str = ""
    crs: str = "EPSG:3857"  # 타일 격자 좌표계(Web Mercator 고정)
    tile_size: int = 256
    image_ext: str = "png"
    license_note: str = ""

    def tile_url(self, zoom: int, x: int, y: int, key: str) -> str:
        """단일 타일 URL. key가 필요 없는 프리셋은 빈 문자열 허용."""
        return self.url_template.format(
            key=key, z=zoom, x=x, y=y, layer=self.layer, matrixset=self.matrixset
        )


# --- 프로바이더 프리셋 ---------------------------------------------------------
# 주의: 두 템플릿 모두 정확한 엔드포인트/레이어명은 각 사의 GetCapabilities 또는
# 발급 키 문서로 최종 확정해야 한다(키 없이는 실호출 검증 불가). 아래는 조사로
# 확인된 형태에 맞춘 초기값이며, 실연동 시 검증 후 고정할 것.

# VWorld Satellite: REST, row(y) 먼저. tileType=jpeg. EPSG:3857, zoom 6~19.
VWORLD_SATELLITE = TileSource(
    name="vworld-satellite",
    url_template="https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{y}/{x}.jpeg",
    layer="Satellite",
    matrixset="GoogleMapsCompatible",
    image_ext="jpeg",
    license_note="상업 이용 제한 정황 — 이용약관 원문 재확인 필요.",
)

# NGII 영상지도 WMTS (data.go.kr 15059358). layer/matrixset은 확정 전 자리표시.
# 공공누리 1유형(출처표시 시 상업·변형 허용). 우리 DEM과 동일 기관 → 1순위.
NGII_AERIAL = TileSource(
    name="ngii-aerial",
    url_template="https://api.vworld.kr/req/wmts/1.0.0/{key}/{layer}/{z}/{y}/{x}.png",
    layer="Satellite",  # TODO: GetCapabilities로 실제 영상지도 레이어명 확정
    matrixset="GoogleMapsCompatible",
    license_note="공공누리 1유형(출처표시 시 상업 허용). '제3자 권리 포함' 여부 확인 필요.",
)


# --- 모자이크(다운로드 → 스티칭 → 재투영 → PNG) ---------------------------------

# 폭주 방지: 한 번에 붙일 타일 수 상한(256타일 = 4096²px 규모).
_MAX_TILES = 256


@dataclass(frozen=True)
class Mosaic:
    """정사영상 모자이크 결과.

    image_path: 저장된 PNG 절대 경로.
    bounds: 이미지가 덮는 실제 범위 (minx, miny, maxx, maxy), `crs` 좌표계 meter.
      UV 매핑 시 이 범위로 정규화한다. 우리 파이프라인은 EPSG:5186으로 받아
      origin_offset을 빼 로컬 범위로 만든다.
    crs: bounds의 좌표계 (기본 EPSG:5186).
    missing_tiles: 다운로드 실패로 회색 채운 타일 수(품질 경고용).
    zoom: 사용한 줌 레벨.
    """

    image_path: str
    bounds: tuple[float, float, float, float]
    crs: str
    missing_tiles: int
    zoom: int


def _default_fetch(url: str) -> bytes | None:
    """기본 타일 페처 — requests GET. 실패 시 None(모자이크에서 회색 처리)."""
    import requests

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException:
        return None


def _write_png_rgb(path: str | Path, rgb) -> None:
    """(H, W, 3) uint8 배열 → PNG 파일. numpy로 스캔라인 조립(가속).

    GDAL PNG 드라이버는 Create 미지원(rasterio 쓰기 불가)이라 순수 stdlib로 쓴다.
    """
    import struct
    import zlib

    import numpy as np

    h, w, _ = rgb.shape
    raw = np.zeros((h, 1 + w * 3), dtype=np.uint8)  # 각 행 앞에 필터바이트 0
    raw[:, 1:] = rgb.reshape(h, w * 3)

    def _chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(raw.tobytes(), 6)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", idat))
        f.write(_chunk(b"IEND", b""))


def build_mosaic(
    bbox: tuple[float, float, float, float],
    zoom: int,
    source: TileSource,
    key: str,
    out_path: str | Path,
    fetch: Callable[[str], bytes | None] | None = None,
    dst_crs: str = "EPSG:5186",
) -> Mosaic:
    """bbox(EPSG:4326) 정사영상 타일 → 재투영 모자이크 PNG.

    타일은 Web Mercator(3857)이라 위도에 따라 남북 스케일이 늘어난다
    (37.5°에서 ~1.26배). dst_crs(기본 5186)로 재투영해 왜곡을 제거한다.
    fetch는 테스트를 위해 주입 가능(기본은 requests).
    """
    import numpy as np
    import rasterio  # noqa: F401  (GDAL 환경 로드)
    from rasterio.io import MemoryFile
    from rasterio.transform import array_bounds, from_bounds
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    if fetch is None:
        fetch = _default_fetch

    x_min, y_min, x_max, y_max = tiles_for_bbox(bbox, zoom)
    nx = x_max - x_min + 1
    ny = y_max - y_min + 1
    if nx * ny > _MAX_TILES:
        raise ValueError(
            f"타일 {nx * ny}장이 상한 {_MAX_TILES} 초과 — zoom을 낮추거나 반경을 줄이세요."
        )
    ts = source.tile_size

    # 3857 격자에 타일을 붙임(회색=128로 초기화 → 결측 타일 자연 처리)
    mosaic = np.full((3, ny * ts, nx * ts), 128, dtype=np.uint8)
    missing = 0
    for ty in range(y_min, y_max + 1):
        for tx in range(x_min, x_max + 1):
            data = fetch(source.tile_url(zoom, tx, ty, key))
            row = (ty - y_min) * ts
            col = (tx - x_min) * ts
            if not data:
                missing += 1
                continue
            with MemoryFile(data) as mf, mf.open() as ds:
                arr = ds.read()  # (bands, ts, ts)
            if arr.shape[0] == 1:
                arr = np.repeat(arr, 3, axis=0)  # 흑백 → RGB
            elif arr.shape[0] >= 3:
                arr = arr[:3]                     # RGBA → RGB
            mosaic[:, row : row + ts, col : col + ts] = arr[:, :ts, :ts]

    # 3857 지리참조 → dst_crs로 재투영
    b3857 = mosaic_bounds_3857(x_min, y_min, x_max, y_max, zoom)
    H, W = ny * ts, nx * ts
    src_tr = from_bounds(*b3857, W, H)
    dtr, dw, dh = calculate_default_transform("EPSG:3857", dst_crs, W, H, *b3857)
    dst = np.full((3, dh, dw), 128, dtype=np.uint8)
    for b in range(3):
        reproject(
            source=mosaic[b],
            destination=dst[b],
            src_transform=src_tr,
            src_crs="EPSG:3857",
            dst_transform=dtr,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )
    minx, miny, maxx, maxy = array_bounds(dh, dw, dtr)

    rgb = np.transpose(dst, (1, 2, 0))  # (H, W, 3)
    _write_png_rgb(out_path, rgb)
    return Mosaic(
        image_path=str(Path(out_path).resolve()),
        bounds=(minx, miny, maxx, maxy),
        crs=dst_crs,
        missing_tiles=missing,
        zoom=zoom,
    )
