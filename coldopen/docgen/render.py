"""Render the 2-page solution doc (PDF + editable Markdown) from real run data.

Every number comes from run_report.json, 08_slate.json, 08_promos.json and,
when present, eval/eval_report.json. Anything that must be written by the
author renders as a visible TODO(Kartik) box. The PDF must be exactly two
pages; if a section overflows, this says which one and stops.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from coldopen.config import load_config
from coldopen.export import ordered
from coldopen.models import Promo, Slate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FONTS = ROOT / "web" / "node_modules" / "@fontsource"
ENGINE = {
    "humiliation_reversal": "Humiliation reversal", "dangerous_protector": "Dangerous protector",
    "hidden_identity": "Hidden identity", "sacrifice_ultimatum": "Sacrifice ultimatum",
    "framed_condemned": "Framed, condemned", "family_betrayal": "Family betrayal",
    "forbidden_scandal": "Forbidden scandal", "other": "Other",
}
STRUCT = {"straight": "Straight", "flash_forward": "Flash-forward", "arc_bridge": "Arc bridge"}
TEAL, AMBER, RED, INK, FOG, RULE = "#1A736F", "#965800", "#C42C33", "#14212A", "#546570", "#D5DEE3"


def todo(text: str) -> Markup:
    return Markup(f'<div class="todo"><b>TODO(Kartik)</b> {escape(text)}</div>')


def todo_inline(value: str | None, label: str) -> Markup:
    if value:
        return Markup(escape(value))
    return Markup(f'<span style="background:#F2A33A;color:#14212A;padding:0 1mm;border-radius:2px">TODO(Kartik): {escape(label)}</span>')


def _font(path: str) -> str:
    p = FONTS / path
    return p.as_uri() if p.exists() else ""


def _clip(t: str, n: int) -> str:
    t = t.strip().strip('"“”')
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------- exhibits


def pipeline_svg() -> str:
    """Static diagram, colour-coded Code / LLM / Human, matching the app palette."""
    stages = [
        ("Parse", "code"), ("Bible", "llm"), ("Lexicon", "llm"), ("EKG", "code"), ("Mine", "llm"),
        ("Validate", "code"), ("Cold viewer", "llm"), ("Risk", "llm"), ("Slate", "code"), ("Editor", "human"),
    ]
    col = {"code": TEAL, "llm": AMBER, "human": RED}
    w, h, gap, per_row = 30, 11, 4.2, 5
    parts = []
    for i, (name, kind) in enumerate(stages):
        r, c = divmod(i, per_row)
        c = c if r == 0 else per_row - 1 - c  # snake: second row runs right to left
        x, y = 1 + c * (w + gap), 4 + r * 20
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="2" fill="#fff" stroke="{col[kind]}" stroke-width="0.6"/>')
        parts.append(f'<text x="{x + w / 2}" y="{y + 7.1}" text-anchor="middle" font-family="PlexCond" font-size="4.6" fill="{INK}">{name}</text>')
        if i < len(stages) - 1:
            nr, nc = divmod(i + 1, per_row)
            if nr == r:
                if r == 0:
                    parts.append(f'<path d="M{x + w + 0.4} {y + h / 2} h{gap - 1.2}" stroke="{FOG}" stroke-width="0.4" marker-end="url(#a)"/>')
                else:
                    parts.append(f'<path d="M{x - 0.4} {y + h / 2} h-{gap - 1.2}" stroke="{FOG}" stroke-width="0.4" marker-end="url(#a)"/>')
            else:
                parts.append(f'<path d="M{x + w / 2} {y + h + 0.4} v{20 - h - 1.6}" stroke="{FOG}" stroke-width="0.4" marker-end="url(#a)"/>')
    # repair loop on Validate
    vx = 1 + (per_row - 1) * (w + gap)  # Validate opens row 2 of the snake, so it sits on the right
    parts.append(f'<path d="M{vx + w} {24 + 3} c 4 0 4 6 0 6" fill="none" stroke="{AMBER}" stroke-width="0.4" marker-end="url(#a2)"/>')
    parts.append(f'<text x="{vx + w - 1}" y="{24 + h + 4.2}" text-anchor="end" font-family="PlexCond" font-size="3.6" fill="{AMBER}">one repair round</text>')
    legend = [("Code", TEAL), ("Model call", AMBER), ("Human", RED)]
    for k, (lab, c) in enumerate(legend):
        lx = 1 + k * 30
        parts.append(f'<rect x="{lx}" y="46" width="5" height="3" rx="0.6" fill="#fff" stroke="{c}" stroke-width="0.6"/>')
        parts.append(f'<text x="{lx + 7}" y="48.6" font-family="PlexCond" font-size="3.8" fill="{FOG}">{lab}</text>')
    return (
        '<svg viewBox="0 0 172 52" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Pipeline">'
        f'<defs><marker id="a" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0 L6 3 L0 6 z" fill="{FOG}"/></marker>'
        f'<marker id="a2" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0 L6 3 L0 6 z" fill="{AMBER}"/></marker></defs>'
        + "".join(parts) + "</svg>"
    )


def _heat(v: float) -> str:
    stops = [(0.0, (59, 166, 160)), (0.55, (242, 163, 58)), (1.0, (232, 73, 79))]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if v <= b:
            t = (v - a) / (b - a)
            c = [round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3)]
            return f"rgb({c[0]},{c[1]},{c[2]})"
    return "rgb(232,73,79)"


def map_svg(ekgs: list[dict], durations: dict[int, float], promos: dict[str, Promo], slate: Slate) -> str:
    """Mini Tension Map: one row per episode, slate brackets on top (vector, prints crisply)."""
    W, label = 86.0, 6.0
    n = len(ekgs)
    rh = max(0.8, min(3.0, 54.0 / max(1, n)))
    gap = rh * 0.25
    pw = W - label - 1
    out = []
    for i, e in enumerate(ekgs):
        y = i * (rh + gap)
        dur = durations.get(e["episode"], len(e["values"])) or 1
        cw = pw / dur
        # merge runs of similar colour to keep the SVG small
        vals = e["values"]
        t = 0
        while t < len(vals):
            q = round(vals[t] * 10)
            u = t
            while u + 1 < len(vals) and round(vals[u + 1] * 10) == q:
                u += 1
            v = q / 10
            out.append(f'<rect x="{label + t * cw:.2f}" y="{y:.2f}" width="{(u - t + 1) * cw + 0.05:.2f}" height="{rh:.2f}" fill="{_heat(v)}" fill-opacity="{0.25 + 0.75 * v ** 1.4:.2f}"/>')
            t = u + 1
        if i % 5 == 0 or n <= 20:
            out.append(f'<text x="{label - 0.8}" y="{y + rh * 0.85:.2f}" text-anchor="end" font-family="PlexCond" font-size="{min(2.2, rh * 1.4):.2f}" fill="{FOG}">E{e["episode"]}</text>')
    row = {e["episode"]: i for i, e in enumerate(ekgs)}
    for pid in [slate.hero, *slate.challengers]:
        p = promos[pid]
        for s in p.segments:
            if s.episode not in row:
                continue
            dur = durations.get(s.episode) or 1
            x = label + s.start / dur * pw
            w = max(0.8, (s.end - s.start) / dur * pw)
            y = row[s.episode] * (rh + gap) - 0.35
            hero = pid == slate.hero
            out.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{rh + 0.7:.2f}" rx="0.3" fill="none" '
                f'stroke="{AMBER if hero else INK}" stroke-width="{0.5 if hero else 0.3}"'
                + ("" if hero else ' stroke-dasharray="0.8 0.5"') + "/>"
            )
    H = n * (rh + gap)
    return f'<svg viewBox="0 -0.6 {W} {H + 1.2:.2f}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Tension map">{"".join(out)}</svg>'


# --------------------------------------------------------------------------- data


def gather(run: Path, eval_dir: Path | None, author: dict) -> dict:
    report = json.loads((run / "run_report.json").read_text())
    slate = Slate.model_validate_json((run / "08_slate.json").read_text())
    promos = {k: Promo(**v) for k, v in json.loads((run / "08_promos.json").read_text()).items()}
    ekgs = json.loads((run / "03_ekg.json").read_text())
    show = json.loads((run / "00_show.json").read_text())
    durations = {e["number"]: e["duration"] for e in show["episodes"]}
    cfg_path = run / "config_snapshot.yaml"
    cfg = load_config(cfg_path) if cfg_path.exists() else load_config(ROOT / "config.yaml")
    ev_path = (eval_dir or run / "eval") / "eval_report.json"
    ev = json.loads(ev_path.read_text()) if ev_path.exists() else None

    rows = []
    for k, p in enumerate(ordered(promos, slate)[: 1 + len(slate.challengers)], 1):
        rows.append({
            "rank": k, "structure": STRUCT.get(p.structure, p.structure), "segments": p.segments, "duration": p.duration,
            "hook": _clip(p.hook_text, 64), "cliff": _clip(p.cliffhanger_text, 56), "engine": ENGINE.get(p.engine, p.engine),
            "why": _clip(p.why, 96),
        })

    def pct(x):
        return "—" if x is None else f"{x * 100:.0f}%"

    if ev:
        g = ev["gold"]
        metrics = [
            {"v": pct(g["hit_rate"]), "l": f"of {g['gold']} hand-picked moments found in the top 20"},
            {"v": pct(ev.get("precision_at_10")), "l": "of the top 10 rated 4+ of 5 by me, blind to scores"},
            {"v": "—" if g["median_start_error_s"] is None else f"{g['median_start_error_s']:.1f} s", "l": "median start-cut error on matches"},
            {"v": pct(min(ev["constraint_pass"].values()) if ev["constraint_pass"] else None), "l": "of final promos pass every hard rule"},
        ]
    else:
        cp = report["constraint_pass"]
        metrics = [
            {"v": "TODO", "l": "hit rate vs gold (run coldopen eval)"},
            {"v": "TODO", "l": "precision@10 (coldopen eval --rate)"},
            {"v": "TODO", "l": "median cut error vs gold"},
            {"v": pct(min(cp.values()) if cp else None), "l": "of final promos pass every hard rule"},
        ]

    ablation_line, llm_only_line = None, "Checked by ablation: see page 2."
    ab = (ev or {}).get("ablations") or {}
    if "llm_only" in ab and "full" in ab:
        v = ab["llm_only"]["violations"]
        n = ab["llm_only"].get("promos", 0) or 1
        ablation_line = (
            f"Ablation: when the model wrote timestamps itself with no validators, {v.get('timestamps_off_atom_boundary', 0)} cut points "
            f"missed a line boundary, {v.get('midline_cuts', 0)} promos cut through a spoken line and {v.get('durations_out_of_range', 0)} "
            f"ran outside 20–60 s (of {n}); the full system produced none. Hit rate: rules only {pct(ab.get('rules', {}).get('gold', {}).get('hit_rate'))}, "
            f"no cold viewer {pct(ab.get('no_viewer', {}).get('gold', {}).get('hit_rate'))}, full {pct(ab['full']['gold']['hit_rate'])}."
        )
        llm_only_line = (
            f"In the ablation, a model writing its own timestamps cut through a spoken line in {v.get('midline_cuts', 0)} of {n} promos; "
            "naming line IDs makes that impossible by construction."
        )

    failures = (ev or {}).get("failure_gallery") or []
    if not failures:
        from coldopen.eval.metrics import failure_gallery

        failures = failure_gallery(run)
    tokens = report["tokens_in"] + report["tokens_out"]
    secs = sum(report["stage_seconds"].values())
    cost = report["cost_usd"]
    free = any("list-price" in n for n in report["notes"])
    cost_text = "cost unknown (prices.yaml not filled)" if cost is None else f"${cost:.2f}" + (" list-price equivalent (free tier)" if free else "")
    log = (ROOT / "docs" / "BUILD_LOG.md").read_text() if (ROOT / "docs" / "BUILD_LOG.md").exists() else ""
    titles = re.findall(r"^## [^·\n]+·[^·\n]+· (.+)$", log, re.M)
    return {
        "report": report, "cfg": cfg, "counts": {k: report["counts"].get(k, 0) for k in
        ["episodes", "atoms", "raw_candidates", "repaired", "rejected", "duplicate", "valid"]},
        "challengers": len(slate.challengers), "slate_rows": rows, "metrics": metrics, "ablation_line": ablation_line,
        "llm_only_line": llm_only_line, "failures": failures[:3], "tokens_m": f"{tokens / 1e6:.2f} M" if tokens > 1e5 else f"{tokens:,}",
        "cost_text": cost_text, "minutes": f"{secs / 60:.1f} min" if secs >= 60 else f"{secs:.0f} s",
        "monthly_tokens": f"{tokens * 150 / 1e6:.0f} M", "monthly_cost": "an unknown cost (fill prices.yaml)" if cost is None else f"${cost * 150:,.0f}",
        "build_log_hint": "; ".join(titles[:2]) if titles else "see docs/BUILD_LOG.md",
        "pipeline_svg": pipeline_svg(), "map_svg": map_svg(ekgs, durations, promos, slate),
        "author": {k: todo_inline(author.get(k), lab) for k, lab in
                   [("name", "name"), ("roll", "roll no."), ("branch", "branch"), ("demo", "demo link"), ("code", "repo link")]},
        "fonts": {
            "plex400": _font("ibm-plex-sans/files/ibm-plex-sans-latin-400-normal.woff2"),
            "plex400i": _font("ibm-plex-sans/files/ibm-plex-sans-latin-400-italic.woff2"),
            "plex600": _font("ibm-plex-sans/files/ibm-plex-sans-latin-600-normal.woff2"),
            "cond400": _font("ibm-plex-sans-condensed/files/ibm-plex-sans-condensed-latin-400-normal.woff2"),
            "cond600": _font("ibm-plex-sans-condensed/files/ibm-plex-sans-condensed-latin-600-normal.woff2"),
            "courier": _font("courier-prime/files/courier-prime-latin-400-normal.woff2"),
        },
    }


def to_markdown(d: dict) -> str:
    r = d["report"]
    lines = [
        "# Cold Open: finding the 40 seconds that make a stranger stay", "",
        "## Why this problem", "", d.get("notes", {}).get("why_this_problem") or "TODO(Kartik)", "",
        "## Results on the full pack", "",
        f"{d['counts']['episodes']} episodes; {d['counts']['raw_candidates']} raw candidates; {d['counts']['valid']} valid.", "",
        "| # | Cut | Hook | Last line | Engine | Why |", "|---|---|---|---|---|---|",
    ]
    for row in d["slate_rows"]:
        cut = "; ".join(f"E{s.episode} {s.start_tc}–{s.end_tc}" for s in row["segments"])
        lines.append(f"| {row['rank']} | {row['structure']}, {cut} | {row['hook']} | {row['cliff']} | {row['engine']} | {row['why']} |")
    lines += ["", "## How I know it's good", ""] + [f"- {m['v']}: {m['l']}" for m in d["metrics"]]
    if d["ablation_line"]:
        lines += ["", d["ablation_line"]]
    lines += ["", "## Where it fails", ""] + [f"- {f['category']} ({f['where']}): {f['what']}" for f in d["failures"]]
    lines += ["", "## Scale and cost", "", f"{r['llm_calls']} calls, {d['tokens_m']} tokens, {d['cost_text']}, {d['minutes']}.",
              "", "## Tools used and where they got it wrong", "",
              d.get("notes", {}).get("tools_used") or f"TODO(Kartik). Suggested: {d['build_log_hint']}", ""]
    return "\n".join(lines)


NOTE_KEYS = ("why_this_problem", "tools_used", "failures_confirmed", "failures_note")


def load_notes(path: Path | None) -> dict:
    """The author's own words for the TODO boxes (a small YAML file, kept out of git)."""
    if not path:
        return {}
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    unknown = set(data) - set(NOTE_KEYS)
    if unknown:
        raise ValueError(f"Unknown keys in {path}: {sorted(unknown)}; use {list(NOTE_KEYS)}")
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}


