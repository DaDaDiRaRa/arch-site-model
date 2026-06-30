"""FastMCP 서버 진입점.

도구는 이후 Phase에서 등록한다(사양서 §4):
  - check_site_data     [Phase 1]
  - generate_site_model [Phase 2+]
  - preview_site        [확장]

현재(Phase 0)는 빈 서버 스켈레톤이다. 실행해 서버가 기동되는지만 확인한다.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("arch-site-model")


# --- 도구 등록 위치 (Phase 1+) ---
# @mcp.tool()
# def check_site_data(address: str, radius_m: int = 250) -> dict: ...


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
