"""Stage 6: the Cold Viewer test, the grader, and the referent check.

Why a separate "judge" model that only sees perceived text: the miner knows the
whole show and will happily pick a clip that only makes sense to fans. The
Cold Viewer never sees IDs, names on screen, or the bible: only what a
scroller would perceive. The grader then compares its understanding with the
bible's truth. Different model family on purpose, so the judge does not share
the miner's blind spots.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

from coldopen.models import Atom, Show, ShowBible
from coldopen.render import perceived_atom
from coldopen.snap import ShowIndex, ValidCandidate

HOOK_WINDOW = 3.0
# Capitalised in address ("Father, please") but never a name a stranger must place.
NOT_NAMES = {
    "Father", "Mother", "Sister", "Brother", "Lord", "Lady", "Sir", "Madam", "Duke", "Duchess", "King", "Queen",
    "Prince", "Princess", "Captain", "Master", "Mistress", "Grandmother", "Grandfather", "Uncle", "Aunt", "Son",
    "Daughter", "Husband", "Wife", "Your", "Grace", "Majesty", "Highness", "Doctor", "Father's", "Mama", "Papa",
}


def _clamp_int(v, lo=1, hi=5) -> int:
    try:
        return min(hi, max(lo, int(round(float(v)))))
    except (TypeError, ValueError):
        return lo


class HookReply(BaseModel):
    stop: int
    reason: str = ""

    @field_validator("stop", mode="before")
    @classmethod
    def _clamp(cls, v):
        return _clamp_int(v)


class FullReply(BaseModel):
    main_character: str = ""
    what_they_want: str = ""
    conflict: str = ""
    left_wondering: str = ""
    need_next: int
    confusing: list[str] = []

    @field_validator("need_next", mode="before")
    @classmethod
    def _clamp(cls, v):
        return _clamp_int(v)

    @field_validator("confusing", mode="before")
    @classmethod
    def _listify(cls, v):
        if v is None:
            return []
        return [v] if isinstance(v, str) else [str(x) for x in v]


def _snap(v, allowed) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return min(allowed, key=lambda a: abs(a - x))


class GradeReply(BaseModel):
    who: float
    want: float
    conflict: float
    open_question: float
    notes: str = ""

    @field_validator("who", "open_question", mode="before")
    @classmethod
    def _binary(cls, v):
        return _snap(v, (0.0, 1.0))

    @field_validator("want", "conflict", mode="before")
    @classmethod
    def _ternary(cls, v):
        return _snap(v, (0.0, 0.5, 1.0))


# --------------------------------------------------------------------------- playback


def playback(vc: ValidCandidate, ix: ShowIndex) -> list[tuple[float, int, Atom]]:
    """[(playback seconds, segment index, atom)] in the order a viewer experiences them."""
    out = []
    offset = 0.0
    for k, s in enumerate(vc.segments):
        for i in s.atom_ids:
            a = ix.by_id[i]
            out.append((round(offset + max(0.0, a.start - s.start), 3), k, a))
        offset += s.end - s.start
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def clip_text(vc: ValidCandidate, ix: ShowIndex, bible: ShowBible | None, until: float | None = None) -> str:
    lines, seg = [], 0
    for t, k, a in playback(vc, ix):
        if until is not None and t >= until:
            break
        if k != seg:
            lines.append("[Hard cut]")
            seg = k
        lines.append(perceived_atom(a, bible))
    return "\n".join(lines)


def truth_text(vc: ValidCandidate, ix: ShowIndex, bible: ShowBible, show: Show) -> tuple[str, str]:
    eps = sorted({s.episode for s in vc.segments})
    speakers = sorted({ix.by_id[i].speaker for s in vc.segments for i in s.atom_ids if ix.by_id[i].speaker})
    lines = [f"Premise: {bible.premise}"]
    for ch in bible.characters:
        if ch.canonical in speakers:
            lines.append(f"{ch.canonical} ({ch.visual_descriptor}): {ch.role}")
    for a in bible.arcs:
        if a.first_ep <= max(eps) and a.last_ep >= min(eps):
            lines.append(f"Arc {a.name}: {a.summary}")
    for ep in show.episodes:
        if ep.number in eps:
            lines.append(f"E{ep.number}: {ep.logline}")
    return ", ".join(f"E{e}" for e in eps), "\n".join(lines)


# --------------------------------------------------------------------------- referents


def referent_check(vc: ValidCandidate, ix: ShowIndex, bible: ShowBible, nouns: set[str]) -> list[str]:
    """Names spoken in the clip that a stranger has no way to place.

    Grounded if: that character speaks in the clip, or is named in a camera or
    action line inside it, or the line itself explains the name ("Mara, my
    sister" / "my sister, Mara").
    """
    atoms = [ix.by_id[i] for s in vc.segments for i in s.atom_ids]
    speakers = {a.speaker for a in atoms if a.speaker}
    directions = " ".join(a.text for a in atoms if a.kind in ("camera", "action"))
    name_to_char = {}
    for ch in bible.characters:
        for part in [ch.canonical] + ch.canonical.split():
            if len(part) >= 4 and part[0].isupper():
                name_to_char.setdefault(part, ch.canonical)
    known = (set(nouns) | set(name_to_char)) - NOT_NAMES
    for c in bible.continuity_conflicts:
        known |= {str(v.get("spelling", "")) for v in c.variants}
    out: list[str] = []
    for a in atoms:
        if a.kind != "dialogue":
            continue
        for m in re.finditer(r"\b[A-Z][a-z]{3,}\b", a.text):
            w = m.group(0)
            if w not in known or w in out:
                continue
            before = a.text[: m.start()].rstrip(" \"'“‘")
            if (not before or before[-1] in ".!?") and w not in name_to_char:
                continue  # sentence-initial capital, not evidence of a name
            ch = name_to_char.get(w)
            if ch and ch in speakers:
                continue
            if re.search(rf"\b{w}\b", directions):
                continue
            if re.search(rf"\b{w}\s*,\s*(the|my|your|his|her|our)\s+\w+", a.text) or re.search(
                rf"\b(my|your|his|her|our|the)\s+\w+\s*,?\s*{w}\b", a.text
            ):
                continue
            out.append(w)
    return out


# --------------------------------------------------------------------------- run


async def judge(vc: ValidCandidate, ix: ShowIndex, bible: ShowBible, show: Show, nouns: set[str], client) -> dict:
    first3 = clip_text(vc, ix, bible, until=HOOK_WINDOW) or "[nothing yet]"
    full = clip_text(vc, ix, bible)
    a = await client.acall("judge", "viewer_hook", {"first_three_seconds": first3}, HookReply)
    b = await client.acall("judge", "viewer_full", {"clip": full}, FullReply)
    eps, truth = truth_text(vc, ix, bible, show)
    g = await client.acall(
        "classifier", "grader", {"episodes": eps, "truth": truth, "clip": full, "answers": b.model_dump_json()}, GradeReply
    )
    refs = referent_check(vc, ix, bible, nouns)
    return {
        "stage_a": a.model_dump(),
        "stage_b": b.model_dump(),
        "grader": g.model_dump(),
        "first_three_seconds": first3,
        "ungrounded_refs": refs,
        "scores": {
            "hook": (a.stop - 1) / 4,
            "cliff": (b.need_next - 1) / 4,
            "viewer": (g.who + g.want + g.conflict + g.open_question) / 4,
        },
    }
