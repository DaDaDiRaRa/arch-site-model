"""생성물 자동 QA (검증 자동화).

KBS TopoMap은 생성 후 사람이 눈으로 나쁜 삼각형·건물 침몰·도로 끊김을 찾아 수동 수리한다.
우리는 그 눈검사를 **코드로** 대체한다 — 파이프라인 산출물(건물 solid·지형 mesh·수계)을 받아
실측 실패 모드를 검사하고 구조화된 findings 목록을 낸다("처음부터 정확"의 담보, docs/kbs §6-B).

각 finding: {"severity": "warn"|"info", "kind": str, "message": str, "at": [x,y]|None, "name": str|None}
좌표 at은 로컬 미터(origin_offset 적용). run_qa는 파이프라인이 layers.qa=True일 때 호출한다.
"""

from __future__ import annotations

import math

# 임계값 — 실측 근거 기본값(필요 시 조정).
STEEP_SITE_M = 3.0     # 건물 footprint 아래 지형 표고차가 이보다 크면 급경사(평면 base 부정확)
FLOAT_M = 1.5          # base_z가 footprint 아래 지형 최저보다 이만큼 위 = 부유
SINK_M = 1.5           # base_z가 지형 최저보다 이만큼 아래 = 침몰(파묻힘)
OVERLAP_FRAC = 0.5     # 두 건물 footprint 겹침이 작은 쪽 면적의 이 비율 초과 = 중복/오류
SPIKE_M = 6.0          # 지형 정점이 이웃 평균과 이만큼 차이 = 스파이크/웅덩이
TINY_AREA_M2 = 2.0     # footprint 면적이 이보다 작으면 슬리버(데이터 오류 의심)
MAX_FINDINGS_PER_KIND = 40  # 종류별 상한(스팸 방지, 초과분은 요약에 개수만)

# finding kind → 심의/검수 실무 라벨 (내부 코드명을 사람이 읽는 말로). A-3.
KIND_LABELS = {
    "steep_site": "급경사 대지",
    "building_float": "건물 부유",
    "building_sink": "건물 침몰",
    "building_no_terrain": "지형 커버리지 밖",
    "building_overlap": "건물 겹침",
    "footprint_invalid": "부정형 footprint",
    "footprint_tiny": "초소형 footprint",
    "terrain_spike": "지형 돌출·웅덩이",
}


def _grid_z(dem, x, y):
    """dem.grid에서 로컬 (x,y) 셀 표고. 범위밖/nan이면 None.

    elev_at은 nan/범위밖을 0.0으로 돌려줘(수면으로 오해) QA엔 부적합 → 격자 직접 샘플로 nan 감지.
    """
    tf = dem.transform
    ox, oy = dem.offset
    ax, ay = x + ox, y + oy
    if tf.a == 0 or tf.e == 0:
        return None
    col = int((ax - tf.c) / tf.a)
    row = int((ay - tf.f) / tf.e)
    g = dem.grid
    if 0 <= row < g.shape[0] and 0 <= col < g.shape[1]:
        v = float(g[row, col])
        return v if math.isfinite(v) else None
    return None


def _centroid(ring):
    n = len(ring)
    if n == 0:
        return None
    return [round(sum(p[0] for p in ring) / n, 2), round(sum(p[1] for p in ring) / n, 2)]


def _check_seating(solids, dem, out):
    """건물 앉힘 — 급경사·부유·침몰·지형밖. dem 없으면 생략."""
    if dem is None:
        return
    steep = float_ = sink = noterr = 0
    for s in solids:
        fp = s.footprint_m
        if len(fp) < 3:
            continue
        cov = [_grid_z(dem, x, y) for x, y in fp]  # 범위밖/NaN 감지용(코너 셀)
        if any(z is None for z in cov):
            if noterr < MAX_FINDINGS_PER_KIND:
                out.append(_f("info", "building_no_terrain",
                             f"건물 '{s.name}' footprint 일부가 DEM 커버리지 밖 — 앉힘 부정확 가능",
                             _centroid(fp), s.name))
            noterr += 1
        # 값 비교는 seat_building과 동일한 bilinear sample로(샘플링 불일치 false 경고 방지).
        zs = [z for z in (dem.sample(x, y) for x, y in fp) if z is not None]
        if not zs:
            continue
        tmin, tmax = min(zs), max(zs)
        if tmax - tmin > STEEP_SITE_M and steep < MAX_FINDINGS_PER_KIND:
            out.append(_f("warn", "steep_site",
                         f"건물 '{s.name}' 아래 지형 표고차 {tmax - tmin:.1f}m (급경사) — "
                         "평면 base 박스가 한쪽은 파묻히고 한쪽은 뜰 수 있음",
                         _centroid(fp), s.name))
            steep += 1
        if s.base_z_m - tmin > FLOAT_M and float_ < MAX_FINDINGS_PER_KIND:
            out.append(_f("warn", "building_float",
                         f"건물 '{s.name}' base_z가 지형 최저보다 {s.base_z_m - tmin:.1f}m 위 (부유)",
                         _centroid(fp), s.name))
            float_ += 1
        if tmin - s.base_z_m > SINK_M and sink < MAX_FINDINGS_PER_KIND:
            out.append(_f("warn", "building_sink",
                         f"건물 '{s.name}' base_z가 지형 최저보다 {tmin - s.base_z_m:.1f}m 아래 (침몰)",
                         _centroid(fp), s.name))
            sink += 1


