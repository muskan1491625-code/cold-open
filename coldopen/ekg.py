"""Stage 3: the tension EKG, a per-second loudness signal for every episode.

Why code and not an LLM: it is cheap, deterministic and exhaustive, so it
gives recall (where the loud moments are) and a "tension" score component.
It is deliberately dumb about meaning: "fire" in a hearth scores as violence.
The LLM miner is told about hotspots but is also asked for a quiet-power
candidate away from them, which is how meaning corrects loudness.
"""

from __future__ import annotations

import math
import re

import numpy as np

from coldopen.models import EKG, Atom, Episode, LexiconEntry, Show

VERBS = re.compile(
    r"\b(slap|strike|struck|grab|seiz|drag|kneel|knelt|kiss|collaps|faint|shatter|explod|explosion|burn|blood|chain"
    r"|hang|hung|gallows|poison|scream|knife|knives|sword|blade|iron|fire)\w*",
    re.I,
)
SFX_PEAK = re.compile(r"peak|swell|sting|gasp|scream|crash|explosion|detonation|cliffhanger", re.I)
CAM_TIGHT = re.compile(r"extreme close-up|push-in|push in|tight", re.I)
SIGMA = 1.5
HOT_MIN = 0.6
HOT_GAP = 8.0


def atom_score(a: Atom, lex: dict[str, LexiconEntry]) -> float:
    """Contribution of one atom to every second it overlaps (spec section 7, stage 3)."""
    if a.kind == "dialogue":
        ar = [lex[t].arousal for t in a.emotion_tags if t in lex and lex[t].is_emotion]
        base = sum(ar) / len(ar) if ar else 0.3
        words = len(a.text.split())
        wps = words / max(a.end - a.start, 0.1)
        return base + 0.25 * min(2, a.text.count("!")) + 0.1 * ("?" in a.text) + 0.05 * wps
    if a.kind == "action":
        return 0.5 * len(VERBS.findall(a.text))
    if a.kind == "sfx":
        return 0.6 if SFX_PEAK.search(a.text) else 0.0
    if a.kind == "camera":
        return 0.15 if CAM_TIGHT.search(a.text) else 0.0
    return 0.0


def raw_signal(ep: Episode, lex: dict[str, LexiconEntry]) -> np.ndarray:
    n = max(1, math.ceil(ep.duration))
    r = np.zeros(n)
    for a in ep.atoms:
        s = atom_score(a, lex)
        if s == 0:
            continue
        lo = max(0, int(math.floor(a.start)))
        hi = min(n, int(math.ceil(a.end)) if a.end > a.start else lo + 1)
        r[lo:hi] += s
    return r


def gaussian_smooth(x: np.ndarray, sigma: float = SIGMA) -> np.ndarray:
    """Normalised Gaussian smoothing; edges are renormalised so they are not damped."""
    rad = max(1, int(math.ceil(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-rad, rad + 1) / sigma) ** 2)
    num = np.convolve(x, k, mode="same")
    den = np.convolve(np.ones_like(x), k, mode="same")
    return num / den


def normalise(signals: list[np.ndarray]) -> tuple[list[np.ndarray], float, float]:
    """Show-wide 5th/95th percentile normalisation to 0..1 (clipped)."""
    allv = np.concatenate(signals) if signals else np.zeros(1)
    lo, hi = float(np.percentile(allv, 5)), float(np.percentile(allv, 95))
    span = hi - lo if hi > lo else 1.0
    return [np.clip((s - lo) / span, 0.0, 1.0) for s in signals], lo, hi


def hotspots(values: np.ndarray, ep: Episode, min_value: float = HOT_MIN, gap: float = HOT_GAP) -> list[dict]:
    """Local maxima >= min_value, at least `gap` seconds apart (strongest first, then by time)."""
    peaks = []
    for t in range(len(values)):
        v = values[t]
        left = values[t - 1] if t > 0 else -1
        right = values[t + 1] if t + 1 < len(values) else -1
        if v >= min_value and v >= left and v >= right:
            peaks.append((float(v), t))
    chosen: list[tuple[float, int]] = []
    for v, t in sorted(peaks, key=lambda p: (-p[0], p[1])):
        if all(abs(t - c) >= gap for _, c in chosen):
            chosen.append((v, t))
    out = []
    for v, t in sorted(chosen, key=lambda p: p[1]):
        ids = [a.id for a in ep.atoms if a.start < t + 1 and a.end > t and a.kind != "camera"]
        out.append({"t": float(t), "value": round(v, 3), "atom_ids": ids})
    return out


def build_ekg(show: Show, lex: dict[str, LexiconEntry]) -> list[EKG]:
    smoothed = [gaussian_smooth(raw_signal(ep, lex)) for ep in show.episodes]
    norm, _, _ = normalise(smoothed)
    return [
        EKG(episode=ep.number, values=[round(float(v), 4) for v in vals], hotspots=hotspots(vals, ep))
        for ep, vals in zip(show.episodes, norm)
    ]


def clip_tension(ekg: EKG, start: float, end: float) -> float:
    """90th percentile of the EKG inside [start, end): the 'tension' score component."""
    lo, hi = int(math.floor(start)), max(int(math.floor(start)) + 1, int(math.ceil(end)))
    vals = ekg.values[lo:hi]
    return float(np.percentile(vals, 90)) if vals else 0.0


def debug_png(ekgs: list[EKG], path) -> None:
    """Heatmap for eyeballing: one row per episode, time in seconds, same ramp as the UI."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("heat", [(0, "#3BA6A0"), (0.55, "#F2A33A"), (1, "#E8494F")])
    width = max(len(e.values) for e in ekgs)
    grid = np.full((len(ekgs), width), np.nan)
    for i, e in enumerate(ekgs):
        grid[i, : len(e.values)] = e.values
    fig, ax = plt.subplots(figsize=(12, max(3, len(ekgs) * 0.16)), dpi=120)
    fig.patch.set_facecolor("#14212A")
    ax.set_facecolor("#14212A")
    ax.imshow(grid, aspect="auto", cmap=cmap, vmin=0, vmax=1, interpolation="nearest")
    for i, e in enumerate(ekgs):
        for h in e.hotspots:
            ax.plot(h["t"], i, marker="|", color="#ECE6D8", markersize=5)
    ax.set_yticks(range(len(ekgs)), [f"E{e.episode}" for e in ekgs], fontsize=6, color="#93A4AE")
    ax.tick_params(colors="#93A4AE", labelsize=7)
    ax.set_xlabel("seconds", color="#93A4AE")
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
