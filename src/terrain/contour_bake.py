"""수치지형도 등고선+표고점 SHP → DEM(.tif) 오프라인 굽기 (Phase 3A).

사용법:
    python -m src.terrain.contour_bake <shp_dir> --cell 5 --out geo_store/dem_daejeon_seogu.tif

shp_dir 안에 수치지형도Ver2.0 SHP 파일이 있어야 한다.
    - 등고선: 레이어코드 F0010000 포함 파일 (라인, 표고 속성)
    - 표고점: 레이어코드 F0020000 포함 파일 (점, 표고 속성)

★ 표고점(F0020000)을 반드시 함께 넣어야 봉우리가 평면으로 안 뜬다.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from scipy.interpolate import CloughTocher2DInterpolator, LinearNDInterpolator

log = logging.getLogger(__name__)

# 수치지형도 등고선 레이어 파일명 패턴 (F0010000)
_CONTOUR_PAT = re.compile(r"F0010000", re.IGNORECASE)
# 표고점 레이어 파일명 패턴 (F0020000)
_SPOT_PAT = re.compile(r"F0020000", re.IGNORECASE)

# 표고 속성 후보 필드명 (우선순위 순, 영문 + 수치지형도 한국어 표준)
_ELEV_FIELDS = [
    "ELEV", "elev", "H", "h", "HEIGHT", "height", "Z_VAL", "z_val", "ALTITUDE",
    "등고수치",  # 수치지형도Ver2.0 F0010000 등고선
    "수치",      # 수치지형도Ver2.0 F0020000 표고점
    "표고",      # 기타 포맷
]


def _find_elev_field(gdf: gpd.GeoDataFrame) -> str:
    """GeoDataFrame에서 표고 속성 필드를 탐지해 반환."""
    cols = list(gdf.columns)
    for candidate in _ELEV_FIELDS:
        if candidate in cols:
            return candidate
    # 영문 이름 힌트 매칭
    for col in cols:
        if any(k in col.lower() for k in ("el", "ht", "alt", "elev")):
            try:
                gdf[col].astype(float)
                return col
            except (ValueError, TypeError):
                pass
    # 최후 폴백: geometry/UFID 제외 최초 숫자형 컬럼
    for col in cols:
        if col in ("geometry", "UFID", "ufid"):
            continue
        try:
            vals = gdf[col].dropna()
            if len(vals) > 0:
                float(vals.iloc[0])
                return col
        except (ValueError, TypeError):
            pass
    raise ValueError(f"표고 필드를 찾을 수 없습니다. 컬럼 목록: {cols}")


def _find_shp(shp_dir: Path, pattern: re.Pattern) -> list[Path]:
    """shp_dir(재귀 포함)에서 패턴과 일치하는 .shp 파일 목록."""
    return [p for p in shp_dir.rglob("*.shp") if pattern.search(p.stem)]


def read_contours(shp_dir: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """수치지형도 SHP에서 (등고선 + 표고점) 좌표+표고를 추출한다.

    Returns:
        (cx, cy, cz, sz) — 등고선 정점 (cx,cy,cz), 표고점 (sx,sy) 배열로 반환.
        실제로는 (x, y, z) 세트 두 쌍을 합쳐서 반환하기 위해 내부에서 합산.
        반환값: (all_x, all_y, all_z) 로 단순화 — 단 표고점 포함 여부 경고.
    """
    shp_dir = Path(shp_dir)
    contour_files = _find_shp(shp_dir, _CONTOUR_PAT)
    spot_files = _find_shp(shp_dir, _SPOT_PAT)

    if not contour_files:
        raise FileNotFoundError(f"등고선 SHP(F0010000)를 찾을 수 없습니다: {shp_dir}")
    if not spot_files:
        log.warning("표고점 SHP(F0020000) 없음 — 봉우리/음폐 지역이 평면으로 보일 수 있습니다.")

    xs, ys, zs = [], [], []

    # 등고선 — LineString 정점마다 표고 복사
    for p in contour_files:
        gdf = gpd.read_file(p)
        elev_field = _find_elev_field(gdf)
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            z = float(row[elev_field])
            coords = list(geom.coords) if hasattr(geom, "coords") else []
            if not coords and hasattr(geom, "geoms"):
                for part in geom.geoms:
                    coords.extend(part.coords)
            for x, y, *_ in coords:
                xs.append(x)
                ys.append(y)
                zs.append(z)
        log.info("등고선 %s: %d 정점 로드", p.name, len(xs))

    # 표고점 — Point 1개당 표고 1개
    for p in spot_files:
        gdf = gpd.read_file(p)
        elev_field = _find_elev_field(gdf)
        cnt_before = len(xs)
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            z = float(row[elev_field])
            xs.append(geom.x)
            ys.append(geom.y)
            zs.append(z)
        log.info("표고점 %s: %d 점 로드", p.name, len(xs) - cnt_before)

    if not xs:
        raise ValueError("등고선/표고점에서 유효한 좌표를 추출하지 못했습니다.")

    return np.array(xs), np.array(ys), np.array(zs)


def bake_dem(
    xs: np.ndarray,
    ys: np.ndarray,
    zs: np.ndarray,
    cell_m: float = 5.0,
    bounds: tuple[float, float, float, float] | None = None,
    method: str = "clough",
    guard_m: float = 3.0,
) -> tuple[np.ndarray, Affine]:
    """등고선+표고점 좌표 → 정규 격자 DEM 보간.

    Args:
        xs, ys, zs: read_contours() 반환값.
        cell_m: 격자 해상도(미터). 5.0=기본, 1.0=정밀.
        bounds: (minx, miny, maxx, maxy) EPSG:5186. None이면 데이터 범위.
        method: 보간법.
            - "clough"(기본): CloughTocher C1 3차 보간을 LinearND로 가드. clough는
              정점 gradient를 추정해 곡면을 맞추므로 세 정점이 같은 등고선 위여도
              평평해지지 않아 계단현상을 줄인다. 다만 급경사부/슬리버 삼각형에서
              큰 오버슈트(스파이크·웅덩이)를 내므로, 안전한 평면보간(linear) 값에서
              ±guard_m 밖으로 벗어난 셀을 그 범위로 클립한다("튜브 클램프").
            - "linear": LinearNDInterpolator(평면 삼각보간)만. 오버슈트 없지만 등고선
              사이가 평탄 삼각형이 돼 계단현상 발생(과거 기본값, 비교/폴백용).
        guard_m: clough가 linear에서 벗어날 수 있는 최대 표고차(m). 등고선 간격(5m)보다
            작게 잡아 정상적인 스무딩 차는 허용하되 오버슈트는 잘라낸다. method="linear"이면 무시.

    Returns:
        (grid, transform): grid[row, col] = 표고(m), transform = Affine(북→남 행 순서).
    """
    if bounds is None:
        bounds = (xs.min(), ys.min(), xs.max(), ys.max())
    minx, miny, maxx, maxy = bounds

    # 격자 정의 (rasterio 관례: 행은 북→남)
    ncols = int(np.ceil((maxx - minx) / cell_m)) + 1
    nrows = int(np.ceil((maxy - miny) / cell_m)) + 1

    # 격자 중심 좌표
    grid_x = np.linspace(minx, minx + (ncols - 1) * cell_m, ncols)
    grid_y = np.linspace(maxy, maxy - (nrows - 1) * cell_m, nrows)  # 북→남
    gx, gy = np.meshgrid(grid_x, grid_y)

    log.info("보간 격자: %d×%d (cell_m=%.1f, method=%s)", nrows, ncols, cell_m, method)
    log.info("입력 점 수: %d", len(xs))

    # Delaunay 삼각망은 두 보간기가 공유(중복 계산 방지).
    from scipy.spatial import Delaunay
    pts = np.column_stack([xs, ys])
    tri = Delaunay(pts)

    grid_lin = LinearNDInterpolator(tri, zs)(gx, gy)
    if method == "linear":
        grid = grid_lin
    elif method == "clough":
        grid_ct = CloughTocher2DInterpolator(tri, zs)(gx, gy)
        # 튜브 클램프: clough를 linear±guard_m 범위로 제한 → 오버슈트 스파이크/웅덩이 제거.
        grid = np.clip(grid_ct, grid_lin - guard_m, grid_lin + guard_m)
    else:
        raise ValueError(f"알 수 없는 보간법: {method!r} (clough|linear)")

    # 격자 밖(nan) 영역을 최근방 값으로 채움 (linear/clough 모두 동일 볼록껍질 → 같은 nan)
    nan_mask = np.isnan(grid)
    if nan_mask.any():
        from scipy.spatial import cKDTree
        valid = ~nan_mask
        tree = cKDTree(np.column_stack([gx[valid], gy[valid]]))
        _, idx = tree.query(np.column_stack([gx[nan_mask], gy[nan_mask]]))
        grid[nan_mask] = grid[valid][idx]

    # 최종 안전 클램프 (입력 표고 범위 밖 값 제거).
    np.clip(grid, float(zs.min()), float(zs.max()), out=grid)

    transform = Affine(cell_m, 0, minx, 0, -cell_m, maxy)
    return grid.astype(np.float32), transform


def write_dem_tif(
    path: str | Path,
    grid: np.ndarray,
    transform: Affine,
    crs: str = "EPSG:5186",
) -> None:
    """DEM 격자를 GeoTIFF로 저장."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    nrows, ncols = grid.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=nrows,
        width=ncols,
        count=1,
        dtype=grid.dtype,
        crs=CRS.from_epsg(int(crs.split(":")[-1])),
        transform=transform,
        nodata=np.nan,
        compress="deflate",
    ) as dst:
        dst.write(grid, 1)
    log.info("DEM 저장: %s (%d×%d)", path, nrows, ncols)


