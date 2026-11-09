"""Evaluation metrics against hand-labelled gold promos, and the failure gallery.

Gold is written by a human (eval/gold.yaml) and is never generated here. What
each metric answers:
- constraint pass: are the outputs usable by an editor at all?
- hit rate @20: does the tool find the moments a human editor would pick?
- median start/end error: when it finds them, how close are the cuts?
- precision@10: of the top ten, how many would a human actually post? (blind rating)
- cold-viewer audit: does the model "stranger" understand clips the way a person does?
"""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

import yaml

from coldopen.timecode import fmt_tc, parse_tc

IOU_HIT = 0.5


def load_gold(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Gold labels must be written by hand (see README: Evaluation). "
            "Use `coldopen show E01` to find timecodes."
        )
    rows = yaml.safe_load(path.read_text()) or []
    out = []
    for r in rows:
        s, e = parse_tc(str(r["start"])), parse_tc(str(r["end"]))
        if e <= s:
            raise ValueError(f"Gold {r.get('id')}: end <= start")
        out.append({"id": r["id"], "episode": int(r["episode"]), "start": s, "end": e, "note": r.get("note", "")})
    return out


def iou_1d(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def ranked_promos(promos: dict, slate: dict | None) -> list[dict]:
    """Slate order first (hero, challengers, bench), then everything else by total."""
    order = [slate["hero"], *slate["challengers"], *slate["bench"]] if slate else []
    rest = sorted((p for k, p in promos.items() if k not in order), key=lambda p: -p["scores"]["total"])
    return [promos[k] for k in order if k in promos] + rest


def match_gold(gold: list[dict], promos: list[dict], top_k: int = 20) -> list[dict]:
    """Best IoU per gold item among the top-k promos (any segment in the same episode)."""
    top = promos[:top_k]
    out = []
    for g in gold:
        best = {"iou": 0.0, "promo": None, "start_err": None, "end_err": None}
        for p in top:
            for s in p["segments"]:
                if s["episode"] != g["episode"]:
                    continue
                v = iou_1d((g["start"], g["end"]), (s["start"], s["end"]))
                if v > best["iou"]:
                    best = {"iou": v, "promo": p["id"], "start_err": abs(s["start"] - g["start"]),
                            "end_err": abs(s["end"] - g["end"])}
        out.append({"gold": g["id"], "episode": g["episode"], "note": g["note"], **best, "hit": best["iou"] >= IOU_HIT})
    return out


def summarise_matches(matches: list[dict]) -> dict:
    hits = [m for m in matches if m["hit"]]
    med = lambda xs: round(statistics.median(xs), 2) if xs else None  # noqa: E731
    return {
        "gold": len(matches),
        "hits": len(hits),
        "hit_rate": round(len(hits) / len(matches), 3) if matches else None,
        "median_start_error_s": med([m["start_err"] for m in hits]),
        "median_end_error_s": med([m["end_err"] for m in hits]),
        "mean_best_iou": round(sum(m["iou"] for m in matches) / len(matches), 3) if matches else None,
    }


def rejection_rates(vlog: list[dict]) -> dict:
    total = len([x for x in vlog if x["status"] != "duplicate"]) or 1
    reasons: dict[str, int] = {}
    for x in vlog:
        if x["status"] == "rejected":
            for r in x.get("reasons", []):
                reasons[r] = reasons.get(r, 0) + 1
    first: dict[str, int] = {}
    for x in vlog:
        for r in x.get("first_reasons", []) if x["status"] == "repaired" else []:
            first[r] = first.get(r, 0) + 1
    return {
        "candidates_checked": total,
        "rejected": sum(1 for x in vlog if x["status"] == "rejected"),
        "repaired": sum(1 for x in vlog if x["status"] == "repaired"),
        "duplicates": sum(1 for x in vlog if x["status"] == "duplicate"),
        "rejected_by_reason": reasons,
        "repaired_from_reason": first,
        "first_pass_valid_rate": round(sum(1 for x in vlog if x["status"] == "valid") / total, 3),
    }


def comprehension(promos: dict) -> dict:
    vals = [p["scores"]["viewer"] for p in promos.values() if p.get("viewer")]
    if not vals:
        return {"n": 0}
    buckets = {f"{b:.2f}": sum(1 for v in vals if abs(v - b) < 0.0626) for b in [x / 8 for x in range(9)]}
    return {"n": len(vals), "median": round(statistics.median(vals), 3), "histogram": buckets}


# --------------------------------------------------------------------------- failure gallery


def failure_gallery(run: Path, matches: list[dict] | None = None, limit: int = 12) -> list[dict]:
    """Real failure cases from a run, each with a category and a pointer to the Cut room."""
    promos = json.loads((run / "08_promos.json").read_text()) if (run / "08_promos.json").exists() else {}
    vlog = [json.loads(x) for x in (run / "05_validation_log.jsonl").read_text().splitlines() if x.strip()] \
        if (run / "05_validation_log.jsonl").exists() else []
    raw = {}
    if (run / "04_candidates_raw.jsonl").exists():
        for line in (run / "04_candidates_raw.jsonl").read_text().splitlines():
            r = json.loads(line)
            raw[r["cid"]] = r
    out: list[dict] = []

    def segs(p):
        return "; ".join(f"E{s['episode']} {s['start_tc']}–{s['end_tc']}" for s in p["segments"])

    for p in sorted(promos.values(), key=lambda p: p["scores"]["viewer"]):
        g = p.get("viewer", {}).get("grader", {})
        if p.get("viewer") and p["scores"]["viewer"] <= 0.5:
            out.append({"category": "stranger confused", "promo_id": p["id"], "where": segs(p),
                        "what": f"Cold Viewer comprehension {p['scores']['viewer']:.2f}. {g.get('notes', '')}".strip(),
                        "episode": p["segments"][0]["episode"]})
        if len([x for x in out if x["category"] == "stranger confused"]) >= 3:
            break
    for p in promos.values():
        if p["ungrounded_refs"]:
            out.append({"category": "unexplained name", "promo_id": p["id"], "where": segs(p),
                        "what": f"Names a stranger cannot place: {', '.join(p['ungrounded_refs'])}",
                        "episode": p["segments"][0]["episode"]})
            break
    for p in promos.values():
        if p["continuity_warnings"]:
            out.append({"category": "continuity drift", "promo_id": p["id"], "where": segs(p),
                        "what": p["continuity_warnings"][0], "episode": p["segments"][0]["episode"]})
            break
    for p in promos.values():
        if p["policy"] == "organic_only":
            out.append({"category": "ad policy", "promo_id": p["id"], "where": segs(p),
                        "what": f"Organic only: {', '.join(p['policy_reasons']) or 'high risk'}",
                        "episode": p["segments"][0]["episode"]})
            break
    for x in vlog:
        if x["status"] == "rejected":
            r = raw.get(x["candidate_id"], {})
            out.append({"category": "rejected by validator", "promo_id": None, "candidate_id": x["candidate_id"],
                        "where": f"E{r.get('episode')}" if r.get("episode") else "arc bridge",
                        "what": f"Rejected after repair: {', '.join(x.get('reasons', []))}", "episode": r.get("episode")})
        if len([o for o in out if o["category"] == "rejected by validator"]) >= 3:
            break
    for m in matches or []:
        if not m["hit"]:
            out.append({"category": "missed gold moment", "promo_id": m["promo"], "where": f"E{m['episode']}",
                        "what": f"Gold {m['gold']} ({m['note']}) best IoU {m['iou']:.2f}", "episode": m["episode"]})
    for o in out:
        o["category"] = o["category"][:1].upper() + o["category"][1:]
    return out[:limit]


# --------------------------------------------------------------------------- human rating


def rating_sample(promos: dict, slate: dict | None, seed: int = 7) -> tuple[list[dict], list[dict]]:
    """Top 10 (for precision@10) and 10 random promos (for the Cold Viewer audit)."""
    ranked = ranked_promos(promos, slate)
    rng = random.Random(seed)
    pool = [p for p in promos.values() if p.get("viewer")]
    return ranked[:10], rng.sample(pool, min(10, len(pool)))


def precision_at_10(ratings: dict[str, int]) -> float | None:
    if not ratings:
        return None
    vals = list(ratings.values())[:10]
    return round(sum(1 for v in vals if v >= 4) / len(vals), 3)


def fmt_range(s: float, e: float) -> str:
    return f"{fmt_tc(s)}–{fmt_tc(e)}"
