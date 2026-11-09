"""Evaluation metrics and the report, on the invented mini show (gold invented for it)."""

from __future__ import annotations

import asyncio
import json

import pytest

from coldopen.config import Config
from coldopen.eval.ablate import run_ablation
from coldopen.eval.metrics import iou_1d, match_gold, precision_at_10, summarise_matches
from coldopen.eval.report import build_report, to_markdown, write_report
from coldopen.llm.mockbrain import MockBrain
from coldopen.llm.provider import LLMClient
from tests.helpers import FIX

CFG = Config(models={"miner": "m/a", "judge": "m/b", "classifier": "m/c"}, rpm=100000, concurrency=8)
GOLD = """
- id: G01
  episode: 1
  start: "00:08.5"
  end: "00:41.0"
  note: "framed at the feast"
- id: G02
  episode: 3
  start: "00:00.0"
  end: "00:30.0"
  note: "poisoned cup"
"""


def test_iou_and_matching():
    assert iou_1d((0, 10), (5, 15)) == pytest.approx(5 / 15)
    promos = [{"id": "P1", "segments": [{"episode": 1, "start": 9.0, "end": 40.0}]}]
    gold = [{"id": "G", "episode": 1, "start": 8.5, "end": 41.0, "note": ""}]
    m = match_gold(gold, promos)
    assert m[0]["hit"] and m[0]["start_err"] == pytest.approx(0.5)
    s = summarise_matches(m)
    assert s["hit_rate"] == 1.0 and s["median_end_error_s"] == 1.0


def test_precision_at_10():
    assert precision_at_10({f"P{i}": (5 if i < 6 else 2) for i in range(10)}) == 0.6
    assert precision_at_10({}) is None


def test_ablation_and_report(tmp_path):
    gold = tmp_path / "gold.yaml"
    gold.write_text(GOLD)
    make = lambda d: LLMClient(CFG, provider=MockBrain(), prices={}, run_dir=d)  # noqa: E731
    res = asyncio.run(run_ablation(FIX / "mini_show", tmp_path / "ab", CFG, make, ["rules", "llm_only", "full", "no_viewer"]))
    assert set(res["variants"]) == {"rules", "llm_only", "full", "no_viewer"}
    assert all(v == 0 for v in res["variants"]["full"]["violations"].values())
    d = write_report(tmp_path / "ab" / "full", gold, tmp_path / "ab")
    r = json.loads((d / "eval_report.json").read_text())
    assert r["gold"]["gold"] == 2 and set(r["ablations"]) == {"rules", "llm_only", "full", "no_viewer"}
    assert all(v == 1.0 for v in r["constraint_pass"].values())
    md = to_markdown(build_report(tmp_path / "ab" / "full", gold, tmp_path / "ab"))
    assert "## Ablations" in md and "Hit rate" in md
