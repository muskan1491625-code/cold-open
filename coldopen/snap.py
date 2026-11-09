"""Stage 5: turn LLM atom-ID proposals into valid, editor-ready segments.

This is where "code owns facts" is enforced. The LLM said *which* lines; this
module decides *exactly where* to cut, and refuses anything that breaks a
hard rule:

1. IDs exist, segments are well-formed and in playback order, hook in the first
   segment, cliffhanger in the last, quoted text matches the atoms (ratio >= 90).
2. IDs become times. 3. Start snaps to <= 3 s before the hook (camera cut if any).
4. End snaps to just after the cliffhanger, never leaking the next line.
5. No dialogue line is ever cut in half. 6. Total 20-60 s. 7. Hook latency <= 3 s.
8. Near-duplicates are removed. 9. Continuity drift across episodes is flagged.

A failing candidate gets one LLM repair attempt; then it is rejected with a
reason code. Every decision is logged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, ValidationError
from rapidfuzz import fuzz

from coldopen.models import Atom, Episode, RawCandidate, Segment, Show, ShowBible
from coldopen.parse import camera_boundaries
from coldopen.render import compact_atom
from coldopen.timecode import fmt_tc

HOOK_MAX = 3.0
DUR_MIN, DUR_MAX = 20.0, 60.0
TEXT_MIN_RATIO = 90.0
END_PAD = 0.3
SHOT_EXTEND = 1.5
EPS = 1e-6


# --------------------------------------------------------------------------- index


@dataclass
class ShowIndex:
    show: Show
    by_id: dict[str, Atom] = field(default_factory=dict)
    pos: dict[str, int] = field(default_factory=dict)
    eps: dict[int, Episode] = field(default_factory=dict)
    cams: dict[int, list[float]] = field(default_factory=dict)

    @classmethod
    def build(cls, show: Show) -> "ShowIndex":
        ix = cls(show=show)
        for ep in show.episodes:
            ix.eps[ep.number] = ep
            ix.cams[ep.number] = camera_boundaries(ep)
            for i, a in enumerate(ep.atoms):
                ix.by_id[a.id] = a
                ix.pos[a.id] = i
        return ix

    def span(self, from_id: str, to_id: str) -> list[Atom]:
        ep = self.eps[self.by_id[from_id].episode]
        return ep.atoms[self.pos[from_id] : self.pos[to_id] + 1]


# --------------------------------------------------------------------------- result types


class ValidCandidate(BaseModel):
    """A candidate that passed every hard constraint (internal; becomes a Promo later)."""

    cid: str
    promo_id: str = ""
    source: str  # "episode" or "arc_bridge"
    raw: RawCandidate
    segments: list[Segment]
    duration: float
    hook_at: float
    on_camera_cuts: bool
    repairs: list[str] = []
    warnings: list[str] = []
    continuity_warnings: list[str] = []


class RepairReply(BaseModel):
    model_config = ConfigDict(extra="allow")
    drop: bool = False
    reason: str = ""


Issue = tuple[str, str]  # (reason code, human message)


# --------------------------------------------------------------------------- helpers


def norm_text(t: str) -> str:
    t = t.strip().strip('"“”\'‘’').strip()
    return re.sub(r"\s+", " ", t).lower()


def text_matches(claim: str, atom: Atom) -> bool:
    return fuzz.ratio(norm_text(claim), norm_text(atom.text)) >= TEXT_MIN_RATIO


def interval_iou(a: list[tuple[int, float, float]], b: list[tuple[int, float, float]]) -> float:
    """Temporal IoU of two multi-segment promos on the shared (episode, time) axis."""

    def merge(iv):
        out: dict[int, list[list[float]]] = {}
        for ep, s, e in sorted(iv):
            lst = out.setdefault(ep, [])
            if lst and s <= lst[-1][1]:
                lst[-1][1] = max(lst[-1][1], e)
            else:
                lst.append([s, e])
        return out

    ma, mb = merge(a), merge(b)
    inter = 0.0
    for ep in set(ma) & set(mb):
        for s1, e1 in ma[ep]:
            for s2, e2 in mb[ep]:
                inter += max(0.0, min(e1, e2) - max(s1, s2))
    total = sum(e - s for v in ma.values() for s, e in v) + sum(e - s for v in mb.values() for s, e in v)
    union = total - inter
    return inter / union if union > 0 else 0.0


def seg_intervals(segments: list[Segment]) -> list[tuple[int, float, float]]:
    return [(s.episode, s.start, s.end) for s in segments]


# --------------------------------------------------------------------------- step 1: ID checks


def id_checks(raw: RawCandidate, ix: ShowIndex) -> list[Issue]:
    issues: list[Issue] = []
    ids = [x for s in raw.segments for x in (s.from_id, s.to_id)] + [raw.hook_id, raw.cliffhanger_id]
    unknown = sorted({i for i in ids if i not in ix.by_id})
    if unknown:
        return [("unknown_id", f"These atom IDs do not exist: {', '.join(unknown)}")]
    if not raw.segments:
        return [("structure", "No segments given")]

    n = len(raw.segments)
    if raw.structure == "straight" and n != 1:
        issues.append(("structure", f"A straight promo has exactly 1 segment, got {n}"))
    if raw.structure in ("flash_forward", "arc_bridge") and not 2 <= n <= 3:
        issues.append(("structure", f"A {raw.structure} promo has 2-3 segments, got {n}"))

    for k, s in enumerate(raw.segments, 1):
        a, b = ix.by_id[s.from_id], ix.by_id[s.to_id]
        if a.episode != b.episode:
            issues.append(("cross_episode_segment", f"Segment {k} starts in E{a.episode} but ends in E{b.episode}"))
        elif ix.pos[s.from_id] > ix.pos[s.to_id]:
            issues.append(("reversed_segment", f"Segment {k}: from_id {s.from_id} comes after to_id {s.to_id}"))
    if issues:
        return issues

    keys = [(ix.by_id[s.from_id].episode, ix.pos[s.from_id], ix.pos[s.to_id]) for s in raw.segments]
    eps = [k[0] for k in keys]
    if raw.structure == "arc_bridge":
        if len(set(eps)) < 2:
            issues.append(("structure", "An arc_bridge must use at least two different episodes"))
        if keys != sorted(keys):
            issues.append(("segment_order", "arc_bridge segments must run in story order"))
    if raw.structure == "flash_forward" and n >= 2:
        rest = keys[1:]
        if rest != sorted(rest):
            issues.append(("segment_order", "Segments after the flash-forward must run in story order"))
        e0, e1 = eps[0], eps[1]
        if e0 not in (e1, e1 + 1):
            issues.append(("structure", f"Flash-forward must come from E{e1} or E{e1 + 1}, got E{e0}"))
        elif (e0, keys[0][1]) <= (e1, keys[1][1]):
            issues.append(("segment_order", "The flash-forward segment must come from LATER in the story"))
    for i in range(n):
        for j in range(i + 1, n):
            if eps[i] == eps[j] and not (keys[i][2] < keys[j][1] or keys[j][2] < keys[i][1]):
                issues.append(("overlap_segments", f"Segments {i + 1} and {j + 1} overlap"))

    first = raw.segments[0]
    if not (ix.by_id[raw.hook_id].episode == ix.by_id[first.from_id].episode
            and ix.pos[first.from_id] <= ix.pos[raw.hook_id] <= ix.pos[first.to_id]):
        issues.append(("hook_not_in_first_segment", f"hook_id {raw.hook_id} is not inside segment 1"))
    last = raw.segments[-1]
    if not (ix.by_id[raw.cliffhanger_id].episode == ix.by_id[last.from_id].episode
            and ix.pos[last.from_id] <= ix.pos[raw.cliffhanger_id] <= ix.pos[last.to_id]):
        issues.append(("cliff_not_in_last_segment", f"cliffhanger_id {raw.cliffhanger_id} is not inside the last segment"))

    if not text_matches(raw.hook_text, ix.by_id[raw.hook_id]):
        issues.append(("text_mismatch", f"hook_text does not match atom {raw.hook_id}; copy its text exactly"))
    if not text_matches(raw.cliffhanger_text, ix.by_id[raw.cliffhanger_id]):
        issues.append(("text_mismatch", f"cliffhanger_text does not match atom {raw.cliffhanger_id}; copy its text exactly"))
    return issues


# --------------------------------------------------------------------------- steps 2-5: times and snapping


def _dialogue(ep: Episode) -> list[Atom]:
    return [a for a in ep.atoms if a.kind == "dialogue"]


def snap_start(hook: Atom, start: float, cams: list[float]) -> tuple[float, str | None]:
    """Promo must open no more than 3 s before the hook; prefer a camera cut."""
    if hook.start - start <= HOOK_MAX + EPS:
        return start, None
    window = [b for b in cams if hook.start - HOOK_MAX - EPS <= b <= hook.start + EPS]
    new = max(window) if window else max(0.0, hook.start - 0.2)
    how = "camera cut" if window else "hook - 0.2 s"
    return new, f"start moved {fmt_tc(start)} -> {fmt_tc(new)} ({how}) to put the hook within 3 s"


def snap_end(cliff: Atom, ep: Episode) -> tuple[float, str]:
    """End just after the cliffhanger; extend to the shot's end only if no new line starts first."""
    end = cliff.end + END_PAD
    later = [d.start for d in _dialogue(ep) if d.id != cliff.id and d.start >= cliff.end - EPS]
    nxt = min(later) if later else None
    if nxt is not None:
        end = min(end, nxt)
    shot = next(
        (c for c in ep.atoms if c.kind == "camera" and c.start - EPS <= cliff.end <= c.end + EPS and c.end > cliff.end),
        None,
    )
    note = "end at cliffhanger + 0.3 s"
    # A shot change inside the 0.3 s pad: end on it, or the promo ends on a few
    # frames of the next shot (a visible flash).
    cuts = [c.start for c in ep.atoms if c.kind == "camera" and cliff.end - EPS <= c.start < end - EPS]
    if cuts:
        return round(min(cuts), 3), "end on the shot change right after the cliffhanger"
    if shot and shot.end - cliff.end <= SHOT_EXTEND + EPS and (nxt is None or nxt >= shot.end - EPS):
        end = max(end, shot.end)
        note = "end extended to the cliffhanger's shot change"
    return min(end, ep.duration), note


