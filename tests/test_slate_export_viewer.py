"""Scoring, MMR selection, exports (CMX3600), perceived rendering and referents."""

from __future__ import annotations

import asyncio
import csv
import re

import pytest

from coldopen.config import Config
from coldopen.export import edl, write_csv
from coldopen.models import Promo, Scores, Segment, Slate
from coldopen.render import perceived_atom
from coldopen.slate import select_slate
from coldopen.snap import validate_one
from coldopen.viewer import clip_text, referent_check
from tests.helpers import cand, mini

SHOW, BIBLE, IX = mini()
CFG = Config(models={"miner": "m/a", "judge": "m/b", "classifier": "m/c"})


def promo(pid, total, engine="other", ep=1, start=0.0, end=30.0, policy="paid_safe"):
    seg = Segment(episode=ep, start=start, end=end, start_tc="", end_tc="", atom_ids=[])
    s = Scores(hook=0.5, cliff=0.5, viewer=0.5, tension=0.5, quality=0.5, policy_penalty=0, spoiler_penalty=0,
               paywall_penalty=0, referent_penalty=0, total=total)
    return Promo(id=pid, structure="straight", segments=[seg], duration=end - start, hook_text="h", hook_speaker=None,
                 hook_at=1.0, cliffhanger_text="c", engine=engine, why="w", landing_episode=ep, free_runway=5,
                 policy=policy, policy_reasons=[], reveals_disclosed=[], ungrounded_refs=[], viewer={}, scores=s,
                 validation={}, continuity_warnings=[])


def test_mmr_prefers_diversity_over_near_duplicates():
    ps = [
        promo("P001", 0.80, "humiliation_reversal", ep=1, start=0, end=30),
        promo("P002", 0.79, "humiliation_reversal", ep=1, start=2, end=32),  # same engine, same footage
        promo("P003", 0.70, "dangerous_protector", ep=7),
        promo("P004", 0.60, "hidden_identity", ep=12),
    ]
    sl = select_slate(ps, CFG)
    assert sl.hero == "P001"
    assert sl.challengers[:2] == ["P003", "P004"]
    assert sl.challengers[2] == "P002"
    assert "Hero" in sl.rationale["P001"] and "new to the slate" in sl.rationale["P003"]


def _vc():
    c = cand([("E01.016", "E01.019"), ("E02.008", "E02.013")], "E01.017", "E02.013", IX, structure="arc_bridge")
    vc, _ = asyncio.run(validate_one("AB-01", "arc_bridge", c, IX, None, BIBLE))
    vc.promo_id = "P007"
    return vc


def test_edl_is_cmx3600_with_contiguous_record_times():
    vc = _vc()
    p = promo("P007", 0.5)
    p.segments = vc.segments
    text = edl(p, fps=30)
    lines = text.splitlines()
    assert lines[0] == "TITLE: P007" and lines[1] == "FCM: NON-DROP FRAME"
    events = [ln for ln in lines if re.match(r"^\d{3}  ", ln)]
    assert len(events) == 2
    pat = re.compile(r"^(\d{3})  (E\d{3})     AA/V  C        (\S{11}) (\S{11}) (\S{11}) (\S{11})$")
    m1, m2 = pat.match(events[0]), pat.match(events[1])
    assert m1 and m2
    assert m1.group(2) == "E001" and m2.group(2) == "E002"
    assert m1.group(3) == "00:00:29:00" and m1.group(5) == "00:00:00:00"
    assert m1.group(6) == m2.group(5)  # record out of event 1 == record in of event 2
    assert "* FROM CLIP NAME: episode-001" in lines
    tc = lambda s: (lambda h, m, sec, f: ((h * 60 + m) * 60 + sec) * 30 + f)(*map(int, s.split(":")))  # noqa: E731
    for m in (m1, m2):
        assert tc(m.group(4)) - tc(m.group(3)) == tc(m.group(6)) - tc(m.group(5))


def test_csv_one_row_per_segment(tmp_path):
    vc = _vc()
    p = promo("P007", 0.5)
    p.segments = vc.segments
    write_csv(tmp_path / "p.csv", {"P007": p}, Slate(hero="P007", challengers=[], bench=[], rationale={}))
    rows = list(csv.DictReader((tmp_path / "p.csv").open()))
    assert [r["segment_no"] for r in rows] == ["1", "2"] and rows[0]["slot"] == "hero"


def test_perceived_rendering_hides_names_on_screen_but_keeps_spoken_ones():
    cam = IX.by_id["E02.004"]  # "Medium shot of Corin stepping towards Mara."
    out = perceived_atom(cam, BIBLE)
    assert "Corin" not in out and "Mara" not in out and "the scarred man in a dark hood" in out
    line = IX.by_id["E03.016"]  # Mara says "Corin? ..." off-screen
    out = perceived_atom(line, BIBLE)
    assert out.startswith("a young woman in a grey wool shawl (off-screen):") and "Corin?" in out


def test_clip_text_marks_hard_cuts():
    text = clip_text(_vc(), IX, BIBLE)
    assert "[Hard cut]" in text and "E0" not in text  # no atom IDs reach the cold viewer


def test_referents():
    from coldopen.bible import proper_nouns

    nouns = set(proper_nouns(SHOW))
    c = cand([("E02.001", "E02.003"), ("E02.010", "E02.011")], "E02.003", "E02.011", IX, structure="arc_bridge")
    vc, _ = asyncio.run(validate_one("x", "arc_bridge", c, IX, None, BIBLE))
    # arc_bridge needs 2 episodes -> invalid; build the clip directly instead
    from coldopen.models import Segment as S
    from coldopen.snap import ValidCandidate

    segs = [S(episode=2, start=0.0, end=5.0, start_tc="", end_tc="", atom_ids=["E02.001", "E02.002", "E02.003"])]
    v = ValidCandidate(cid="x", source="episode", raw=c, segments=segs, duration=5, hook_at=1.6, on_camera_cuts=True)
    assert referent_check(v, IX, BIBLE, nouns) == ["Corin", "Ashvale"]  # named, never seen speaking
    segs2 = segs + [S(episode=2, start=5.0, end=9.0, start_tc="", end_tc="", atom_ids=["E02.004", "E02.005"])]
    v2 = ValidCandidate(cid="y", source="episode", raw=c, segments=segs2, duration=9, hook_at=1.6, on_camera_cuts=True)
    assert "Corin" not in referent_check(v2, IX, BIBLE, nouns)  # he speaks in the clip