def update_manifest(
    geo_store: Path,
    out_path: Path,
    bounds_5186: tuple,
    cell_m: float,
    region: str,
    sheets: list[str] | None = None,
    method: str | None = None,
    guard_m: float | None = None,
) -> None:
    """manifest.json에 새 DEM 타일 항목을 추가/갱신."""
    from pyproj import Transformer

    tr = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
    minx5, miny5, maxx5, maxy5 = bounds_5186
    min_lon, min_lat = tr.transform(minx5, miny5)
    max_lon, max_lat = tr.transform(maxx5, maxy5)
    bounds_4326 = [min_lon, min_lat, max_lon, max_lat]

    manifest_path = geo_store / "manifest.json"
    tiles = []
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        tiles = data.get("tiles", data) if isinstance(data, dict) else data

    # 동일 file 이름이면 갱신, 없으면 추가
    entry = {
        "region": region,
        "file": out_path.name,
        "crs": "EPSG:5186",
        "bounds_4326": bounds_4326,
        "source": "CONTOUR_BAKE",
        "cell_m": cell_m,
        "updated": __import__("datetime").date.today().isoformat(),
    }
    if sheets:
        entry["sheets"] = sheets
    if method:
        entry["method"] = method
    if guard_m is not None and method == "clough":
        entry["guard_m"] = guard_m

    tiles = [t for t in tiles if t.get("file") != entry["file"]]
    tiles.append(entry)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(tiles, f, ensure_ascii=False, indent=2)
    log.info("manifest.json 갱신: %s", entry)


