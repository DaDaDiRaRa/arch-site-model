"""ortho.py 타일 수학 + 프로바이더 설정 + 모자이크 테스트 — 네트워크 없음.

타일 수학 기준값은 표준 Web Mercator 슬리피맵 공식에서 해석적으로 유도.
모자이크는 mock 페처(합성 PNG 타일)로 다운로드를 대체한다(사양서 테스트 규칙).
"""

import struct
import zlib

import pytest

from src.geo.ortho import (
    NGII_AERIAL,
    VWORLD_SATELLITE,
    TileSource,
    build_mosaic,
    deg2tile,
    mosaic_bounds_3857,
    tile2deg,
    tile_extent_4326,
    tiles_for_bbox,
)


def _solid_png(size: int, rgb: tuple[int, int, int]) -> bytes:
    """단색 RGB PNG 바이트 생성(합성 타일). stdlib만 사용."""
    r, g, b = rgb
    raw = bytearray()
    for _ in range(size):
        raw.append(0)  # 필터바이트
        raw += bytes((r, g, b)) * size

    def _chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


class TestDeg2Tile:
    def test_world_center_zoom0(self):
        # z=0: 전 세계가 타일 1장, 중심(0,0)은 타일 중앙(0.5, 0.5)
        x, y = deg2tile(0.0, 0.0, 0)
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.5)

    def test_center_zoom1_is_grid_corner(self):
        # z=1: 중심(0,0)은 4타일 교차점(1.0, 1.0)
        x, y = deg2tile(0.0, 0.0, 1)
        assert x == pytest.approx(1.0)
        assert y == pytest.approx(1.0)

    def test_nw_corner_of_world(self):
        # 북서 극단(-180, +85.0511)은 원점(0, 0) 근처
        x, y = deg2tile(-180.0, 85.05112878, 3)
        assert x == pytest.approx(0.0, abs=1e-6)
        assert y == pytest.approx(0.0, abs=1e-4)

    def test_latitude_clamped_not_raised(self):
        # 위도 극단 초과는 예외가 아니라 클램프(격자 밖 방지)
        _, y_hi = deg2tile(0.0, 89.0, 2)
        _, y_lim = deg2tile(0.0, 85.05112878, 2)
        assert y_hi == pytest.approx(y_lim, abs=1e-6)

    def test_lon_out_of_range_raises(self):
        with pytest.raises(ValueError):
            deg2tile(200.0, 0.0, 2)


class TestTile2Deg:
    def test_nw_corner_zoom0(self):
        lon, lat = tile2deg(0, 0, 0)
        assert lon == pytest.approx(-180.0)
        assert lat == pytest.approx(85.05112878, abs=1e-5)

    def test_se_corner_zoom0(self):
        lon, lat = tile2deg(1, 1, 0)
        assert lon == pytest.approx(180.0)
        assert lat == pytest.approx(-85.05112878, abs=1e-5)

    def test_roundtrip(self):
        # deg2tile ∘ tile2deg 왕복 (여러 지점)
        for lon, lat, z in [(127.0, 37.5, 14), (0.0, 0.0, 5), (-122.3, 47.6, 12)]:
            tx, ty = deg2tile(lon, lat, z)
            rlon, rlat = tile2deg(tx, ty, z)
            assert rlon == pytest.approx(lon, abs=1e-6)
            assert rlat == pytest.approx(lat, abs=1e-6)


class TestTilesForBbox:
    def test_center_bbox_zoom1_covers_four_tiles(self):
        # (0,0) 둘레 작은 bbox는 z=1의 4타일 전부 포함
        assert tiles_for_bbox((-1.0, -1.0, 1.0, 1.0), 1) == (0, 0, 1, 1)

    def test_single_tile(self):
        # 한 타일 내부의 좁은 bbox → 단일 타일 범위
        x_min, y_min, x_max, y_max = tiles_for_bbox((0.1, 0.1, 0.2, 0.2), 5)
        assert x_min == x_max
        assert y_min == y_max

    def test_clamped_to_grid(self):
        # 전 세계 bbox at z=2 → 0..3 격자 전체
        assert tiles_for_bbox((-180.0, -85.0, 180.0, 85.0), 2) == (0, 0, 3, 3)

    def test_invalid_bbox_raises(self):
        with pytest.raises(ValueError):
            tiles_for_bbox((1.0, 0.0, -1.0, 1.0), 5)  # minlon > maxlon


