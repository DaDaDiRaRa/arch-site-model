"""FastMCP 서버 진입점.

도구는 이후 Phase에서 등록한다(사양서 §4):
  - check_site_data     [Phase 1]
  - generate_site_model [Phase 2+]
  - preview_site        [확장]

현재(Phase 0)는 빈 서버 스켈레톤이다. 실행해 서버가 기동되는지만 확인한다.
"""

from mcp.server.fastmcp import FastMCP

from src.config import DEFAULT_FLOOR_H_M
from src.pipeline import generate as _generate
from src.preview import preview_site as _preview_site
from src.site_check import check_site_data as _check_site_data
from src.tiles import generate_tiles as _generate_tiles

mcp = FastMCP("arch-site-model")


@mcp.tool()
def check_site_data(address: str, radius_m: int = 250) -> dict:
    """주소의 3D 대지모델 생성 가능성 선검사 (사양서 §4.1).

    주소 → 좌표 → 건물/지적 취득 가능 여부 + 지형 비축 여부를 리포트한다.
    실제 생성은 하지 않는다.
    """
    return _check_site_data(address, radius_m)


@mcp.tool()
def generate_site_model(
    address: str,
    radius_m: int = 250,
    floor_height_m: float = DEFAULT_FLOOR_H_M,
    outputs: list[str] | None = None,
    layers: dict | None = None,
    output_dir: str | None = None,
    missing_floors_policy: str = "default",
    setback: bool = False,
) -> dict:
    """주소 → 건물 매싱 3D 대지모델 생성 (사양서 §4.2).

    Phase 2: 건물만 (LT_C_SPBD footprint × gro_flo_co 층수 → 쿼드 솔리드).
    Phase 4: outputs=["skp","3dm"] 로 .3dm 이중 출력. output_dir 미지정 시 "output/" 사용.
    Phase 5: layers={"cadastral": True} 로 지적 경계 레이어 추가.
             missing_floors_policy: "default"|"skip"|"flag" (§6.4).
    Tier 1: layers={"terrain": True, "orthophoto": True} 로 지형에 정사영상 텍스처.
            .3dm 전용(SketchUp `.skp`는 이미지 텍스처 미지원) + terrain 필요.
            정사영상 PNG는 .3dm과 같은 output_dir에 저장(같이 두어야 텍스처 참조 유효).
            소스는 config.ORTHO_SOURCE("vworld" 기본 | "ngii", .env). 문제 시 조용한
            fallback(warnings 추가 후 건물/지형만 생성).
    반환의 outputs.skp.code 는 SketchUp MCP build_model 에 넣을 Python 코드.
    반환의 outputs["3dm"]["path"] 는 저장된 .3dm 절대 경로.
    origin_offset(stats)은 실제 위치 복원용으로 반드시 보존한다.
    """
    return _generate(
        address,
        radius_m=radius_m,
        floor_h_m=floor_height_m,
        outputs=outputs,
        layers=layers,
        output_dir=output_dir,
        missing_floors_policy=missing_floors_policy,
        setback=setback,
    )


@mcp.tool()
def preview_site(
    address: str,
    radius_m: int = 250,
    floor_height_m: float = DEFAULT_FLOOR_H_M,
) -> dict:
    """모델 생성 없이 건물 목록·층수·예상 규모를 미리보기.

    generate_site_model 실행 전 "뭐가 들어갈까?"를 사람이 검토할 수 있도록
    건물별 이름·층수·면적·중정 여부를 반환한다.
    실제 .skp/.3dm 파일은 생성하지 않는다.
    """
    return _preview_site(address, radius_m=radius_m, floor_height_m=floor_height_m)


@mcp.tool()
def generate_site_tiles(
    address: str,
    radius_m: int = 500,
    tile_size_m: float = 200.0,
    floor_height_m: float = DEFAULT_FLOOR_H_M,
    layers: dict | None = None,
    missing_floors_policy: str = "default",
) -> dict:
    """대량 건물 반경(500m+)을 tile_size_m 격자로 분할해 build_model 코드를 나눠 생성 (백로그5).

    generate_site_model은 전체를 단일 code 문자열로 반환하므로 밀집 지역·큰 반경에서는
    build_model 호출 인자가 지나치게 커진다(수백 KB). 이 도구는 VWorld 조회/origin_offset은
    한 번만 수행하되(중복 조회·좌표 불일치 방지), footprint 중심점 기준으로 건물을
    tile_size_m(m) 격자에 배정해 타일마다 별도 code로 반환한다.

    반환의 tiles[]는 각 {"tile_id", "tile_bbox_m", "code", "solids", ...}를 담으며,
    오케스트레이터가 타일마다 순서대로 build_model을 호출해 조립한다.
    layers={"terrain": True} 시 지형도 타일 경계에 맞춰 분할된다.
    stats.origin_offset은 모든 타일에 공통 적용되므로 반드시 보존한다.
    """
    return _generate_tiles(
        address,
        radius_m=radius_m,
        tile_size_m=tile_size_m,
        floor_h_m=floor_height_m,
        layers=layers,
        missing_floors_policy=missing_floors_policy,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
