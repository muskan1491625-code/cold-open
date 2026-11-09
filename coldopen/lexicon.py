"""Stage 2: map every emotion tag ("cold, commanding") to arousal/valence.

Why an LLM: there are ~600 free-text delivery notes per show and no fixed
vocabulary. Why cached per model across shows: most tags recur from show to
show ("furious", "whispering"), so a second show costs only its new tags.
Missing or out-of-range values are never invented: a missing tag is asked
for again once, then recorded as missing in the run notes with a neutral
default that is marked as such.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel

from coldopen.llm.provider import LLMClient
from coldopen.models import LexiconEntry, Show

CHUNK = 150
DEFAULT = LexiconEntry(arousal=0.3, valence=0.0, is_emotion=True)


class LexiconChunk(BaseModel):
    tags: dict[str, LexiconEntry]


def all_tags(show: Show) -> list[str]:
    return sorted({t for ep in show.episodes for a in ep.atoms for t in a.emotion_tags})


def _store_path(cache_root: Path, model: str) -> Path:
    return cache_root / "lexicon" / (re.sub(r"[^A-Za-z0-9._-]+", "_", model) + ".json")


def _clamp(e: LexiconEntry) -> LexiconEntry:
    return LexiconEntry(
        arousal=min(1.0, max(0.0, float(e.arousal))),
        valence=min(1.0, max(-1.0, float(e.valence))),
        is_emotion=bool(e.is_emotion),
    )


async def build_lexicon(
    show: Show, client: LLMClient, cache_root: Path = Path(".cache")
) -> tuple[dict[str, LexiconEntry], dict]:
    """Return ({tag: entry}, stats). Reuses the per-model store across shows."""
    tags = all_tags(show)
    model = client.config.model_for("classifier")
    store_file = _store_path(cache_root, model)
    store: dict[str, dict] = json.loads(store_file.read_text()) if store_file.exists() else {}
    if client.provider.name == "mock":
        store = {}  # never mix heuristic stand-ins into the real per-model store
    todo = [t for t in tags if t not in store]
    stats = {"tags": len(tags), "reused": len(tags) - len(todo), "asked": len(todo), "missing": 0}

    async def ask(batch: list[str]) -> dict[str, LexiconEntry]:
        out = await client.acall("classifier", "lexicon", {"tags": "\n".join(batch)}, LexiconChunk)
        norm = {k.strip().lower(): v for k, v in out.tags.items()}
        return {t: _clamp(norm[t]) for t in batch if t in norm}

    import asyncio

    chunks = [todo[i : i + CHUNK] for i in range(0, len(todo), CHUNK)]
    results = await asyncio.gather(*(ask(c) for c in chunks))
    got: dict[str, LexiconEntry] = {k: v for r in results for k, v in r.items()}
    missing = [t for t in todo if t not in got]
    if missing:  # one follow-up for anything the model skipped
        got.update(await ask(missing))
        missing = [t for t in todo if t not in got]
    for t, e in got.items():
        store[t] = e.model_dump()
    if client.provider.name != "mock" and got:
        store_file.parent.mkdir(parents=True, exist_ok=True)
        store_file.write_text(json.dumps(dict(sorted(store.items())), indent=1))
    lex = {t: LexiconEntry(**store[t]) if t in store else DEFAULT for t in tags}
    stats["missing"] = len(missing)
    stats["missing_tags"] = missing
    return lex, stats
