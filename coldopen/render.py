"""How atoms are shown to models and people.

Two renderings, on purpose:
- compact: what the *miner* sees (IDs, times, labels), so it can point at atoms;
- perceived: what a *scroller* sees, with no IDs, no names on screen, speakers
  described by how they look. The Cold Viewer only ever gets this one.
"""

from __future__ import annotations

import re

from coldopen.models import Atom, ShowBible
from coldopen.timecode import fmt_tc

KIND_SHORT = {"camera": "CAM", "action": "ACT", "dialogue": "DLG", "sfx": "SFX"}
CAMERA_MAX = 110
TITLES = {"lord", "lady", "sir", "the", "duke", "duchess", "earl", "king", "queen", "prince", "princess", "mr", "mrs"}


def _trunc(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def speaker_cue(a: Atom) -> str:
    tags = a.tech_tags + a.emotion_tags
    return (a.speaker_label or "") + (f" ({', '.join(tags)})" if tags else "")


def compact_atom(a: Atom) -> str:
    """``E01.031 | 00:33.80-00:35.00 | DLG | LADY X (shaken) | "Line"``"""
    text = _trunc(a.text, CAMERA_MAX) if a.kind == "camera" else a.text
    who = speaker_cue(a) if a.kind == "dialogue" else ""
    parts = [a.id, f"{fmt_tc(a.start)}-{fmt_tc(a.end)}", KIND_SHORT[a.kind]]
    if who:
        parts.append(who)
    parts.append(text)
    return " | ".join(parts)


def descriptor(bible: ShowBible | None, a: Atom) -> str:
    if bible is not None and a.speaker:
        for ch in bible.characters:
            if ch.canonical == a.speaker and ch.visual_descriptor and ch.visual_descriptor.lower() != "unknown":
                d = ch.visual_descriptor
                break
        else:
            d = "a person in the scene"
    else:
        d = "a person in the scene"
    if "O.S." in a.tech_tags or "Off-screen" in a.tech_tags:
        d += " (off-screen)"
    if "V.O." in a.tech_tags:
        d += " (voice-over)"
    return d


def _the(desc: str) -> str:
    return re.sub(r"^(a|an)\s+", "the ", desc.strip(), flags=re.I)


def unname(text: str, bible: ShowBible | None) -> str:
    """Replace character names in stage directions with how they look.

    Viewers see faces, not name cards: "Close-up on Mara's hands" becomes
    "Close-up on the young woman in a grey shawl's hands". Dialogue is NOT passed
    through this, because names spoken aloud are things a viewer hears.
    """
    if bible is None:
        return text
    for ch in sorted(bible.characters, key=lambda c: -len(c.canonical)):
        d = ch.visual_descriptor
        if not d or d.lower() == "unknown":
            continue
        names = {ch.canonical}
        given = [p for p in ch.canonical.split() if p.lower() not in TITLES]
        if given:
            names.add(given[0])  # "Lady Mara Vell" is "Mara" in stage directions
        for n in sorted(names, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(n)}\b", _the(d), text)
    return text


def perceived_atom(a: Atom, bible: ShowBible | None) -> str:
    """One line of what a stranger perceives. Names spoken inside dialogue are kept."""
    if a.kind == "camera":
        return f"[On screen: {_trunc(unname(a.text, bible), CAMERA_MAX)}]"
    if a.kind == "action":
        return f"[Action: {unname(a.text, bible)}]"
    if a.kind == "sfx":
        return f"[Sound: {unname(a.text, bible)}]"
    line = a.text.strip()
    if not (line.startswith('"') or line.startswith("“")):
        line = f'"{line}"'
    return f"{descriptor(bible, a)}: {line}"
