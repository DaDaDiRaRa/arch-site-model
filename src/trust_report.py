"""데이터 신뢰도 리포트 (로드맵 A-1) — 파이프라인 결과 위의 순수 뷰 함수.

`generate()` 결과(provenance + stats + qa + outputs)를 읽어 "이 모델을 얼마나 믿을 수 있나"를
한 장으로 요약한다. **신규 데이터 취득 0** — 이미 조립된 result만 읽는다(엔진은 데이터만 만들고
소비자는 렌더만 한다는 정사영상/QA와 동일 원칙). 소비자: 웹 패널·인쇄 1페이지·.3dm/.skp 노트.

핵심 지표 "실측 vs 추정" 층수는 `stats.with_floors`(gro_flo_co 있는 건물 수)와 `stats.buildings`
(총 건물 수)로 계산 — BuildingSolid 수정 불필요. 상세: docs/trust_and_utilization_roadmap.md §4-A1.
"""

from __future__ import annotations

from src.config import TERRAIN_MAX_ERROR_M


def _terrain_triangles(outputs: dict) -> int:
    """skp/3dm 어느 출력이든 지형 삼각형 수를 집는다(0 = 지형 없음)."""
    for k in ("skp", "3dm"):
        o = outputs.get(k)
        if o and o.get("terrain_triangles"):
            return int(o["terrain_triangles"])
    return 0


def _orthophoto(outputs: dict) -> dict | None:
    """3dm/skp 출력에서 orthophoto 블록(image·missing_tiles·zoom)을 집는다."""
    for k in ("3dm", "skp"):
        o = (outputs.get(k) or {}).get("orthophoto")
        if o:
            return o
    return None


def build_trust_report(result: dict) -> dict:
    """generate() result → 신뢰도 리포트 dict. 결측 키에 안전(건물만 생성돼도 동작)."""
    stats = result.get("stats") or {}
    prov = result.get("provenance") or {}
    outputs = result.get("outputs") or {}
    qa = result.get("qa")
    qa_summary = qa.get("summary") if isinstance(qa, dict) else None

    total = int(stats.get("buildings") or 0)
    measured = int(stats.get("with_floors") or 0)
    estimated = max(0, total - measured)
    measured_pct = round(measured / total * 100) if total else 0

    floor_h = prov.get("floor_height_m")

    # 지형 — DEM 타일이 붙었거나 표고범위가 잡혔을 때만.
    elev = stats.get("elev_range_m")
    terrain = None
    if prov.get("terrain_tile") or elev:
        terrain = {
            "source": prov.get("terrain_tile"),
            "max_error_m": TERRAIN_MAX_ERROR_M,
            "elev_range_m": elev,
            "triangles": _terrain_triangles(outputs),
        }

    # 정사영상 — 출처가 기록됐거나 출력에 ortho 블록이 있을 때만.
    o = _orthophoto(outputs)
    ortho = None
    if prov.get("orthophoto_src") or o:
        ortho = {
            "source": prov.get("orthophoto_src"),
            "zoom": prov.get("orthophoto_zoom") or (o.get("zoom") if o else None),
            "missing_tiles": (o.get("missing_tiles") if o else None),
        }

    # 정직한 한계 고지 — 임원이 스스로 발견할 약점을 먼저 명시(신뢰의 핵심).
    caveats: list[str] = []
    if floor_h is not None:
        caveats.append(f"건물 높이는 실제 층수 × {floor_h}m 층고 가정이며 실측 높이가 아닙니다.")
    else:
        caveats.append("건물 높이는 층수 × 가정 층고이며 실측 높이가 아닙니다.")
    if estimated > 0:
        caveats.append(f"층수 미확인 {estimated}동은 기본 층수로 추정되었습니다.")
    if ortho and ortho.get("missing_tiles"):
        caveats.append(f"정사영상 {ortho['missing_tiles']}장이 결측(회색)입니다.")
    caveats.append("고가·교량은 DSM 부재로 제외될 수 있습니다.")

    return {
        "address": result.get("address"),
        "buildings": {
            "total": total,
            "measured": measured,
            "estimated": estimated,
            "measured_pct": measured_pct,
        },
        "terrain": terrain,
        "orthophoto": ortho,
        "qa": qa_summary,
        "meta": {
            "fetched_at": prov.get("fetched_at"),
            "radius_m": prov.get("radius_m"),
            "missing_floors_policy": prov.get("missing_floors_policy"),
            "floor_height_m": floor_h,
            "crs": "EPSG:5186",
            "building_src": prov.get("building_src"),
        },
        "caveats": caveats,
    }
