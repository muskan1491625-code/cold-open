"""Stage 8: score every valid promo and choose a diverse launch slate.

Scores are a transparent weighted sum (weights in config.yaml), so any rank can
be explained number by number. Diversity uses MMR: after the hero, each pick
trades a little score for being different (another story engine, other
episodes, no overlapping footage), because a slate of five versions of the
same slap teaches the ad platform nothing.
"""

from __future__ import annotations

import numpy as np

from coldopen.config import Config
from coldopen.models import EKG, Promo, Scores, Slate
from coldopen.snap import ShowIndex, ValidCandidate, interval_iou, seg_intervals

ENGINE_LABEL = {
    "humiliation_reversal": "humiliation-reversal",
    "dangerous_protector": "dangerous-protector",
    "hidden_identity": "hidden-identity",
    "sacrifice_ultimatum": "sacrifice",
    "framed_condemned": "framed-and-condemned",
    "family_betrayal": "family-betrayal",
    "forbidden_scandal": "forbidden-scandal",
    "other": "other",
}
POLICY_LABEL = {"paid_safe": "paid-safe", "review": "needs policy review", "organic_only": "organic only"}


def tension_of(vc: ValidCandidate, ekgs: dict[int, EKG]) -> float:
    vals: list[float] = []
    for s in vc.segments:
        e = ekgs[s.episode].values
        lo, hi = int(np.floor(s.start)), max(int(np.floor(s.start)) + 1, int(np.ceil(s.end)))
        vals.extend(e[lo:hi])
    return float(np.percentile(vals, 90)) if vals else 0.0


def quality_of(vc: ValidCandidate) -> float:
    return ((1 - min(vc.hook_at, 3.0) / 3.0) + (1.0 if vc.on_camera_cuts else 0.7)) / 2


def build_promo(
    vc: ValidCandidate, view: dict | None, risk: dict, ekgs: dict[int, EKG], cfg: Config, ix: ShowIndex,
    use_viewer: bool = True,
) -> Promo:
    w, pen = cfg.weights, cfg.penalties
    if use_viewer and view:
        hook, cliff, viewer = view["scores"]["hook"], view["scores"]["cliff"], view["scores"]["viewer"]
        refs = view["ungrounded_refs"]
    else:  # ablation `no_viewer`: the miner grades its own homework
        hook, cliff, viewer = (vc.raw.miner_hook - 1) / 4, (vc.raw.miner_cliff - 1) / 4, 0.0
        refs = []
    tension, quality = tension_of(vc, ekgs), quality_of(vc)
    ref_pen = min(pen.referent_cap, pen.referent * len(refs))
    total = (
        w.hook * hook + w.cliff * cliff + w.viewer * viewer + w.tension * tension + w.quality * quality
        - risk["policy_penalty"] - risk["spoiler_penalty"] - risk["paywall_penalty"] - ref_pen
    )
    hook_atom = ix.by_id[vc.raw.hook_id]
    r4 = lambda x: round(float(x), 4)  # noqa: E731
    return Promo(
        id=vc.promo_id,
        structure=vc.raw.structure,
        segments=vc.segments,
        duration=vc.duration,
        hook_text=vc.raw.hook_text,
        hook_speaker=hook_atom.speaker if hook_atom.kind == "dialogue" else None,
        hook_at=vc.hook_at,
        cliffhanger_text=vc.raw.cliffhanger_text,
        engine=vc.raw.engine,
        why=vc.raw.why,
        landing_episode=risk["landing_episode"],
        free_runway=risk["free_runway"],
        policy=risk["policy"],
        policy_reasons=risk["policy_reasons"],
        reveals_disclosed=risk["reveals_disclosed"],
        ungrounded_refs=refs,
        viewer=view or {},
        scores=Scores(
            hook=r4(hook), cliff=r4(cliff), viewer=r4(viewer), tension=r4(tension), quality=r4(quality),
            policy_penalty=r4(risk["policy_penalty"]), spoiler_penalty=r4(risk["spoiler_penalty"]),
            paywall_penalty=r4(risk["paywall_penalty"]), referent_penalty=r4(ref_pen), total=r4(total),
        ),
        validation={
            "candidate_id": vc.cid,
            "source": vc.source,
            "repairs": vc.repairs,
            "warnings": vc.warnings,
            "on_camera_cuts": vc.on_camera_cuts,
            "quiet_power": vc.raw.quiet_power,
            "miner_hook": vc.raw.miner_hook,
            "miner_cliff": vc.raw.miner_cliff,
            "hook_id": vc.raw.hook_id,
            "cliffhanger_id": vc.raw.cliffhanger_id,
            "evidence_ids": risk.get("evidence_ids", []),
            "spoilers": risk.get("spoilers", []),
        },
        continuity_warnings=vc.continuity_warnings,
    )


