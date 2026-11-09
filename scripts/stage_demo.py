"""Copy finished runs into demo_runs/ so a deploy can bake them into the image.

Usage: uv run python scripts/stage_demo.py runs/ablate/full:kuku-pack runs/demo-lamp:sample-show

Why: the demo runs include the client's scripts, so they never go to git. A CLI
deploy uploads demo_runs/ (git-ignored, kept by .vercelignore) and the Dockerfile
copies it into /data/runs, where the access code guards it. Only what the review
UI reads is copied: never llm_calls/ (full prompts), owner tokens or old decisions.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

DEST = Path("demo_runs")
SKIP = {"llm_calls", ".owner_token", "decisions.jsonl", "debug_ekg.png"}


def stage(src: Path, name: str) -> int:
    if not (src / "run_report.json").exists() or not (src / "08_slate.json").exists():
        raise SystemExit(f"{src} is not a finished run (no run_report.json / 08_slate.json)")
    out = DEST / name
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(src, out, ignore=lambda d, names: [n for n in names if n in SKIP])
    return sum(f.stat().st_size for f in out.rglob("*") if f.is_file())


def main(args: list[str]) -> None:
    if not args:
        raise SystemExit(__doc__)
    DEST.mkdir(exist_ok=True)
    for a in args:
        src, _, name = a.partition(":")
        size = stage(Path(src), name or Path(src).name)
        print(f"staged {src} -> {DEST / (name or Path(src).name)} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1:])
