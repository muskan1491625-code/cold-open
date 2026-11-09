"""Stage 0: parse a folder (or .zip) of episode scripts into Episode/Atom objects.

Why a hand-written state machine and not an LLM: timestamps are facts. Every
later guarantee (no mid-line cuts, hook <= 3 s, valid EDLs) depends on these
numbers being exactly what the script says, so parsing is code and it fails
loudly, naming the file, line number and what was expected. It never drops a
block silently.

Format notes (verified on the real pack, see docs/BUILD_LOG.md):
- labels are matched case-insensitively (one episode uses upper-case labels);
- dialogue headers carry 0-3 parentheticals, and one parenthetical can mix a
  technical tag with emotions separated by "/" (e.g. "(O.S. / Low, Tense)");
- camera blocks tile the timeline, and their boundaries are the shot changes.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from coldopen.models import Atom, Episode, Show
from coldopen.timecode import parse_tc

KIND_ORDER = {"camera": 0, "action": 1, "dialogue": 2, "sfx": 3}


class ParseError(ValueError):
    def __init__(self, file: str, line: int, expected: str, got: str = ""):
        self.file, self.line, self.expected, self.got = file, line, expected, got
        msg = f"{file}:{line}: expected {expected}"
        if got:
            msg += f", got {got[:80]!r}"
        super().__init__(msg)


# --------------------------------------------------------------------------- line patterns

RE_META = re.compile(r"^(EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES)\s*:\s*(.*)$", re.I)
RE_START = re.compile(r"^-{2,}\s*SCRIPT START\s*-{2,}\s*$", re.I)
RE_END = re.compile(r"^-{2,}\s*SCRIPT END\s*-{2,}\s*$", re.I)
RE_SCENE = re.compile(r"^SCENE\s+(\d+)\s*$", re.I)
RE_SCENE_FIELD = re.compile(r"^SCENE (HEADING|START|END)\s*:\s*(.*)$", re.I)
RE_AD = re.compile(r"^ACTIONS AND DIALOGUE\s*:?\s*$", re.I)
RE_HEADER = re.compile(r"^\*\*(.+?):\*\*\s*$")
RE_TIMING = re.compile(r"^Start\s*:\s*\(([^)]*)\)\s*\|\s*End\s*:\s*\(([^)]*)\)\s*$", re.I)
RE_PAREN = re.compile(r"\(([^)]*)\)")
RE_TECH = re.compile(
    r"^(o\.?\s?s\.?|v\.?\s?o\.?|off[- ]?screen|cont'?d\.?|cont\.|continues|continued|continuing)$", re.I
)
RE_EP_FILE = re.compile(r"episode-(\d+)", re.I)


def _norm_tech(tag: str) -> str:
    t = tag.lower().replace(" ", "").replace(".", "")
    if t == "os":
        return "O.S."
    if t == "vo":
        return "V.O."
    if t.startswith("off"):
        return "Off-screen"
    return "CONT'D"


def split_header(label: str) -> tuple[str, list[str], list[str]]:
    """``NAME (O.S.) (Cold, Commanding)`` -> ("NAME", ["O.S."], ["cold", "commanding"])."""
    label = label.replace("’", "'")
    speaker = RE_PAREN.split(label, maxsplit=1)[0].strip() if "(" in label else label.strip()
    tech: list[str] = []
    emo: list[str] = []
    for group in RE_PAREN.findall(label):
        for part in re.split(r"[/,]", group):
            p = part.strip()
            if not p:
                continue
            if RE_TECH.match(p):
                n = _norm_tech(p)
                if n not in tech:
                    tech.append(n)
            else:
                e = p.lower()
                if e not in emo:
                    emo.append(e)
    return speaker, tech, emo


def kind_of(speaker_label: str) -> str:
    u = speaker_label.upper()
    if u.startswith("CAMERA"):
        return "camera"
    if u.startswith("ACTION"):
        return "action"
    if u.startswith("SOUND") or u.startswith("SFX"):
        return "sfx"
    return "dialogue"


# --------------------------------------------------------------------------- single file


@dataclass
class _Block:
    line: int
    scene: int
    kind: str
    speaker_label: str | None
    tech: list[str]
    emo: list[str]
    start: float
    end: float
    body: list[str]


def parse_episode_text(text: str, name: str = "<text>") -> tuple[Episode, list[_Block]]:
    meta: dict[str, str] = {}
    meta_key: str | None = None
    scenes: list[dict] = []
    blocks: list[_Block] = []
    state = "preamble"  # preamble -> script -> (await_timing | body) -> done
    current: _Block | None = None
    pending_header: tuple[int, str] | None = None

    def close_block() -> None:
        nonlocal current
        if current is not None:
            blocks.append(current)
            current = None

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for no, raw in enumerate(lines, 1):
        line = raw.strip()

        if state == "preamble":
            if not line:
                continue
            m = RE_META.match(line)
            if m:
                meta_key = m.group(1).upper()
                meta[meta_key] = m.group(2).strip()
                continue
            if RE_START.match(line):
                state = "script"
                continue
            if meta_key:  # a wrapped logline / continuity paragraph
                meta[meta_key] = (meta[meta_key] + " " + line).strip()
                continue
            raise ParseError(name, no, "EPISODE NUMBER / LOGLINE / CONTINUITY NOTES or '--- SCRIPT START ---'", line)

        if state == "done":
            if line:
                raise ParseError(name, no, "nothing after '--- SCRIPT END ---'", line)
            continue

        if state == "await_timing":
            if not line:
                continue
            m = RE_TIMING.match(line)
            if not m:
                raise ParseError(name, no, "'Start: (MM:SS.ss) | End: (MM:SS.ss)' after block header", line)
            hdr_line, label = pending_header  # type: ignore[misc]
            try:
                start, end = parse_tc(m.group(1)), parse_tc(m.group(2))
            except ValueError as e:
                raise ParseError(name, no, "valid timecodes", line) from e
            if end < start:
                raise ParseError(name, no, f"End >= Start (got {start} > {end})", line)
            if not scenes:
                raise ParseError(name, hdr_line, "a SCENE line before the first block", label)
            sp, tech, emo = split_header(label)
            k = kind_of(sp)
            current = _Block(
                line=hdr_line,
                scene=scenes[-1]["number"],
                kind=k,
                speaker_label=sp if k == "dialogue" else None,
                tech=tech,
                emo=emo,
                start=start,
                end=end,
                body=[],
            )
            state = "body"
            continue

        # state in ("script", "body"): structural lines end any open body
        if RE_END.match(line):
            close_block()
            state = "done"
            continue
        m = RE_HEADER.match(line)
        if m:
            close_block()
            pending_header = (no, m.group(1).strip())
            state = "await_timing"
            continue
        m = RE_SCENE.match(line)
        if m:
            close_block()
            scenes.append({"number": int(m.group(1)), "heading": "", "start": None, "end": None})
            state = "script"
            continue
        m = RE_SCENE_FIELD.match(line)
        if m and state == "script":
            if not scenes:
                raise ParseError(name, no, "a SCENE line before scene fields", line)
            field, value = m.group(1).lower(), m.group(2).strip()
            if field == "heading":
                scenes[-1]["heading"] = value
            else:
                try:
                    scenes[-1][field] = parse_tc(value)
                except ValueError as e:
                    raise ParseError(name, no, f"timecode for Scene {field}", line) from e
            continue
        if RE_AD.match(line) and state == "script":
            continue
        if state == "body":
            if line:
                current.body.append(line)  # type: ignore[union-attr]
            continue
        if not line:
            continue
        raise ParseError(name, no, "a '**LABEL:**' block header, SCENE line or '--- SCRIPT END ---'", line)

    if state == "preamble":
        raise ParseError(name, len(lines), "'--- SCRIPT START ---'")
    if state == "await_timing":
        raise ParseError(name, len(lines), "a timing line for the last block header")
    if state != "done":
        raise ParseError(name, len(lines), "'--- SCRIPT END ---'")
    if not blocks:
        raise ParseError(name, len(lines), "at least one timed block")

    number = _episode_number(meta.get("EPISODE NUMBER", ""), name)
    for s in scenes:
        inside = [b for b in blocks if b.scene == s["number"]]
        if s["start"] is None:
            s["start"] = min((b.start for b in inside), default=0.0)
        if s["end"] is None:
            s["end"] = max((b.end for b in inside), default=s["start"])
    duration = max([b.end for b in blocks] + [s["end"] for s in scenes])
    ep = Episode(
        number=number,
        logline=meta.get("EPISODE LOGLINE", ""),
        continuity_notes=meta.get("CONTINUITY NOTES", ""),
        duration=round(duration, 3),
        scenes=scenes,
        atoms=[],
    )
    return ep, blocks


def _episode_number(value: str, name: str) -> int:
    m = re.search(r"(\d+)", value)
    if m:
        return int(m.group(1))
    m = RE_EP_FILE.search(name)
    if m:
        return int(m.group(1))
    raise ParseError(name, 1, "'EPISODE NUMBER: Episode N' or a file name like episode-001-...")


def _assign_atoms(ep: Episode, blocks: list[_Block], ep_width: int) -> None:
    """Order atoms in time; ties go camera < action < dialogue < sfx, then file order."""
    order = sorted(range(len(blocks)), key=lambda i: (blocks[i].start, KIND_ORDER[blocks[i].kind], i))
    idx_width = max(3, len(str(len(blocks))))
    ep.atoms = [
        Atom(
            id=f"E{ep.number:0{ep_width}d}.{n:0{idx_width}d}",
            episode=ep.number,
            scene=blocks[i].scene,
            kind=blocks[i].kind,  # type: ignore[arg-type]
            start=blocks[i].start,
            end=blocks[i].end,
            speaker_label=blocks[i].speaker_label,
            emotion_tags=blocks[i].emo,
            tech_tags=blocks[i].tech,
            text=" ".join(blocks[i].body),
        )
        for n, i in enumerate(order, 1)
    ]


# --------------------------------------------------------------------------- show


def _read_sources(path: Path) -> tuple[str, list[tuple[str, str]]]:
    """Return (title, [(file name, text)]) from a folder or a .zip."""
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            members = [
                n for n in z.namelist() if n.lower().endswith(".txt") and "__MACOSX" not in n and not n.endswith("/")
            ]
            files = [(n, z.read(n).decode("utf-8-sig", errors="strict")) for n in members]
            roots = {n.split("/")[0] for n in members if "/" in n}
        title = roots.pop() if len(roots) == 1 else path.stem
        return title, files
    if path.is_dir():
        files = [(str(p), p.read_text(encoding="utf-8-sig")) for p in sorted(path.glob("*.txt"))]
        return path.name, files
    raise FileNotFoundError(f"Input must be a folder of .txt scripts or a .zip: {path}")


def load_show(path: str | Path, max_episodes: int | None = None, title: str | None = None) -> Show:
    """Parse every episode file. Raises ParseError on the first malformed file."""
    p = Path(path)
    show_title, files = _read_sources(p)
    if not files:
        raise ParseError(str(p), 0, "at least one episode .txt file")
    parsed = [parse_episode_text(text, name) for name, text in files]
    parsed.sort(key=lambda eb: eb[0].number)
    seen: dict[int, int] = Counter(e.number for e, _ in parsed)
    dupes = [n for n, c in seen.items() if c > 1]
    if dupes:
        raise ParseError(str(p), 0, f"unique episode numbers (duplicates: {dupes})")
    if max_episodes:
        parsed = parsed[:max_episodes]
    width = max(2, len(str(max(e.number for e, _ in parsed))))
    for ep, blocks in parsed:
        _assign_atoms(ep, blocks, width)
    return Show(title=title or show_title, episodes=[e for e, _ in parsed])


def show_stats(show: Show) -> dict:
    """Counts used by the Phase 1 acceptance check and the run report."""
    atoms = [a for e in show.episodes for a in e.atoms]
    kinds = Counter(a.kind for a in atoms)
    tags = {t for a in atoms for t in a.emotion_tags}
    weak_open = sum(
        1 for e in show.episodes if not any(a.kind == "dialogue" and a.start < 3.0 for a in e.atoms)
    )
    return {
        "episodes": len(show.episodes),
        "blocks": len(atoms),
        **{k: kinds.get(k, 0) for k in ("camera", "dialogue", "action", "sfx")},
        "emotion_tags": len(tags),
        "end_before_start": sum(1 for a in atoms if a.end < a.start),
        "minutes": round(sum(e.duration for e in show.episodes) / 60, 1),
        "min_duration": min(e.duration for e in show.episodes),
        "max_duration": max(e.duration for e in show.episodes),
        "episodes_without_dialogue_first_3s": weak_open,
    }


def atom_index(show: Show) -> dict[str, Atom]:
    return {a.id: a for e in show.episodes for a in e.atoms}


def camera_boundaries(ep: Episode) -> list[float]:
    """Shot changes: the natural places for an editor to cut."""
    b = {0.0, ep.duration}
    for a in ep.atoms:
        if a.kind == "camera":
            b.add(a.start)
            b.add(a.end)
    return sorted(b)


__all__ = [
    "ParseError",
    "load_show",
    "parse_episode_text",
    "split_header",
    "show_stats",
    "atom_index",
    "camera_boundaries",
]
