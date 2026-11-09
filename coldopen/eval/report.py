"""Builds eval_report.json / eval_report.md from a run, the gold file, ratings and ablations."""

from __future__ import annotations

import json
from pathlib import Path

from coldopen.eval.metrics import (
    comprehension, failure_gallery, load_gold, match_gold, precision_at_10, ranked_promos, rejection_rates,
    summarise_matches,
)

VARIANT_ORDER = ["rules", "llm_only", "no_viewer", "full"]
VIOLATION_LABEL = {
    "timestamps_off_atom_boundary": "timestamps not on a line boundary (±0.25 s)",
    "midline_cuts": "cuts through a spoken line",
    "durations_out_of_range": "durations outside 20–60 s",
    "hook_latency_over_3s": "hook later than 3 s",
    "quotes_not_in_script": "quoted text not in the script",
}


def build_report(run: Path, gold_path: Path, ablate_dir: Path | None = None) -> dict:
    gold = load_gold(gold_path)
    promos = json.loads((run / "08_promos.json").read_text())
    slate = json.loads((run / "08_slate.json").read_text())
    report = json.loads((run / "run_report.json").read_text())
    vlog = [json.loads(x) for x in (run / "05_validation_log.jsonl").read_text().splitlines() if x.strip()]
    ranked = ranked_promos(promos, slate)
    matches = match_gold(gold, ranked)
    eval_dir = run / "eval"
    ratings = json.loads((eval_dir / "ratings.json").read_text()) if (eval_dir / "ratings.json").exists() else {}
    out = {
        "run_id": report["run_id"],
        "show_title": report["show_title"],
        "provider": report["models"].get("provider"),
        "constraint_pass": report["constraint_pass"],
        "validation": rejection_rates(vlog),
        "gold": summarise_matches(matches),
        "gold_matches": matches,
        "precision_at_10": precision_at_10(ratings.get("top10", {})),
        "ratings": ratings,
        "viewer_agreement": _agreement(ratings.get("audit", {})),
        "comprehension": comprehension(promos),
        "failure_gallery": failure_gallery(run, matches),
        "ablations": {},
        "cost": {"llm_calls": report["llm_calls"], "tokens_in": report["tokens_in"], "tokens_out": report["tokens_out"],
                 "cost_usd": report["cost_usd"], "seconds": round(sum(report["stage_seconds"].values()), 1)},
    }
    if ablate_dir and (ablate_dir / "ablation.json").exists():
        ab = json.loads((ablate_dir / "ablation.json").read_text())
        for name, v in ab["variants"].items():
            m = summarise_matches(match_gold(gold, v["promos"]))
            out["ablations"][name] = {"gold": m, "violations": v["violations"], "promos": len(v["promos"]),
                                      **({"top10_windows": v["top10_windows"]} if "top10_windows" in v else {})}
    return out


def _agreement(audit: dict) -> dict:
    if not audit:
        return {"n": 0}
    vals = list(audit.values())
    score = {"y": 1.0, "p": 0.5, "n": 0.0}
    return {"n": len(vals), "agreement": round(sum(score.get(v, 0) for v in vals) / len(vals), 3)}


def to_markdown(r: dict) -> str:
    g = r["gold"]
    lines = [
        f"# Evaluation: {r['show_title']}", "",
        f"Run `{r['run_id']}` (provider: {r['provider']}).", "",
        "## Results", "",
        "| Metric | Value |", "|---|---:|",
        f"| Gold moments | {g['gold']} |",
        f"| Hit rate (top 20, IoU ≥ 0.5) | {_pct(g['hit_rate'])} |",
        f"| Median start error | {_s(g['median_start_error_s'])} |",
        f"| Median end error | {_s(g['median_end_error_s'])} |",
        f"| Precision@10 (human, rating ≥ 4) | {_pct(r['precision_at_10'])} |",
        f"| Cold Viewer agreement (human audit) | {_pct(r['viewer_agreement'].get('agreement'))} |",
        f"| Comprehension median | {r['comprehension'].get('median', '—')} |",
        f"| First-pass valid rate | {_pct(r['validation']['first_pass_valid_rate'])} |",
        "", "## Constraint pass on the final set", "",
        "| Constraint | Pass |", "|---|---:|",
        *[f"| {k.replace('_', ' ')} | {_pct(v)} |" for k, v in r["constraint_pass"].items()],
        "", "## Validation", "",
        f"Rejected {r['validation']['rejected']}, repaired {r['validation']['repaired']}, "
        f"duplicates {r['validation']['duplicates']}.",
        "", "Rejected by reason: " + (", ".join(f"{k} {v}" for k, v in r["validation"]["rejected_by_reason"].items()) or "none"),
    ]
    if r["ablations"]:
        names = [n for n in VARIANT_ORDER if n in r["ablations"]]
        lines += ["", "## Ablations", "", "| | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
        lines.append("| Hit rate | " + " | ".join(_pct(r["ablations"][n]["gold"]["hit_rate"]) for n in names) + " |")
        lines.append("| Median start error | " + " | ".join(_s(r["ablations"][n]["gold"]["median_start_error_s"]) for n in names) + " |")
        for k, label in VIOLATION_LABEL.items():
            lines.append(f"| {label} | " + " | ".join(str(r["ablations"][n]["violations"].get(k, 0)) for n in names) + " |")
    lines += ["", "## Failure gallery", "", "| Category | Where | What went wrong |", "|---|---|---|"]
    lines += [f"| {f['category']} | {f['where']} | {f['what']} |" for f in r["failure_gallery"]]
    return "\n".join(lines) + "\n"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _s(x) -> str:
    return "—" if x is None else f"{x:.1f} s"


def write_report(run: Path, gold: Path, ablate_dir: Path | None = None) -> Path:
    r = build_report(run, gold, ablate_dir)
    d = run / "eval"
    d.mkdir(exist_ok=True)
    (d / "eval_report.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
    (d / "eval_report.md").write_text(to_markdown(r))
    return d
