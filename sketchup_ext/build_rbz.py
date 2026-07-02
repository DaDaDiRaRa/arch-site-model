"""SketchUp 확장 폴더 → .rbz 패키징.

.rbz 는 확장 로더(.rb)와 서브폴더를 담은 zip이다(확장자만 .rbz). SketchUp
확장 관리자(Extension Manager)에서 이 파일을 설치한다.

    # 개발용(기본 백엔드=localhost:8000)
    python sketchup_ext/build_rbz.py

    # 배포용(기본 백엔드=Cloud Run URL 주입 → 팀원은 URL 입력 불필요)
    python sketchup_ext/build_rbz.py --backend-url https://arch-site-model-xxxx.run.app

    → sketchup_ext/dist/arch_site_model.rbz

zip 루트 구조:
    arch_site_model.rb
    arch_site_model/{main,settings,api_client,builder}.rb
    arch_site_model/dialog.html

--backend-url 을 주면 zip에 들어가는 settings.rb 의 DEFAULT_BACKEND 한 줄만 그 URL로
치환한다(디스크의 소스는 건드리지 않음 — 소스 기본값은 localhost 유지).
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOADER = "arch_site_model.rb"
PKG_DIR = "arch_site_model"
SETTINGS_REL = f"{PKG_DIR}/settings.rb"
INCLUDE_SUFFIXES = {".rb", ".html"}
OUT = HERE / "dist" / "arch_site_model.rbz"

_DEFAULT_RE = re.compile(r'DEFAULT_BACKEND = ".*?"\.freeze')


def _members() -> list[Path]:
    members = [HERE / LOADER]
    for p in sorted((HERE / PKG_DIR).rglob("*")):
        if p.is_file() and p.suffix.lower() in INCLUDE_SUFFIXES:
            members.append(p)
    return members


def _inject_backend(settings_text: str, url: str) -> str:
    """settings.rb 내용의 DEFAULT_BACKEND 한 줄을 url로 치환."""
    new_line = f'DEFAULT_BACKEND = "{url}".freeze'
    result, n = _DEFAULT_RE.subn(new_line, settings_text)
    if n != 1:
        raise RuntimeError(
            f"settings.rb 에서 DEFAULT_BACKEND 치환 대상 {n}건 (1건이어야 함)."
        )
    return result


def build(backend_url: str | None = None) -> Path:
    if backend_url and not re.match(r"^https?://", backend_url):
        raise ValueError(f"backend-url은 http(s)://로 시작해야 합니다: {backend_url!r}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    members = _members()
    missing = [m for m in members if not m.exists()]
    if missing:
        raise FileNotFoundError(f"누락 파일: {missing}")

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for m in members:
            arcname = m.relative_to(HERE).as_posix()
            if backend_url and arcname == SETTINGS_REL:
                text = m.read_text(encoding="utf-8")
                zf.writestr(arcname, _inject_backend(text, backend_url))
            else:
                zf.write(m, arcname)
    return OUT


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SketchUp 확장 → .rbz 패키징")
    ap.add_argument(
        "--backend-url",
        help="배포용 기본 백엔드 URL (예: https://...run.app). 생략 시 소스 기본값(localhost).",
    )
    args = ap.parse_args()

    out = build(args.backend_url)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    baked = args.backend_url or "http://localhost:8000 (소스 기본값)"
    print(f"생성: {out}  ({len(names)} 파일)")
    print(f"기본 백엔드: {baked}")
    for n in names:
        print("  ", n)