def fix_midline(
    ep: Episode, s: float, e: float, is_last: bool, protect_start: float | None, cliff_end: float | None = None
) -> tuple[float, float, list[str]]:
    """No dialogue atom may be partly inside a segment.

    At the start: exclude the straddling line (move start to its end), unless that
    would pass the hook; then include it. At the end: include it for middle
    segments; for the last segment include it only if it starts before the
    cliffhanger ends (overlapping speech), otherwise exclude it (it is the answer).
    """
    notes = []
    for d in _dialogue(ep):
        if d.start < s - EPS and d.end > s + EPS:
            if protect_start is None or d.end <= protect_start + EPS:
                notes.append(f"start {fmt_tc(s)} -> {fmt_tc(d.end)} to avoid cutting line {d.id}")
                s = d.end
            else:
                notes.append(f"start {fmt_tc(s)} -> {fmt_tc(d.start)} to include line {d.id}")
                s = d.start
    for d in _dialogue(ep):
        if d.start < e - EPS and d.end > e + EPS and d.start >= s - EPS:
            if is_last and not (cliff_end is not None and d.start < cliff_end - EPS):
                notes.append(f"end {fmt_tc(e)} -> {fmt_tc(d.start)} to drop half-cut line {d.id}")
                e = d.start
            else:
                notes.append(f"end {fmt_tc(e)} -> {fmt_tc(d.end)} to finish line {d.id}")
                e = d.end
    return s, e, notes


