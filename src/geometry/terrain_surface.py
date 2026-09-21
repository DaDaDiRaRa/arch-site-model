"""DEM 격자 → 3차 NURBS 서피스 데이터 (Rhino에서 자르기·투영·솔리드 작업용).

메시(적응형 TIN)는 정확·가볍지만 Rhino의 Split/Trim/Project/부울이 잘 안 먹는다. 설계 작업용으로
같은 DEM에서 서피스를 함께 만든다. 제어점을 DEM 값에 그대로 두면 B-스플라인이 데이터를 **근사**해
능선·골이 뭉개진다 — 대신 격자점을 **정확히 통과하는** 보간 스플라인(scipy RectBivariateSpline,
s=0, 3차)을 풀어 그 매듭·계수를 NURBS로 옮긴다. 제어점 x·y는 그레빌 좌표라 매개변수 (u,v)가
곧 로컬 (x,y)가 되고, 서피스는 모든 DEM 격자점에서 z가 같다.
좌표 규약은 terrain_mesh.pixel_to_local_m과 같다(픽셀 모서리 기준, 로컬 = 5186 − offset).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# 한 방향 제어점 상한 — 넘으면 격자를 건너뛰어 줄인다(4km 영역 5m = 800 → 400, 파일·Rhino 부담 억제).
MAX_CP_PER_SIDE = 400


@dataclass
class NurbsGrid:
    knots_u: list[float]              # Rhino 규약 매듭 벡터(길이 n + order − 2)
    knots_v: list[float]
    points: np.ndarray                # (nu, nv, 3) 제어점, 로컬 미터
    degree: int = 3
    stride: int = 1                   # 격자 건너뛴 간격(1 = DEM 전 격자점 통과)


def dem_to_nurbs(dem, max_cp: int = MAX_CP_PER_SIDE) -> NurbsGrid | None:
    """DEMPatch → 격자점 보간 3차 NURBS 데이터. 유효 표고가 없거나 격자가 4×4 미만이면 None."""
    from scipy.interpolate import RectBivariateSpline
    from scipy.ndimage import distance_transform_edt

    g = np.asarray(dem.grid, dtype=float)
    nan = np.isnan(g)
    if nan.all():
        return None
    if nan.any():  # 구멍(범위 밖·nodata)은 가장 가까운 유효값으로 — 서피스는 끊김 없이 한 장이어야 한다
        idx = distance_transform_edt(nan, return_distances=False, return_indices=True)
        g = g[tuple(idx)]

    stride = max(1, math.ceil(max(g.shape) / max_cp))
    g = g[::stride, ::stride]
    rows, cols = g.shape
    if rows < 4 or cols < 4:
        return None

    tf = dem.transform
    ox, oy = dem.offset
    xs = tf.c + tf.a * (np.arange(cols) * stride) - ox
    ys = tf.f + tf.e * (np.arange(rows) * stride) - oy
    z = g
    if ys[0] > ys[-1]:          # 북→남 행 순서를 y 증가 순으로
        ys, z = ys[::-1], z[::-1]
    k = 3
    spl = RectBivariateSpline(xs, ys, z.T, kx=k, ky=k, s=0)
    tx, ty, c = spl.tck
    nu, nv = len(tx) - k - 1, len(ty) - k - 1
    coef = np.asarray(c).reshape(nu, nv)
    gx = np.array([tx[i + 1:i + k + 1].mean() for i in range(nu)])   # 그레빌 좌표 → X(u) = u
    gy = np.array([ty[j + 1:j + k + 1].mean() for j in range(nv)])
    pts = np.empty((nu, nv, 3))
    pts[:, :, 0] = gx[:, None]
    pts[:, :, 1] = gy[None, :]
    pts[:, :, 2] = coef
    return NurbsGrid(
        knots_u=[float(v) for v in tx[1:-1]],
        knots_v=[float(v) for v in ty[1:-1]],
        points=pts, degree=k, stride=stride,
    )
