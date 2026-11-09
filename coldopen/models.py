"""All data contracts for Cold Open (Section 6 of the build spec).

Why one module: every stage reads and writes these shapes, and the LLM is
validated against them. Keeping them in one place makes a contract change a
visible, reviewable diff (and contract changes need sign-off before they land).

Conventions: times are float seconds internally; anything shown to a human
also carries an ``MM:SS.ss`` string. The LLM never produces times, durations
or ranks, only atom IDs, which code resolves.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AtomKind = Literal["camera", "action", "dialogue", "sfx"]

Engine = Literal[
    "humiliation_reversal",
    "dangerous_protector",
    "hidden_identity",
    "sacrifice_ultimatum",
    "framed_condemned",
    "family_betrayal",
    "forbidden_scandal",
    "other",
]


# --------------------------------------------------------------------------- parse


class Atom(BaseModel):
    """One timed block of the script: the smallest unit the LLM may reference."""

    id: str  # "E01.042": episode (2-3 digits) + zero-padded index in time order
    episode: int
    scene: int
    kind: AtomKind
    start: float
    end: float
    speaker_label: str | None = None  # raw label, dialogue only
    speaker: str | None = None  # canonical name, filled after the bible
    emotion_tags: list[str] = Field(default_factory=list)
    tech_tags: list[str] = Field(default_factory=list)
    text: str


class Episode(BaseModel):
    number: int
    logline: str
    continuity_notes: str
    duration: float
    scenes: list[dict]  # number, heading, start, end
    atoms: list[Atom]


class Show(BaseModel):
    title: str
    episodes: list[Episode]


# --------------------------------------------------------------------------- bible


class AliasSpan(BaseModel):
    """A label is only valid for an episode range: the same label can name different people."""

    label: str
    first_ep: int
    last_ep: int


class Character(BaseModel):
    canonical: str
    aliases: list[AliasSpan]
    role: str
    visual_descriptor: str  # how a stranger sees them on screen, no names


class Reveal(BaseModel):
    id: str  # "R01"
    description: str
    episode: int


class Arc(BaseModel):
    name: str
    first_ep: int
    last_ep: int
    summary: str


class ContinuityConflict(BaseModel):
    entity: str
    variants: list[dict]  # {"spelling": str, "episodes": [int]}
    note: str


class ShowBible(BaseModel):
    premise: str  # 2 sentences
    characters: list[Character]
    arcs: list[Arc]
    key_reveals: list[Reveal]
    continuity_conflicts: list[ContinuityConflict]


# --------------------------------------------------------------------------- lexicon + EKG


class LexiconEntry(BaseModel):
    arousal: float  # 0..1
    valence: float  # -1..1
    is_emotion: bool  # False for delivery-only tags (o.s., v.o., off-screen)


class EKG(BaseModel):
    episode: int
    hz: int = 1
    values: list[float]  # normalised 0..1, one per second
    hotspots: list[dict]  # {"t": float, "value": float, "atom_ids": [str]}


# --------------------------------------------------------------------------- mining


class SegmentRef(BaseModel):
    """What the LLM returns for a segment: atom IDs only, never timestamps."""

    from_id: str
    to_id: str


class RawCandidate(BaseModel):
    """LLM output, validated against this schema before code touches it."""

    structure: Literal["straight", "flash_forward", "arc_bridge"]
    segments: list[SegmentRef]  # in PLAYBACK order
    hook_id: str
    cliffhanger_id: str
    engine: Engine
    hook_text: str  # must match the hook atom's text (fuzzy >= 0.9)
    cliffhanger_text: str
    why: str  # one line
    miner_hook: int  # 1..5 self-rating (tie-break only)
    miner_cliff: int
    quiet_power: bool = False


# --------------------------------------------------------------------------- promos


class Segment(BaseModel):
    episode: int
    start: float
    end: float
    start_tc: str
    end_tc: str
    atom_ids: list[str]


class Scores(BaseModel):
    hook: float  # 0..1 from cold viewer stage A
    cliff: float  # 0..1 from cold viewer stage B "need next"
    viewer: float  # 0..1 comprehension (grader)
    tension: float  # 0..1 EKG peak inside clip
    quality: float  # 0..1 hook latency + clean cuts
    policy_penalty: float
    spoiler_penalty: float
    paywall_penalty: float
    referent_penalty: float
    total: float


class Promo(BaseModel):
    id: str  # "P007"
    structure: str
    segments: list[Segment]
    duration: float
    hook_text: str
    hook_speaker: str | None
    hook_at: float  # seconds from promo start
    cliffhanger_text: str
    engine: str
    why: str
    landing_episode: int
    free_runway: int  # episodes before paywall from landing
    policy: Literal["paid_safe", "organic_only", "review"]
    policy_reasons: list[str]
    reveals_disclosed: list[str]
    ungrounded_refs: list[str]
    viewer: dict  # stage A + B answers + grader result
    scores: Scores
    validation: dict  # repairs applied, warnings
    continuity_warnings: list[str]


class Slate(BaseModel):
    hero: str
    challengers: list[str]
    bench: list[str]
    rationale: dict[str, str]  # promo id -> one line on why this slot


class RunReport(BaseModel):
    run_id: str
    show_title: str
    started_at: str
    finished_at: str
    models: dict[str, str]
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    stage_seconds: dict[str, float]
    counts: dict[str, int]  # raw_candidates, repaired, rejected_by_reason..., valid, slate
    constraint_pass: dict[str, float]
    notes: list[str]