def materialise(raw: RawCandidate, ix: ShowIndex) -> tuple[list[Segment], float, list[str], list[str]]:
    """Steps 2-5. Returns (segments, hook_at, repairs, warnings). Assumes id_checks passed."""
    repairs: list[str] = []
    warnings: list[str] = []
    hook, cliff = ix.by_id[raw.hook_id], ix.by_id[raw.cliffhanger_id]
    out: list[Segment] = []
    n = len(raw.segments)
    for k, ref in enumerate(raw.segments):
        atoms = ix.span(ref.from_id, ref.to_id)
        ep = ix.eps[atoms[0].episode]
        s = atoms[0].start
        spoken = [a.end for a in atoms if a.kind != "camera"]
        e = max(spoken) if spoken else atoms[-1].end
        if k == 0:
            s, note = snap_start(hook, s, ix.cams[ep.number])
            if note:
                repairs.append(note)
        if k == n - 1:
            e, note = snap_end(cliff, ep)
            repairs.append(note)
        s, e, notes = fix_midline(
            ep, s, e, is_last=(k == n - 1), protect_start=hook.start if k == 0 else None,
            cliff_end=cliff.end if k == n - 1 else None,
        )
        repairs.extend(notes)
        s, e = round(max(0.0, s), 3), round(min(ep.duration, e), 3)
        ids = [a.id for a in ep.atoms if a.end > s + 0.05 and a.start < e - 0.05]
        out.append(Segment(episode=ep.number, start=s, end=e, start_tc=fmt_tc(s), end_tc=fmt_tc(e), atom_ids=ids))
    if raw.structure == "flash_forward":
        d0 = out[0].end - out[0].start
        if not 2.0 <= d0 <= 4.0:
            warnings.append(f"flash-forward segment is {d0:.1f} s (aim for 2-4 s)")
    hook_at = round(hook.start - out[0].start, 3)
    return out, hook_at, repairs, warnings


