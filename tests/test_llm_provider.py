"""Provider layer: templating, JSON parsing, validation retries, cache, replay, run logging."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from coldopen.llm.cache import ResponseCache, cache_key
from coldopen.llm.provider import (
    PROMPTS_DIR,
    BaseProvider,
    Completion,
    LLMClient,
    LLMError,
    MockProvider,
    load_prompt,
    parse_json,
    render_prompt,
)


class Ping(BaseModel):
    ok: bool
    word: str


class CountingProvider(BaseProvider):
    """Stands in for a live API: counts real calls so cache behaviour is observable."""

    name = "fake-live"

    def __init__(self, text='{"ok": true, "word": "ready"}'):
        self.text, self.n = text, 0

    async def complete(self, model, messages, temperature, prompt_name):
        self.n += 1
        return Completion(text=self.text, tokens_in=100, tokens_out=10)


# -- templating ---------------------------------------------------------------


def test_render_fills_and_serialises():
    out = render_prompt("A {{x}} B {{ y }}", {"x": "one", "y": [1, 2]})
    assert out.startswith("A one B [")


def test_render_missing_variable_fails_loudly():
    with pytest.raises(KeyError, match="needs variables"):
        render_prompt("Hello {{who}}", {}, "greet")


@pytest.mark.parametrize("path", sorted(PROMPTS_DIR.glob("*.md")), ids=lambda p: p.stem)
def test_every_prompt_template_loads(path):
    text = load_prompt(path.stem)
    assert "JSON" in text  # every prompt must ask for JSON


# -- JSON parsing -------------------------------------------------------------


def test_parse_plain_fenced_and_repaired():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2}
    assert parse_json('{"a": 3,}') == {"a": 3}  # trailing comma -> json_repair


def test_parse_garbage_raises():
    with pytest.raises(ValueError):
        parse_json("I cannot help with that.")


# -- client -------------------------------------------------------------------


def test_mock_call_validates(cfg, prices):
    client = LLMClient(cfg, provider=MockProvider({"ping": {"ok": True, "word": "ready"}}), prices=prices)
    out = client.call("miner", "ping", {}, Ping)
    assert out == Ping(ok=True, word="ready")
    assert client.usage.calls == 1


def test_validation_retry_appends_error_then_succeeds(cfg, prices):
    mock = MockProvider({"ping": ['{"ok": "maybe"}', {"ok": True, "word": "ready"}]})
    client = LLMClient(cfg, provider=mock, prices=prices)
    assert client.call("judge", "ping", {}, Ping).word == "ready"
    assert len(mock.calls) == 2
    retry_msgs = mock.calls[1]["messages"]
    assert [m["role"] for m in retry_msgs] == ["user", "assistant", "user"]
    assert "word" in retry_msgs[-1]["content"]  # the missing field is named back to the model


def test_gives_up_after_max_retries(cfg, prices):
    mock = MockProvider({"ping": "not json at all"})
    client = LLMClient(cfg, provider=mock, prices=prices)
    with pytest.raises(LLMError, match="after 3 attempts"):
        client.call("miner", "ping", {}, Ping)
    assert len(mock.calls) == 3


def test_unknown_role_rejected(cfg, prices):
    client = LLMClient(cfg, provider=MockProvider({"ping": {}}), prices=prices)
    with pytest.raises(KeyError, match="Unknown LLM role"):
        client.call("director", "ping", {}, Ping)


def test_cache_hit_skips_provider_and_replay_reproduces(cfg, prices, tmp_path):
    cache = ResponseCache(tmp_path / "cache")
    live = CountingProvider()
    c1 = LLMClient(cfg, provider=live, cache=cache, prices=prices)
    c1.call("miner", "ping", {}, Ping)
    c1.call("miner", "ping", {}, Ping)
    assert live.n == 1 and c1.usage.cache_hits == 1

    replay = LLMClient(cfg, provider="replay", cache=cache, prices=prices)
    assert replay.call("miner", "ping", {}, Ping).word == "ready"


def test_replay_miss_is_an_error(cfg, prices, tmp_path):
    client = LLMClient(cfg, provider="replay", cache=ResponseCache(tmp_path / "empty"), prices=prices)
    with pytest.raises(LLMError, match="Replay cache miss"):
        client.call("judge", "ping", {}, Ping)


def test_cache_key_depends_on_model_temperature_schema():
    msgs = [{"role": "user", "content": "hi"}]
    base = cache_key("m", msgs, 0.0, "S")
    assert base != cache_key("m2", msgs, 0.0, "S")
    assert base != cache_key("m", msgs, 0.4, "S")
    assert base != cache_key("m", msgs, 0.0, "T")


def test_every_attempt_is_written_to_run_folder(cfg, prices, tmp_path):
    mock = MockProvider({"ping": ["{}", {"ok": True, "word": "ready"}]})
    client = LLMClient(cfg, provider=mock, prices=prices, run_dir=tmp_path / "run")
    client.call("classifier", "ping", {}, Ping)
    files = sorted((tmp_path / "run" / "llm_calls").glob("*.json"))
    assert len(files) == 2
    first, second = (json.loads(f.read_text()) for f in files)
    assert first["valid"] is False and first["validation_error"]
    assert second["valid"] is True and second["attempt"] == 1


def test_cost_totals_known_and_unknown(cfg, prices, tmp_path):
    live = CountingProvider()
    client = LLMClient(cfg, provider=live, cache=ResponseCache(enabled=False), prices=prices)
    client.call("miner", "ping", {}, Ping)  # 100 in @ $1/M + 10 out @ $2/M
    assert client.usage.cost_usd == pytest.approx(0.00012)
    client.call("judge", "ping", {}, Ping)  # TODO prices -> total becomes unknown
    assert client.usage.cost_usd is None


def test_fallback_model_answers_when_main_is_overloaded(tmp_path):
    """A 503 that outlasts retries moves to the configured fallback; the record says who answered."""
    from coldopen.cli import _Ping
    from coldopen.config import Config
    from coldopen.llm.provider import BaseProvider, Completion, LLMClient

    class Overloaded(Exception):
        status_code = 503

    class Flaky(BaseProvider):
        name = "live"

        async def complete(self, model, messages, temperature, prompt_name):
            if model == "g/main":
                raise Overloaded("high demand")
            return Completion(text='{"ok": true, "word": "ready"}', tokens_in=3, tokens_out=2)

    cfg = Config(models={"miner": "g/main", "judge": "g/b", "classifier": "g/c"}, rpm=100000,
                 fallbacks={"g/main": ["g/backup"]})
    from coldopen.llm.cache import ResponseCache

    client = LLMClient(cfg, provider=Flaky(), prices={}, cache=ResponseCache(enabled=False))
    client.call("miner", "ping", {}, _Ping)
    assert client.last_record["answered_by"] == "g/backup" and client.last_record["model"] == "g/main"
    assert client.fallbacks_used[0]["to"] == "g/backup"


def test_fallback_also_covers_a_used_up_daily_quota():
    """Gemini's per-day quota error can arrive as a 400; the fallback model has its own quota."""
    from coldopen.cli import _Ping
    from coldopen.config import Config
    from coldopen.llm.cache import ResponseCache
    from coldopen.llm.provider import BaseProvider, Completion, LLMClient

    class Exhausted(Exception):
        status_code = 400

    class OutOfQuota(BaseProvider):
        name = "live"

        async def complete(self, model, messages, temperature, prompt_name):
            if model == "g/main":
                raise Exhausted('{"status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota"}')
            return Completion(text='{"ok": true, "word": "ready"}', tokens_in=3, tokens_out=2)

    cfg = Config(models={"miner": "g/main", "judge": "g/b", "classifier": "g/c"}, rpm=100000,
                 fallbacks={"g/main": ["g/backup"]})
    client = LLMClient(cfg, provider=OutOfQuota(), prices={}, cache=ResponseCache(enabled=False))
    client.call("miner", "ping", {}, _Ping)
    assert client.last_record["answered_by"] == "g/backup"


def test_daily_quota_is_not_retried_but_overload_is():
    from coldopen.llm.provider import _is_daily_quota, _is_transient

    class E(Exception):
        status_code = 429

    daily = E('quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier')
    burst = E("Rate limit reached for requests per minute")
    assert _is_transient(daily) and _is_daily_quota(daily)
    assert _is_transient(burst) and not _is_daily_quota(burst)
