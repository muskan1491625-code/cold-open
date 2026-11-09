"""Exports editors can use directly: promos.csv, promos.md, and CMX3600 EDLs.

EDL conventions: CMX3600, NON-DROP FRAME, one event per segment, reel = E001
style (one source reel per episode), source in/out from segment times, record
times laid end to end from 00:00:00:00. Frame maths is done in integer frames
so record times never drift from rounding.
"""

from __future__ import annotations

import csv
from pathlib import Path

from coldopen.models import Promo, Slate
from coldopen.timecode import fmt_smpte


def slot_of(pid: str, slate: Slate) -> tuple[str, int]:
    if pid == slate.hero:
        return "hero", 1
    if pid in slate.challengers:
        return "challenger", 2 + slate.challengers.index(pid)
    if pid in slate.bench:
        return "bench", 2 + len(slate.challengers) + slate.bench.index(pid)
    return "", 0


def ordered(promos: dict[str, Promo], slate: Slate) -> list[Promo]:
    ids = [slate.hero, *slate.challengers, *slate.bench]
    rest = sorted((p for k, p in promos.items() if k not in ids), key=lambda p: -p.scores.total)
    return [promos[i] for i in ids] + rest


def edl(promo: Promo, fps: int, title: str | None = None) -> str:
    lines = [f"TITLE: {title or promo.id}", "FCM: NON-DROP FRAME", ""]
    rec = 0  # frames
    for n, s in enumerate(promo.segments, 1):
        src_in, src_out = int(round(s.start * fps)), int(round(s.end * fps))
        length = src_out - src_in
        reel = f"E{s.episode:03d}"
        lines.append(
            f"{n:03d}  {reel:<8} AA/V  C        "
            f"{fmt_smpte(src_in / fps, fps)} {fmt_smpte(src_out / fps, fps)} "
            f"{fmt_smpte(rec / fps, fps)} {fmt_smpte((rec + length) / fps, fps)}"
        )
        lines.append(f"* FROM CLIP NAME: episode-{s.episode:03d}")
        lines.append("")
        rec += length
    return "\n".join(lines).rstrip() + "\n"


CSV_COLS = [
    "promo_id", "rank", "slot", "structure", "segment_no", "episode", "start_tc", "end_tc", "duration",
    "hook", "cliffhanger", "engine", "why", "policy", "landing_episode", "total",
]


def write_csv(path: Path, promos: dict[str, Promo], slate: Slate) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for rank, p in enumerate(ordered(promos, slate), 1):
            slot, _ = slot_of(p.id, slate)
            for k, s in enumerate(p.segments, 1):
                w.writerow([
                    p.id, rank, slot, p.structure, k, s.episode, s.start_tc, s.end_tc, round(s.end - s.start, 2),
                    p.hook_text, p.cliffhanger_text, p.engine, p.why, p.policy, p.landing_episode, p.scores.total,
                ])


def write_markdown(path: Path, title: str, promos: dict[str, Promo], slate: Slate) -> None:
    out = [f"# Launch slate: {title}", ""]
    for p in ordered(promos, slate)[: 1 + len(slate.challengers) + len(slate.bench)]:
        slot, _ = slot_of(p.id, slate)
        segs = "; ".join(f"E{s.episode} {s.start_tc}–{s.end_tc}" for s in p.segments)
        out += [
            f"## {p.id} ({slot}), total {p.scores.total:.2f}",
            "",
            f"- **Why this slot:** {slate.rationale.get(p.id, '')}",
            f"- **Cut:** {segs} ({p.duration:.1f} s, {p.structure}); hook at {p.hook_at:.1f} s",
            f"- **Hook:** {p.hook_text}",
            f"- **Cliffhanger:** {p.cliffhanger_text}",
            f"- **Engine:** {p.engine}; **policy:** {p.policy}; **lands on:** E{p.landing_episode} ({p.free_runway} free episodes)",
            f"- **Why:** {p.why}",
            (
                f"- **Scores:** hook {p.scores.hook:.2f}, cliff {p.scores.cliff:.2f}, viewer {p.scores.viewer:.2f}, "
                f"tension {p.scores.tension:.2f}, quality {p.scores.quality:.2f}"
            ),
        ]
        if p.continuity_warnings:
            out.append(f"- **Continuity:** {'; '.join(p.continuity_warnings)}")
        if p.ungrounded_refs:
            out.append(f"- **Unexplained names:** {', '.join(p.ungrounded_refs)}")
        out.append("")
    path.write_text("\n".join(out))


def write_exports(out_dir: Path, title: str, promos: dict[str, Promo], slate: Slate, fps: int) -> dict[str, int]:
    write_csv(out_dir / "promos.csv", promos, slate)
    write_markdown(out_dir / "promos.md", title, promos, slate)
    edl_dir = out_dir / "edl"
    edl_dir.mkdir(exist_ok=True)
    for p in promos.values():
        (edl_dir / f"{p.id}.edl").write_text(edl(p, fps))
    return {"edl_files": len(promos)}