# --------------------------------------------------------------------------- constraint check (also used as an assertion)


def constraint_report(segments: list[Segment], hook_at: float, raw: RawCandidate, ix: ShowIndex) -> dict[str, bool]:
    """Independent re-check of every hard rule on final segments."""
    ok_ts = all(
        0 <= s.start < s.end <= ix.eps[s.episode].duration + EPS and s.episode in ix.eps for s in segments
    )
    dur = sum(s.end - s.start for s in segments)
    midline = not any(
        (d.start < s.start - EPS and d.end > s.start + EPS) or (d.start < s.end - EPS and d.end > s.end + EPS)
        for s in segments
        for d in _dialogue(ix.eps[s.episode])
    )
    hook = ix.by_id[raw.hook_id]
    cliff = ix.by_id[raw.cliffhanger_id]
    in_first = segments[0].episode == hook.episode and segments[0].start - EPS <= hook.start < segments[0].end
    in_last = segments[-1].episode == cliff.episode and cliff.end <= segments[-1].end + EPS
    return {
        "valid_timestamps": ok_ts,
        "duration": DUR_MIN - EPS <= dur <= DUR_MAX + EPS,
        "hook_within_3s": 0 <= hook_at <= HOOK_MAX + EPS and in_first,
        "no_midline_cuts": midline,
        "cliffhanger_included": in_last,
        "text_match": text_matches(raw.hook_text, hook) and text_matches(raw.cliffhanger_text, cliff),
    }


def time_issues(segments: list[Segment], hook_at: float, raw: RawCandidate, ix: ShowIndex) -> list[Issue]:
    rep = constraint_report(segments, hook_at, raw, ix)
    dur = sum(s.end - s.start for s in segments)
    issues: list[Issue] = []
    if not rep["duration"]:
        issues.append(("duration_out_of_range", f"Total length is {dur:.1f} s; it must be 20-60 s (aim 30-45)"))
    if not rep["hook_within_3s"]:
        issues.append(("hook_late", f"The hook starts {hook_at:.1f} s into the promo; it must be <= 3 s"))
    if not rep["no_midline_cuts"]:
        issues.append(("midline_cut", "A segment boundary cuts a dialogue line in half"))
    if not rep["valid_timestamps"]:
        issues.append(("invalid_timestamps", "A segment falls outside its episode"))
    if not rep["cliffhanger_included"]:
        issues.append(("cliff_cut", "The cliffhanger line is cut off"))
    return issues


# --------------------------------------------------------------------------- continuity (step 9)


def continuity_warnings(segments: list[Segment], bible: ShowBible | None, ix: ShowIndex) -> list[str]:
    if bible is None:
        return []
    eps = sorted({s.episode for s in segments})
    clip_text = " ".join(ix.by_id[i].text for s in segments for i in s.atom_ids)
    out = []
    for c in bible.continuity_conflicts:
        used: dict[str, list[int]] = {}
        for v in c.variants:
            sp = str(v.get("spelling", ""))
            ve = [int(x) for x in v.get("episodes", [])]
            hit = [e for e in eps if e in ve]
            if hit or (sp and re.search(rf"\b{re.escape(sp)}\b", clip_text)):
                used[sp] = hit
        if len(used) > 1:
            desc = ", ".join(f"{sp} ({'E' + '/E'.join(map(str, e)) if e else 'in clip'})" for sp, e in used.items())
            out.append(f"{c.entity}: spelled differently across this promo's episodes: {desc}")
    return out


# --------------------------------------------------------------------------- single candidate


def evaluate(raw: RawCandidate, ix: ShowIndex) -> tuple[list[Segment] | None, float, list[str], list[str], list[Issue]]:
    issues = id_checks(raw, ix)
    if issues:
        return None, 0.0, [], [], issues
    segs, hook_at, repairs, warnings = materialise(raw, ix)
    return segs, hook_at, repairs, warnings, time_issues(segs, hook_at, raw, ix)