def bake(
    shp_dir: str | Path,
    out_path: str | Path,
    cell_m: float = 5.0,
    bounds: tuple | None = None,
    region: str = "",
    sheets: list[str] | None = None,
    update_manifest_flag: bool = True,
    method: str = "clough",
    guard_m: float = 3.0,
) -> None:
    """end-to-end: SHP 읽기 → 보간 → .tif 저장 → manifest 갱신."""
    shp_dir = Path(shp_dir)
    out_path = Path(out_path)

    log.info("=== contour_bake 시작 === shp_dir=%s cell_m=%s method=%s", shp_dir, cell_m, method)
    xs, ys, zs = read_contours(shp_dir)

    if bounds is None:
        bounds = (xs.min(), ys.min(), xs.max(), ys.max())

    grid, transform = bake_dem(xs, ys, zs, cell_m=cell_m, bounds=bounds, method=method, guard_m=guard_m)
    write_dem_tif(out_path, grid, transform)

    if update_manifest_flag:
        from src import config
        update_manifest(
            config.GEO_STORE, out_path, bounds, cell_m, region or shp_dir.name,
            sheets, method=method, guard_m=guard_m,
        )

    log.info("=== contour_bake 완료 === %s", out_path)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="수치지형도 등고선+표고점 → DEM(.tif) 굽기")
    parser.add_argument("shp_dir", help="수치지형도 SHP 폴더 경로")
    parser.add_argument("--cell", type=float, default=5.0, help="격자 해상도(m). 기본 5.0")
    parser.add_argument("--out", required=True, help="출력 .tif 경로 (예: geo_store/dem_daejeon.tif)")
    parser.add_argument("--region", default="", help="manifest region 이름")
    parser.add_argument("--sheets", nargs="*", help="도엽 번호 목록")
    parser.add_argument("--no-manifest", action="store_true", help="manifest.json 갱신 안 함")
    parser.add_argument(
        "--method", default="clough", choices=["clough", "linear"],
        help="보간법. clough=C1 3차 가드(계단제거, 기본) | linear=평면삼각(계단발생, 비교용)",
    )
    parser.add_argument(
        "--guard", type=float, default=3.0,
        help="clough 튜브 클램프 폭(m). linear에서 벗어날 최대 표고차. 기본 3.0",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    bake(
        shp_dir=args.shp_dir,
        out_path=args.out,
        cell_m=args.cell,
        region=args.region,
        sheets=args.sheets,
        update_manifest_flag=not args.no_manifest,
        method=args.method,
        guard_m=args.guard,
    )


if __name__ == "__main__":
    _cli()
