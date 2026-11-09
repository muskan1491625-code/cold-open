"""Orchestrates the stages, writes every artifact, emits progress, builds the run report.

Re-run semantics: stages named in `stages` are recomputed; every other stage
is loaded from the run folder if its artifact exists, otherwise computed. With
the LLM response cache, re-running a stage after a code fix costs nothing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from coldopen.bible import apply_speakers, bible_variables, proper_nouns
from coldopen.config import Config
from coldopen.ekg import build_ekg, debug_png
from coldopen.export import write_exports
from coldopen.lexicon import build_lexicon
from coldopen.llm.provider import LLMClient
from coldopen.llm.provider import ProviderLimitError
from coldopen.mine import mine_all, mine_arc_bridges, pick_moments
from coldopen.models import EKG, LexiconEntry, Promo, RawCandidate, RunReport, Show, ShowBible, Slate
from coldopen.parse import load_show, show_stats
from coldopen.risk import assess
from coldopen.slate import build_promo, select_slate, tension_of
from coldopen.snap import ShowIndex, ValidCandidate, constraint_report, dedupe, validate_one
from coldopen.viewer import judge

STAGES = ["parse", "bible", "lexicon", "ekg", "mine", "snap", "viewer", "risk", "slate"]
STAGE_LABEL = {
    "parse": "Parse", "bible": "Bible", "lexicon": "Lexicon", "ekg": "EKG", "mine": "Mining",
    "snap": "Validate", "viewer": "Cold Viewer", "risk": "Risk", "slate": "Slate",
}

Emit = Callable[[dict], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


class Pipeline:
    def __init__(
        self,
        input: Path,
        out: Path,
        cfg: Config,
        client: LLMClient,
        title: str | None = None,
        max_episodes: int | None = None,
        emit: Emit | None = None,
        use_viewer: bool = True,
    ):
        self.input, self.out, self.cfg, self.client = Path(input), Path(out), cfg, client
        self.title, self.max_episodes = title, max_episodes
        self.emit_fn = emit or (lambda e: None)
        self.use_viewer = use_viewer
        self.stage_seconds: dict[str, float] = {}
        self.counts: Counter = Counter()
        self.notes: list[str] = []
        self.constraint_pass: dict[str, float] = {}
        self.show: Show | None = None
        self.ix: ShowIndex | None = None
        self.bible: ShowBible | None = None
        self.lexicon: dict[str, LexiconEntry] = {}
        self.ekgs: dict[int, EKG] = {}
        self.raw: list[dict] = []
        self.valid: list[ValidCandidate] = []
        self.views: dict[str, dict] = {}
        self.risks: dict[str, dict] = {}
        self.promos: dict[str, Promo] = {}
        self.slate: Slate | None = None
        self.vlog: list[dict] = []
        self._schema_rejects: list[dict] = []

    # -- events -------------------------------------------------------------

    def emit(self, stage: str, status: str, done: int = 0, total: int = 0, message: str = "") -> None:
        u = self.client.usage
        self.emit_fn({
            "type": "stage", "stage": stage, "label": STAGE_LABEL.get(stage, stage), "status": status,
            "done": done, "total": total, "message": message,
            "llm_calls": u.calls, "tokens_in": u.tokens_in, "tokens_out": u.tokens_out, "cost_usd": u.cost_usd,
            "elapsed": round(time.perf_counter() - self._t0, 2),
        })

    # -- run ----------------------------------------------------------------

    def _limited(self, stage: str, what: str, n: int, total: int, err: ProviderLimitError) -> None:
        """Record work skipped because of an API limit: a counted, plain-English note, not a crash."""
        self.counts[f"api_limited_{stage}"] = self.counts.get(f"api_limited_{stage}", 0) + n
        msg = (f"API limit in {stage}: {what} ({n} of {total}) skipped because the {err.human()}. "
               "Re-run the same command later to finish; every finished call is cached.")
        self.notes.append(msg)
        self.emit_fn({"type": "warning", "stage": stage, "message": msg})

    async def run(self, stages: list[str] | None = None) -> RunReport:
        rerun = set(STAGES if not stages or stages == ["all"] else stages)
        bad = rerun - set(STAGES)
        if bad:
            raise ValueError(f"Unknown stages {sorted(bad)}; choose from {STAGES}")
        self.out.mkdir(parents=True, exist_ok=True)
        self._t0 = time.perf_counter()
        started = _now()
        if self.client.provider.name == "mock":
            self.notes.append("Mock provider: LLM answers are heuristic stand-ins, not model judgements.")
        if self.client.provider.name == "replay":
            self.notes.append("Replay provider: every LLM answer served from the recorded cache.")
        for name in STAGES:
            t = time.perf_counter()
            self.emit(name, "running")
            await getattr(self, f"_stage_{name}")(name in rerun)
            self.stage_seconds[name] = round(time.perf_counter() - t, 2)
            self.emit(name, "done", message=self._stage_summary(name))
        report = self._report(started)
        (self.out / "run_report.json").write_text(report.model_dump_json(indent=2))
        (self.out / "config_snapshot.yaml").write_text(yaml.safe_dump(self.cfg.model_dump(), sort_keys=False))
        self.emit_fn({"type": "finished", "run_id": report.run_id})
        return report

    def _stage_summary(self, name: str) -> str:
        c = self.counts
        return {
            "parse": f"{c['episodes']} episodes, {c['atoms']} atoms",
            "bible": f"{len(self.bible.characters) if self.bible else 0} characters, "
                     f"{len(self.bible.continuity_conflicts) if self.bible else 0} continuity conflicts",
            "lexicon": f"{len(self.lexicon)} tags",
            "ekg": f"{sum(len(e.hotspots) for e in self.ekgs.values())} hotspots",
            "mine": f"{c['raw_candidates']} raw candidates",
            "snap": f"{c['valid']} valid of {c['raw_candidates']}",
            "viewer": f"{len(self.views)} promos viewed",
            "risk": f"{len(self.risks)} promos checked",
            "slate": "hero + challengers chosen" if self.slate else "",
        }.get(name, "")

    # -- stages -------------------------------------------------------------

    async def _stage_parse(self, force: bool) -> None:
        self.show = load_show(self.input, max_episodes=self.max_episodes, title=self.title)
        self.ix = ShowIndex.build(self.show)
        st = show_stats(self.show)
        self.counts.update({"episodes": st["episodes"], "atoms": st["blocks"]})
        _jsonl(self.out / "00_atoms.jsonl", [a.model_dump() for e in self.show.episodes for a in e.atoms])
        meta = {
            "title": self.show.title,
            "stats": st,
            "episodes": [e.model_dump(exclude={"atoms"}) for e in self.show.episodes],
        }
        (self.out / "00_show.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    async def _stage_bible(self, force: bool) -> None:
        path = self.out / "01_bible.json"
        variables, detector = bible_variables(self.show)
        if path.exists() and not force:
            self.bible = ShowBible.model_validate_json(path.read_text())
        else:
            self.bible = await self.client.acall("miner", "bible", variables, ShowBible)
            path.write_text(self.bible.model_dump_json(indent=2))
        detector["speaker_resolution"] = apply_speakers(self.show, self.bible)
        (self.out / "01_bible_detector.json").write_text(json.dumps(detector, ensure_ascii=False, indent=1))
        _jsonl(self.out / "00_atoms.jsonl", [a.model_dump() for e in self.show.episodes for a in e.atoms])
        unresolved = detector["speaker_resolution"].get("unresolved", 0)
        if unresolved:
            self.notes.append(f"{unresolved} dialogue lines kept their raw speaker label (no bible alias matched).")

    async def _stage_lexicon(self, force: bool) -> None:
        path = self.out / "02_lexicon.json"
        if path.exists() and not force:
            self.lexicon = {k: LexiconEntry(**v) for k, v in json.loads(path.read_text())["tags"].items()}
            return
        self.lexicon, stats = await build_lexicon(self.show, self.client, cache_root=self.client.cache.root.parent)
        path.write_text(json.dumps({"stats": stats, "tags": {k: v.model_dump() for k, v in self.lexicon.items()}}, indent=1))
        if stats["missing"]:
            self.notes.append(f"Lexicon: {stats['missing']} tags got no rating and use a neutral default (arousal 0.3).")

    async def _stage_ekg(self, force: bool) -> None:
        path = self.out / "03_ekg.json"
        if path.exists() and not force:
            self.ekgs = {e["episode"]: EKG(**e) for e in json.loads(path.read_text())}
            return
        ekgs = build_ekg(self.show, self.lexicon)
        self.ekgs = {e.episode: e for e in ekgs}
        path.write_text(json.dumps([e.model_dump() for e in ekgs]))
        debug_png(ekgs, self.out / "debug_ekg.png")

    async def _stage_mine(self, force: bool) -> None:
        path = self.out / "04_candidates_raw.jsonl"
        if path.exists() and not force:
            self.raw = _read_jsonl(path)
            self._schema_rejects = []
        else:
            total = len(self.show.episodes)
            done = [0]

            def tick(_ep: int) -> None:
                done[0] += 1
                self.emit("mine", "running", done[0], total)

            limited: list = []
            self.raw, self._schema_rejects = await mine_all(self.show, self.bible, self.ekgs, self.client, tick, limited)
            if limited:
                eps = ", ".join(f"E{n}" for n, _ in sorted(limited, key=lambda x: x[0]))
                self._limited("mine", f"episodes not mined: {eps}", len(limited), total, limited[0][1])
            _jsonl(path, self.raw)
        self.counts["raw_candidates"] = len(self.raw) + len(self._schema_rejects)

    async def _stage_snap(self, force: bool) -> None:
        path = self.out / "05_candidates_valid.json"
        if path.exists() and not force:
            data = json.loads(path.read_text())
            self.valid = [ValidCandidate(**v) for v in data["valid"]]
            self.vlog = _read_jsonl(self.out / "05_validation_log.jsonl")
            self.counts.update(data["counts"])
            return
        ix, total = self.ix, len(self.raw)
        logs = list(getattr(self, "_schema_rejects", []))
        done = [0]

        snap_limited: list[ProviderLimitError] = []

        async def one(rec: dict):
            try:
                res = await validate_one(rec["cid"], rec["source"], RawCandidate(**rec["raw"]), ix, self.client, self.bible)
            except ProviderLimitError as e:  # the repair call hit a limit: reject, labelled, and go on
                snap_limited.append(e)
                res = (None, {"cid": rec["cid"], "source": rec["source"], "status": "rejected",
                              "reasons": ["api_limit"], "detail": e.human()})
            done[0] += 1
            self.emit("snap", "running", done[0], total)
            return res

        singles = [r for r in self.raw if r["source"] == "episode"]
        results = await asyncio.gather(*(one(r) for r in singles))
        valid = [v for v, _ in results if v]
        logs += [lg for _, lg in results]

        # Arc bridge pass: needs validated single-episode moments first.
        moments = pick_moments(valid, lambda c: tension_of(c, self.ekgs))
        try:
            bridges, b_rejects = await mine_arc_bridges(moments, self.bible, ix, self.client)
        except ProviderLimitError as e:
            bridges, b_rejects = [], []
            self._limited("snap", "cross-episode arc bridges not mined", 1, 1, e)
        logs += b_rejects
        if bridges:
            self.raw += bridges
            _jsonl(self.out / "04_candidates_raw.jsonl", self.raw)
            bres = await asyncio.gather(*(one(r) for r in bridges))
            valid += [v for v, _ in bres if v]
            logs += [lg for _, lg in bres]
        self.counts["raw_candidates"] = len(self.raw) + len([x for x in logs if "schema" in x.get("reasons", [])])
        self.counts["arc_bridge_raw"] = len(bridges) + len(b_rejects)

        if snap_limited:
            self._limited("snap", "candidates rejected because their repair call could not run",
                          len(snap_limited), total, snap_limited[0])
        valid, dup_logs = dedupe(valid)
        logs += dup_logs
        for k, v in enumerate(sorted(valid, key=lambda c: (c.source == "arc_bridge", c.cid)), 1):
            v.promo_id = f"P{k:03d}"
        self.valid = sorted(valid, key=lambda c: c.promo_id)
        self.vlog = logs

        c = Counter()
        for lg in logs:
            st = lg["status"]
            c[st] += 1
            if st == "rejected":
                for r in lg.get("reasons", []):
                    c[f"rejected_{r}"] += 1
            if lg.get("llm_repair"):
                c["repair_attempts"] += 1
        c["valid"] = len(self.valid)
        self.counts.update(c)
        _jsonl(self.out / "05_validation_log.jsonl", logs)
        path.write_text(json.dumps({"counts": dict(c), "valid": [v.model_dump() for v in self.valid]}, ensure_ascii=False, indent=1))

    def _judged(self) -> list[ValidCandidate]:
        lim = self.cfg.judge_limit
        if not lim or lim >= len(self.valid):
            return self.valid
        pre = sorted(self.valid, key=lambda c: -((c.raw.miner_hook + c.raw.miner_cliff) / 10 + tension_of(c, self.ekgs)))
        keep = {c.promo_id for c in pre[:lim]}
        return [c for c in self.valid if c.promo_id in keep]

    async def _stage_viewer(self, force: bool) -> None:
        path = self.out / "06_viewer.json"
        if not self.use_viewer:
            self.views = {}
            return
        if path.exists() and not force:
            self.views = json.loads(path.read_text())
            return
        todo = self._judged()
        nouns = set(proper_nouns(self.show))
        done = [0]

        limited: list[ProviderLimitError] = []

        async def one(vc: ValidCandidate):
            try:
                r = await judge(vc, self.ix, self.bible, self.show, nouns, self.client)
            except ProviderLimitError as e:
                limited.append(e)
                r = None
            done[0] += 1
            self.emit("viewer", "running", done[0], len(todo))
            return vc.promo_id, r

        self.views = {k: v for k, v in await asyncio.gather(*(one(v) for v in todo)) if v is not None}
        if limited:
            self._limited("viewer", "promos not shown to the Cold Viewer", len(limited), len(todo), limited[0])
        path.write_text(json.dumps(self.views, ensure_ascii=False, indent=1))

    async def _stage_risk(self, force: bool) -> None:
        path = self.out / "07_risk.json"
        if path.exists() and not force:
            self.risks = json.loads(path.read_text())
            return
        todo = self._judged()
        done = [0]

        limited: list[ProviderLimitError] = []

        async def one(vc: ValidCandidate):
            try:
                r = await assess(vc, self.ix, self.bible, self.cfg, self.client)
            except ProviderLimitError as e:
                limited.append(e)
                r = None
            done[0] += 1
            self.emit("risk", "running", done[0], len(todo))
            return vc.promo_id, r

        self.risks = {k: v for k, v in await asyncio.gather(*(one(v) for v in todo)) if v is not None}
        if limited:
            self._limited("risk", "promos without a policy and landing check", len(limited), len(todo), limited[0])
        path.write_text(json.dumps(self.risks, ensure_ascii=False, indent=1))

    async def _stage_slate(self, force: bool) -> None:
        use_viewer = self.use_viewer
        if use_viewer and not self.views and self.counts.get("api_limited_viewer"):
            # The Cold Viewer could not judge anything: rank on the miner's own scores (the
            # `no_viewer` ablation) rather than show nothing, and say so.
            use_viewer = False
            self.notes.append("Slate ranked without the Cold Viewer (API limit): stranger-comprehension scores are missing.")
        eligible = [v for v in self.valid if v.promo_id in self.risks and (not use_viewer or v.promo_id in self.views)]
        self.promos = {
            v.promo_id: build_promo(v, self.views.get(v.promo_id), self.risks[v.promo_id], self.ekgs, self.cfg, self.ix, use_viewer)
            for v in eligible
        }
        if not self.promos:
            self.notes.append("No valid promos survived; no slate written.")
            return
        self.slate = select_slate(list(self.promos.values()), self.cfg)
        self.counts["slate"] = 1 + len(self.slate.challengers)
        self.counts["bench"] = len(self.slate.bench)
        (self.out / "08_slate.json").write_text(self.slate.model_dump_json(indent=2))
        (self.out / "08_promos.json").write_text(
            json.dumps({k: p.model_dump() for k, p in self.promos.items()}, ensure_ascii=False, indent=1)
        )
        write_exports(self.out, self.show.title, self.promos, self.slate, self.cfg.fps)
        # Every final promo is re-checked against every hard rule.
        reps = [constraint_report(v.segments, v.hook_at, v.raw, self.ix) for v in eligible]
        keys = reps[0].keys() if reps else []
        self.constraint_pass = {k: round(sum(r[k] for r in reps) / len(reps), 4) for k in keys}
        assert all(v == 1.0 for v in self.constraint_pass.values()), self.constraint_pass

    # -- report -------------------------------------------------------------

    def _report(self, started: str) -> RunReport:
        u = self.client.usage
        if u.cost_usd is not None and u.any_free_tier:
            self.notes.append("Cost is a list-price equivalent: models ran on a free tier.")
        if u.cost_usd is None:
            self.notes.append("Cost unknown: at least one model has no price in prices.yaml.")
        fb = getattr(self.client, "fallbacks_used", [])
        if fb:
            pairs = sorted({f"{f['from']} -> {f['to']}" for f in fb})
            self.notes.append(f"Fallback model answered {len(fb)} call(s) while the main model was overloaded: "
                              + "; ".join(pairs) + ". See answered_by in llm_calls/.")
        models = dict(self.cfg.models)
        models["provider"] = self.client.provider.name
        return RunReport(
            run_id=self.out.name,
            show_title=self.show.title if self.show else "",
            started_at=started,
            finished_at=_now(),
            models=models,
            llm_calls=u.calls,
            tokens_in=u.tokens_in,
            tokens_out=u.tokens_out,
            cost_usd=u.cost_usd,
            stage_seconds=self.stage_seconds,
            counts={k: int(v) for k, v in self.counts.items()},
            constraint_pass=self.constraint_pass,
            notes=self.notes,
        )
