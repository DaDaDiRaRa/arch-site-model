"""SketchUp 실기 검증 — 사람 손 없이 .dae 패키지를 데스크톱 SketchUp에 가져와 확인한다.

SketchUp을 `-RubyStartup verify_dae.rb`로 띄우면 스크립트가 가져오기 → (확장의 자동 모서리 정리 대기)
→ 통계·스크린샷 → 저장 → 종료까지 혼자 한다. 창이 1~3분 떴다 저절로 닫힌다.

    python scripts/sketchup_verify/run.py <패키지.zip> [--sketchup "C:/Program Files/SketchUp/SketchUp 2026/SketchUp/SketchUp.exe"]
    python scripts/sketchup_verify/run.py --extension     # 확장 생성 흐름(운영 백엔드 호출→조립) 검증

blank.skp를 함께 열어 시작 화면("SketchUp에 환영합니다")을 건너뛴다 — 직전에 강제 종료됐으면 시작 화면에서
멈춰 스크립트가 아예 안 돌았다(2026-09-21).

결과: 작업 폴더의 report.json(가져오기 성공·크기·재질·텍스처·지형 모서리 soft/hard·자동 정리 횟수)과
shot_iso.png / shot_top.png. 확인할 것: auto_runs[1] > 0 (확장이 가져오기를 감지해 정리함),
z_min_m가 지형 표고 근처(0으로 떨어진 선 없음), ortho_texture에 PNG가 연결됨.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_EXE = r"C:\Program Files\SketchUp\SketchUp 2026\SketchUp\SketchUp.exe"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", nargs="?", help="웹/파이프라인이 만든 *_package.zip")
    ap.add_argument("--extension", action="store_true", help="확장 생성 흐름 검증(verify_extension.rb)")
    ap.add_argument("--address", help="--extension: 생성할 주소(기본 대전 괴정동 358)")
    ap.add_argument("--backend", help="--extension: 백엔드 URL(기본 확장에 박힌 운영 주소, 예: http://127.0.0.1:8000)")
    ap.add_argument("--sketchup", default=DEFAULT_EXE)
    ap.add_argument("--timeout", type=int, default=420)
    a = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="asm_su_verify_"))
    shutil.copy(HERE / "blank.skp", work / "start.skp")
    if a.extension:
        script, report = "verify_extension.rb", work / "ext_report.json"
        shutil.copy(HERE / script, work / script)
        over = {k: v for k, v in (("address", a.address), ("backend", a.backend)) if v}
        if over:
            import json as _json
            (work / "params.json").write_text(_json.dumps(over, ensure_ascii=False), encoding="utf-8")
        _run(a, work, script, report, ["ext_shot.png"])
        return
    if not a.package:
        ap.error("패키지 zip 경로 또는 --extension 이 필요합니다")
    (work / "pkg").mkdir()
    # Ruby 쪽 경로 문제를 피하려 ASCII 이름으로 풀고, .dae 안의 텍스처 참조도 맞춘다.
    z = zipfile.ZipFile(a.package)
    png = next((n for n in z.namelist() if n.endswith("_ortho.png")), None)
    for n in z.namelist():
        if n.endswith(".dae"):
            dae = z.read(n).decode("utf-8")
            if png:
                dae = dae.replace(png, "site_ortho.png")
            (work / "pkg" / "site.dae").write_text(dae, encoding="utf-8")
        elif n == png:
            (work / "pkg" / "site_ortho.png").write_bytes(z.read(n))
    shutil.copy(HERE / "verify_dae.rb", work / "verify_dae.rb")
    _run(a, work, "verify_dae.rb", work / "report.json", ["shot_iso.png", "shot_top.png"])


def _run(a, work: Path, script: str, report: Path, shots: list[str]) -> None:
    proc = subprocess.Popen([a.sketchup, "-RubyStartup", str(work / script), str(work / "start.skp")])
    t0 = time.time()
    while not report.exists() and time.time() - t0 < a.timeout:
        time.sleep(3)
    time.sleep(8)
    if proc.poll() is None:  # 종료 대화상자 등으로 남으면 정리
        proc.kill()
    if not report.exists():
        raise SystemExit(f"보고서 없음 — SketchUp이 {a.timeout}s 안에 끝나지 않았다. 작업 폴더: {work}")
    print(json.dumps(json.loads(report.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    print(f"\n스크린샷: {work / 'shot_iso.png'}\n          {work / 'shot_top.png'}")


if __name__ == "__main__":
    main()
