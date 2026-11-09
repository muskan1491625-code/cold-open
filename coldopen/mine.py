"""Stage 4: candidate mining (LLM role `miner`), per episode and then across arcs.

The miner reads compact atoms (IDs, times, labels) and returns atom IDs plus a
one-line "why". It never writes a timestamp. Each candidate is validated one by
one, so a single malformed suggestion does not throw away the whole reply.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ValidationError

from coldopen.bible import bible_digest
from coldopen.models import EKG, RawCandidate, Show, ShowBible
from coldopen.render import compact_atom
from coldopen.snap import ShowIndex, ValidCandidate

ENGINES = {
    "humiliation_reversal", "dangerous_protector", "hidden_identity", "sacrifice_ultimatum",
    "framed_condemned", "family_betrayal", "forbidden_scandal", "other",
}


class MinerReply(BaseModel):
    candidates: list[dict[str, Any]] = []


def coerce(d: dict) -> tuple[RawCandidate | None, str | None, list[str]]:
    """Light normalisation of obvious formatting slips, then strict validation.

    Only format is fixed here (case, an unknown engine -> "other", ratings
    clamped to 1-5). Anything about *which atoms* is left to the validator.
    """
    notes = []
    d = dict(d)
    if isinstance(d.get("structure"), str):
        d["structure"] = d["structure"].strip().lower().replace("-", "_").replace(" ", "_")
    eng = str(d.get("engine", "other")).strip().lower().replace(" ", "_").replace("-", "_")
    if eng not in ENGINES:
        notes.append(f"engine {d.get('engine')!r} -> other")
        eng = "other"
    d["engine"] = eng
    for k in ("miner_hook", "miner_cliff"):
        try:
            d[k] = min(5, max(1, int(round(float(d.get(k, 3))))))
        except (TypeError, ValueError):
            d[k] = 3
    try:
        return RawCandidate.model_validate(d), None, notes
    except ValidationError as e:
        return None, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5]), notes


def hotspots_text(ekg: EKG) -> str:
    if not ekg.hotspots:
        return "(none)"
    return "\n".join(f"{h['t']:.0f}s | {h['value']:.2f} | {', '.join(h['atom_ids'][:6])}" for h in ekg.hotspots)


def next_peaks_text(show: Show, idx: int, ekgs: dict[int, EKG]) -> str:
    if idx + 1 >= len(show.episodes):
        return "(no next episode)"
    nxt = show.episodes[idx + 1]
    hs = sorted(ekgs[nxt.number].hotspots, key=lambda h: -h["value"])[:3]
    if not hs:
        return "(none)"
    keep = [a for a in nxt.atoms if any(h["t"] - 3 <= a.start <= h["t"] + 3 for h in hs)]
    return "\n".join(compact_atom(a) for a in keep)


def miner_variables(show: Show, idx: int, bible: ShowBible, ekgs: dict[int, EKG]) -> dict:
    ep = show.episodes[idx]
    prev_ep = show.episodes[idx - 1] if idx > 0 else None
    next_ep = show.episodes[idx + 1] if idx + 1 < len(show.episodes) else None
    return {
        "bible_digest": bible_digest(bible),
        "prev_logline": prev_ep.logline if prev_ep else "(this is the first episode)",
        "next_logline": next_ep.logline if next_ep else "(this is the last episode)",
        "next_peaks": next_peaks_text(show, idx, ekgs),
        "hotspots": hotspots_text(ekgs[ep.number]),
        "episode": str(ep.number),
        "atoms": "\n".join(compact_atom(a) for a in ep.atoms),
    }


async def mine_episode(show: Show, idx: int, bible: ShowBible, ekgs: dict[int, EKG], client) -> tuple[list[dict], list[dict]]:
    """Returns (raw candidate records, schema rejections) for one episode."""
    ep = show.episodes[idx]
    reply = await client.acall("miner", "miner", miner_variables(show, idx, bible, ekgs), MinerReply)
    recs, rejects = [], []
    for k, d in enumerate(reply.candidates, 1):
        cid = f"C{ep.number:02d}-{k:02d}"
        raw, err, notes = coerce(d)
        if raw is None:
            rejects.append({"candidate_id": cid, "source": "episode", "status": "rejected", "reasons": ["schema"], "detail": err})
            continue
        if raw.structure == "arc_bridge":
            notes.append("arc_bridge proposed in single-episode pass")
        recs.append({"cid": cid, "source": "episode", "episode": ep.number, "raw": raw.model_dump(), "notes": notes})
    return recs, rejects


async def mine_all(show: Show, bible: ShowBible, ekgs: dict[int, EKG], client, on_done=None,
                   limited: list | None = None) -> tuple[list[dict], list[dict]]:
    """Mine every episode. With ``limited`` given, an episode whose call hits a provider
    limit is skipped and recorded there as (episode number, error) instead of ending the run."""
    from coldopen.llm.provider import ProviderLimitError

    async def one(i: int):
        try:
            r = await mine_episode(show, i, bible, ekgs, client)
        except ProviderLimitError as e:
            if limited is None:
                raise
            limited.append((show.episodes[i].number, e))
            r = ([], [])
        if on_done:
            on_done(show.episodes[i].number)
        return r

    results = await asyncio.gather(*(one(i) for i in range(len(show.episodes))))
    return [r for rs, _ in results for r in rs], [x for _, xs in results for x in xs]


# --------------------------------------------------------------------------- arc bridge


def moments_text(cands: list[ValidCandidate], ix: ShowIndex) -> str:
    blocks = []
    for c in cands:
        seg = c.segments[0] if len(c.segments) == 1 else None
        rng = (
            f"E{seg.episode} {seg.start_tc}-{seg.end_tc}" if seg else " + ".join(f"E{s.episode}" for s in c.segments)
        )
        head = f'{c.cid} | E{c.segments[0].episode} | {rng} | {c.raw.engine} | hook: "{c.raw.hook_text}" | cliff: "{c.raw.cliffhanger_text}"'
        atoms = [ix.by_id[i] for s in c.segments for i in s.atom_ids]
        blocks.append(head + "\n" + "\n".join("    " + compact_atom(a) for a in atoms))
    return "\n".join(blocks)


def arcs_text(bible: ShowBible) -> str:
    return "\n".join(f"- E{a.first_ep}-E{a.last_ep} {a.name}: {a.summary}" for a in bible.arcs)


def pick_moments(valid: list[ValidCandidate], ekg_tension, limit: int = 30) -> list[ValidCandidate]:
    singles = [c for c in valid if c.source == "episode"]
    return sorted(singles, key=lambda c: (-(c.raw.miner_hook + c.raw.miner_cliff) - ekg_tension(c), c.cid))[:limit]


async def mine_arc_bridges(moments: list[ValidCandidate], bible: ShowBible, ix: ShowIndex, client) -> tuple[list[dict], list[dict]]:
    if len(moments) < 2:
        return [], []
    reply = await client.acall(
        "miner", "arc_bridge", {"arcs": arcs_text(bible), "moments": moments_text(moments, ix)}, MinerReply
    )
    recs, rejects = [], []
    for k, d in enumerate(reply.candidates[:8], 1):
        cid = f"AB-{k:02d}"
        d = dict(d, structure="arc_bridge")
        raw, err, notes = coerce(d)
        if raw is None:
            rejects.append({"candidate_id": cid, "source": "arc_bridge", "status": "rejected", "reasons": ["schema"], "detail": err})
            continue
        recs.append({"cid": cid, "source": "arc_bridge", "episode": None, "raw": raw.model_dump(), "notes": notes})
    return recs, rejects
