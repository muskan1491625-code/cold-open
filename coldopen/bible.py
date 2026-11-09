"""Stage 1: the show bible (one LLM call) plus code-side detectors.

Why split: code is good at exhaustive, boring recall (every speaker label with
its episode span; every pair of proper nouns spelled almost alike). The LLM is
good at judgement (is "Hollowmere" the same house as "Hollowmoor"? is "Holloway" a
different family?). So code proposes, the LLM decides, and the result is a
contract (ShowBible) that later stages consume.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import combinations

from rapidfuzz import fuzz

from coldopen.models import ShowBible, Show

# --------------------------------------------------------------------------- code detectors

_CAP = re.compile(r"\b([A-Z][a-z]{3,}(?:[A-Z][a-z]+)?)(?:'s)?\b")
_WORD = re.compile(r"[A-Za-z]+")
_STOP = set(
    """This That There These Those Then When What Where Which While With Without Your Yours They Their Them Have
    Will Would Could Should Shall Must Never Every Only Just Even Into Upon Over Under After Before Because Please
    Yes Not Now Here Some Such Very Much Many More Most Lord Lady Sir Madam Mother Father Sister Brother Duke Duchess
    Earl King Queen Prince Princess Captain Master Mistress God Heaven Close Wide Medium Extreme Camera Action Sound
    Scene Episode Continuity Start End Night Day""".split()
)


def label_table(show: Show) -> list[dict]:
    """Every dialogue speaker label with first/last episode and line count."""
    eps: dict[str, list[int]] = defaultdict(list)
    for ep in show.episodes:
        for a in ep.atoms:
            if a.kind == "dialogue" and a.speaker_label:
                eps[a.speaker_label].append(ep.number)
    rows = [{"label": k, "first_ep": min(v), "last_ep": max(v), "lines": len(v)} for k, v in eps.items()]
    return sorted(rows, key=lambda r: (r["first_ep"], -r["lines"], r["label"]))


def proper_nouns(show: Show) -> dict[str, set[int]]:
    """Capitalised words never seen in lower case anywhere in the show, with their episodes.

    The lower-case test is what removes sentence-initial words ("Close", "Wide")
    without a hand-made list per show.
    """
    texts: list[tuple[int, str]] = []
    for ep in show.episodes:
        texts.append((ep.number, ep.logline))
        texts.append((ep.number, ep.continuity_notes))
        texts.extend((ep.number, s.get("heading", "").title()) for s in ep.scenes)
        texts.extend((ep.number, a.text) for a in ep.atoms)
    lower_seen = {w for _, t in texts for w in _WORD.findall(t) if w.islower()}
    found: dict[str, set[int]] = defaultdict(set)
    for n, t in texts:
        for w in _CAP.findall(t):
            if w in _STOP or w.lower() in lower_seen:
                continue
            found[w].add(n)
    return dict(found)


def spelling_conflicts(nouns: dict[str, set[int]], min_ratio: float = 75.0) -> list[dict]:
    """Group near-identical spellings (same first 3 letters, fuzzy ratio >= 75).

    Pairs whose episode sets are identical are skipped: two spellings used together
    in exactly the same episodes are usually two different things, not drift.
    Groups are connected components, so A~B and B~C put A, B, C together; the
    LLM then decides which members are truly one entity.
    """
    names = sorted(nouns)
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in combinations(names, 2):
        if a[:3].lower() != b[:3].lower() or a.lower() == b.lower():
            continue
        if nouns[a] == nouns[b]:
            continue
        if fuzz.ratio(a.lower(), b.lower()) >= min_ratio:
            parent[find(a)] = find(b)

    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[find(n)].append(n)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        out.append(
            {
                "variants": [
                    {"spelling": m, "episodes": sorted(nouns[m])} for m in sorted(members, key=lambda m: min(nouns[m]))
                ]
            }
        )
    return sorted(out, key=lambda g: min(g["variants"][0]["episodes"]))


# --------------------------------------------------------------------------- prompt material


def episode_summaries(show: Show) -> str:
    return "\n".join(f"E{ep.number}: {ep.logline} || {ep.continuity_notes}" for ep in show.episodes)


def labels_text(rows: list[dict]) -> str:
    return "\n".join(f"{r['label']} | E{r['first_ep']} | E{r['last_ep']} | {r['lines']}" for r in rows)


def conflicts_text(groups: list[dict]) -> str:
    if not groups:
        return "(none found)"
    lines = []
    for i, g in enumerate(groups, 1):
        parts = [f"{v['spelling']} (E{', E'.join(map(str, v['episodes']))})" for v in g["variants"]]
        lines.append(f"C{i}: " + " ~ ".join(parts))
    return "\n".join(lines)


def bible_variables(show: Show) -> tuple[dict, dict]:
    labels = label_table(show)
    groups = spelling_conflicts(proper_nouns(show))
    variables = {
        "title": show.title,
        "episodes": episode_summaries(show),
        "labels": labels_text(labels),
        "conflicts": conflicts_text(groups),
    }
    return variables, {"labels": labels, "conflict_candidates": groups}


# --------------------------------------------------------------------------- applying the bible


def resolve_speaker(bible: ShowBible, label: str, episode: int) -> str | None:
    """Canonical name for a raw label in a given episode (aliases are episode-scoped)."""
    lab = label.strip().upper()
    for ch in bible.characters:
        for al in ch.aliases:
            if al.label.strip().upper() == lab and al.first_ep <= episode <= al.last_ep:
                return ch.canonical
    for ch in bible.characters:  # label is the bare name, or "LADY <name>" style
        cu = ch.canonical.upper()
        first = cu.split()[0] if cu else ""
        if lab == cu or (first and (lab == first or lab.endswith(" " + first) or lab.startswith(first + " "))):
            return ch.canonical
    return None


def apply_speakers(show: Show, bible: ShowBible) -> dict[str, int]:
    """Fill Atom.speaker in place. Returns counts for the run report."""
    c: Counter = Counter()
    for ep in show.episodes:
        for a in ep.atoms:
            if a.kind != "dialogue" or not a.speaker_label:
                continue
            name = resolve_speaker(bible, a.speaker_label, ep.number)
            a.speaker = name or a.speaker_label.title()
            c["resolved" if name else "unresolved"] += 1
    return dict(c)


def descriptor_for(bible: ShowBible, canonical: str | None, label: str | None = None) -> str:
    """How a stranger sees this speaker: the bible's visual descriptor, never a name."""
    if canonical:
        for ch in bible.characters:
            if ch.canonical == canonical and ch.visual_descriptor and ch.visual_descriptor.lower() != "unknown":
                return ch.visual_descriptor
    return "someone off-screen" if label is None else "a person in the scene"


def bible_digest(bible: ShowBible, max_chars: int = 2500) -> str:
    """Compact bible for the miner prompt (it is sent once per episode)."""
    lines = [f"Premise: {bible.premise}", "Characters:"]
    for ch in bible.characters:
        al = ", ".join(f"{a.label} E{a.first_ep}-{a.last_ep}" for a in ch.aliases)
        lines.append(f"- {ch.canonical}: {ch.role}" + (f" (labels: {al})" if al else ""))
    lines.append("Arcs:")
    lines.extend(f"- E{a.first_ep}-{a.last_ep} {a.name}: {a.summary}" for a in bible.arcs)
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def conflict_spellings(bible: ShowBible) -> list[tuple[str, dict[str, list[int]]]]:
    """[(entity, {spelling: episodes})] for continuity warnings."""
    out = []
    for c in bible.continuity_conflicts:
        out.append((c.entity, {v.get("spelling", ""): list(v.get("episodes", [])) for v in c.variants}))
    return out