def similarity(a: Promo, b: Promo) -> float:
    same_engine = 0.5 * (a.engine == b.engine)
    shares_ep = 0.3 * bool({s.episode for s in a.segments} & {s.episode for s in b.segments})
    iou = interval_iou(seg_intervals(a.segments), seg_intervals(b.segments))
    return min(1.0, same_engine + shares_ep + 1.0 * iou)


def _eps(p: Promo) -> str:
    eps = sorted({s.episode for s in p.segments})
    return f"E{eps[0]}" if len(eps) == 1 else f"E{eps[0]}–{eps[-1]}" if eps == list(range(eps[0], eps[-1] + 1)) else "E" + " + E".join(map(str, eps))


def _strength(p: Promo, pool: list[Promo]) -> str:
    """The component where this promo stands out most, counting ties fairly.

    Rank = (promos strictly better + half the ties) / pool size, so a score shared
    by half the pool is never described as "the best".
    """
    names = {"hook": "hook", "cliff": "cliffhanger", "viewer": "stranger comprehension", "tension": "tension"}
    n = max(1, len(pool))
    best, best_rank = "hook", 2.0
    for k in names:
        mine = getattr(p.scores, k)
        better = sum(1 for x in pool if getattr(x.scores, k) > mine + 1e-9)
        ties = sum(1 for x in pool if abs(getattr(x.scores, k) - mine) <= 1e-9) - 1
        rank = (better + ties / 2) / n
        if rank < best_rank:
            best, best_rank = k, rank
    better = sum(1 for x in pool if getattr(x.scores, best) > getattr(p.scores, best) + 1e-9)
    ties = sum(1 for x in pool if abs(getattr(x.scores, best) - getattr(p.scores, best)) <= 1e-9) - 1
    if better == 0 and ties == 0:
        return f"best {names[best]} score of all {n} promos"
    return f"top {max(1, round(best_rank * 100))}% for {names[best]}"


def select_slate(promos: list[Promo], cfg: Config) -> Slate:
    if not promos:
        raise ValueError("No valid promos to build a slate from")
    lam = cfg.mmr_lambda
    ranked = sorted(promos, key=lambda p: (-p.scores.total, p.id))
    picked = [ranked[0]]
    rest = ranked[1:]
    while rest and len(picked) < 1 + cfg.slate.challengers + cfg.slate.bench:
        best = max(rest, key=lambda c: (lam * c.scores.total - (1 - lam) * max(similarity(c, p) for p in picked), -int(c.id[1:])))
        picked.append(best)
        rest.remove(best)
    hero, ch, bench = picked[0], picked[1 : 1 + cfg.slate.challengers], picked[1 + cfg.slate.challengers :]

    rationale = {
        hero.id: (
            f"Hero: highest total score ({hero.scores.total:.2f}); {ENGINE_LABEL[hero.engine]} engine; "
            f"{_strength(hero, promos)}; lands on {_eps(hero)}; {POLICY_LABEL[hero.policy]}."
        )
    }
    seen_engines = {hero.engine}
    for i, p in enumerate(ch, 1):
        novelty = "new to the slate" if p.engine not in seen_engines else "repeats an engine with different footage"
        seen_engines.add(p.engine)
        rationale[p.id] = (
            f"Challenger {i}: {ENGINE_LABEL[p.engine]} engine, {novelty}; {_strength(p, promos)}; "
            f"{_eps(p)}; {POLICY_LABEL[p.policy]}."
        )
    for i, p in enumerate(bench, 1):
        rationale[p.id] = f"Bench {i}: total {p.scores.total:.2f}; {ENGINE_LABEL[p.engine]}; {_eps(p)}."
    return Slate(hero=hero.id, challengers=[p.id for p in ch], bench=[p.id for p in bench], rationale=rationale)
