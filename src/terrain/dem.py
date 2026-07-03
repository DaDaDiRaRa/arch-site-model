"""런타임 DEM 클립 + 표고 보간 (Phase 3B).

clip_dem: geo_store DEM 타일을 bbox_5186 영역으로 윈도우 클립.
elev_at : EPSG:5186 로컬 좌표 → bilinear 보간 표고(m).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

log = logging.getLogger(__name__)


@dataclass
class DEMPatch:
    """클립된 DEM 격자 + 좌표 메타데이터."""

    grid: np.ndarray             # shape (rows, cols), float32, NaN=nodata
    transform: object            # rasterio Affine: pixel(col,row) → EPSG:5186 절대 (x,y)
    offset: tuple[float, float]  # origin_offset (건물과 동일 로컬 좌표 기준)

    def elev_at(self, x_local: float, y_local: float) -> float:
        """로컬 미터좌표 → 보간 표고(m). 범위 밖·NaN이면 0.0."""
        return elev_at(x_local, y_local, self)

    def z_range(self) -> tuple[float, float] | None:
        """NaN 제외 (min_m, max_m). 유효값 없으면 None."""
        valid = self.grid[~np.isnan(self.grid)]
        if valid.size == 0:
            return None
        return (float(valid.min()), float(valid.max()))


def clip_dem(
    tile_path: Path,
    bbox_5186: tuple[float, float, float, float],
    offset: tuple[float, float],
) -> DEMPatch:
    """DEM 타일을 bbox_5186(EPSG:5186, minx miny maxx maxy)로 윈도우 클립.

    offset: pipeline에서 계산한 origin_offset — 건물과 동일 로컬 좌표 기준을 공유.
    반환: DEMPatch(grid float32 NaN=nodata, transform, offset).
    """
    with rasterio.open(tile_path) as src:
        nodata = src.nodata
        # boundless 영역 채움값: float 타일은 NaN 사용, 아니면 nodata 또는 0
        if src.dtypes[0].startswith("float"):
            fill = float("nan")
        elif nodata is not None:
            fill = float(nodata)
        else:
            fill = 0.0

        window = from_bounds(*bbox_5186, transform=src.transform)
        data = src.read(1, window=window, boundless=True, fill_value=fill)
        transform = src.window_transform(window)

    grid = data.astype(np.float32)

    # nodata → NaN 정규화
    if nodata is not None:
        nd = float(nodata)
        if not np.isnan(nd):
            grid[grid == nd] = np.nan
    # float 타일에서 inf 방어
    grid[~np.isfinite(grid)] = np.nan

    return DEMPatch(grid=grid, transform=transform, offset=offset)


def clip_dem_mosaic(
    tile_paths,
    bbox_5186: tuple[float, float, float, float],
    offset: tuple[float, float],
) -> DEMPatch:
    """여러 DEM 타일(모두 EPSG:5186)을 bbox_5186 영역으로 병합 클립.

    단일 타일이면 clip_dem에 위임(기존 경로·nodata 처리 보존). 여러 타일이면
    rasterio.merge로 가장 고운 해상도 격자에 모자이크한다. tile_paths를 고해상도
    우선 순서로 주면 겹침부에서 앞선(고해상도) 타일이 이긴다(method="first").

    tile_paths는 로컬 경로 또는 원격 URI(config.dem_tile_path 산출: /vsigs/…)를 문자열로
    받는다. Path로 감싸지 않는다 — Windows에서 /vsigs URI가 역슬래시로 뭉개지기 때문.
    열리지 않는 타일(로컬 누락·GCS 미도달·객체 없음)은 건너뛰고, 하나도 못 열면 예외.

    offset: origin_offset(건물과 동일 로컬 좌표 기준). 반환 DEMPatch는 clip_dem과 동일 계약.
    """
    paths = [str(p) for p in tile_paths]
    if not paths:
        raise ValueError("clip_dem_mosaic: 타일 목록이 비었습니다")
    if len(paths) == 1:
        return clip_dem(paths[0], bbox_5186, offset)

    from rasterio.merge import merge as _merge

    # 목적지 nodata는 실수 센티넬(-9999)로 둔다. nan을 nodata로 쓰면 merge 내부의
    # 'dest==nodata' 비교가 NaN 동등 실패로 깨질 수 있어서다. 병합 후 nan으로 되돌린다.
    # (contour_bake 타일은 조밀 사각격자라 내부 nodata가 없고, 커버 안 된 bbox 영역만
    #  센티넬로 채워진다.)
    sentinel = -9999.0
    srcs = []
    for p in paths:
        try:
            srcs.append(rasterio.open(p))
        except Exception as e:  # noqa: BLE001 — 누락 타일은 건너뛰고 나머지로 진행
            log.warning("DEM 타일 열기 실패, 건너뜀: %s (%s)", p, e)
    if not srcs:
        raise FileNotFoundError("DEM 타일을 하나도 열 수 없습니다: " + ", ".join(paths))
    try:
        res = min(min(abs(s.res[0]), abs(s.res[1])) for s in srcs)
        mosaic, transform = _merge(
            srcs, bounds=bbox_5186, res=res, nodata=sentinel, method="first"
        )
    finally:
        for s in srcs:
            s.close()

    grid = mosaic[0].astype(np.float32)
    grid[grid == sentinel] = np.nan
    grid[~np.isfinite(grid)] = np.nan
    return DEMPatch(grid=grid, transform=transform, offset=offset)


def elev_at(x_local: float, y_local: float, dem: DEMPatch) -> float:
    """로컬 미터좌표 → bilinear 보간 표고(m).

    x_local, y_local: origin_offset 적용된 로컬 좌표(m).
    범위 밖이면 가장 가까운 가장자리 픽셀로 클램프한다. (0.0 반환 금지 — 절대표고
    지형(예: 65~116m)에서는 0.0이 건물을 z≈0으로 침몰시킨다: seating의 min과 결합해
    footprint 꼭짓점 하나만 경계를 넘어도 건물 전체가 지형 아래로 빠짐.)
    4개 이웃이 모두 NaN이면 0.0(그 자리에 유효 표고 없음).
    """
    # 로컬 → EPSG:5186 절대 좌표
    ox, oy = dem.offset
    x_abs = x_local + ox
    y_abs = y_local + oy

    # 절대 좌표 → 연속 픽셀 좌표
    # Affine: x_abs = tf.c + tf.a * col, y_abs = tf.f + tf.e * row  (tf.e < 0, 북→남)
    tf = dem.transform
    col_f = (x_abs - tf.c) / tf.a
    row_f = (y_abs - tf.f) / tf.e

    rows, cols = dem.grid.shape
    # 범위 밖 → 가장 가까운 가장자리로 클램프(0.0 침몰 버그 방지). 경계를 살짝 넘는
    # footprint 꼭짓점이 가장자리 표고를 받아 건물이 지형 위에 앉는다.
    col_f = min(max(col_f, 0.0), float(cols - 1))
    row_f = min(max(row_f, 0.0), float(rows - 1))

    # 클램핑: 정확히 경계(col_f=cols-1, row_f=rows-1)일 때 bilinear 인덱스 오버플로 방지
    c0 = min(int(col_f), cols - 2)
    r0 = min(int(row_f), rows - 2)
    dc = col_f - c0
    dr = row_f - r0

    v00 = dem.grid[r0,     c0]
    v01 = dem.grid[r0,     c0 + 1]
    v10 = dem.grid[r0 + 1, c0]
    v11 = dem.grid[r0 + 1, c0 + 1]

    neighbors = (v00, v01, v10, v11)
    if any(np.isnan(v) for v in neighbors):
        valid = [v for v in neighbors if not np.isnan(v)]
        return float(np.mean(valid)) if valid else 0.0

    z = (
        v00 * (1.0 - dc) * (1.0 - dr)
        + v01 * dc       * (1.0 - dr)
        + v10 * (1.0 - dc) * dr
        + v11 * dc       * dr
    )
    return float(z)