class TestTileExtent:
    def test_whole_world_zoom1(self):
        ext = tile_extent_4326(0, 0, 1, 1, 1)
        minlon, minlat, maxlon, maxlat = ext
        assert minlon == pytest.approx(-180.0)
        assert maxlon == pytest.approx(180.0)
        assert maxlat == pytest.approx(85.05112878, abs=1e-5)
        assert minlat == pytest.approx(-85.05112878, abs=1e-5)

    def test_extent_contains_source_bbox(self):
        # 타일 범위 실제 extent는 요청 bbox를 감싸야 한다(타일 경계로 확장)
        bbox = (127.02, 37.49, 127.05, 37.52)
        z = 15
        x_min, y_min, x_max, y_max = tiles_for_bbox(bbox, z)
        minlon, minlat, maxlon, maxlat = tile_extent_4326(x_min, y_min, x_max, y_max, z)
        assert minlon <= bbox[0] and minlat <= bbox[1]
        assert maxlon >= bbox[2] and maxlat >= bbox[3]


class TestTileSource:
    def test_vworld_url_row_before_col(self):
        # VWorld REST: /{z}/{y}/{x} — row(y)가 col(x)보다 먼저
        url = VWORLD_SATELLITE.tile_url(14, x=111, y=222, key="KEY123")
        assert url.endswith("/14/222/111.jpeg")
        assert "KEY123" in url

    def test_ngii_layer_substituted(self):
        url = NGII_AERIAL.tile_url(10, x=1, y=2, key="K")
        assert NGII_AERIAL.layer in url
        assert "{layer}" not in url

    def test_custom_source_template(self):
        src = TileSource(
            name="xyz", url_template="https://t/{z}/{x}/{y}.png"
        )
        assert src.tile_url(3, 4, 5, key="") == "https://t/3/4/5.png"
        assert src.crs == "EPSG:3857"
        assert src.tile_size == 256


class TestMosaicBounds3857:
    def test_whole_world_zoom0(self):
        minx, miny, maxx, maxy = mosaic_bounds_3857(0, 0, 0, 0, 0)
        shift = 20037508.342789244
        assert minx == pytest.approx(-shift, rel=1e-6)
        assert maxx == pytest.approx(shift, rel=1e-6)
        assert miny == pytest.approx(-shift, rel=1e-6)
        assert maxy == pytest.approx(shift, rel=1e-6)

    def test_north_is_larger_y(self):
        # y_min(북쪽) 타일이 maxy에 대응
        minx, miny, maxx, maxy = mosaic_bounds_3857(3, 1, 4, 2, 5)
        assert maxy > miny and maxx > minx


# 8px 소형 타일 소스(테스트 가속). bounds는 tile 인덱스+zoom에서 나오므로 무관.
_SMALL = TileSource(name="test8", url_template="http://t/{z}/{x}/{y}.png", tile_size=8)


class TestBuildMosaic:
    def _korea_bbox(self):
        # 대전 서구 근방 ~작은 bbox (여러 타일 걸치게)
        return (127.36, 36.30, 127.40, 36.33)

    def test_mosaic_writes_valid_png_and_5186_bounds(self, tmp_path):
        import rasterio
        from rasterio.io import MemoryFile

        zoom = 16
        calls = []

        def fetch(url):
            calls.append(url)
            return _solid_png(8, (10, 200, 60))

        out = tmp_path / "mosaic.png"
        m = build_mosaic(self._korea_bbox(), zoom, _SMALL, "KEY", out, fetch=fetch)

        assert len(calls) >= 1                       # 타일 실제로 요청됨
        assert m.missing_tiles == 0
        assert m.crs == "EPSG:5186"
        # 출력 PNG 유효 + 재디코드
        with MemoryFile(out.read_bytes()) as mf, mf.open() as ds:
            assert ds.count == 3 and ds.width > 0 and ds.height > 0
        # 5186 bounds 정합: 대전은 x~230k, y~415k 부근
        minx, miny, maxx, maxy = m.bounds
        assert maxx > minx and maxy > miny
        assert 150_000 < minx < 350_000
        assert 350_000 < miny < 500_000

    def test_missing_tiles_counted(self, tmp_path):
        def fetch(url):
            return None  # 전부 실패 → 회색 채움

        out = tmp_path / "gray.png"
        m = build_mosaic(self._korea_bbox(), 16, _SMALL, "KEY", out, fetch=fetch)
        assert m.missing_tiles >= 1
        assert out.exists()  # 실패해도 회색 이미지 생성

    def test_max_tiles_guard(self, tmp_path):
        # 큰 bbox + 높은 zoom → 타일 폭주 → ValueError
        with pytest.raises(ValueError, match="상한"):
            build_mosaic(
                (127.0, 36.0, 128.0, 37.0), 18, _SMALL, "KEY",
                tmp_path / "x.png", fetch=lambda u: _solid_png(8, (0, 0, 0)),
            )
