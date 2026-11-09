"""Content-hash response cache.

Why: (1) re-running a stage after a code fix must not re-pay for LLM calls;
(2) the replay provider serves *only* from this cache, so a demo or an
interview re-run works offline and reproduces the final run exactly.

Key = sha256(model + messages + temperature + schema name). On a first attempt
the messages are just the rendered prompt; validation retries append to the
conversation, so each retry gets its own deterministic key too.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def cache_key(model: str, messages: list[dict], temperature: float, schema_name: str) -> str:
    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": round(float(temperature), 4), "schema": schema_name},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, root: str | Path = ".cache/llm", enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None  # a torn write; treat as a miss and overwrite

    def put(self, key: str, record: dict) -> None:
        if not self.enabled:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        tmp.replace(self._path(key))  # atomic rename: no half-written cache files
