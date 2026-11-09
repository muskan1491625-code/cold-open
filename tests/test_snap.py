"""Stage 5 validators on the invented mini show (exact, hand-checked numbers)."""

from __future__ import annotations

import asyncio

import pytest

from coldopen.config import Config
from coldopen.llm.provider import LLMClient, MockProvider
from coldopen.snap import constraint_report, dedupe, evaluate, fix_midline, interval_iou, validate_one

from tests.helpers import cand, mini

SHOW, BIBLE, IX = mini()


def codes(issues):
    return {c for c, _ in issues}


def test_straight_valid_and_end_on_cliff_plus_pad():
    segs, hook_at, repairs, _, issues = evaluate(cand([("E01.006", "E01.025")], "E01.007", "E01.025", IX), IX)
    assert issues == []
    assert segs[0].start == 8.5 and segs[0].end == pytest.approx(57.7)  # shot ends 2.6 s later: too far
    assert hook_at == pytest.approx(0.6)


def test_late_hook_snaps_to_camera_cut_and_end_extends_to_shot():
    segs, hook_at, repairs, _, issues = evaluate(cand([("E01.001", "E01.019")], "E01.007", "E01.019", IX), IX)
    assert issues == []
    assert segs[0].start == 8.5  # latest camera boundary in [hook-3, hook]
    assert segs[0].end == 41.0  # shot change 1.4 s after the line, next line starts at 41.3
    assert hook_at == pytest.approx(0.6)
    assert any("camera cut" in r for r in repairs)


def test_end_never_leaks_next_line():
    # Cliff E01.010 ends 16.9; its shot ends 17.0 and the next line starts 17.4 -> extend to 17.0 is safe.
    segs, *_ = evaluate(cand([("E01.006", "E01.012")], "E01.007", "E01.010", IX), IX)
    assert segs[0].end == 17.0


def test_too_short_is_flagged():
    *_, issues = evaluate(cand([("E01.006", "E01.010")], "E01.007", "E01.010", IX), IX)
    assert "duration_out_of_range" in codes(issues)


def test_midline_rules():
    ep = SHOW.episodes[0]
    s, e, notes = fix_midline(ep, 5.0, 20.0, is_last=False, protect_start=9.1)
    assert (s, e) == (7.9, 21.6)  # exclude the line cut at the start, finish the line cut at the end
    s, e, _ = fix_midline(ep, 5.0, 20.0, is_last=True, protect_start=9.1, cliff_end=16.9)
    assert e == 17.4  # last segment: drop the half line after the cliffhanger


@pytest.mark.parametrize(
    "kwargs,code",
    [
        (dict(segs=[("E01.006", "E09.999")], hook="E01.007", cliff="E01.025"), "unknown_id"),
        (dict(segs=[("E01.025", "E01.006")], hook="E01.007", cliff="E01.025"), "reversed_segment"),
        (dict(segs=[("E01.006", "E02.010")], hook="E01.007", cliff="E01.025"), "cross_episode_segment"),
        (dict(segs=[("E01.010", "E01.025")], hook="E01.007", cliff="E01.025"), "hook_not_in_first_segment"),
        (dict(segs=[("E01.006", "E01.019")], hook="E01.007", cliff="E01.025"), "cliff_not_in_last_segment"),
    ],
)
def test_id_errors(kwargs, code):
    *_, issues = evaluate(cand(kwargs["segs"], kwargs["hook"], kwargs["cliff"], IX), IX)
    assert code in codes(issues)


def test_text_mismatch():
    c = cand([("E01.006", "E01.025")], "E01.007", "E01.025", IX, hook_text="Arrest the stranger at once.")
    *_, issues = evaluate(c, IX)
    assert codes(issues) == {"text_mismatch"}


def test_text_match_tolerates_quotes_and_case():
    t = IX.by_id["E01.007"].text.strip('"').upper()
    *_, issues = evaluate(cand([("E01.006", "E01.025")], "E01.007", "E01.025", IX, hook_text=t), IX)
    assert issues == []


