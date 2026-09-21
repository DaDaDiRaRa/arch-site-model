"""DEM → NURBS 서피스 — 서피스가 DEM 격자점을 정확히 통과하고 .3dm에 꺼진 레이어로 들어가는지."""

import numpy as np
import rhino3dm
from affine import Affine

from src.geometry.terrain_mesh import grid_to_tin
from src.geometry.terrain_surface import dem_to_nurbs
from src.output.rhino import write_3dm
from src.terrain.dem import DEMPatch

OX, OY = 200000.0, 450000.0


def _dem(rows=24, cols=30, cell=5.0, nan_block=False):
    # 굴곡 있는 합성 지형(능선 + 골). 행은 북→남(transform.e < 0) — 실제 GeoTIFF와 같은 방향.
    r, c = np.mgrid[0:rows, 0:cols]
    z = 50 + 8 * np.sin(c / 3.0) + 5 * np.cos(r / 2.5) + 0.3 * c
    z = z.astype(np.float32)
    if nan_block:
        z[:3, :3] = np.nan
    top = OY + rows * cell
    return DEMPatch(grid=z, transform=Affine(cell, 0, OX, 0, -cell, top), offset=(OX, OY))


def _surface(dem):
    ng = dem_to_nurbs(dem)
    nu, nv, _ = ng.points.shape
    s = rhino3dm.NurbsSurface.Create(3, False, 4, 4, nu, nv)
    for i, v in enumerate(ng.knots_u):
        s.KnotsU[i] = v
    for i, v in enumerate(ng.knots_v):
        s.KnotsV[i] = v
    for i in range(nu):
        for j in range(nv):
            p = ng.points[i, j]
            s.Points[i, j] = rhino3dm.Point4d(p[0], p[1], p[2], 1.0)
    return s


def test_surface_interpolates_every_dem_grid_point():
    dem = _dem()
    s = _surface(dem)
    assert s.IsValid
    rows, cols = dem.grid.shape
    worst = 0.0
    for row in range(0, rows, 3):
        for col in range(0, cols, 3):
            x = dem.transform.c + dem.transform.a * col - OX
            y = dem.transform.f + dem.transform.e * row - OY
            p = s.PointAt(x, y)            # 매개변수 = 로컬 좌표(그레빌 제어점)
            assert abs(p.X - x) < 1e-6 and abs(p.Y - y) < 1e-6
            worst = max(worst, abs(p.Z - float(dem.grid[row, col])))
    assert worst < 1e-3                    # 근사가 아니라 통과(보간)


def test_nan_filled_and_large_grid_strided():
    ng = dem_to_nurbs(_dem(nan_block=True))
    assert np.isfinite(ng.points).all()
    ng2 = dem_to_nurbs(_dem(rows=60, cols=90), max_cp=40)
    assert ng2.stride == 3 and max(ng2.points.shape[:2]) <= 40


def test_3dm_has_hidden_terrain_surface_layer(tmp_path):
    dem = _dem()
    p = write_3dm([], grid_to_tin(dem), tmp_path / "s.3dm", (OX, OY), terrain_dem=dem)
    m = rhino3dm.File3dm.Read(p)
    layer = next(l for l in m.Layers if l.Name == "terrain_surface")
    assert layer.Visible is False
    srf = [o for o in m.Objects if o.Attributes.Name == "terrain_nurbs"]
    assert len(srf) == 1
    assert srf[0].Geometry.ObjectType == rhino3dm.ObjectType.Surface