def repair_variables(raw: RawCandidate, issues: list[Issue], ix: ShowIndex) -> dict:
    """Errors + the candidate + the atoms around it, for prompts/repair.md."""
    ids = [x for s in raw.segments for x in (s.from_id, s.to_id)] + [raw.hook_id, raw.cliffhanger_id]
    known = [i for i in ids if i in ix.by_id]
    lines: list[str] = []
    for epn in sorted({ix.by_id[i].episode for i in known}):
        ep = ix.eps[epn]
        ps = [ix.pos[i] for i in known if ix.by_id[i].episode == epn]
        lo, hi = max(0, min(ps) - 25), min(len(ep.atoms), max(ps) + 26)
        lines.append(f"EPISODE {epn} (duration {fmt_tc(ep.duration)}):")
        lines.extend(compact_atom(a) for a in ep.atoms[lo:hi])
    if not lines:
        lines.append("(none of the IDs exist; pick IDs from the episode list you were given)")
    return {
        "errors": "\n".join(f"- [{c}] {m}" for c, m in issues),
        "candidate": json.dumps(raw.model_dump(), ensure_ascii=False, indent=1),
        "atoms": "ATOMS:\n" + "\n".join(lines),
    }


async def validate_one(cid: str, source: str, raw: RawCandidate, ix: ShowIndex, client, bible) -> tuple[ValidCandidate | None, dict]:
    """Validate, repairing once via the LLM if needed. Returns (valid or None, log entry)."""
    segs, hook_at, repairs, warnings, issues = evaluate(raw, ix)
    log = {"candidate_id": cid, "source": source, "status": "valid", "reasons": [], "repairs": [], "llm_repair": False}
    if issues:
        first_issues = issues
        log["llm_repair"] = True
        try:
            reply = await client.acall("miner", "repair", repair_variables(raw, issues, ix), RepairReply)
        except Exception as e:  # noqa: BLE001 - a failed repair is a rejection, not a crash
            reply = RepairReply(drop=True, reason=f"repair call failed: {type(e).__name__}")
        if reply.drop:
            log.update(status="rejected", reasons=[c for c, _ in first_issues], drop_reason=reply.reason)
            return None, log
        try:
            fixed = RawCandidate.model_validate(reply.model_dump(exclude={"drop", "reason"}))
        except ValidationError:
            log.update(status="rejected", reasons=[c for c, _ in first_issues] + ["repair_schema"])
            return None, log
        segs, hook_at, repairs, warnings, issues = evaluate(fixed, ix)
        if issues:
            log.update(status="rejected", reasons=sorted({c for c, _ in issues}), first_reasons=[c for c, _ in first_issues])
            return None, log
        raw = fixed
        log.update(status="repaired", first_reasons=[c for c, _ in first_issues])
    assert segs is not None
    rep = constraint_report(segs, hook_at, raw, ix)
    assert all(rep.values()), f"{cid} passed validation but failed constraints {rep}"  # belt and braces
    cams_ok = all(
        any(abs(b - x) <= 0.05 for b in ix.cams[s.episode]) for s in segs for x in (s.start, s.end)
    )
    vc = ValidCandidate(
        cid=cid,
        source=source,
        raw=raw,
        segments=segs,
        duration=round(sum(s.end - s.start for s in segs), 3),
        hook_at=hook_at,
        on_camera_cuts=cams_ok,
        repairs=repairs,
        warnings=warnings,
        continuity_warnings=continuity_warnings(segs, bible, ix),
    )
    log["repairs"] = repairs
    return vc, log


def dedupe(cands: list[ValidCandidate], threshold: float = 0.5) -> tuple[list[ValidCandidate], list[dict]]:
    """Step 8: same-episode temporal IoU > 0.5 -> keep the higher miner_hook + miner_cliff."""
    order = sorted(
        cands, key=lambda c: (-(c.raw.miner_hook + c.raw.miner_cliff), c.hook_at, c.cid)
    )
    kept: list[ValidCandidate] = []
    logs = []
    for c in order:
        dup = next((k for k in kept if interval_iou(seg_intervals(c.segments), seg_intervals(k.segments)) > threshold), None)
        if dup:
            logs.append({"candidate_id": c.cid, "status": "duplicate", "reasons": ["duplicate"], "kept": dup.cid})
        else:
            kept.append(c)
    kept.sort(key=lambda c: c.cid)
    return kept, logs
