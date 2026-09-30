"""지형 표면 조립 — 버닝 시퀀스의 **단일 출처**.

`pipeline.generate`(단발)와 `tiles_stream.generate_tile`(대반경 타일)이 같은 순서를 각자
구현하고 있었고 이미 갈라져 있었다 — 타일 경로는 `burn_walls`도 `burn_pads`도 부르지 않아
**같은 주소가 모드에 따라 다른 지형**을 냈다. 레이어가 늘어날수록 그 격차가 벌어지므로
순서를 이 파일 하나로 모은다.

버닝 순서(순서가 결과를 바꾸는 지점만 표시):

    지적 → 도로/보도/중심선 클립 → [dem_natural 스냅샷] → burn_roads
    → burn_walls(건물 발자국 보호) → surface_zs → burn_water → 통합 삼각화 → 스커트

- **건물은 이 함수에 들어오기 전에 이미 지면에 앉아 있다**(`pipeline` §6). 그래서 지반을
  내리는 버닝은 건물 발자국을 보호해야 한다(안 하면 건물이 뜬다 — QA `building_float`).
- `dem_natural`은 도로 버닝 직전 스냅샷이다. 수면 표고(`surface_zs`)는 도로가 깎은 둑이
  아니라 자연 둑에서 읽어야 하므로 그 용도로 남긴다.
- 호출자는 `bbox_4326`(manifest 조회용)과 `bbox_5186`(클립용)을 **짝으로** 넘긴다.
  단발은 사이트 bbox, 타일은 이음매 margin을 더한 clip 영역을 쓴다 — DEM 조회 bbox와
  다를 수 있으므로 `dem`은 이미 클립된 것을 받는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src import config


@dataclass
class SurfaceResult:
    """버닝·삼각화 산출물 묶음. 필드가 None이면 그 레이어가 꺼졌거나 데이터가 없었다."""

    dem: object | None = None            # 모든 버닝이 끝난 DEM (지적·도시계획 드레이프가 씀)
    dem_natural: object | None = None    # 도로 버닝 직전 스냅샷 — 수면 표고 전용
    terrain: object | None = None
    road: object | None = None
    sidewalk: object | None = None
    deck: object | None = None           # 교량 데크 메시(RoadMesh, 로컬 미터)
    water: object | None = None
    lanes: object | None = None
    walls_geom: list | None = None
    road_features: list | None = None    # 통합표면 재구성·QA가 재사용
    sidewalk_features: list | None = None
    centerlines: list | None = None
    water_features: list | None = None
    water_zs: list | None = None
    decks: object | None = None          # deck.DeckSurface — 종단이 풀린 교량 데크
    road_paths: list | None = None       # 읽은 도로 GeoJSON 경로 — 차선 클립이 재사용
    counts: dict = field(default_factory=dict)


def build_surface(
    dem,
    bbox_4326,
    bbox_5186,
    offset,
    layers: dict,
    solids: list,
    warnings: list[str],
    *,
    skirt: bool = True,
) -> SurfaceResult:
    """클립된 DEM + 앉힌 건물 → 버닝된 DEM + 지형/도로/보도/수계 메시.

    dem: 이미 클립된 `DEMPatch` 또는 None(지형 미요청·타일 없음·열기 실패).
    bbox_4326 / bbox_5186: 도로·옹벽·수계 조회와 클립에 쓰는 같은 영역의 두 표현.
    solids: **이미 지면에 앉은** 건물 — 발자국이 버닝 보호 마스크로 쓰인다.
    warnings: 비축 없음·반경 내 없음 등을 append 한다(조용한 fallback 원칙).
    skirt: 지형 둘레에 스커트(벽)를 세운다. **타일 모드는 False** — 타일마다 세우면
        타일 경계에 벽이 생긴다.
    """
    out = SurfaceResult(dem=dem)

    # --- 도로/보도 (Phase R) — 클립 + 버닝 + 차선. 메시는 아래 통합 삼각화에서.
    if layers.get("roads"):
        _roads(out, bbox_4326, bbox_5186, offset, warnings)

    # --- 수계 클립만 먼저. 폴리곤이 교량 프록시(도로 ∩ 수계)의 입력이고, 수면 표고는
    #     도로가 둑을 깎기 **전** DEM에서 읽어야 한다. 버닝·메시는 아래 §수계에서.
    if layers.get("water") and out.dem is not None:
        _water_clip(out, bbox_4326, bbox_5186, offset, warnings)

    # --- 수면 표고 전용 스냅샷: 도로가 둑을 깎기 **전** DEM.
    out.dem_natural = out.dem

    # --- 교량 데크: 지형은 강·골짜기 그대로 두고 데크만 DEM 위로 띄운다. 도로 버닝 **전**에
    #     풀어야 한다 — 제외 마스크와 (강 중간 샘플을 뺀) 중심선이 버닝 입력이기 때문.
    burn_cls = out.centerlines
    if out.dem is not None and out.centerlines:
        _decks(out, bbox_4326, bbox_5186, offset, warnings)
        if out.decks is not None:
            from src.geometry.deck import split_centerlines

            hide = out.decks.polygon()
            hidden = out.decks.hidden_polygon()
            from shapely.ops import unary_union

            cut = unary_union([g for g in (hide, hidden) if g is not None]) or None
            burn_cls = split_centerlines(out.centerlines, cut)

    if out.road_features and burn_cls and out.dem is not None:
        from src.geometry.road import burn_roads

        out.dem = burn_roads(
            out.dem, out.road_features, burn_cls,
            win_m=config.ROAD_SMOOTH_WIN_M,
            sample_m=config.ROAD_CL_SAMPLE_M,
            max_dist_m=config.ROAD_CL_MAX_DIST_M,
            skirt_m=config.ROAD_SKIRT_M,
            max_dev=config.ROAD_MAX_DEV_M,
            exclude_polys=out.decks.exclude_polys() if out.decks else None,
        )

    # 차선(표시용 다차선 마킹)은 버닝된 노면에 드레이프 — 버닝 중심선과 분리.
    if out.dem is not None and (out.road_features or out.sidewalk_features):
        from src.geometry.road import clip_lane_markings, drape_centerlines

        out.lanes = drape_centerlines(
            clip_lane_markings(out.road_paths, bbox_5186, offset), out.dem
        )

    # --- 옹벽(F0040000): 등고선이 완만한 비탈로 뭉갠 레벨차를 실측 높이로 수직 단차 복원.
    #     도로 다음, 수계 앞 — 수계가 이 DEM에서 수면 z를 잡도록.
    if layers.get("walls") and out.dem is not None:
        _walls(out, bbox_4326, bbox_5186, offset, solids, warnings)

    # --- 수계: 표고고정 평면 수면 + 지형을 물 아래로(위에서 클립해 둔 폴리곤으로).
    if out.water_features:
        _water_burn(out)

    # --- 통합 삼각화: 지형·도로·보도를 한 번의 Delaunay로 → 재질별 분리(정점 공유 →
    #     이음매·구멍·z-fighting 구조적 제거). 도로/보도 없으면 일반 TIN.
    _surface(out, skirt, layers)
    return out


# --- 레이어별 -----------------------------------------------------------------


def _roads(out: SurfaceResult, bbox_4326, bbox_5186, offset, warnings) -> None:
    from src.geometry.road import clip_centerlines, clip_roads, clip_sidewalks
    from src.terrain.store import find_road_files

    rfs = find_road_files(bbox_4326)
    if not rfs:
        warnings.append(
            "도로 비축 없음: 반경이 도로 GeoJSON 밖입니다 "
            "(road_manifest.json 확인 또는 road_bake 실행 필요)."
        )
        return
    # 메트로는 도로가 타일로 쪼개져 겹치는 타일이 여럿 — 전부 읽어 합침(하드클립이라 중복 없음).
    paths = [config.road_file_path(rf["file"]) for rf in rfs]
    out.road_paths = paths
    out.road_features = clip_roads(paths, bbox_5186, offset)
    out.sidewalk_features = clip_sidewalks(paths, bbox_5186, offset)
    out.counts["roads"] = len(out.road_features)
    if not out.road_features and not out.sidewalk_features:
        warnings.append("반경 내 도로/보도 폴리곤 없음 (A0010000/A0033320).")
        return
    out.centerlines = (
        clip_centerlines(paths, bbox_5186, offset) if out.dem is not None else []
    )


def _walls(out: SurfaceResult, bbox_4326, bbox_5186, offset, solids, warnings) -> None:
    from src.geometry.wall import burn_walls, clip_walls, walls_to_geometry
    from src.terrain.store import find_wall_files

    wl = find_wall_files(bbox_4326)
    if not wl:
        warnings.append(
            "옹벽 비축 없음: 반경이 옹벽 GeoJSON 밖입니다 "
            "(wall_manifest.json 확인 또는 wall_bake 실행 필요)."
        )
        return
    feats = clip_walls(
        [config.wall_file_path(w["file"]) for w in wl], bbox_5186, offset
    )
    if not feats:
        warnings.append("반경 내 옹벽 없음 (F0040000).")
        return
    # 건물 아래 지면은 건드리지 않는다 — 건물은 이미 지면에 앉아 있다.
    out.dem = burn_walls(
        out.dem, feats, protect_footprints=[s.footprint_m for s in solids]
    )
    out.walls_geom = walls_to_geometry(feats, out.dem)
    out.counts["walls"] = len(feats)


def _water_clip(out: SurfaceResult, bbox_4326, bbox_5186, offset, warnings) -> None:
    """수계 폴리곤 + 수면 표고. **도로 버닝 전**에 부른다.

    수면 표고는 폴리곤 경계(둑) DEM의 저백분위인데, 교량 지점에서 그 경계는 도로 바로 아래를
    지난다. 도로 버닝 후에 읽으면 `burn_roads`가 하천 바닥까지 깎아 놓은 셀을 읽게 되고,
    저백분위는 **바로 그 낮은 이상치를 골라 쓰는** 통계라 편향이 최대가 된다. 그래서 자연 둑
    표고(도로 버닝 전 DEM)에서 읽는다.
    """
    from src.geometry.water import clip_water, surface_zs
    from src.terrain.store import find_water_files

    wfs = find_water_files(bbox_4326)       # 넓은 지역은 수계도 타일 → 겹치는 것 전부
    if not wfs:
        warnings.append(
            "수계 비축 없음: 반경이 수계 GeoJSON 밖입니다 "
            "(water_manifest.json 확인 또는 water_bake 실행 필요)."
        )
        return
    feats = clip_water(
        [config.water_file_path(w["file"]) for w in wfs], bbox_5186, offset
    )
    if not feats:
        warnings.append("반경 내 수계 폴리곤 없음 (E계열).")
        return
    out.water_features = feats
    out.water_zs = surface_zs(feats, out.dem)
    out.counts["water"] = len(feats)


def _water_burn(out: SurfaceResult) -> None:
    from src.geometry.water import build_water_mesh, burn_water

    out.dem = burn_water(out.dem, out.water_features, out.water_zs)
    out.water = build_water_mesh(
        out.water_features, out.water_zs, out.dem, config.WATER_CELL_M
    )


def _decks(out: SurfaceResult, bbox_4326, bbox_5186, offset, warnings) -> None:
    """교량·터널 발자국 확보 → 종단 풀이. `config.DECK_SOURCE`가 데이터원을 고른다.

    "auto"(기본)는 **두 소스를 합친다** — 실측 데크 레이어와 수계 프록시(도로 ∩ 수계).
    둘 다 실측이고 서로를 보완한다: 레이어는 물 없는 곳의 교량·터널까지 주지만 교량이 없는
    타일은 아예 없어 공백이 생기고(실측: 부여 구교리 2km 타일에 교량 폴리곤 없음), 프록시는
    물을 건너는 곳만 보지만 빠짐이 없다. 겹치면 `deck_u` union이 흡수한다.
    "layer"/"water"는 한쪽만 — 두 소스를 교차검증할 때 쓴다.
    """
    from src.geometry import deck as D
    from src.terrain.store import find_deck_files

    src = config.DECK_SOURCE
    if src == "off":
        return
    feats: list = []
    if src in ("auto", "layer"):
        dl = find_deck_files(bbox_4326)
        if dl:
            feats += D.clip_decks(
                [config.deck_file_path(d["file"]) for d in dl], bbox_5186, offset
            )
    if src in ("auto", "water"):
        feats += D.decks_from_water(
            out.water_features or [], out.road_features or [],
            margin_m=config.DECK_MARGIN_M,
        )
    if not feats:
        return
    surface = D.solve_decks(
        feats, out.centerlines, out.dem, out.water_features, out.water_zs,
        sample_m=config.ROAD_CL_SAMPLE_M,
        anchor_span_m=config.DECK_ANCHOR_SPAN_M,
        max_span_m=config.DECK_MAX_SPAN_M,
        min_straightness=config.DECK_MIN_STRAIGHTNESS,
        min_clearance_m=config.DECK_MIN_CLEARANCE_M,
    )
    if surface.empty():
        if surface.dropped:
            # 후보는 있었는데 종단을 못 풀었다 = 양쪽 아붓먼트가 육상에 없다(하천부지를 지나는
            # 도로 등). 교량이 아니므로 만들지 않는 게 맞지만, 조용히 넘기면 진단이 안 된다.
            warnings.append(
                f"교량 후보 {surface.dropped}개를 버렸습니다(양단 육상 표고를 못 읽음 — "
                "수계 폴리곤 안을 지나는 도로일 수 있습니다)."
            )
        return
    out.decks = surface
    out.counts["decks"] = len(surface.profiles)
    if surface.flags:
        # 추정·보정이 개입한 지점은 반드시 드러낸다(단일 앵커 수평 데크, 수면 위로 들어올림 등).
        warnings.append("교량 데크 보정: " + ", ".join(surface.flags))


def _surface(out: SurfaceResult, skirt: bool, layers: dict) -> None:
    dem = out.dem
    if layers.get("terrain") and dem is not None and dem.z_range() is not None:
        if out.road_features or out.sidewalk_features:
            from src.geometry.road import build_unified_surface

            u = build_unified_surface(
                dem, config.TERRAIN_MAX_ERROR_M,
                out.road_features, out.sidewalk_features,
                config.ROAD_CELL_M, config.M2I,
                centerlines=out.centerlines,
                crown_pct=config.ROAD_CROWN_PCT, crown_cap=config.ROAD_CROWN_CAP_M,
                edge_cell=config.ROAD_EDGE_CELL_M,
                decks=out.decks,
            )
            out.terrain, out.road = u.terrain, u.road
            out.sidewalk, out.deck = u.sidewalk, u.deck
        else:
            from src.geometry.terrain_mesh import build_tin

            out.terrain = build_tin(dem, config.TERRAIN_MAX_ERROR_M)

        # 지형 둘레 스커트(벽) — 대지모델을 흙덩어리처럼 마감. 도로 구멍엔 안 세우고 외곽만.
        if skirt and out.terrain is not None and config.TERRAIN_SKIRT_M > 0:
            from src.geometry.terrain_mesh import add_skirt

            out.terrain = add_skirt(out.terrain, config.TERRAIN_SKIRT_M)

    # 통합표면이 안 만들어진 경우(지형 미요청·DEM 없음) 도로/보도는 드레이프 메시로 폴백.
    if out.road is None and out.road_features:
        from src.geometry.road import apply_crown, build_road_mesh

        out.road = build_road_mesh(out.road_features, dem, config.ROAD_CELL_M)
        if out.road is not None and out.centerlines and config.ROAD_CROWN_PCT > 0:
            out.road = apply_crown(
                out.road, out.centerlines, config.ROAD_CROWN_PCT,
                config.ROAD_CL_SAMPLE_M, config.ROAD_CROWN_CAP_M,
            )
    if out.sidewalk is None and out.sidewalk_features:
        from src.geometry.road import build_road_mesh

        out.sidewalk = build_road_mesh(out.sidewalk_features, dem, config.ROAD_CELL_M)
