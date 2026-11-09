"""Cold Open command line (Typer).

Phase 0 ships `models` and `llm-ping`; the other commands exist with their
final signatures so `--help` documents the whole surface, and say which phase
builds them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from coldopen.config import ROLES, load_config, load_prices
from coldopen.llm.cache import ResponseCache
from coldopen.llm.provider import LLMClient, LLMError, ProviderLimitError

app = typer.Typer(
    help="Cold Open: find launch-ready promo clips in microdrama screenplays.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

ConfigOpt = typer.Option("config.yaml", "--config", help="Path to config.yaml")


# --------------------------------------------------------------------------- models


def _list_gemini(key: str) -> list[str]:
    out, token = [], None
    with httpx.Client(timeout=20) as http:
        while True:
            params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
            r = http.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params=params,
                headers={"x-goog-api-key": key},
            )
            r.raise_for_status()
            data = r.json()
            for m in data.get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    out.append("gemini/" + m["name"].removeprefix("models/"))
            token = data.get("nextPageToken")
            if not token:
                return sorted(out)


def _list_groq(key: str) -> list[str]:
    with httpx.Client(timeout=20) as http:
        r = http.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        return sorted("groq/" + m["id"] for m in r.json().get("data", []) if m.get("active", True))


@app.command()
def models(config: Path = ConfigOpt) -> None:
    """List the models your API keys can use (fill config.yaml with these exact names)."""
    cfg = load_config(config)
    in_use = {m: r for r, m in cfg.models.items()}
    providers = [("gemini", "GEMINI_API_KEY", _list_gemini), ("groq", "GROQ_API_KEY", _list_groq)]
    found_any = False
    keys_present = []
    for name, env, lister in providers:
        key = os.environ.get(env)
        if not key:
            console.print(f"[dim]{name}: no {env} set, skipped[/dim]")
            continue
        keys_present.append(name)
        try:
            names = lister(key)
        except httpx.HTTPError as e:
            console.print(f"[red]{name}: listing failed: {e}[/red]")
            continue
        found_any = True
        t = Table(title=f"{name} models ({len(names)})", title_justify="left")
        t.add_column("model (paste into config.yaml)")
        t.add_column("used by role")
        for n in names:
            t.add_row(n, in_use.get(n, ""))
        console.print(t)
    if keys_present == ["gemini"] and cfg.models.get("judge", "").startswith("groq/"):
        console.print(
            "[yellow]Note: only a Gemini key is set. Use two different Gemini models for "
            "`miner` and `judge` so the Cold Viewer is not the model that picked the clips.[/yellow]"
        )
    todo = [r for r in ROLES if "TODO" in cfg.models[r]]
    if todo:
        console.print(f"[yellow]config.yaml still has TODO models for roles: {', '.join(todo)}[/yellow]")
    if not found_any:
        console.print("[red]No models listed. Add keys to .env (see .env.example).[/red]")
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- llm-ping


class _Ping(BaseModel):
    ok: bool
    word: str


@app.command("llm-ping")
def llm_ping(
    config: Path = ConfigOpt,
    provider: str = typer.Option("live", help="live | replay | mock"),
) -> None:
    """Make one tiny call per role and print model, tokens, cost and latency."""
    cfg = load_config(config)
    todo = [r for r in ROLES if "TODO" in cfg.models[r]]
    if todo and provider == "live":
        console.print(
            f"[red]Roles {todo} still point at TODO models. Run `coldopen models` and paste real names into config.yaml.[/red]"
        )
        raise typer.Exit(code=1)
    cache = ResponseCache(enabled=provider == "replay")  # a ping must actually reach the API
    if provider == "mock":
        from coldopen.llm.provider import MockProvider

        prov = MockProvider(responses={"ping": {"ok": True, "word": "ready"}})
    else:
        prov = provider
    client = LLMClient(cfg, provider=prov, cache=cache, prices=load_prices())
    t = Table(title="llm-ping", title_justify="left")
    for col in ("role", "model", "result", "tokens in", "tokens out", "cost (USD)", "latency s"):
        t.add_column(col, justify="right" if col.startswith(("tokens", "cost", "latency")) else "left")
    failed = False
    for role in ROLES:
        before = (client.usage.tokens_in, client.usage.tokens_out)
        try:
            client.call(role, "ping", {}, _Ping)
            rec = client.last_record or {}
            cost = rec.get("cost_usd")
            cost_s = "null" if cost is None else f"{cost:.6f}"
            if rec.get("list_price_equivalent") and cost is not None:
                cost_s += " (list-price equiv.)"
            t.add_row(
                role,
                cfg.models[role],
                "[green]ok[/green]",
                str(client.usage.tokens_in - before[0]),
                str(client.usage.tokens_out - before[1]),
                cost_s,
                f"{rec.get('latency_s', 0):.2f}",
            )
        except (LLMError, Exception) as e:  # noqa: BLE001 - report every role, then fail
            failed = True
            t.add_row(role, cfg.models[role], f"[red]{type(e).__name__}: {str(e)[:80]}[/red]", "-", "-", "-", "-")
    console.print(t)
    if failed:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- later phases


def _client(config: Path, provider: str, run_dir: Path | None = None):
    cfg = load_config(config)
    if provider == "live":
        todo = [r for r in ROLES if "TODO" in cfg.models[r]]
        if todo:
            console.print(f"[red]Roles {todo} still point at TODO models. Run `coldopen models` first, "
                          "or use --provider mock for an offline heuristic run.[/red]")
            raise typer.Exit(code=1)
    return cfg, LLMClient(cfg, provider=provider, prices=load_prices(), run_dir=run_dir)


@app.command()
def run(
    input: Path = typer.Option(..., "--input", help="Folder of episode .txt files, or a .zip"),
    out: Path = typer.Option(..., "--out", help="Run folder, e.g. runs/demo"),
    config: Path = ConfigOpt,
    provider: str = typer.Option("live", help="live | replay | mock"),
    stages: str = typer.Option("all", help="Stages to recompute (others load from the run folder if present): all, or parse,bible,lexicon,ekg,mine,snap,viewer,risk,slate"),
    max_episodes: Optional[int] = typer.Option(None, help="Limit episodes, for quick tests"),
    title: Optional[str] = typer.Option(None, help="Show title (defaults to the folder or zip name)"),
) -> None:
    """Run the pipeline on a show and write a run folder."""
    import asyncio

    from rich.live import Live

    from coldopen.parse import ParseError
    from coldopen.pipeline import STAGE_LABEL, STAGES, Pipeline

    cfg, client = _client(config, provider, out)
    rows: dict[str, dict] = {s: {"status": "waiting"} for s in STAGES}
    totals: dict = {}

    def table() -> Table:
        t = Table(title=f"Cold Open  {out}", title_justify="left")
        for c in ("stage", "status", "progress", "calls", "tokens", "cost", "elapsed"):
            t.add_column(c, justify="right" if c in ("calls", "tokens", "cost", "elapsed") else "left")
        for s in STAGES:
            r = rows[s]
            st = r["status"]
            colour = {"done": "green", "running": "yellow"}.get(st, "dim")
            prog = f"{r.get('done', 0)}/{r['total']}" if r.get("total") else r.get("message", "")
            cost = r.get("cost_usd")
            t.add_row(
                STAGE_LABEL[s], f"[{colour}]{st}[/{colour}]", prog, str(r.get("llm_calls", "")),
                f"{r.get('tokens_in', 0) + r.get('tokens_out', 0):,}" if "tokens_in" in r else "",
                ("null" if cost is None else f"${cost:.4f}") if "llm_calls" in r else "",
                f"{r['elapsed']:.1f}s" if "elapsed" in r else "",
            )
        return t

    with Live(table(), console=console, refresh_per_second=8) as live:
        def on_event(e: dict) -> None:
            if e.get("type") == "stage":
                rows[e["stage"]] = e
                live.update(table())
            totals.update(e)

        pipe = Pipeline(input, out, cfg, client, title=title, max_episodes=max_episodes, emit=on_event)
        try:
            report = asyncio.run(pipe.run(stages.split(",")))
        except ParseError as e:
            console.print(f"[red]Parse error: {e}[/red]")
            raise typer.Exit(code=1)
        except ProviderLimitError as e:
            _limit_exit(e, out)
    c = report.counts
    console.print(
        f"Done. {c.get('raw_candidates', 0)} raw candidates, {c.get('valid', 0)} valid, slate of {c.get('slate', 0)}. "
        f"LLM calls {report.llm_calls} (cache hits {client.usage.cache_hits}), tokens {report.tokens_in + report.tokens_out:,}, "
        f"cost {'null' if report.cost_usd is None else f'${report.cost_usd:.4f}'}."
    )
    for n in report.notes:
        console.print(f"[dim]note: {n}[/dim]")
    if pipe.slate:
        console.print("\n[bold]Top 10 by miner score[/bold]")
        for v in sorted(pipe.valid, key=lambda v: -(v.raw.miner_hook + v.raw.miner_cliff))[:10]:
            segs = " + ".join(f"E{s.episode} {s.start_tc}-{s.end_tc}" for s in v.segments)
            console.print(f"  {v.promo_id} {v.raw.structure:<13} {segs}  ({v.duration:.1f}s, hook @{v.hook_at:.1f}s)  {v.raw.engine}")
        console.print(f"\n[bold]Slate[/bold]  hero {pipe.slate.hero}; challengers {', '.join(pipe.slate.challengers)}")


@app.command()
def show(
    episode: str = typer.Argument(..., help="e.g. E01 or 1"),
    input: Path = typer.Option(Path("data/pack"), "--input", help="Folder of scripts or .zip"),
) -> None:
    """Pretty-print one episode with atom IDs and timestamps (for gold labelling)."""
    from rich.markup import escape

    from coldopen.parse import ParseError, load_show
    from coldopen.timecode import fmt_tc

    try:
        sh = load_show(input)
    except ParseError as e:
        console.print(f"[red]Parse error: {e}[/red]")
        raise typer.Exit(code=1)
    n = int(episode.upper().lstrip("E") or 0)
    ep = next((e for e in sh.episodes if e.number == n), None)
    if ep is None:
        console.print(f"[red]No episode {n}. Available: {sh.episodes[0].number}-{sh.episodes[-1].number}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold]{escape(sh.title)}[/bold]  episode {ep.number}  ({fmt_tc(ep.duration)})")
    console.print(f"[dim]{escape(ep.logline)}[/dim]\n")
    style = {"camera": "dim", "action": "italic", "sfx": "magenta", "dialogue": ""}
    short = {"camera": "CAM", "action": "ACT", "dialogue": "DLG", "sfx": "SFX"}
    scene = None
    for a in ep.atoms:
        if a.scene != scene:
            scene = a.scene
            sc = next(s for s in ep.scenes if s["number"] == scene)
            console.print(f"\n[bold cyan]Scene {scene}[/bold cyan]  {escape(sc['heading'])}  "
                          f"{fmt_tc(sc['start'])}-{fmt_tc(sc['end'])}")
        who = ""
        if a.kind == "dialogue":
            tags = ", ".join(a.tech_tags + a.emotion_tags)
            who = f"[bold]{escape(a.speaker_label or '')}[/bold]" + (f" [dim]({escape(tags)})[/dim]" if tags else "") + "  "
        console.print(
            f"[yellow]{a.id}[/yellow]  {fmt_tc(a.start)}-{fmt_tc(a.end)}  {short[a.kind]}  {who}"
            f"[{style[a.kind]}]{escape(a.text)}[/{style[a.kind]}]" if style[a.kind] else
            f"[yellow]{a.id}[/yellow]  {fmt_tc(a.start)}-{fmt_tc(a.end)}  {short[a.kind]}  {who}{escape(a.text)}",
            soft_wrap=True,
        )


def _run_atoms(run: Path):
    import json as _json

    from coldopen.models import Atom, ShowBible

    atoms = {}
    for line in (run / "00_atoms.jsonl").read_text().splitlines():
        a = Atom(**_json.loads(line))
        atoms[a.id] = a
    bible = ShowBible.model_validate_json((run / "01_bible.json").read_text())
    return atoms, bible


def _render_promo(p: dict, atoms, bible) -> str:
    from coldopen.render import perceived_atom
    from coldopen.timecode import fmt_tc

    out, offset = [], 0.0
    for k, s in enumerate(p["segments"]):
        if k:
            out.append("        [hard cut]")
        for i in s["atom_ids"]:
            a = atoms[i]
            out.append(f"  {fmt_tc(offset + max(0.0, a.start - s['start']))}  {perceived_atom(a, bible)}")
        offset += s["end"] - s["start"]
    return "\n".join(out)


def _rate_session(run: Path) -> None:
    import json as _json

    from coldopen.eval.metrics import rating_sample

    promos = _json.loads((run / "08_promos.json").read_text())
    slate = _json.loads((run / "08_slate.json").read_text())
    atoms, bible = _run_atoms(run)
    top10, audit = rating_sample(promos, slate)
    d = run / "eval"
    d.mkdir(exist_ok=True)
    path = d / "ratings.json"
    ratings = _json.loads(path.read_text()) if path.exists() else {"top10": {}, "audit": {}}
    console.print("[bold]Precision@10.[/bold] Rate each promo 1-5 as if deciding whether to post it. Scores are hidden.")
    for n, p in enumerate(top10, 1):
        if p["id"] in ratings["top10"]:
            continue
        console.rule(f"{n}/10  ({p['duration']:.0f} s)")
        console.print(_render_promo(p, atoms, bible), soft_wrap=True)
        ratings["top10"][p["id"]] = typer.prompt("Rating 1-5", type=int)
        path.write_text(_json.dumps(ratings, indent=1))
    console.print("\n[bold]Cold Viewer audit.[/bold] Does the model viewer's reading match yours? y / p (partly) / n")
    for n, p in enumerate(audit, 1):
        if p["id"] in ratings["audit"]:
            continue
        console.rule(f"audit {n}/{len(audit)}")
        console.print(_render_promo(p, atoms, bible), soft_wrap=True)
        b = p["viewer"]["stage_b"]
        console.print(f"[cyan]Viewer:[/cyan] main {b['main_character']!r}; wants {b['what_they_want']!r}; "
                      f"conflict {b['conflict']!r}; wondering {b['left_wondering']!r}")
        ratings["audit"][p["id"]] = typer.prompt("Agree? (y/p/n)", type=click_choice(["y", "p", "n"]))
        path.write_text(_json.dumps(ratings, indent=1))
    console.print(f"Saved {path}")


def click_choice(options):
    import click

    return click.Choice(options)


@app.command("eval")
def eval_(
    run: Path = typer.Option(..., "--run"),
    gold: Path = typer.Option(Path("eval/gold.yaml"), "--gold"),
    ablate_dir: Optional[Path] = typer.Option(None, "--ablate", help="Ablation folder (from `coldopen ablate`)"),
    rate: bool = typer.Option(False, "--rate", help="Blind 1-5 rating session for precision@10 + Cold Viewer audit"),
) -> None:
    """Score a run against hand-labelled gold promos and write eval_report.json/.md."""
    from coldopen.eval.report import write_report

    if rate:
        _rate_session(run)
    if not gold.exists():
        console.print(
            f"[yellow]{gold} is missing. Gold labels must be written by hand, never generated.[/yellow]\n"
            "Use `coldopen show E01` to read timecodes, then write ~15 entries like:\n"
            '  - id: G01\n    episode: 1\n    start: "00:33.0"\n    end: "01:05.0"\n    note: "what happens"'
        )
        raise typer.Exit(code=2)
    d = write_report(run, gold, ablate_dir)
    console.print((d / "eval_report.md").read_text())
    console.print(f"[green]Wrote {d / 'eval_report.json'}[/green]")


def _limit_exit(e: "ProviderLimitError", out: Path) -> None:
    """A stage that cannot run without the model (the show bible) hit an API limit."""
    console.print(
        f"[yellow]Paused: the {e.human()}.[/yellow]\n"
        "Nothing is lost: every finished call is cached. Run the same command again later and it continues "
        "where it stopped.\nTo go faster: add fallbacks in config.yaml, lower judge_limit, or use a key on a paid tier. "
        f"Partial files are in {out}."
    )
    raise typer.Exit(code=2)


@app.command()
def ablate(
    input: Path = typer.Option(..., "--input"),
    variants: str = typer.Option("rules,llm_only,full,no_viewer", "--variants"),
    out: Path = typer.Option(Path("runs/ablate"), "--out"),
    config: Path = ConfigOpt,
    provider: str = typer.Option("live", help="live | replay | mock"),
    title: Optional[str] = typer.Option(None),
) -> None:
    """Run the ablation variants and write ablation.json (fed into `coldopen eval --ablate`)."""
    import asyncio

    from coldopen.eval.ablate import run_ablation

    cfg, _ = _client(config, provider)

    def make_client(run_dir: Path):
        return LLMClient(cfg, provider=provider, prices=load_prices(), run_dir=run_dir)

    vs = [v.strip() for v in variants.split(",") if v.strip()]
    try:
        res = asyncio.run(run_ablation(input, out, cfg, make_client, vs, title=title))
    except ProviderLimitError as e:
        _limit_exit(e, out)
    t = Table(title="Ablation: invalid outputs", title_justify="left")
    t.add_column("check")
    names = [n for n in ("rules", "llm_only", "no_viewer", "full") if n in res["variants"]]
    for n in names:
        t.add_column(n, justify="right")
    for k in ("timestamps_off_atom_boundary", "midline_cuts", "durations_out_of_range", "hook_latency_over_3s",
              "quotes_not_in_script"):
        t.add_row(k.replace("_", " "), *[str(res["variants"][n]["violations"].get(k, 0)) for n in names])
    t.add_row("promos", *[str(len(res["variants"][n]["promos"])) for n in names])
    console.print(t)
    console.print(f"Wrote {out / 'ablation.json'}. Next: coldopen eval --run {out / 'full'} --ablate {out}")


@app.command()
def serve(port: int = typer.Option(7860, "--port"), host: str = typer.Option("127.0.0.1", "--host")) -> None:
    """Serve the API and the review UI (build the UI first: cd web && npm run build)."""
    import uvicorn

    uvicorn.run("coldopen.api.server:app", host=host, port=port, log_level="info")


@app.command()
def doc(
    run: Path = typer.Option(..., "--run"),
    eval: Optional[Path] = typer.Option(None, "--eval", help="Folder holding eval_report.json (default: RUN/eval)"),
    out: Path = typer.Option(Path("docs/solution_doc.pdf"), "--out"),
    name: Optional[str] = typer.Option(None), roll: Optional[str] = typer.Option(None), branch: Optional[str] = typer.Option(None),
    demo: Optional[str] = typer.Option(None, help="Demo link"), code: Optional[str] = typer.Option(None, help="Repo link"),
    notes: Optional[Path] = typer.Option(None, "--notes", help="YAML with your own words: why_this_problem, tools_used, "
                                         "failures_confirmed (true/false), failures_note"),
) -> None:
    """Render the 2-page solution PDF (and an editable Markdown copy) from real run data."""
    from coldopen.docgen.render import load_notes, render

    res = render(run, eval, out, out.with_suffix(".md"),
                 {"name": name, "roll": roll, "branch": branch, "demo": demo, "code": code}, load_notes(notes))
    for o in res["overflow"]:
        console.print(f"[red]Too long: {o}[/red]")
    colour = "green" if res["pages"] == 2 and not res["overflow"] else "red"
    console.print(f"[{colour}]{res['pdf']}: {res['pages']} pages[/{colour}]  (editable text: {res['md']})")
    if res["pages"] != 2 or res["overflow"]:
        raise typer.Exit(code=1)


@app.command()
def bundle(
    run: Path = typer.Option(..., "--run"),
    out: Path = typer.Option(Path("raw_outputs.zip"), "--out"),
) -> None:
    """Zip a run folder (raw LLM calls, artifacts, eval report) with docs/BUILD_LOG.md for submission."""
    import zipfile

    if not (run / "run_report.json").exists():
        console.print(f"[red]{run} is not a finished run[/red]")
        raise typer.Exit(code=1)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(run.rglob("*")):
            if f.is_file() and f.name != ".owner_token":
                z.write(f, Path(run.name) / f.relative_to(run))
                n += 1
        if Path("docs/BUILD_LOG.md").exists():
            z.write("docs/BUILD_LOG.md", "BUILD_LOG.md")
            n += 1
    console.print(f"[green]Wrote {out} ({n} files, {out.stat().st_size / 1e6:.1f} MB)[/green]")


if __name__ == "__main__":
    app()
