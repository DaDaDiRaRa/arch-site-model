"""스카이라인 종/횡단면 (로드맵 B-2, 경관심의). 건물 상단 실루엣을 축에 투영한 2D 프로파일.

현황+제안 건물을 횡단(동–서, x축)·종단(남–북, y축)에 투영해, 축 위치 t마다 최고 상단 표고
(silhouette)를 낸다. before(현황) vs after(현황+제안)로 제안이 스카이라인에 주는 영향을 본다.
2D 차트 데이터(프론트가 SVG로 렌더). 좌표: 로컬 미터. None = 하늘(그 위치에 건물 없음).
"""

from __future__ import annotations


def _silhouette(buildings, ts, axis):
    """축(ax,ay) 위 샘플 ts마다 최고 상단 표고(bz+h). 건물 없으면 None."""
    ax, ay = axis
    top: list[float | None] = [None] * len(ts)
    for fp, bz, h in buildings:
        proj = [x * ax + y * ay for x, y in fp]
        lo, hi = min(proj), max(proj)
        topz = round(bz + h, 2)
        for i, t in enumerate(ts):
            if lo <= t <= hi and (top[i] is None or topz > top[i]):
                top[i] = topz
    return top


def _profile(existing, proposed_b, axis, name, n=160):
    allb = existing + proposed_b
    tvals = [x * axis[0] + y * axis[1] for fp, _, _ in allb for x, y in fp]
    if not tvals:
        return None
    tmin, tmax = min(tvals), max(tvals)
    if tmax - tmin < 1e-6:
        return None
    ts = [round(tmin + (tmax - tmin) * i / (n - 1), 2) for i in range(n)]
    return {
        "name": name,
        "t": ts,
        "before": _silhouette(existing, ts, axis),
        "after": _silhouette(allb, ts, axis),
        "ground": round(min(bz for _, bz, _ in allb), 2),
    }


def build_skylines(solids, proposed=None) -> list[dict]:
    """현황 solids(+제안 dict)로 횡단·종단 스카이라인 프로파일 목록. 건물 없으면 []."""
    existing = [
        (s.footprint_m, s.base_z_m, s.height_m)
        for s in solids
        if getattr(s, "height_m", 0) > 0 and len(s.footprint_m) >= 3
    ]
    proposed_b = []
    if proposed and proposed.get("footprint") and proposed.get("height", 0) > 0:
        proposed_b = [(proposed["footprint"], proposed["base_z"], proposed["height"])]
    out = []
    for axis, name in (((1.0, 0.0), "횡단 (동–서)"), ((0.0, 1.0), "종단 (남–북)")):
        p = _profile(existing, proposed_b, axis, name)
        if p:
            out.append(p)
    return out
