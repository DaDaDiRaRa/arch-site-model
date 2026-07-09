"""건물 footprint + 층수 → 쿼드 솔리드 데이터 (사양서 §6.2/§6.3).

VWorld LT_C_SPBD 피처(EPSG:4326 MultiPolygon)를:
  1. 폴리곤별로 분리(외곽 링 + 내부 홀 링)
  2. 각 정점을 EPSG:5186 으로 변환 후 origin offset 적용 → 로컬 미터좌표
  3. gro_flo_co → height = 층수 × 층고
하나의 BuildingSolid 로 만든다. 인치 변환은 출력 단계(skp_mcp)에서 수행.
"""

from dataclasses import dataclass, field

from shapely.geometry import shape

from src.config import DEFAULT_FLOOR_H_M
from src.geo.crs import apply_offset, to_5186


@dataclass
class BuildingSolid:
    name: str
    footprint_m: list[tuple[float, float]]                    # 외곽 링 (로컬 미터, 닫힘점 제거)
    base_z_m: float
    height_m: float
    floors: int | None                                         # None = gro_flo_co 누락/0
    attrs: dict                                                # bd_mgt_sn, buld_nm, rd_nm 등
    holes_m: list[list[tuple[float, float]]] = field(default_factory=list)  # 중정 내부 링 목록
    flagged: bool = False                                      # True when floors 누락 + policy="flag"
    floors_source: str = "measured"                            # "measured"(gro_flo_co 실측) | "default"(누락→기본층수 추정)


def floors_of(props: dict) -> int | None:
    """gro_flo_co → 지상층수(int). 누락/0/null/비정상은 None (사양서 §6.4, 임의 추정 금지)."""
    v = (props or {}).get("gro_flo_co")
    if v in (None, "", "null"):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _ring_coords(ring) -> list[tuple[float, float]]:
    """shapely LinearRing → (lon,lat) 리스트 (닫힘 중복 정점 제거)."""
    coords = [(float(x), float(y)) for x, y in ring.coords]
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def _polygon_rings(geom: dict) -> list[tuple[list, list]]:
    """Polygon/MultiPolygon → [(exterior_coords, [interior_coords, ...]), ...].

    각 튜플은 (외곽 링, 홀 링 목록). 외곽 링이 3점 미만이면 건너뜀.
    홀 링도 3점 미만이면 개별 건너뜀.
    """
    if not geom:
        return []
    g = shape(geom)
    if g.geom_type == "MultiPolygon":
        polys = list(g.geoms)
    elif g.geom_type == "Polygon":
        polys = [g]
    else:
        return []
    result = []
    for poly in polys:
        ext = _ring_coords(poly.exterior)
        if len(ext) < 3:
            continue
        holes = [_ring_coords(r) for r in poly.interiors]
        holes = [h for h in holes if len(h) >= 3]
        result.append((ext, holes))
    return result


def _exterior_rings(geom: dict) -> list[list[tuple[float, float]]]:
    """Polygon/MultiPolygon → 외곽 링 목록 (하위 호환용)."""
    return [ext for ext, _ in _polygon_rings(geom)]


def collect_5186_coords(features: list[dict]) -> list[tuple[float, float]]:
    """모든 건물 외곽 정점을 EPSG:5186 으로 변환해 모은다(origin offset 산출용)."""
    coords: list[tuple[float, float]] = []
    for feat in features:
        for ext, _ in _polygon_rings(feat.get("geometry")):
            coords.extend(to_5186(lon, lat) for lon, lat in ext)
    return coords


def features_to_solids(
    features: list[dict],
    floor_h_m: float = DEFAULT_FLOOR_H_M,
    offset: tuple[float, float] = (0.0, 0.0),
    default_floors: int = 1,
    missing_policy: str = "default",
) -> list[BuildingSolid]:
    """LT_C_SPBD 피처 → BuildingSolid 목록.

    MultiPolygon 은 폴리곤별로 분리. base_z 는 0(지형은 Phase 3에서 앉힘).

    missing_policy 옵션 (gro_flo_co 누락/0 건물 처리 방식, 사양서 §6.4):
      "default" : default_floors 층고 적용, flagged=False (기존 동작)
      "skip"    : 층수 누락 건물 제외 (solid 미생성)
      "flag"    : default_floors 층고 적용, flagged=True → 출력 시 별도 레이어/표시
    """
    solids: list[BuildingSolid] = []
    for feat in features:
        props = feat.get("properties") or {}
        floors = floors_of(props)
        if floors is None and missing_policy == "skip":
            continue
        flagged = floors is None and missing_policy == "flag"
        floors_source = "measured" if floors is not None else "default"  # 실측 vs 추정(A-2 시각구분)
        n_floors = floors if floors is not None else default_floors
        height = n_floors * floor_h_m
        name = props.get("buld_nm") or props.get("bd_mgt_sn") or "building"
        attrs = {
            k: props.get(k)
            for k in ("bd_mgt_sn", "buld_nm", "rd_nm")
            if props.get(k)
        }
        for ext, holes in _polygon_rings(feat.get("geometry")):
            fp_5186 = [to_5186(lon, lat) for lon, lat in ext]
            fp_local = apply_offset(fp_5186, offset)
            holes_local = [
                apply_offset([to_5186(lon, lat) for lon, lat in h], offset)
                for h in holes
            ]
            solids.append(
                BuildingSolid(
                    name=name,
                    footprint_m=fp_local,
                    holes_m=holes_local,
                    base_z_m=0.0,
                    height_m=height,
                    floors=floors,
                    attrs=attrs,
                    flagged=flagged,
                    floors_source=floors_source,
                )
            )
    return solids


def extrude_face_loops(n: int, hole_ns: list[int] | None = None) -> list[list[int]]:
    """돌출 솔리드의 면을 정점 인덱스 루프로 반환 (SketchUp 무관, 검증용).

    정점 배열 규칙:
      [outer_bottom 0..n-1] [outer_top n..2n-1]
      [hole0_bottom 2n..] [hole0_top ..] ...

    반환: 모든 면의 정점 인덱스 루프.
      바닥(하향) + 천장(상향) + 외벽 쿼드×n + 중정 내벽 쿼드×Σm
    홀의 바닥/천장 inner loop는 SketchUp add_face_inner_loop로 처리하므로 여기선 별도 면으로 계산.
    총 면수 = n + 2 + Σhole_ns (사양서 §6.2).
    """
    if n < 3:
        raise ValueError(f"footprint must have >= 3 vertices, got {n}")
    hole_ns = hole_ns or []
    faces = [list(range(n - 1, -1, -1)), [n + i for i in range(n)]]
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j, n + i])
    off = 2 * n
    for m in hole_ns:
        for i in range(m):
            j = (i + 1) % m
            faces.append([off + i, off + j, off + m + j, off + m + i])
        off += 2 * m
    return faces
