"""Shared fixtures. Tests marked `pack` need the private pack and skip without it."""

from __future__ import annotations

from pathlib import Path

import pytest

from coldopen.config import Config

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "data" / "pack"


def pytest_collection_modifyitems(config, items):
    if PACK.exists() and any(PACK.glob("*.txt")):
        return
    skip = pytest.mark.skip(reason="data/pack/ not present (private input pack)")
    for item in items:
        if "pack" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def cfg() -> Config:
    return Config(
        models={"miner": "fake/miner-a", "judge": "fake/judge-b", "classifier": "fake/clf-c"},
        temperature={"miner": 0.4, "judge": 0.7, "classifier": 0.0},
        concurrency=2,
        rpm=600,
    )


@pytest.fixture
def prices() -> dict:
    return {
        "fake/miner-a": {"input_per_m": 1.0, "output_per_m": 2.0, "free_tier": False},
        "fake/judge-b": {"input_per_m": "TODO", "output_per_m": "TODO", "free_tier": True},
    }
