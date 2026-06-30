"""런타임 DEM 클립 + 표고 보간 (Phase 3B).

clip_dem: geo_store DEM 타일을 bbox_5186 영역으로 윈도우 클립.
elev_at : EPSG:5186 로컬 좌표 → bilinear 보간 표고(m).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds


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


def elev_at(x_local: float, y_local: float, dem: DEMPatch) -> float:
    """로컬 미터좌표 → bilinear 보간 표고(m).

    x_local, y_local: origin_offset 적용된 로컬 좌표(m).
    범위 밖 또는 NaN 이웃이면 0.0 반환(건물이 지면에 닿도록 안전 fallback).
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
    if col_f < 0.0 or row_f < 0.0 or col_f > cols - 1 or row_f > rows - 1:
        return 0.0

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
