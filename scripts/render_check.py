"""생성물을 PNG로 렌더해 **눈으로 볼 수 있게** 한다 — 데스크톱·브라우저 없이.

SketchUp·Rhino·브라우저를 띄우지 않고도 "교량 데크가 수면 위에 떠 있나", "제방 마루가
섰나"를 바로 확인할 수 있어야 한다. 이 스크립트는 `pipeline.generate`의 geometry를 받아
두 장을 그린다.

1. **종단면도** — 데크 중심선을 따라 지형·수면·노면·데크 표고를 겹쳐 그린다. 코즈웨이
   (도로가 수면 아래로 내려감)는 이 그림에서 한눈에 보인다. 가장 진단력이 높다.
2. **평면도** — 지형 음영 위에 도로·보도·데크·수계·건물·단차선을 색으로 얹는다.
   데크가 **어디**에 생겼는지, 지형 가운데 구멍이 났는지 확인용.

`--compare` 를 주면 `DECK_SOURCE=off`로 한 번 더 생성해 **같은 축으로 전/후**를 나란히
그린다 — 기능이 실제로 무엇을 바꿨는지 보여 주는 가장 정직한 방법이다.

    python scripts/render_check.py "서울특별시 용산구 이촌동 302" --radius 400 --compare
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa: E402

def _korean_font() -> str:
    """한글 라벨이 □로 깨지지 않게 — ttflist에 없는 .ttf도 파일로 직접 등록한다."""
    from matplotlib import font_manager

    for path in (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\gulim.ttc"):
        if Path(path).exists():
            try:
                font_manager.fontManager.addfont(path)
                return font_manager.FontProperties(fname=path).get_name()
            except Exception:  # noqa: BLE001
                continue
    return "DejaVu Sans"


matplotlib.rcParams["font.family"] = _korean_font()
matplotlib.rcParams["axes.unicode_minus"] = False

C = {
    "terrain": "#9ab87f", "road": "#74797f", "sidewalk": "#b0aca0",
    "deck": "#d94f3d", "water": "#3a6ea5", "building": "#4682b4",
    "wall": "#8c5c3c", "scarp": "#967846",
}


def _tris(mesh, scale=1.0):
    """mesh dict → (삼각형 꼭짓점 배열(N,3,2), 삼각형별 평균 z)."""
    if not mesh or not mesh.get("triangles"):
        return None, None
    V = np.asarray(mesh["vertices"], dtype=float) * scale
    T = np.asarray(mesh["triangles"], dtype=int)
    return V[T][:, :, :2], V[T][:, :, 2].mean(axis=1)


def plan_view(ax, g, title):
    """평면도 — 지형 음영 + 레이어 색."""
    tv, tz = _tris(g.get("terrain"))   # geometry의 지형 정점은 이미 미터다
    if tv is not None:
        lo, hi = np.percentile(tz, [2, 98])
        shade = np.clip((tz - lo) / max(hi - lo, 1e-6), 0, 1)
        ax.add_collection(PolyCollection(
            tv, facecolors=plt.cm.terrain(0.25 + 0.5 * shade), edgecolors="none", zorder=1))
    for key, z in (("sidewalks", 2), ("roads", 3), ("water", 4), ("decks", 6)):
        v, _ = _tris(g.get(key))
        if v is not None:
            ax.add_collection(PolyCollection(
                v, facecolors=C[key[:-1] if key.endswith("s") else key],
                edgecolors="none", alpha=0.95, zorder=z))
    for b in g.get("buildings") or []:
        fp = np.asarray(b["footprint"], dtype=float)
        ax.add_collection(PolyCollection([fp], facecolors=C["building"],
                                         edgecolors="#27303a", linewidths=0.3, zorder=5))
    for key, col in (("walls", "wall"), ("scarps", "scarp")):
        segs = [np.asarray(x["points"], dtype=float)[:, :2] for x in (g.get(key) or [])
                if len(x.get("points") or []) >= 2]
        if segs:
            ax.add_collection(LineCollection(segs, colors=C[col], linewidths=1.0, zorder=7))
    if tv is not None:
        allv = tv.reshape(-1, 2)
        ax.set_xlim(allv[:, 0].min(), allv[:, 0].max())
        ax.set_ylim(allv[:, 1].min(), allv[:, 1].max())
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])


def _station(line):
    P = np.asarray(line, dtype=float)
    d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(P[:, :2], axis=0).T))])
    return P, d


def _sample_mesh_z(mesh, pts, scale=1.0, radius=6.0):
    """메시 정점 중 각 측점 반경 안의 **최고** z (노면은 위에서 보는 값)."""
    if not mesh or not mesh.get("vertices"):
        return np.full(len(pts), np.nan)
    V = np.asarray(mesh["vertices"], dtype=float) * scale
    out = np.full(len(pts), np.nan)
    for i, (x, y) in enumerate(pts):
        d2 = (V[:, 0] - x) ** 2 + (V[:, 1] - y) ** 2
        m = d2 <= radius * radius
        if m.any():
            out[i] = V[m, 2].max()
    return out


def section_view(ax, g, line, title, water_z=None):
    """종단면도 — 데크 중심선을 따라 지형/수면/노면/데크 표고."""
    P, st = _station(line)
    pts = P[:, :2]
    # 통합표면은 도로·데크 **밑 지형을 컬링**한다(정점 공유 삼각화) — 중심선 바로 위엔 지형
    # 정점이 없다. 그래서 주변 지반을 잡도록 반경을 넓게 둔다(강둑·하천 바닥이 잡힌다).
    zt = _sample_mesh_z(g.get("terrain"), pts, radius=40.0)
    zr = _sample_mesh_z(g.get("roads"), pts, radius=6.0)
    zd = _sample_mesh_z(g.get("decks"), pts, radius=6.0)
    zw = _sample_mesh_z(g.get("water"), pts, radius=30.0)

    floor = (np.nanmin(zt) - 3) if np.isfinite(zt).any() else 0.0
    if np.isfinite(zt).any():
        ax.fill_between(st, floor, zt, color="#cbbf9a", zorder=1)
        ax.plot(st, zt, color="#6b5f3a", lw=1.2, label="주변 지형", zorder=2)
    if np.isfinite(zw).any():
        ax.fill_between(st, floor, zw, color=C["water"], alpha=0.45, zorder=3)
        ax.plot(st, zw, color=C["water"], lw=1.6, label="수면", zorder=4)
    elif water_z is not None:
        ax.axhline(water_z, color=C["water"], lw=1.6, label="수면", zorder=4)
    if np.isfinite(zr).any():
        ax.plot(st, zr, color=C["road"], lw=2.2, label="도로 노면", zorder=5)
    if np.isfinite(zd).any():
        ax.plot(st, zd, color=C["deck"], lw=3.0, label="교량 데크", zorder=6)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("중심선 거리 (m)", fontsize=8)
    ax.set_ylabel("표고 (m)", fontsize=8)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7, loc="upper right")
    ax.tick_params(labelsize=7)


def run(address, radius, out_dir, compare):
    from src import config
    from src.pipeline import generate

    L = {"buildings": True, "terrain": True, "roads": True, "water": True,
         "walls": True, "scarps": True, "cadastral": True}

    def gen():
        return generate(address, radius_m=radius, layers=L, outputs=[],
                        include_geometry=True)

    after = gen()
    g_after = after["geometry"]
    # 데크 중심선 = 가장 긴 데크 종단(없으면 수계를 건너는 도로 외곽선 하나)
    decks = g_after.get("decks") or {}
    line = None
    if decks.get("outlines"):
        line = max(decks["outlines"], key=len)

    befores = None
    if compare:
        src = config.DECK_SOURCE
        config.DECK_SOURCE = "off"
        try:
            befores = gen()["geometry"]
        finally:
            config.DECK_SOURCE = src

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "".join(ch if ch.isalnum() else "_" for ch in address)[:40]

    n = 2 if befores else 1
    fig, axes = plt.subplots(1, n, figsize=(7.5 * n, 7.0))
    for ax, g, t in zip(np.atleast_1d(axes),
                        ([befores, g_after] if befores else [g_after]),
                        (["데크 없음 (DECK_SOURCE=off)", "데크 적용"] if befores
                         else ["데크 적용"])):
        plan_view(ax, g, f"{t}")
    fig.suptitle(f"평면도 — {address} 반경 {radius}m", fontsize=11)
    p1 = out_dir / f"{stem}_plan.png"
    fig.savefig(p1, dpi=110, bbox_inches="tight")
    plt.close(fig)

    p2 = None
    if line:
        fig, axes = plt.subplots(n, 1, figsize=(11, 4.2 * n), sharex=True)
        for ax, g, t in zip(np.atleast_1d(axes),
                            ([befores, g_after] if befores else [g_after]),
                            (["데크 없음 — 노면이 지형(강바닥)을 따라간다",
                              "데크 적용 — 노면이 수면 위로 뜬다"] if befores
                             else ["데크 적용"])):
            section_view(ax, g, line, t)
        fig.suptitle(f"교량 종단면 — {address}", fontsize=11)
        p2 = out_dir / f"{stem}_section.png"
        fig.savefig(p2, dpi=110, bbox_inches="tight")
        plt.close(fig)

    st = after["stats"]
    print(f"{address} · 반경 {radius}m")
    print("  건물 %d · 도로 %d · 수계 %d · 옹벽 %d · 데크 %d · 단차 %d" % (
        st["solids"], st["roads"], st["water"], st["walls"], st["decks"], st["scarps"]))
    print(f"  → {p1}")
    if p2:
        print(f"  → {p2}")
    return p1, p2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="생성물을 PNG로 렌더(평면도 + 교량 종단면)")
    ap.add_argument("address")
    ap.add_argument("--radius", type=int, default=400)
    ap.add_argument("--out", default="output/render_check")
    ap.add_argument("--compare", action="store_true",
                    help="DECK_SOURCE=off로 한 번 더 생성해 전/후를 나란히")
    a = ap.parse_args(argv)
    run(a.address, a.radius, a.out, a.compare)
    return 0


if __name__ == "__main__":
    sys.exit(main())
