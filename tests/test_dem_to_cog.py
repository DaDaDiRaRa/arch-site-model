"""scripts/dem_to_cog.to_cog — GeoTIFF → COG(내부 타일링) 변환.

/vsigs 윈도우 읽기 효율을 위해 GCS 업로드 전 타일을 COG로 변환한다.
"""
import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS

from scripts.dem_to_cog import to_cog


def test_to_cog_tiled_and_values_preserved(tmp_path):
    src = tmp_path / "src.tif"
    n = 1024
    transform = Affine(5, 0, 200_000, 0, -5, 400_000)
    grid = (np.arange(n * n, dtype="float32").reshape(n, n) % 500).astype("float32")
    with rasterio.open(
        src, "w", driver="GTiff", height=n, width=n, count=1,
        dtype="float32", crs=CRS.from_epsg(5186), transform=transform, nodata=np.nan,
    ) as d:
        d.write(grid, 1)

    dst = tmp_path / "out.tif"
    to_cog(src, dst)

    with rasterio.open(dst) as c:
        assert c.profile["tiled"] is True          # 내부 타일링(COG 핵심)
        assert c.block_shapes[0] == (512, 512)
        assert c.crs.to_epsg() == 5186
        assert np.allclose(c.read(1), grid, equal_nan=True)
