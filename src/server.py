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
from src.site_check import check_site_data as _check_site_data

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
    setback: bool = False,
) -> dict:
    """주소 → 건물 매싱 3D 대지모델 생성 (사양서 §4.2).

    Phase 2: 건물만 (LT_C_SPBD footprint × gro_flo_co 층수 → 쿼드 솔리드).
    반환의 outputs.skp.code 는 SketchUp MCP build_model 에 넣을 Python 코드.
    origin_offset(stats)은 실제 위치 복원용으로 반드시 보존한다.
    """
    return _generate(
        address,
        radius_m=radius_m,
        floor_h_m=floor_height_m,
        outputs=outputs,
        layers=layers,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
