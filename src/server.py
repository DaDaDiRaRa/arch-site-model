"""FastMCP 서버 진입점.

도구는 이후 Phase에서 등록한다(사양서 §4):
  - check_site_data     [Phase 1]
  - generate_site_model [Phase 2+]
  - preview_site        [확장]

현재(Phase 0)는 빈 서버 스켈레톤이다. 실행해 서버가 기동되는지만 확인한다.
"""

from mcp.server.fastmcp import FastMCP

from src.site_check import check_site_data as _check_site_data

mcp = FastMCP("arch-site-model")


@mcp.tool()
def check_site_data(address: str, radius_m: int = 250) -> dict:
    """주소의 3D 대지모델 생성 가능성 선검사 (사양서 §4.1).

    주소 → 좌표 → 건물/지적 취득 가능 여부 + 지형 비축 여부를 리포트한다.
    실제 생성은 하지 않는다.
    """
    return _check_site_data(address, radius_m)


# --- 도구 등록 위치 (Phase 2+) ---
# @mcp.tool()
# def generate_site_model(...): ...


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
