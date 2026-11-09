"""End to end on the invented show: mock run, artifacts, constraints, and offline replay."""

from __future__ import annotations

import asyncio
import json

from coldopen.config import Config
from coldopen.llm.cache import ResponseCache
from coldopen.llm.mockbrain import MockBrain
from coldopen.llm.provider import LLMClient
from coldopen.pipeline import Pipeline
from tests.helpers import FIX

CFG = Config(models={"miner": "m/a", "judge": "m/b", "classifier": "m/c"}, rpm=100000, concurrency=8)


class RecordedBrain(MockBrain):
    """MockBrain posing as a live API, so its answers go through the cache."""

    name = "recorded"


def run(out, provider, cache=None):
    client = LLMClient(CFG, provider=provider, cache=cache, prices={}, run_dir=out)
    events = []
    rep = asyncio.run(Pipeline(FIX / "mini_show", out, CFG, client, emit=events.append).run())
    run.client = client
    return rep, events


def test_mock_run_writes_every_artifact(tmp_path):
    out = tmp_path / "run"
    rep, events = run(out, MockBrain())
    for f in ["00_atoms.jsonl", "01_bible.json", "02_lexicon.json", "03_ekg.json", "debug_ekg.png",
              "04_candidates_raw.jsonl", "05_validation_log.jsonl", "05_candidates_valid.json", "06_viewer.json",
              "07_risk.json", "08_slate.json", "promos.csv", "promos.md", "run_report.json", "config_snapshot.yaml"]:
        assert (out / f).exists(), f
    slate = json.loads((out / "08_slate.json").read_text())
    assert slate["hero"] and len(slate["rationale"]) >= 1
    assert all(v == 1.0 for v in rep.constraint_pass.values())
    assert rep.counts["valid"] >= 1 and rep.llm_calls > 0
    assert (out / "edl" / f"{slate['hero']}.edl").exists()
    assert any(e.get("type") == "finished" for e in events)
    assert any("Mock provider" in n for n in rep.notes)


def test_replay_reproduces_offline(tmp_path):
    cache = ResponseCache(tmp_path / "cache")
    rep1, _ = run(tmp_path / "a", RecordedBrain(), cache)
    rep2, _ = run(tmp_path / "b", "replay", cache)
    s1 = json.loads((tmp_path / "a" / "08_slate.json").read_text())
    s2 = json.loads((tmp_path / "b" / "08_slate.json").read_text())
    assert s1 == s2
    assert run.client.usage.cache_hits == run.client.usage.calls > 0  # nothing left the machine


class QuotaBrain(MockBrain):
    """Answers like MockBrain until a prompt's daily quota is 'used up'."""

    name = "recorded"

    def __init__(self, exhausted: set[str]):
        super().__init__()
        self.exhausted = exhausted

    async def complete(self, model, messages, temperature, prompt_name):
        if prompt_name in self.exhausted:
            class Quota(Exception):
                status_code = 429

            raise Quota('GenerateRequestsPerDayPerProjectPerModel-FreeTier "retryDelay": "3600s"')
        return await super().complete(model, messages, temperature, prompt_name)


def test_daily_limit_mid_run_finishes_with_a_labelled_partial_slate(tmp_path):
    """The Cold Viewer's quota runs out: the run still ends with a slate and says why, plainly."""
    out = tmp_path / "run"
    rep, events = run(out, QuotaBrain({"viewer_hook", "viewer_full"}), ResponseCache(tmp_path / "c"))
    assert rep.counts["api_limited_viewer"] > 0 and rep.counts["slate"] >= 1
    assert any("API limit in viewer" in n and "daily request limit" in n for n in rep.notes)
    assert any("ranked without the Cold Viewer" in n for n in rep.notes)
    assert any(e.get("type") == "warning" for e in events)


def test_limit_during_mining_skips_episodes_and_names_them(tmp_path):
    out = tmp_path / "run"
    rep, _ = run(out, QuotaBrain({"miner"}), ResponseCache(tmp_path / "c"))
    assert rep.counts["api_limited_mine"] == 3
    assert any("episodes not mined: E1, E2, E3" in n for n in rep.notes)
