"""The single entry point for every LLM call in Cold Open.

    client.call(role, prompt_name, variables, schema) -> validated Pydantic object

Why one door: the pipeline must be model-agnostic (reviewers may re-run it with
a different model), so stage code names a *role* (miner / judge / classifier)
and config.yaml decides the model. Everything provider-specific (LiteLLM,
retries, back-off, JSON repair, caching, cost) lives here and nowhere else.

Providers:
  live   - LiteLLM ``acompletion``; back-off on 429/5xx.
  replay - serves only from the response cache; a miss is an error (offline demos).
  mock   - fixture JSON per prompt name (tests).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

import json_repair
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from coldopen.config import Config, load_prices
from coldopen.llm.cache import ResponseCache, cache_key
from coldopen.llm.cost import CallCost, UsageTotals, cost_of
from coldopen.llm.ratelimit import RateLimiter

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
T = TypeVar("T", bound=BaseModel)

RETRY_MESSAGE = (
    "Your previous reply could not be used. Problem:\n{error}\n\n"
    "Reply again with ONLY the corrected JSON object. No prose, no code fences."
)


class LLMError(RuntimeError):
    """Raised when a call cannot produce a schema-valid object (or replay misses)."""


class ProviderLimitError(LLMError):
    """Every model for a role is out of quota or overloaded, even after retries and fallbacks.

    Why a separate type: this is not a bug and not bad output. Stages that work item by
    item catch it, finish with what they have and say plainly which limit was hit, so a
    free-tier run ends with labelled partial results instead of a traceback.
    """

    def __init__(self, models: list[str], cause: BaseException):
        self.models = models
        self.daily = "PerDay" in str(cause) or "per day" in str(cause).lower()
        self.retry_after_s = _retry_after(cause)
        self.cause = type(cause).__name__
        super().__init__(self.human())

    def human(self) -> str:
        provider = ", ".join(sorted({m.split("/", 1)[0].capitalize() for m in self.models}))
        kind = "free-tier daily request limit" if self.daily else "rate limit (or the model is overloaded)"
        when = ""
        if self.retry_after_s:
            mins = max(1, round(self.retry_after_s / 60))
            when = f"; it resets in about {mins // 60} h {mins % 60} min" if mins >= 60 else f"; try again in about {mins} min"
        return f"{provider} {kind} was reached for {', '.join(self.models)}{when}"


def _retry_after(exc: BaseException) -> float | None:
    """Seconds to wait, from Gemini's retryDelay ("6876s") or Groq's "try again in 1m2.5s"."""
    text = str(exc)
    m = re.search(r'retryDelay"?\s*:\s*"(\d+(?:\.\d+)?)s"', text)
    if m:
        return float(m.group(1))
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s", text)
    if m:
        h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + sec
    return None


@dataclass
class Completion:
    text: str
    tokens_in: int
    tokens_out: int


# --------------------------------------------------------------------------- prompts + parsing

_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_prompt(template: str, variables: dict[str, Any], name: str = "<inline>") -> str:
    """Fill ``{{var}}`` placeholders. A placeholder with no value is a bug: fail loudly."""
    needed = set(_VAR.findall(template))
    missing = sorted(needed - set(variables))
    if missing:
        raise KeyError(f"Prompt {name!r} needs variables {missing}; got {sorted(variables)}")

    def sub(m: re.Match) -> str:
        v = variables[m.group(1)]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)

    return _VAR.sub(sub, template)


def load_prompt(name: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    p = prompts_dir / f"{name}.md"
    if not p.exists():
        raise FileNotFoundError(f"Prompt template not found: {p}")
    return p.read_text()


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(text: str) -> Any:
    """Strict JSON first; then strip code fences; then json_repair as last resort."""
    if text is None:
        raise ValueError("Empty response")
    candidates = [text.strip()]
    m = _FENCE.search(text)
    if m:
        candidates.insert(0, m.group(1).strip())
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass
    repaired = json_repair.loads(candidates[0])
    if not isinstance(repaired, (dict, list)) or repaired in ({}, []):
        raise ValueError(f"Response is not JSON (first 200 chars): {text[:200]!r}")
    return repaired


def _short_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        lines = []
        for err in e.errors()[:12]:
            loc = ".".join(str(x) for x in err["loc"]) or "<root>"
            lines.append(f"- {loc}: {err['msg']}")
        more = len(e.errors()) - 12
        if more > 0:
            lines.append(f"- ... and {more} more")
        return "Schema validation failed:\n" + "\n".join(lines)
    return str(e)[:1500]


# --------------------------------------------------------------------------- providers


class BaseProvider:
    name = "base"

    async def complete(self, model: str, messages: list[dict], temperature: float, prompt_name: str) -> Completion:
        raise NotImplementedError


def _is_quota_exhausted(exc: BaseException) -> bool:
    """A used-up quota (e.g. a free tier's requests per day). Retrying the same model is
    pointless, but another model has its own quota, so a fallback can still answer.
    LiteLLM sometimes maps Gemini's 429 RESOURCE_EXHAUSTED to a BadRequestError."""
    text = str(exc)
    return getattr(exc, "status_code", None) == 429 or "RESOURCE_EXHAUSTED" in text or "exceeded your current quota" in text


def _is_daily_quota(exc: BaseException) -> bool:
    text = str(exc)
    return "PerDay" in text or "per day" in text.lower() or "requests per day" in text.lower()


def _is_transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    return type(exc).__name__ in {
        "RateLimitError",
        "ServiceUnavailableError",
        "InternalServerError",
        "APIConnectionError",
        "Timeout",
        "APITimeoutError",
    }


class LiveProvider(BaseProvider):
    """LiteLLM. Keys come from the environment unless passed per request (never stored)."""

    name = "live"

    def __init__(self, api_keys: dict[str, str] | None = None, max_attempts: int = 6):
        self.api_keys = api_keys or {}
        self.max_attempts = max_attempts

    async def complete(self, model, messages, temperature, prompt_name) -> Completion:
        import litellm

        litellm.suppress_debug_info = True
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        key = self.api_keys.get(model.split("/", 1)[0])
        if key:
            kwargs["api_key"] = key
        async for attempt in AsyncRetrying(
            # A per-day quota will not recover in seconds: skip the back-off and let the
            # client move to a fallback model straight away.
            retry=retry_if_exception(lambda e: _is_transient(e) and not _is_daily_quota(e)),
            wait=wait_exponential(multiplier=2, min=2, max=60),
            stop=stop_after_attempt(self.max_attempts),
            reraise=True,
        ):
            with attempt:
                resp = await litellm.acompletion(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return Completion(
            text=text,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
        )


class ReplayProvider(BaseProvider):
    """Never calls the network. The client serves hits from cache; reaching here is a miss."""

    name = "replay"

    async def complete(self, model, messages, temperature, prompt_name) -> Completion:
        raise LLMError(
            f"Replay cache miss for prompt {prompt_name!r} on model {model!r}. "
            "The inputs, prompt, model or temperature differ from the recorded run."
        )


MockResponse = str | dict | list | Callable[[list[dict]], Any]


class MockProvider(BaseProvider):
    """Returns fixture JSON per prompt name.

    ``responses[name]`` may be a str/dict (always returned), a list (consumed in
    order, to script retries), or a callable(messages) -> str|dict.
    Falls back to ``fixtures_dir/<name>.json``.
    """

    name = "mock"

    def __init__(self, responses: dict[str, MockResponse] | None = None, fixtures_dir: str | Path | None = None):
        self.responses = dict(responses or {})
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None
        self.calls: list[dict] = []

    async def complete(self, model, messages, temperature, prompt_name) -> Completion:
        self.calls.append({"model": model, "prompt_name": prompt_name, "messages": messages})
        if prompt_name in self.responses:
            r = self.responses[prompt_name]
            if isinstance(r, list):  # a script of responses; the last one repeats
                if not r:
                    raise LLMError(f"MockProvider script for {prompt_name!r} is empty")
                r = r.pop(0) if len(r) > 1 else r[0]
            if callable(r):
                r = r(messages)
        elif self.fixtures_dir and (self.fixtures_dir / f"{prompt_name}.json").exists():
            r = (self.fixtures_dir / f"{prompt_name}.json").read_text()
        else:
            raise LLMError(f"MockProvider has no response for prompt {prompt_name!r}")
        text = r if isinstance(r, str) else json.dumps(r)
        n_in = sum(len(m["content"]) for m in messages) // 4
        return Completion(text=text, tokens_in=n_in, tokens_out=len(text) // 4)


def make_provider(kind: str, **kwargs) -> BaseProvider:
    if kind == "live":
        return LiveProvider(**kwargs)
    if kind == "replay":
        return ReplayProvider()
    if kind == "mock":
        if kwargs:
            return MockProvider(**kwargs)
        from coldopen.llm.mockbrain import MockBrain  # heuristic stand-in for offline runs

        return MockBrain()
    raise ValueError(f"Unknown provider {kind!r}; use live, replay or mock")


# --------------------------------------------------------------------------- client


class LLMClient:
    """Holds config, provider, cache, limiter and usage totals for one run."""

    def __init__(
        self,
        config: Config,
        provider: BaseProvider | str = "live",
        cache: ResponseCache | None = None,
        prices: dict[str, dict] | None = None,
        run_dir: str | Path | None = None,
        prompts_dir: Path = PROMPTS_DIR,
        max_retries: int = 2,
    ):
        self.config = config
        self.provider = make_provider(provider) if isinstance(provider, str) else provider
        if cache is None:
            # Mock calls are not cached: tests must not depend on disk state.
            cache = ResponseCache(enabled=self.provider.name != "mock")
        if self.provider.name == "replay" and not cache.enabled:
            raise ValueError("Replay provider needs an enabled cache")
        self.cache = cache
        self.prices = load_prices() if prices is None else prices
        self.run_dir = Path(run_dir) if run_dir else None
        self.prompts_dir = prompts_dir
        self.max_retries = max_retries
        # One limiter per provider: each provider's free tier has its own quota, so a slow
        # Gemini bucket should not hold back Groq calls (and vice versa).
        self.limiters: dict[str, RateLimiter] = {}
        self.fallbacks_used: list[dict] = []
        self.usage = UsageTotals()
        self.last_record: dict | None = None
        self._seq = 0

    # -- public API -------------------------------------------------------

    def limiter_for(self, model: str) -> RateLimiter:
        prov = model.split("/", 1)[0]
        if prov not in self.limiters:
            rpm = self.config.rpm_by_provider.get(prov, self.config.rpm)
            self.limiters[prov] = RateLimiter(self.config.concurrency, rpm)
        return self.limiters[prov]

    def call(self, role: str, prompt_name: str, variables: dict[str, Any], schema: type[T]) -> T:
        """Synchronous wrapper. Inside async code use ``acall``."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.acall(role, prompt_name, variables, schema))
        raise RuntimeError("LLMClient.call() used inside an event loop; await acall() instead")

    async def acall(self, role: str, prompt_name: str, variables: dict[str, Any], schema: type[T]) -> T:
        model = self.config.model_for(role)
        temperature = self.config.temperature_for(role)
        prompt = render_prompt(load_prompt(prompt_name, self.prompts_dir), variables, prompt_name)
        messages: list[dict] = [{"role": "user", "content": prompt}]
        last_error = ""
        for attempt in range(self.max_retries + 1):
            record = await self._complete(role, prompt_name, model, messages, temperature, schema.__name__, attempt)
            text = record["response_text"]
            try:
                obj = schema.model_validate(parse_json(text))
            except (ValueError, ValidationError) as e:
                last_error = _short_error(e)
                self._log_outcome(record, ok=False, error=last_error)
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": RETRY_MESSAGE.format(error=last_error)},
                ]
                continue
            self._log_outcome(record, ok=True, error=None)
            return obj
        raise LLMError(
            f"{prompt_name} ({role} -> {model}) failed schema {schema.__name__} after "
            f"{self.max_retries + 1} attempts. Last error:\n{last_error}"
        )

    # -- internals --------------------------------------------------------

    async def _complete_with_fallbacks(self, model, messages, temperature, prompt_name):
        """Try the configured model, then its fallbacks, when a provider stays overloaded.

        Free tiers have demand spikes (503 "high demand") that outlast the provider's own
        retries; one overloaded model should not kill a long run. The record keeps both
        the requested model and the one that answered, so nothing is hidden.
        """
        chain = [model, *self.config.fallbacks.get(model, [])]
        for i, m in enumerate(chain):
            try:
                async with self.limiter_for(m):
                    t0 = time.perf_counter()
                    comp = await self.provider.complete(m, messages, temperature, prompt_name)
                    return comp, time.perf_counter() - t0, m
            except Exception as e:  # noqa: BLE001 - re-raised unless a fallback is left
                limited = _is_transient(e) or _is_quota_exhausted(e)
                if not limited:
                    raise
                if i == len(chain) - 1:
                    raise ProviderLimitError(chain, e) from e
                self.fallbacks_used.append({"prompt": prompt_name, "from": m, "to": chain[i + 1],
                                            "error": type(e).__name__})
        raise AssertionError("unreachable")

    async def _complete(self, role, prompt_name, model, messages, temperature, schema_name, attempt) -> dict:
        key = cache_key(model, messages, temperature, schema_name)
        hit = self.cache.get(key)
        if hit is not None:
            record = dict(hit, cached=True)
        else:
            if self.provider.name == "replay":
                await self.provider.complete(model, messages, temperature, prompt_name)  # raises
            used = model
            if self.provider.name == "mock":  # no network, no quota: skip the limiter
                t0 = time.perf_counter()
                comp = await self.provider.complete(model, messages, temperature, prompt_name)
                latency = time.perf_counter() - t0
            else:
                comp, latency, used = await self._complete_with_fallbacks(model, messages, temperature, prompt_name)
            record = {
                "key": key,
                "role": role,
                "prompt_name": prompt_name,
                "model": model,
                "temperature": temperature,
                "schema": schema_name,
                "messages": messages,
                "response_text": comp.text,
                "answered_by": used,  # differs from "model" when a fallback answered
                "tokens_in": comp.tokens_in,
                "tokens_out": comp.tokens_out,
                "latency_s": round(latency, 3),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self.cache.put(key, record)
            record = dict(record, cached=False)
        if self.provider.name == "mock":
            cost = CallCost(usd=0.0, list_price_equivalent=False)  # stand-in answers: no tokens were bought
        else:
            cost = cost_of(record.get("answered_by", model), record["tokens_in"], record["tokens_out"], self.prices)
        self.usage.add(record["tokens_in"], record["tokens_out"], cost, cached=record["cached"])
        record["attempt"] = attempt
        record["cost_usd"] = cost.usd
        record["list_price_equivalent"] = cost.list_price_equivalent
        return record

    def _log_outcome(self, record: dict, ok: bool, error: str | None) -> None:
        """Copy every call (including failed attempts) into the run's llm_calls/ folder."""
        record["valid"] = ok
        record["validation_error"] = error
        self.last_record = record
        if self.run_dir is None:
            return
        self._seq += 1
        out = self.run_dir / "llm_calls"
        out.mkdir(parents=True, exist_ok=True)
        name = f"{self._seq:04d}_{record['prompt_name']}_{record['role']}_a{record['attempt']}.json"
        (out / name).write_text(json.dumps(record, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- module-level door

_default: LLMClient | None = None


def configure(client: LLMClient) -> None:
    global _default
    _default = client


def call(role: str, prompt_name: str, variables: dict[str, Any], schema: type[T]) -> T:
    """``call(role, prompt_name, variables, schema)`` on the configured default client."""
    if _default is None:
        raise RuntimeError("coldopen.llm.configure(LLMClient(...)) must be called first")
    return _default.call(role, prompt_name, variables, schema)
