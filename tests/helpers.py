"""Shared builders for tests on the invented mini show."""

from __future__ import annotations

from pathlib import Path

from coldopen.bible import apply_speakers
from coldopen.models import RawCandidate, SegmentRef, ShowBible
from coldopen.parse import load_show
from coldopen.snap import ShowIndex

FIX = Path(__file__).resolve().parent / "fixtures"


def mini():
    show = load_show(FIX / "mini_show")
    bible = ShowBible.model_validate_json((FIX / "mini_bible.json").read_text())
    apply_speakers(show, bible)
    return show, bible, ShowIndex.build(show)


def cand(segs, hook, cliff, ix, structure="straight", engine="framed_condemned", **kw) -> RawCandidate:
    return RawCandidate(
        structure=structure,
        segments=[SegmentRef(from_id=a, to_id=b) for a, b in segs],
        hook_id=hook,
        cliffhanger_id=cliff,
        engine=engine,
        hook_text=kw.pop("hook_text", ix.by_id[hook].text if hook in ix.by_id else "x"),
        cliffhanger_text=kw.pop("cliffhanger_text", ix.by_id[cliff].text if cliff in ix.by_id else "x"),
        why="test",
        miner_hook=kw.pop("miner_hook", 3),
        miner_cliff=kw.pop("miner_cliff", 3),
        **kw,
    )
