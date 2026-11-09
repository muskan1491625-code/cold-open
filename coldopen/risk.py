"""Stage 7: paid-ad policy risk, spoilers, and where the viewer lands.

The LLM reads the clip (with atom IDs, so it can cite evidence) and names
categories and disclosed reveals. Code turns that into a policy label and
penalties, so the numbers are consistent and auditable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from coldopen.config import Config
from coldopen.models import ShowBible
from coldopen.render import compact_atom
from coldopen.snap import ShowIndex, ValidCandidate

CATEGORIES = {"graphic_violence", "sexual_suggestive", "self_harm", "profanity", "bullying"}
POLICY = {"none": "paid_safe", "low": "review", "high": "organic_only"}


class RiskReply(BaseModel):
    policy_level: Literal["none", "low", "high"]
    categories: list[str] = []
    evidence_ids: list[str] = []
    reveals_disclosed: list[str] = []

    @field_validator("policy_level", mode="before")
    @classmethod
    def _lvl(cls, v):
        v = str(v).strip().lower()
        return {"medium": "low", "moderate": "low", "safe": "none"}.get(v, v)


def risk_variables(vc: ValidCandidate, ix: ShowIndex, bible: ShowBible) -> dict:
    lines = []
    for k, s in enumerate(vc.segments):
        if k:
            lines.append("[Hard cut]")
        lines.extend(compact_atom(ix.by_id[i]) for i in s.atom_ids)
    reveals = "\n".join(f"{r.id} (E{r.episode}): {r.description}" for r in bible.key_reveals) or "(none)"
    return {"clip": "\n".join(lines), "reveals": reveals}


async def assess(vc: ValidCandidate, ix: ShowIndex, bible: ShowBible, cfg: Config, client) -> dict:
    r = await client.acall("classifier", "risk", risk_variables(vc, ix, bible), RiskReply)
    cats = [c for c in r.categories if c in CATEGORIES]
    clip_ids = {i for s in vc.segments for i in s.atom_ids}
    evidence = [i for i in r.evidence_ids if i in clip_ids]
    reveal_ep = {x.id: x.episode for x in bible.key_reveals}
    disclosed = [x for x in r.reveals_disclosed if x in reveal_ep]
    landing = min(s.episode for s in vc.segments)
    pen = cfg.penalties
    policy = POLICY[r.policy_level]
    spoilers = [x for x in disclosed if reveal_ep[x] > landing]
    return {
        "policy": policy,
        "policy_level": r.policy_level,
        "policy_reasons": cats,
        "evidence_ids": evidence,
        "reveals_disclosed": disclosed,
        "spoilers": spoilers,
        "landing_episode": landing,
        "free_runway": max(0, cfg.paywall_episode - landing + 1),
        "policy_penalty": {"paid_safe": 0.0, "review": pen.review, "organic_only": pen.organic_only}[policy],
        "spoiler_penalty": round(pen.spoiler * len(spoilers), 4),
        "paywall_penalty": round(pen.paywall * min(1.0, max(0, landing - cfg.paywall_episode) / 10), 4),
    }
