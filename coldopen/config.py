"""Configuration: config.yaml (models per role, weights, thresholds) and prices.yaml.

Why typed: weights and thresholds feed scoring directly. A typo in a key should
fail at load time, not silently fall back to a default and change the ranking.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

ROLES = ("miner", "judge", "classifier")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Weights(_Strict):
    hook: float = 0.25
    cliff: float = 0.20
    viewer: float = 0.25
    tension: float = 0.15
    quality: float = 0.15


class Penalties(_Strict):
    review: float = 0.05
    organic_only: float = 0.15
    spoiler: float = 0.10
    paywall: float = 0.10
    referent: float = 0.03
    referent_cap: float = 0.09


class SlateSize(_Strict):
    challengers: int = 4
    bench: int = 10


class Config(_Strict):
    models: dict[str, str]
    temperature: dict[str, float] = Field(default_factory=lambda: {"miner": 0.4, "judge": 0.7, "classifier": 0.0})
    concurrency: int = 2
    rpm: int = 10
    # Per-provider override of rpm (requests per minute), e.g. {"groq": 30}. Each provider has its own bucket.
    rpm_by_provider: dict[str, int] = Field(default_factory=dict)
    # model -> models to try, in order, when it is still overloaded (503/429) after retries.
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    paywall_episode: int = 10
    fps: int = 30
    weights: Weights = Field(default_factory=Weights)
    penalties: Penalties = Field(default_factory=Penalties)
    mmr_lambda: float = 0.7
    slate: SlateSize = Field(default_factory=SlateSize)
    # Optional cap for slow free tiers: only the top-N valid promos (by miner
    # score + tension) get the Cold Viewer and risk calls. None = judge all.
    judge_limit: int | None = None

    def model_for(self, role: str) -> str:
        if role not in self.models:
            raise KeyError(f"Unknown LLM role {role!r}. Configured roles: {sorted(self.models)}")
        return self.models[role]

    def temperature_for(self, role: str) -> float:
        return float(self.temperature.get(role, 0.0))


def load_config(path: str | Path = "config.yaml") -> Config:
    """Load config.yaml and the local .env (API keys live only in the environment)."""
    load_dotenv(override=False)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p.resolve()}")
    data = yaml.safe_load(p.read_text()) or {}
    cfg = Config.model_validate(data)
    missing = [r for r in ROLES if r not in cfg.models]
    if missing:
        raise ValueError(f"config.yaml is missing models for roles: {missing}")
    return cfg


def load_prices(path: str | Path = "prices.yaml") -> dict[str, dict]:
    """Return {model: {input_per_m, output_per_m, free_tier, ...}}. Missing file -> {}."""
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    return dict(data.get("models") or {})