def _check_overlap(solids, out):
    """건물 footprint 큰 겹침 — 중복 feature/데이터 오류. STRtree로 후보만 검사."""
    try:
        from shapely.geometry import Polygon
        from shapely.strtree import STRtree
    except Exception:  # noqa: BLE001
        return
    polys, keep = [], []
    for s in solids:
        if len(s.footprint_m) < 3:
            continue
        try:
            p = Polygon(s.footprint_m)
            if p.is_valid and p.area > 0:
                polys.append(p)
                keep.append(s)
        except Exception:  # noqa: BLE001
            continue
    if len(polys) < 2:
        return
    tree = STRtree(polys)
    seen: set[tuple[int, int]] = set()
    n = 0
    for i, p in enumerate(polys):
        for j in tree.query(p):
            j = int(j)
            if j <= i:
                continue
            key = (i, j)
            if key in seen:
                continue
            seen.add(key)
            q = polys[j]
            if not p.intersects(q):
                continue
            inter = p.intersection(q).area
            if inter > OVERLAP_FRAC * min(p.area, q.area):
                if n < MAX_FINDINGS_PER_KIND:
                    out.append(_f("warn", "building_overlap",
                                 f"건물 '{keep[i].name}'·'{keep[j].name}' footprint {inter:.0f}m² 겹침 "
                                 "(중복 feature/데이터 오류 의심)",
                                 _centroid(keep[i].footprint_m), keep[i].name))
                n += 1


def _check_footprints(solids, out):
    """건물 footprint 유효성 — 자기교차(invalid)·슬리버(초소형). 데이터 오류 신호."""
    try:
        from shapely.geometry import Polygon
    except Exception:  # noqa: BLE001
        return
    invalid = tiny = 0
    for s in solids:
        fp = s.footprint_m
        if len(fp) < 3:
            continue
        try:
            p = Polygon(fp)
        except Exception:  # noqa: BLE001
            continue
        if not p.is_valid and invalid < MAX_FINDINGS_PER_KIND:
            out.append(_f("warn", "footprint_invalid",
                         f"건물 '{s.name}' footprint 자기교차/부정형 (shapely invalid)",
                         _centroid(fp), s.name))
            invalid += 1
        elif p.area < TINY_AREA_M2 and tiny < MAX_FINDINGS_PER_KIND:
            out.append(_f("info", "footprint_tiny",
                         f"건물 '{s.name}' footprint {p.area:.1f}m² (슬리버 의심)",
                         _centroid(fp), s.name))
            tiny += 1


def _check_terrain_spikes(terrain_mesh, m2i, out):
    """지형 TIN 정점이 이웃 평균과 SPIKE_M 이상 차이 = 스파이크/웅덩이(등고선 오류·보간 오버슈트)."""
    if terrain_mesh is None or not terrain_mesh.vertices or not terrain_mesh.triangles:
        return
    verts = terrain_mesh.vertices
    nv = len(verts)
    zs = [v[2] / m2i for v in verts]  # 인치 → 미터
    nbr_sum = [0.0] * nv
    nbr_cnt = [0] * nv
    for a, b, c in terrain_mesh.triangles:
        for u, w in ((a, b), (b, c), (c, a)):
            if 0 <= u < nv and 0 <= w < nv:
                nbr_sum[u] += zs[w]; nbr_cnt[u] += 1
                nbr_sum[w] += zs[u]; nbr_cnt[w] += 1
    n = 0
    worst = []
    for i in range(nv):
        if nbr_cnt[i] == 0:
            continue
        dev = zs[i] - nbr_sum[i] / nbr_cnt[i]
        if abs(dev) > SPIKE_M:
            worst.append((abs(dev), i, dev))
    worst.sort(reverse=True)
    for adev, i, dev in worst[:MAX_FINDINGS_PER_KIND]:
        out.append(_f("warn", "terrain_spike",
                     f"지형 정점이 이웃보다 {dev:+.1f}m ({'스파이크' if dev > 0 else '웅덩이'})",
                     [round(verts[i][0] / m2i, 2), round(verts[i][1] / m2i, 2)], None))
        n += 1


def _f(severity, kind, message, at, name):
    return {"severity": severity, "kind": kind, "label": KIND_LABELS.get(kind, kind),
            "message": message, "at": at, "name": name}


def run_qa(solids, dem=None, terrain_mesh=None, m2i: float = 39.3701) -> dict:
    """생성물 자동 검증 → {"findings": [...], "summary": {...}}.

    solids: BuildingSolid 목록(로컬 미터, seated). dem: 앉힘/지형밖 검사용(로컬 offset). terrain_mesh:
    스파이크 검사용(정점 인치). 검사는 실패해도 조용히 건너뛴다(QA가 생성 자체를 막지 않게).
    """
    findings: list[dict] = []
    for check in (
        lambda: _check_seating(solids, dem, findings),
        lambda: _check_overlap(solids, findings),
        lambda: _check_footprints(solids, findings),
        lambda: _check_terrain_spikes(terrain_mesh, m2i, findings),
    ):
        try:
            check()
        except Exception:  # noqa: BLE001 — QA 실패가 생성을 막지 않음
            continue

    by_kind: dict[str, int] = {}
    warns = 0
    for f in findings:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
        if f["severity"] == "warn":
            warns += 1
    passed = warns == 0  # 경고 0건 = 검수 통과(info는 참고일 뿐 통과 방해 안 함)
    if not findings:
        stamp = "검수 통과 — 결함 없음"
    elif passed:
        stamp = f"검수 통과 — 참고 {len(findings)}건"
    else:
        stamp = f"경고 {warns}건 — 검토 필요"
    return {
        "findings": findings,
        "summary": {
            "total": len(findings), "warnings": warns, "passed": passed,
            "stamp": stamp, "by_kind": by_kind,
        },
    }
