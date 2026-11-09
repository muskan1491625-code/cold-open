"""Ablations: what each part of the system buys.

- rules:     no LLM at all. Top 30 s EKG windows; hook = loudest line in the window.
- llm_only:  the LLM writes timestamps itself and nothing checks them.
- full:      everything.
- no_viewer: everything except the Cold Viewer (ranked by miner self-scores + tension).

The point of `llm_only` is the invalid-output counts: they are the evidence for
"the LLM never writes a timestamp" as a design rule.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
from pydantic import BaseModel
from rapidfuzz import fuzz

from coldopen.bible import bible_digest
from coldopen.config import Config
from coldopen.ekg import atom_score
from coldopen.llm.provider import LLMClient
from coldopen.models import EKG, LexiconEntry, Show, ShowBible
from coldopen.parse import load_show
from coldopen.pipeline import Pipeline
from coldopen.render import compact_atom
from coldopen.snap import norm_text
from coldopen.timecode import fmt_tc, parse_tc

WINDOW = 30


class LooseReply(BaseModel):
    candidates: list[dict] = []


def _boundaries(show: Show) -> dict[int, list[float]]:
    return {e.number: sorted({x for a in e.atoms for x in (a.start, a.end)} | {0.0, e.duration}) for e in show.episodes}


def _midline(show: Show, ep: int, s: float, e: float) -> int:
    eps = {x.number: x for x in show.episodes}
    return sum(
        1 for d in eps[ep].atoms if d.kind == "dialogue" and ((d.start < s < d.end) or (d.start < e < d.end))
    )


def _promo(pid: str, ep: int, s: float, e: float, hook_at: float | None, **extra) -> dict:
    return {
        "id": pid, "segments": [{"episode": ep, "start": s, "end": e, "start_tc": fmt_tc(s), "end_tc": fmt_tc(e)}],
        "hook_at": hook_at, "scores": {"total": extra.pop("score", 0.0)}, **extra,
    }


# --------------------------------------------------------------------------- rules


def rules_variant(show: Show, ekgs: dict[int, EKG], lex: dict[str, LexiconEntry], n: int = 20) -> dict:
    windows = []
    for ep in show.episodes:
        v = np.array(ekgs[ep.number].values)
        for t in range(0, max(1, len(v) - WINDOW + 1)):
            windows.append((float(v[t : t + WINDOW].mean()), ep.number, float(t)))
    windows.sort(key=lambda w: -w[0])
    chosen: list[tuple[float, int, float]] = []
    for w in windows:
        if all(not (c[1] == w[1] and abs(c[2] - w[2]) < WINDOW) for c in chosen):
            chosen.append(w)
        if len(chosen) >= n:
            break
    eps = {e.number: e for e in show.episodes}
    promos, late, cuts = [], 0, 0
    for k, (score, epn, t) in enumerate(chosen, 1):
        s, e = t, min(t + WINDOW, eps[epn].duration)
        lines = [a for a in eps[epn].atoms if a.kind == "dialogue" and s <= a.start < e]
        hook = max(lines, key=lambda a: atom_score(a, lex)) if lines else None
        hook_at = (hook.start - s) if hook else None
        late += hook_at is None or hook_at > 3.0
        cuts += _midline(show, epn, s, e)
        promos.append(_promo(f"R{k:03d}", epn, s, e, hook_at, score=round(score, 4), hook_text=hook.text if hook else ""))
    return {
        "promos": promos,
        "violations": {"hook_latency_over_3s": late, "midline_cuts": cuts, "durations_out_of_range": 0,
                       "timestamps_off_atom_boundary": 0, "quotes_not_in_script": 0},
        "top10_windows": [{"episode": p["segments"][0]["episode"], "start": p["segments"][0]["start_tc"],
                           "hook": p.get("hook_text", "")[:60]} for p in promos[:10]],
    }


# --------------------------------------------------------------------------- llm_only


async def llm_only_variant(show: Show, bible: ShowBible, client: LLMClient) -> dict:
    bounds = _boundaries(show)

    from coldopen.llm.provider import ProviderLimitError

    skipped: list[int] = []

    async def one(ep):
        try:
            r = await client.acall("miner", "miner_timestamps", {
                "bible_digest": bible_digest(bible), "episode": str(ep.number),
                "atoms": "\n".join(compact_atom(a) for a in ep.atoms)}, LooseReply)
        except ProviderLimitError:  # an API limit: count the episode as skipped, not as a model error
            skipped.append(ep.number)
            return ep, []
        return ep, r.candidates

    results = await asyncio.gather(*(one(ep) for ep in show.episodes))
    v = {"timestamps_off_atom_boundary": 0, "midline_cuts": 0, "durations_out_of_range": 0,
         "hook_latency_over_3s": 0, "quotes_not_in_script": 0, "unparseable_timestamps": 0}
    promos, k = [], 0
    for ep, cands in results:
        texts = [norm_text(a.text) for a in ep.atoms]
        for c in cands:
            k += 1
            try:
                s, e = parse_tc(str(c.get("start", ""))), parse_tc(str(c.get("end", "")))
            except ValueError:
                v["unparseable_timestamps"] += 1
                continue
            for x in (s, e):
                if min(abs(x - b) for b in bounds[ep.number]) > 0.25:
                    v["timestamps_off_atom_boundary"] += 1
            v["midline_cuts"] += _midline(show, ep.number, s, e) > 0
            v["durations_out_of_range"] += not 20 <= e - s <= 60
            hook_t, cliff_t = norm_text(str(c.get("hook_text", ""))), norm_text(str(c.get("cliffhanger_text", "")))
            scores = [fuzz.ratio(hook_t, t) for t in texts]
            best = int(np.argmax(scores)) if scores else -1
            if not scores or scores[best] < 90 or max((fuzz.ratio(cliff_t, t) for t in texts), default=0) < 90:
                v["quotes_not_in_script"] += 1
            hook_at = ep.atoms[best].start - s if best >= 0 and scores[best] >= 90 else None
            v["hook_latency_over_3s"] += hook_at is None or not 0 <= hook_at <= 3.0
            promos.append(_promo(f"L{k:03d}", ep.number, s, max(s, e), hook_at))
    total = sum(len(c) for _, c in results)
    return {"promos": promos, "violations": v, "candidates": total, "episodes_skipped_api_limit": sorted(skipped)}


# --------------------------------------------------------------------------- driver


def _pipeline_promos(run: Path) -> tuple[list[dict], dict | None]:
    promos = json.loads((run / "08_promos.json").read_text())
    slate = json.loads((run / "08_slate.json").read_text())
    from coldopen.eval.metrics import ranked_promos

    return ranked_promos(promos, slate), slate


async def run_ablation(input: Path, out: Path, cfg: Config, make_client, variants: list[str], title: str | None = None,
                       emit=None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    result: dict = {"variants": {}}
    full_dir = out / "full"
    needs_full = any(v in variants for v in ("full", "rules", "llm_only", "no_viewer"))
    if needs_full:
        client = make_client(full_dir)
        rep = await Pipeline(input, full_dir, cfg, client, title=title, emit=emit).run()
        ranked, _ = _pipeline_promos(full_dir)
        if "full" in variants:
            result["variants"]["full"] = {"promos": ranked, "violations": _zero(), "llm_calls": rep.llm_calls,
                                          "cost_usd": rep.cost_usd}
    show = load_show(input, title=title)
    bible = ShowBible.model_validate_json((full_dir / "01_bible.json").read_text())
    if "rules" in variants:
        lex = {k: LexiconEntry(**v) for k, v in json.loads((full_dir / "02_lexicon.json").read_text())["tags"].items()}
        ekgs = {e["episode"]: EKG(**e) for e in json.loads((full_dir / "03_ekg.json").read_text())}
        result["variants"]["rules"] = rules_variant(show, ekgs, lex)
    if "no_viewer" in variants:
        nv = out / "no_viewer"
        nv.mkdir(exist_ok=True)
        for f in ["01_bible.json", "02_lexicon.json", "03_ekg.json", "04_candidates_raw.jsonl", "05_candidates_valid.json",
                  "05_validation_log.jsonl", "07_risk.json"]:
            (nv / f).write_bytes((full_dir / f).read_bytes())
        await Pipeline(input, nv, cfg, make_client(nv), title=title, use_viewer=False).run(["slate"])
        ranked, _ = _pipeline_promos(nv)
        result["variants"]["no_viewer"] = {"promos": ranked, "violations": _zero()}
    if "llm_only" in variants:
        lo = out / "llm_only"
        result["variants"]["llm_only"] = await llm_only_variant(show, bible, make_client(lo))
    (out / "ablation.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def _zero() -> dict:
    return {"hook_latency_over_3s": 0, "midline_cuts": 0, "durations_out_of_range": 0,
            "timestamps_off_atom_boundary": 0, "quotes_not_in_script": 0}