def render(run: Path, eval_dir: Path | None, out_pdf: Path, out_md: Path, author: dict | None = None,
           notes: dict | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader

    d = gather(run, eval_dir, author or {})
    d["notes"] = notes or {}
    env = Environment(loader=FileSystemLoader(HERE), autoescape=select_autoescape(["html", "j2"]))
    env.globals["todo"] = todo
    html = env.get_template("solution_doc.html.j2").render(**d)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    html_path = out_pdf.with_suffix(".html")
    html_path.write_text(html)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(to_markdown(d))

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.goto(html_path.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        overflow = page.evaluate(
            """() => [...document.querySelectorAll('.page')].flatMap((pg, i) => {
                const limit = pg.getBoundingClientRect().bottom - 13 * 3.78;  // keep clear of the footer
                return [...pg.querySelectorAll('[data-name]')]
                  .filter(s => s.getBoundingClientRect().bottom > limit)
                  .map(s => `page ${i + 1}: "${s.dataset.name}" ends ${(s.getBoundingClientRect().bottom - limit) / 3.78 | 0} mm too low`);
            })"""
        )
        page.pdf(path=str(out_pdf), format="A4", print_background=True, prefer_css_page_size=True)
        b.close()
    pages = len(PdfReader(str(out_pdf)).pages)
    return {"pages": pages, "overflow": overflow, "pdf": str(out_pdf), "md": str(out_md), "html": str(html_path)}
