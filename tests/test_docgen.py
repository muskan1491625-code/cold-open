"""The solution PDF renders to exactly two pages from a real (mock) run."""

from __future__ import annotations

import asyncio

import pytest

from coldopen.config import Config
from coldopen.llm.mockbrain import MockBrain
from coldopen.llm.provider import LLMClient
from coldopen.pipeline import Pipeline
from tests.helpers import FIX

CFG = Config(models={"miner": "m/a", "judge": "m/b", "classifier": "m/c"}, rpm=100000, concurrency=8)


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not installed")
def test_two_page_pdf(tmp_path):
    from coldopen.docgen.render import render

    out = tmp_path / "run"
    asyncio.run(Pipeline(FIX / "mini_show", out, CFG, LLMClient(CFG, provider=MockBrain(), prices={}, run_dir=out)).run())
    res = render(out, None, tmp_path / "doc.pdf", tmp_path / "doc.md", {})
    assert res["pages"] == 2 and res["overflow"] == []
    md = (tmp_path / "doc.md").read_text()
    assert "TODO(Kartik)" in md and "## Results on the full pack" in md
    # The author's own words replace the TODO boxes and still fit on two pages.
    notes = {"why_this_problem": "Invented reason " * 12, "tools_used": "Invented tools note " * 10,
             "failures_confirmed": True}
    res = render(out, None, tmp_path / "doc2.pdf", tmp_path / "doc2.md", {}, notes)
    assert res["pages"] == 2 and res["overflow"] == []
    md = (tmp_path / "doc2.md").read_text()
    assert "Invented reason" in md and "Invented tools note" in md


def test_notes_reject_unknown_keys(tmp_path):
    from coldopen.docgen.render import load_notes

    f = tmp_path / "w.yaml"
    f.write_text("why_this_problem: ok\ntypo_key: x\n")
    with pytest.raises(ValueError):
        load_notes(f)
