"""DEM 격자 → TIN 삼각망 (Phase 3B, 사양서 §6.5).

grid_to_tin: 각 셀을 대각 교차 2삼각형으로 분할.
TerrainMesh: SketchUp 인치 단위 (x,y,z) 정점 + 삼각형 인덱스.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.config import M2I
from src.terrain.dem import DEMPatch


@dataclass
class TerrainMesh:
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)


def grid_to_tin(dem: DEMPatch) -> TerrainMesh:
    """DEMPatch 격자 → TIN.

    각 셀을 SW–NE 대각선으로 2삼각형 분할(사양서 §6.5).
    NaN 픽셀을 포함하는 셀은 건너뜀.
    출력 좌표 = 로컬 미터 × M2I (인치, SketchUp 단위).
    """
    grid = dem.grid
    rows, cols = grid.shape
    tf = dem.transform
    ox, oy = dem.offset

    # 픽셀 (row, col) → EPSG:5186 절대 좌표 → 로컬 미터
    # Affine: x_abs = tf.c + tf.a * col, y_abs = tf.f + tf.e * row
    col_idx = np.arange(cols, dtype=np.float64)
    row_idx = np.arange(rows, dtype=np.float64)
    x_local = (tf.c + tf.a * col_idx) - ox   # shape (cols,)
    y_local = (tf.f + tf.e * row_idx) - oy   # shape (rows,), tf.e < 0

    # (row, col) → vertex index 맵. -1 = NaN(제외)
    vid = np.full((rows, cols), -1, dtype=np.int32)
    vertices: list[tuple[float, float, float]] = []

    for r in range(rows):
        for c in range(cols):
            z = grid[r, c]
            if np.isnan(z):
                continue
            vid[r, c] = len(vertices)
            vertices.append((
                float(x_local[c]) * M2I,
                float(y_local[r]) * M2I,
                float(z) * M2I,
            ))

    triangles: list[tuple[int, int, int]] = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            # 셀 4 꼭짓점: (r,c)=NW (r,c+1)=NE (r+1,c)=SW (r+1,c+1)=SE
            vnw = vid[r,     c]
            vne = vid[r,     c + 1]
            vsw = vid[r + 1, c]
            vse = vid[r + 1, c + 1]
            # SW–NE 대각 교차
            if vnw >= 0 and vne >= 0 and vse >= 0:
                triangles.append((vnw, vne, vse))
            if vnw >= 0 and vse >= 0 and vsw >= 0:
                triangles.append((vnw, vse, vsw))

    return TerrainMesh(vertices=vertices, triangles=triangles)
