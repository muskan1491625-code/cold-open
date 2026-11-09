"""Timecode helpers. Internally everything is float seconds; humans see MM:SS.ss."""

from __future__ import annotations

import re

_TC = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d+))?\s*$")


def parse_tc(text: str) -> float:
    """Parse ``MM:SS.ss``, ``HH:MM:SS.ss`` or ``MM:SS`` into seconds."""
    m = _TC.match(text)
    if not m:
        raise ValueError(f"Not a timecode: {text!r} (expected MM:SS.ss, HH:MM:SS.ss or MM:SS)")
    h, mnt, s, frac = m.groups()
    secs = int(h or 0) * 3600 + int(mnt) * 60 + int(s)
    if frac:
        secs += int(frac) / (10 ** len(frac))
    return round(secs, 3)


def fmt_tc(seconds: float) -> str:
    """Seconds -> ``MM:SS.ss`` (minutes may exceed 59 for long material)."""
    if seconds < 0:
        raise ValueError(f"Negative time {seconds}")
    cs = int(round(seconds * 100))
    m, rem = divmod(cs, 6000)
    s, c = divmod(rem, 100)
    return f"{m:02d}:{s:02d}.{c:02d}"


def fmt_smpte(seconds: float, fps: int) -> str:
    """Seconds -> non-drop-frame ``HH:MM:SS:FF`` for EDLs."""
    frames = int(round(seconds * fps))
    ff = frames % fps
    total = frames // fps
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"
