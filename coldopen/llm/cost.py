"""Token cost accounting.

Why "None" instead of an estimate: a cost figure ends up in the solution doc.
If the price of a model is unknown, a guess would look like a fact, so we
report null and let the human fill prices.yaml from the pricing page.
"""

from __future__ import annotations

from dataclasses import dataclass


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None  # "TODO", missing, or anything non-numeric


@dataclass(frozen=True)
class CallCost:
    usd: float | None
    list_price_equivalent: bool  # True when the model is on a free tier


def cost_of(model: str, tokens_in: int, tokens_out: int, prices: dict[str, dict]) -> CallCost:
    """Cost of one call from prices.yaml; unknown model or TODO price -> usd=None."""
    entry = prices.get(model)
    if not entry:
        return CallCost(usd=None, list_price_equivalent=False)
    p_in, p_out = _num(entry.get("input_per_m")), _num(entry.get("output_per_m"))
    if p_in is None or p_out is None:
        return CallCost(usd=None, list_price_equivalent=bool(entry.get("free_tier")))
    usd = tokens_in / 1_000_000 * p_in + tokens_out / 1_000_000 * p_out
    return CallCost(usd=round(usd, 6), list_price_equivalent=bool(entry.get("free_tier")))


@dataclass
class UsageTotals:
    """Running totals for a run. cost_usd becomes None as soon as one call's price is unknown."""

    calls: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = 0.0
    any_free_tier: bool = False

    def add(self, tokens_in: int, tokens_out: int, cost: CallCost, cached: bool) -> None:
        self.calls += 1
        self.cache_hits += int(cached)
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.any_free_tier |= cost.list_price_equivalent
        if self.cost_usd is None or cost.usd is None:
            self.cost_usd = None
        else:
            self.cost_usd = round(self.cost_usd + cost.usd, 6)
