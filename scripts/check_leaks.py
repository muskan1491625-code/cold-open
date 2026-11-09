#!/usr/bin/env python3
"""Pre-commit guard: fail if a staged file shares 4+ consecutive words with a script line.

Why: the input pack is confidential, and AI-written tests or docs can quietly
quote it. This compares word 4-grams of every staged text file against the
4-grams of every line in data/pack/*.txt.

Noise control: 4-grams made only of very common words ("and in the of") and
the pack's structural boilerplate (labels like "Scene heading") are ignored,
so ordinary English and the documented file format don't trip it.

Usage:  python scripts/check_leaks.py            # staged files (git hook)
        python scripts/check_leaks.py FILE ...   # explicit files
Exit 0 = clean (or no pack present), 1 = possible leak.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

N = 4
PACK = Path("data/pack")
WORD = re.compile(r"[a-z0-9']+")
COMMON = set(
    """a an the and or but of to in on at by for with from as is are was were be been it its this that these those
    he she they we you i his her their our your him them me my not no so if then than into out up down over
    has have had do does did will would can could should all any some one two three s t""".split()
)
# Words of the screenplay *format* and generic shot vocabulary ("extreme close-up on the"),
# which any screenplay shares; they are not story text.
FORMAT = set(
    """episode number logline continuity notes script start end scene heading int ext day night
    actions dialogue camera action sound fx first
    shot close up extreme medium wide tight push pull slow over shoulder angle low high tracking pan profile
    insert pov two reverse establishing frame framing o s v""".split()
)


def grams(text: str) -> set[tuple[str, ...]]:
    words = WORD.findall(text.lower())
    out = set()
    for i in range(len(words) - N + 1):
        g = tuple(words[i : i + N])
        if all(w in COMMON or w in FORMAT or w.isdigit() for w in g):
            continue
        out.add(g)
    return out


def pack_grams() -> set[tuple[str, ...]]:
    gs: set[tuple[str, ...]] = set()
    for f in sorted(PACK.glob("*.txt")):
        for line in f.read_text(errors="replace").splitlines():
            gs |= grams(line)
    return gs


NAME = re.compile(r"\b[A-Z][a-z]{3,}\b")
# Capitalised words that are ordinary English, so they never count as show-specific names.
ENGLISH_CAPS = {"Monday", "Sunday", "English", "French", "Christmas", "Bible", "Episode", "Scene", "Camera", "Action",
                "Sound", "Start", "Lord", "Lady", "Duke", "Duchess", "King", "Queen", "Father", "Mother", "Sister",
                "Brother", "Madam", "Captain", "Master", "Doctor", "Heaven", "Hell", "Sir", "Mama", "Papa", "Majesty",
                "Grace", "Highness", "Prince", "Princess", "Count", "Countess", "Earl", "Baron", "Marquess"}


DICT = Path("/usr/share/dict/words")


def pack_names() -> set[str]:
    """Show-specific proper nouns: capitalised in the story text, never lower case in the pack, not English.

    Block headers (speaker cues and delivery tags) are skipped. A word whose lower-case form is in the system
    word list is ordinary English. Without a word list the name check is skipped (the 4-gram check still runs).
    """
    if not DICT.exists():
        return set()
    english = {w.strip() for w in DICT.read_text(errors="replace").splitlines() if w[:1].islower()}
    caps, lower = set(), set()
    for f in sorted(PACK.glob("*.txt")):
        for line in f.read_text(errors="replace").splitlines():
            lower |= set(re.findall(r"\b[a-z]+\b", line))
            if line.startswith("**"):
                continue
            caps |= set(NAME.findall(line))
    out = set()
    for w in caps:
        base = w[:-1] if w.endswith("s") and w[:-1].lower() in english else w
        if w.lower() in lower or w.lower() in english or base.lower() in english or w in ENGLISH_CAPS:
            continue
        out.add(w)
    return out


def staged_files() -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"], capture_output=True, text=True, check=True
    ).stdout
    return [Path(p) for p in out.splitlines() if p]


def main(argv: list[str]) -> int:
    if not PACK.exists():
        print("check_leaks: data/pack not present, skipping")
        return 0
    files = [Path(a) for a in argv] if argv else staged_files()
    secret = pack_grams()
    names = pack_names()
    bad = 0
    for f in files:
        if not f.is_file() or f.suffix in {".png", ".jpg", ".pdf", ".woff", ".woff2", ".ico", ".zip"}:
            continue
        try:
            text = f.read_text()
        except UnicodeDecodeError:
            continue
        hits = sorted(grams(text) & secret)
        found = sorted(set(NAME.findall(text)) & names)
        if found:
            bad += 1
            lines = [i for i, ln in enumerate(text.splitlines(), 1) if set(NAME.findall(ln)) & names]
            print(f"check_leaks: {f} names {len(found)} show-specific proper noun(s) (lines {', '.join(map(str, lines[:20]))})")
        if hits:
            bad += 1
            # Report locations only; never echo pack text into logs.
            lines = [i for i, ln in enumerate(text.splitlines(), 1) if grams(ln) & secret]
            where = f" (lines {', '.join(map(str, lines[:20]))})" if lines else ""
            print(f"check_leaks: {f} shares {len(hits)} four-word sequence(s) with the input pack{where}")
    if bad:
        print("check_leaks: commit blocked. Remove quoted script text (run with the file path to re-check).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
