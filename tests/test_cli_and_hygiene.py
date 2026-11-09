"""CLI surface and repository hygiene."""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from coldopen.cli import app

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def test_help_lists_commands():
    out = runner.invoke(app, ["--help"])
    assert out.exit_code == 0
    for cmd in ("run", "show", "models", "llm-ping", "eval", "ablate", "serve", "doc"):
        assert cmd in out.output


def test_llm_ping_with_mock_provider(monkeypatch):
    monkeypatch.chdir(ROOT)
    out = runner.invoke(app, ["llm-ping", "--provider", "mock"])
    assert out.exit_code == 0, out.output
    assert out.output.count("ok") >= 3


def test_confidential_paths_are_gitignored():
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True, text=True)
    if inside.stdout.strip() != "true":
        import pytest

        pytest.skip("not a git checkout")
    paths = ["PROMPT.md", "data/pack/x.txt", "runs/r/a.json", ".cache/llm/k.json", ".env"]
    res = subprocess.run(["git", "check-ignore", *paths], cwd=ROOT, capture_output=True, text=True)
    assert sorted(res.stdout.split()) == sorted(paths)


def test_leak_checker_ignores_common_english():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_leaks", ROOT / "scripts" / "check_leaks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.grams("and in the of it") == set()
    assert ("purple", "llama", "eats", "toast") in mod.grams("A purple llama eats toast.")