def test_flash_forward_from_next_episode():
    c = cand([("E02.003", "E02.003"), ("E01.016", "E01.025")], "E02.003", "E01.025", IX, structure="flash_forward")
    segs, hook_at, _, warnings, issues = evaluate(c, IX)
    assert issues == [] and hook_at == 0.0
    assert [s.episode for s in segs] == [2, 1]
    assert warnings == []  # 3.2 s peak is inside the 2-4 s guide


def test_flash_forward_must_come_from_later():
    c = cand([("E01.004", "E01.004"), ("E01.016", "E01.025")], "E01.004", "E01.025", IX, structure="flash_forward")
    *_, issues = evaluate(c, IX)
    assert "segment_order" in codes(issues)


def test_straight_with_two_segments_is_structure_error():
    c = cand([("E01.006", "E01.010"), ("E01.016", "E01.025")], "E01.007", "E01.025", IX)
    *_, issues = evaluate(c, IX)
    assert "structure" in codes(issues)


def test_arc_bridge_and_continuity_warning():
    c = cand([("E01.016", "E01.019"), ("E02.008", "E02.013")], "E01.017", "E02.013", IX, structure="arc_bridge")
    vc, log = asyncio.run(validate_one("AB-01", "arc_bridge", c, IX, None, BIBLE))
    assert vc is not None and log["status"] == "valid"
    assert vc.segments[1].end == 38.0
    assert vc.duration == pytest.approx(10.6 + 20.0)
    assert any("Marrowind" in w for w in vc.continuity_warnings)


def test_constraint_report_all_true_for_valid():
    c = cand([("E01.006", "E01.025")], "E01.007", "E01.025", IX)
    segs, hook_at, *_ = evaluate(c, IX)
    assert all(constraint_report(segs, hook_at, c, IX).values())


def _client(responses):
    cfg = Config(models={"miner": "m/a", "judge": "m/b", "classifier": "m/c"}, rpm=1000)
    return LLMClient(cfg, provider=MockProvider(responses), prices={})


def test_repair_loop_fixes_then_validates():
    bad = cand([("E01.006", "E01.010")], "E01.007", "E01.010", IX)  # 8.5 s: too short
    fixed = cand([("E01.006", "E01.025")], "E01.007", "E01.025", IX).model_dump()
    client = _client({"repair": fixed})
    vc, log = asyncio.run(validate_one("C01-01", "episode", bad, IX, client, BIBLE))
    assert vc is not None and log["status"] == "repaired" and log["first_reasons"] == ["duration_out_of_range"]
    prompt = client.provider.calls[0]["messages"][0]["content"]
    assert "duration_out_of_range" in prompt and "E01.025" in prompt  # errors and atoms are sent back


def test_repair_drop_is_rejection():
    bad = cand([("E01.006", "E01.010")], "E01.007", "E01.010", IX)
    vc, log = asyncio.run(validate_one("C01-02", "episode", bad, IX, _client({"repair": {"drop": True, "reason": "no"}}), BIBLE))
    assert vc is None and log["status"] == "rejected" and log["reasons"] == ["duration_out_of_range"]


def test_iou_and_dedupe():
    a = [(1, 10.0, 40.0)]
    assert interval_iou(a, [(1, 25.0, 55.0)]) == pytest.approx(15 / 45)
    assert interval_iou(a, [(2, 10.0, 40.0)]) == 0.0
    c1 = cand([("E01.006", "E01.025")], "E01.007", "E01.025", IX, miner_hook=5)
    c2 = cand([("E01.006", "E01.023")], "E01.007", "E01.023", IX, miner_hook=2)
    v1, _ = asyncio.run(validate_one("C01-01", "episode", c1, IX, None, BIBLE))
    v2, _ = asyncio.run(validate_one("C01-02", "episode", c2, IX, None, BIBLE))
    kept, logs = dedupe([v1, v2])
    assert [k.cid for k in kept] == ["C01-01"] and logs[0]["kept"] == "C01-01"
