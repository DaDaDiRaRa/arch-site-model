"""건물 footprint + 층수 → 쿼드 솔리드 데이터 (사양서 §6.2/§6.3).

VWorld LT_C_SPBD 피처(EPSG:4326 MultiPolygon)를:
  1. 폴리곤별로 분리(외곽 링만, 내부 홀은 [목표])
  2. 각 정점을 EPSG:5186 으로 변환 후 origin offset 적용 → 로컬 미터좌표
  3. gro_flo_co → height = 층수 × 층고
하나의 BuildingSolid 로 만든다. 인치 변환은 출력 단계(skp_mcp)에서 수행.
"""

from dataclasses import dataclass

from shapely.geometry import shape

from src.config import DEFAULT_FLOOR_H_M
from src.geo.crs import apply_offset, to_5186


@dataclass
class BuildingSolid:
    name: str
    footprint_m: list[tuple[float, float]]   # 단일 폴리곤 외곽 (로컬 미터좌표, 닫힘점 제거)
    base_z_m: float
    height_m: float
    floors: int | None                       # None = gro_flo_co 누락/0 (확인 불가)
    attrs: dict                              # bd_mgt_sn, buld_nm, rd_nm 등


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


def _exterior_rings(geom: dict) -> list[list[tuple[float, float]]]:
    """Polygon/MultiPolygon → 외곽 링 목록. 각 링은 (lon,lat) 리스트(닫힘점 제거)."""
    if not geom:
        return []
    g = shape(geom)
    if g.geom_type == "MultiPolygon":
        polys = list(g.geoms)
    elif g.geom_type == "Polygon":
        polys = [g]
    else:
        return []
    rings = []
    for poly in polys:
        coords = [(float(x), float(y)) for x, y in poly.exterior.coords]
        if len(coords) >= 2 and coords[0] == coords[-1]:
            coords = coords[:-1]   # GeoJSON 닫힘 중복 정점 제거
        if len(coords) >= 3:
            rings.append(coords)
    return rings


def collect_5186_coords(features: list[dict]) -> list[tuple[float, float]]:
    """모든 건물 정점을 EPSG:5186 으로 변환해 모은다(origin offset 산출용)."""
    coords: list[tuple[float, float]] = []
    for feat in features:
        for ring in _exterior_rings(feat.get("geometry")):
            coords.extend(to_5186(lon, lat) for lon, lat in ring)
    return coords


def features_to_solids(
    features: list[dict],
    floor_h_m: float = DEFAULT_FLOOR_H_M,
    offset: tuple[float, float] = (0.0, 0.0),
    default_floors: int = 1,
) -> list[BuildingSolid]:
    """LT_C_SPBD 피처 → BuildingSolid 목록.

    MultiPolygon 은 폴리곤별로 분리. floors 누락 시 default_floors 로 높이만 부여하되
    floors=None 을 보존(확인 불가 표시). base_z 는 0(지형은 Phase 3에서 앉힘).
    """
    solids: list[BuildingSolid] = []
    for feat in features:
        props = feat.get("properties") or {}
        floors = floors_of(props)
        n_floors = floors if floors is not None else default_floors
        height = n_floors * floor_h_m
        name = props.get("buld_nm") or props.get("bd_mgt_sn") or "building"
        attrs = {
            k: props.get(k)
            for k in ("bd_mgt_sn", "buld_nm", "rd_nm")
            if props.get(k)
        }
        for ring in _exterior_rings(feat.get("geometry")):
            fp_5186 = [to_5186(lon, lat) for lon, lat in ring]
            fp_local = apply_offset(fp_5186, offset)
            solids.append(
                BuildingSolid(
                    name=name,
                    footprint_m=fp_local,
                    base_z_m=0.0,
                    height_m=height,
                    floors=floors,
                    attrs=attrs,
                )
            )
    return solids


def extrude_face_loops(n: int) -> list[list[int]]:
    """돌출 솔리드의 면을 정점 인덱스 루프로 반환 (SketchUp 무관, 검증용).

    정점 배열은 [바닥 0..n-1] + [천장 n..2n-1] 순서.
    면 = 바닥(하향) + 천장(상향) + 변마다 수직 쿼드 → 총 n+2 면 (사양서 §6.2).
    """
    if n < 3:
        raise ValueError(f"footprint must have >= 3 vertices, got {n}")
    faces = [list(range(n - 1, -1, -1)), [n + i for i in range(n)]]
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j, n + i])   # 옆면 = 쿼드
    return faces
