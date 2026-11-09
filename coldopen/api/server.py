"""FastAPI app: serves run data to the review UI, records editor decisions, and runs new jobs.

Access (see coldopen/api/security.py for the reasoning): if DEMO_ACCESS_CODE is
set, every run endpoint needs a signed session token from POST /api/access
(or the X-Access-Code header, for scripts). A run started with the user's own
API key is readable with the run-scoped token returned at creation. Keys sent
with a request are passed to the provider for that job only; they are never
written to disk, logged or echoed in errors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
from contextlib import asynccontextmanager
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from coldopen.api import security as sec
from coldopen.config import load_config, load_prices
from coldopen.export import edl
from coldopen.models import Atom, Promo, Segment, ShowBible
from coldopen.llm.provider import ProviderLimitError
from coldopen.parse import ParseError
from coldopen.render import descriptor, perceived_atom
from coldopen.snap import DUR_MAX, DUR_MIN, EPS, HOOK_MAX
from coldopen.timecode import fmt_tc

# Load .env before anything reads DEMO_ACCESS_CODE: otherwise the first requests after a
# restart would see no code configured and serve the demo runs openly.
load_dotenv(override=False)

ROOT = Path(os.environ.get("COLDOPEN_HOME", Path.cwd()))
RUNS = Path(os.environ.get("COLDOPEN_RUNS", ROOT / "runs"))
WEB = Path(os.environ.get("COLDOPEN_WEB", ROOT / "web" / "dist"))
CONFIG = Path(os.environ.get("COLDOPEN_CONFIG", ROOT / "config.yaml"))

log = logging.getLogger("coldopen.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    code = sec.access_code()
    if not code and sec.require_code():
        log.error("DEMO_ACCESS_CODE is not set on a hosted server: client runs stay locked until it is")
    elif not code:
        log.warning("DEMO_ACCESS_CODE is not set: every run is readable without a code (fine on localhost only)")
    elif len(code) < 12:
        log.warning("DEMO_ACCESS_CODE is short; use 12+ random characters "
                    "(python -c 'import secrets; print(secrets.token_urlsafe(12))')")
    yield


app = FastAPI(title="Cold Open", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
JOBS: dict[str, dict] = {}
MAX_JOBS = int(os.environ.get("COLDOPEN_MAX_JOBS", 2))  # concurrent pipeline runs
# Runs on the server's own (free-tier) keys are capped so one visitor cannot spend the day's
# quota for everyone. Bring your own key to run a whole show.
SERVER_KEY_MAX_EPISODES = int(os.environ.get("COLDOPEN_SERVER_KEY_MAX_EPISODES", 3))


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in sec.SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    path = request.url.path
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"  # confidential run data must not sit in any cache
    elif path.startswith("/assets/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # content-hashed names
    return resp


# --------------------------------------------------------------------------- access


def _check(request: Request, run_id: str) -> Path:
    return sec.require_run(request, RUNS, run_id)


def _j(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


DEFAULT_HINT = ("Reviewers: open the demo link from the submission and it signs you in by itself. "
                "Or type the access code printed on page 1 of the solution PDF.")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "access_code_required": sec.code_required(),
            "public_runs": sorted(sec.public_runs()), "code_hint": os.environ.get("DEMO_CODE_HINT") or DEFAULT_HINT}


class AccessBody(BaseModel):
    code: str = Field(min_length=1, max_length=200)


@app.post("/api/access")
def access(request: Request, body: AccessBody | None = None, x_access_code: str | None = Header(None)) -> dict:
    """Trade the access code for a signed session token (the page keeps the token, never the code)."""
    code = (body.code if body else None) or x_access_code
    if not sec.check_code(request, code):
        raise HTTPException(401, "That code did not match")
    token, exp = sec.SIGNER.issue("demo")
    log.info("session issued client=%s", sec.fingerprint(sec.client_ip(request)))
    return {"ok": True, "token": token, "expires_at": exp, "scope": "demo"}


@app.get("/api/session")
def session(request: Request) -> dict:
    """What the current credentials unlock; the UI calls this to validate a stored token."""
    p = sec.principal(request)
    locked = 0
    if RUNS.exists():
        locked = sum(1 for d in RUNS.iterdir() if d.is_dir() and sec.RUN_ID_RE.match(d.name)
                     and (d / "08_slate.json").exists() and not p.can_read(d.name))
    return {"demo": p.demo, "runs": sorted(p.runs), "expires_at": p.expires_at, "via": p.via,
            "access_code_required": sec.code_required(), "locked_runs": locked}


# --------------------------------------------------------------------------- runs


@app.get("/api/runs")
def list_runs(request: Request) -> list[dict]:
    out: list[dict] = []
    if not RUNS.exists():
        return out
    who = sec.principal(request)
    for d in sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir() or not sec.RUN_ID_RE.match(d.name) or not who.can_read(d.name):
            continue
        rep = _j(d / "run_report.json")
        if not rep or not (d / "08_slate.json").exists():
            continue
        out.append({
            "id": d.name, "show": rep["show_title"], "public": d.name in sec.public_runs(), "date": rep["finished_at"], "models": rep["models"],
            "cost_usd": rep["cost_usd"], "valid": rep["counts"].get("valid", 0), "llm_calls": rep["llm_calls"],
            "seconds": round(sum(rep["stage_seconds"].values()), 1),
        })
    return out


@app.get("/api/runs/{run_id}/show")
def run_show(run_id: str, request: Request) -> dict:
    d = _check(request, run_id)
    meta = _j(d / "00_show.json", {})
    bible = _j(d / "01_bible.json", {})
    detector = _j(d / "01_bible_detector.json", {})
    ekgs = _j(d / "03_ekg.json", [])
    lines = _dialogue_by_second(d)
    return {
        "title": meta.get("title"), "stats": meta.get("stats"), "report": _j(d / "run_report.json"),
        "episodes": [{k: e[k] for k in ("number", "logline", "duration", "scenes")} for e in meta.get("episodes", [])],
        "bible": bible, "labels": detector.get("labels", []), "ekg": ekgs, "lines": lines,
    }


def _atoms(d: Path) -> dict[str, Atom]:
    out = {}
    for line in (d / "00_atoms.jsonl").read_text().splitlines():
        a = Atom(**json.loads(line))
        out[a.id] = a
    return out


def _dialogue_by_second(d: Path) -> dict[str, list]:
    """Per episode, compact [start, end, speaker, text] dialogue rows for the map's hover."""
    out: dict[str, list] = {}
    for a in _atoms(d).values():
        if a.kind == "dialogue":
            out.setdefault(str(a.episode), []).append([a.start, a.end, a.speaker or a.speaker_label, a.text])
    return out


def _cue(a: Atom | None, bible: ShowBible | None) -> dict | None:
    if a is None:
        return None
    return {"label": a.speaker_label, "tags": a.tech_tags + a.emotion_tags, "kind": a.kind,
            "descriptor": descriptor(bible, a) if a.kind == "dialogue" else None}


def _summary(p: dict, slate: dict, atoms: dict[str, Atom] | None = None, bible: ShowBible | None = None) -> dict:
    slot = "hero" if p["id"] == slate["hero"] else "challenger" if p["id"] in slate["challengers"] else \
        "bench" if p["id"] in slate["bench"] else "pool"
    return {
        "id": p["id"], "slot": slot, "structure": p["structure"], "engine": p["engine"],
        "segments": [{k: s[k] for k in ("episode", "start", "end", "start_tc", "end_tc")} for s in p["segments"]],
        "duration": p["duration"], "hook_text": p["hook_text"], "cliffhanger_text": p["cliffhanger_text"],
        "hook_speaker": p["hook_speaker"], "hook_at": p["hook_at"], "why": p["why"], "policy": p["policy"],
        "landing_episode": p["landing_episode"], "free_runway": p["free_runway"], "scores": p["scores"],
        "rationale": slate["rationale"].get(p["id"], ""), "reveals_disclosed": p["reveals_disclosed"],
        "ungrounded_refs": p["ungrounded_refs"], "continuity_warnings": p["continuity_warnings"],
        "hook_cue": _cue(atoms.get(p["validation"].get("hook_id", "")), bible) if atoms else None,
        "cliff_cue": _cue(atoms.get(p["validation"].get("cliffhanger_id", "")), bible) if atoms else None,
    }


@app.get("/api/runs/{run_id}/slate")
def run_slate(run_id: str, request: Request) -> dict:
    d = _check(request, run_id)
    slate = _j(d / "08_slate.json")
    promos = _j(d / "08_promos.json", {})
    order = [slate["hero"], *slate["challengers"], *slate["bench"]]
    rest = sorted((k for k in promos if k not in order), key=lambda k: -promos[k]["scores"]["total"])
    atoms = _atoms(d)
    bible = ShowBible.model_validate_json((d / "01_bible.json").read_text())
    return {
        "slate": slate,
        "promos": [_summary(promos[k], slate, atoms, bible) for k in order + rest],
        "decisions": _decisions(d),
    }


@app.get("/api/runs/{run_id}/promos/{pid}")
def run_promo(run_id: str, pid: str, request: Request) -> dict:
    d = _check(request, run_id)
    promos = _j(d / "08_promos.json", {})
    if pid not in promos:
        raise HTTPException(404, f"No promo {pid}")
    p = promos[pid]
    slate = _j(d / "08_slate.json")
    atoms = _atoms(d)
    bible = ShowBible.model_validate_json((d / "01_bible.json").read_text())
    ekgs = {e["episode"]: e for e in _j(d / "03_ekg.json", [])}
    eps = {e["number"]: e for e in _j(d / "00_show.json", {}).get("episodes", [])}
    context = []
    for s in p["segments"]:
        lo, hi = s["start"] - 20, s["end"] + 20  # room for trim handles
        ep_atoms = sorted((a for a in atoms.values() if a.episode == s["episode"]), key=lambda a: (a.start, a.id))
        context.append({
            "episode": s["episode"], "duration": eps.get(s["episode"], {}).get("duration"),
            "atoms": [_atom_view(a, bible) for a in ep_atoms if a.end > lo and a.start < hi],
            "ekg": ekgs.get(s["episode"], {}).get("values", []),
        })
    return {"promo": p, "summary": _summary(p, slate, atoms, bible), "context": context, "decision": _decisions(d).get(pid)}


def _atom_view(a: Atom, bible: ShowBible) -> dict:
    return {
        "id": a.id, "kind": a.kind, "start": a.start, "end": a.end, "text": a.text, "speaker": a.speaker,
        "label": a.speaker_label, "tags": a.tech_tags + a.emotion_tags, "tc": fmt_tc(a.start),
        "descriptor": descriptor(bible, a) if a.kind == "dialogue" else None,
        "perceived": perceived_atom(a, bible),
    }


@app.get("/api/runs/{run_id}/quality")
def run_quality(run_id: str, request: Request) -> dict:
    from coldopen.eval.metrics import comprehension, failure_gallery, rejection_rates

    d = _check(request, run_id)
    vlog = [json.loads(x) for x in (d / "05_validation_log.jsonl").read_text().splitlines() if x.strip()]
    ev = _j(d / "eval" / "eval_report.json")
    return {
        "report": _j(d / "run_report.json"),
        "eval": ev,
        "validation": rejection_rates(vlog),
        "gallery": ev["failure_gallery"] if ev else failure_gallery(d),
        "comprehension": comprehension(_j(d / "08_promos.json", {})),
    }


# --------------------------------------------------------------------------- decisions + exports


class Trim(BaseModel):
    episode: int = Field(ge=1, le=10_000)
    start: float = Field(ge=0, le=36_000)
    end: float = Field(ge=0, le=36_000)


class Decision(BaseModel):
    action: Literal["approve_paid", "organic_only", "reject"]
    reason: str | None = Field(None, max_length=500)
    trims: list[Trim] | None = Field(None, max_length=10)  # one per segment


def _trim_errors(p: dict, trims: list[Trim], atoms: dict[str, Atom], durations: dict[int, float]) -> list[str]:
    """Re-check the hard rules on an editor's trims. The UI checks too, but code owns facts."""
    segs = p["segments"]
    if len(trims) != len(segs):
        return [f"Expected {len(segs)} trimmed segments, got {len(trims)}"]
    errs = []
    for s, t in zip(segs, trims):
        if t.episode != s["episode"]:
            errs.append(f"Segment for E{s['episode']} was sent as E{t.episode}")
        elif not (0 <= t.start < t.end <= durations.get(t.episode, 0) + EPS):
            errs.append(f"E{t.episode} {t.start:.2f}-{t.end:.2f} is outside the episode")
    if errs:
        return errs
    total = sum(t.end - t.start for t in trims)
    if not DUR_MIN - EPS <= total <= DUR_MAX + EPS:
        errs.append(f"Length {total:.1f} s is outside 20-60 s")
    hook = atoms.get(p["validation"].get("hook_id", ""))
    cliff = atoms.get(p["validation"].get("cliffhanger_id", ""))
    first, last = trims[0], trims[-1]
    if hook and not (first.start - EPS <= hook.start < first.end and hook.start - first.start <= HOOK_MAX + EPS):
        errs.append("The hook must start within 3 s of the cut")
    if cliff and not cliff.end <= last.end + EPS:
        errs.append("The cliffhanger line must stay in the cut")
    for t in trims:
        for a in atoms.values():
            if a.episode == t.episode and a.kind == "dialogue" and any(
                a.start < edge - EPS and a.end > edge + EPS for edge in (t.start, t.end)
            ):
                errs.append(f"Cut at E{t.episode} {fmt_tc(t.start)}-{fmt_tc(t.end)} splits a spoken line")
                break
    return errs


def _decisions(d: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    f = d / "decisions.jsonl"
    if f.exists():
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["promo_id"]] = r
    return out


@app.post("/api/runs/{run_id}/promos/{pid}/decision")
def decide(run_id: str, pid: str, body: Decision, request: Request) -> dict:
    d = _check(request, run_id)
    promos = _j(d / "08_promos.json", {})
    if pid not in promos:
        raise HTTPException(404, f"No promo {pid}")
    trims = body.trims if body.action != "reject" else None
    if trims:
        durations = {e["number"]: e["duration"] for e in _j(d / "00_show.json", {}).get("episodes", [])}
        errs = _trim_errors(promos[pid], trims, _atoms(d), durations)
        if errs:
            raise HTTPException(422, "; ".join(errs))
    rec = {"promo_id": pid, "action": body.action, "reason": body.reason,
           "trims": [t.model_dump() for t in trims] if trims else None,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with (d / "decisions.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def _with_trims(p: dict, dec: dict | None) -> Promo:
    promo = Promo(**p)
    if dec and dec.get("trims") and len(dec["trims"]) == len(promo.segments):
        promo.segments = [
            Segment(episode=s.episode, start=float(t["start"]), end=float(t["end"]), start_tc=fmt_tc(float(t["start"])),
                    end_tc=fmt_tc(float(t["end"])), atom_ids=s.atom_ids)
            for s, t in zip(promo.segments, dec["trims"])
        ]
    return promo


@app.get("/api/runs/{run_id}/export/{name}")
def export(run_id: str, name: str, request: Request) -> Response:
    # No query-string credentials: URLs land in logs and history. The UI downloads with fetch + Blob.
    d = _check(request, run_id)
    if name == "promos.csv":
        return FileResponse(d / "promos.csv", media_type="text/csv", filename=f"{run_id}-promos.csv")
    if name.endswith(".edl"):
        pid = name[:-4]
        promos = _j(d / "08_promos.json", {})
        if pid not in promos:
            raise HTTPException(404, f"No promo {pid}")
        cfg = load_config(CONFIG) if CONFIG.exists() else None
        text = edl(_with_trims(promos[pid], _decisions(d).get(pid)), cfg.fps if cfg else 30)
        return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{pid}.edl"'})
    raise HTTPException(404, "Unknown export")


# --------------------------------------------------------------------------- models + new runs


@app.get("/api/models")
def models() -> dict:
    cfg = load_config(CONFIG)
    known = sorted(set(load_prices().keys()) | set(cfg.models.values()))
    return {"roles": cfg.models, "options": known, "server_key_max_episodes": SERVER_KEY_MAX_EPISODES}


def _allowed_providers(cfg) -> set[str]:
    extra = {p.strip() for p in os.environ.get("COLDOPEN_PROVIDERS", "").split(",") if p.strip()}
    return {m.split("/", 1)[0] for m in cfg.models.values()} | {"gemini", "groq"} | extra


@app.post("/api/runs")
async def create_run(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(None, max_length=120),
    provider: str = Form("live"),
    miner: str | None = Form(None, max_length=160),
    judge: str | None = Form(None, max_length=160),
    classifier: str | None = Form(None, max_length=160),
    api_key: str | None = Form(None, max_length=300),
    max_episodes: int | None = Form(None, ge=1, le=500),
) -> dict:
    from coldopen.llm.provider import LLMClient, LiveProvider

    if provider not in {"live", "mock"}:
        raise HTTPException(422, "provider must be live or mock")
    who = sec.principal(request)
    api_key = (api_key or "").strip() or None
    if not who.demo and not (api_key and provider == "live"):
        raise HTTPException(401, "Enter the access code, or use your own API key with real models")
    ip = sec.client_ip(request)
    wait = sec.CREATE_THROTTLE.retry_after(ip)
    if wait:
        raise HTTPException(429, f"Too many runs from here. Try again in {wait} s.", headers={"Retry-After": str(wait)})
    if sum(not j["done"] for j in JOBS.values()) >= MAX_JOBS:
        raise HTTPException(429, "The server is busy with other runs. Try again in a few minutes.",
                            headers={"Retry-After": "60"})

    cfg = load_config(CONFIG)
    allowed = _allowed_providers(cfg)
    for role, val in (("miner", miner), ("judge", judge), ("classifier", classifier)):
        if val:
            val = val.strip()
            if not sec.MODEL_RE.match(val) or val.split("/", 1)[0] not in allowed:
                raise HTTPException(422, f"{role}: use provider/model with a provider in {sorted(allowed)}")
            cfg.models[role] = val
    if provider == "live" and any("TODO" in m for m in cfg.models.values()):
        raise HTTPException(422, "Choose a real model for every role")
    clean_title = " ".join((title or "").split())[:120] or None
    capped = provider == "live" and not api_key and SERVER_KEY_MAX_EPISODES > 0
    if capped:
        max_episodes = min(max_episodes or SERVER_KEY_MAX_EPISODES, SERVER_KEY_MAX_EPISODES)

    data = await sec.read_upload(file)
    work = Path(tempfile.mkdtemp(prefix="coldopen-"))
    src = work / "input.zip"
    src.write_bytes(data)
    try:
        sec.inspect_zip(src)
    except HTTPException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    sec.CREATE_THROTTLE.hit(ip)

    job = secrets.token_hex(12)
    run_id = f"upload-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{job[:4]}"
    run_token = secrets.token_urlsafe(24)
    session, exp = sec.SIGNER.issue(f"run:{run_id}")
    _forget_old_jobs()
    JOBS[job] = {"run_id": run_id, "events": [], "done": False}
    events: list[dict] = JOBS[job]["events"]  # append-only; every subscriber reads it by index
    if api_key:
        keys = {p: api_key for p in {m.split("/", 1)[0] for m in cfg.models.values()}}
        prov: Any = LiveProvider(api_keys=keys)
    else:
        prov = provider

    async def go():
        from coldopen.pipeline import Pipeline

        out = RUNS / run_id
        try:
            client = LLMClient(cfg, provider=prov, prices=load_prices(), run_dir=out)

            def emit(e: dict) -> None:
                events.append(e)

            out.mkdir(parents=True, exist_ok=True)
            (out / ".owner_token").write_text(sec.hash_token(run_token))
            await Pipeline(src, out, cfg, client, title=clean_title, max_episodes=max_episodes, emit=emit).run()
        except ParseError as e:
            events.append({"type": "error", "message": sec.redact(f"Parse error: {e}", api_key)})
        except ProviderLimitError as e:
            events.append({"type": "limit", "message": sec.redact(
                f"Paused: the {e.human()}. Finished calls are cached, so starting the same run later continues "
                "from here. Free API tiers allow only so many requests per day; a run with your own key avoids "
                "the shared limit.", api_key)})
        except asyncio.CancelledError:
            events.append({"type": "error", "message": "The run was cancelled (server shutting down?)"})
            raise
        except Exception as e:  # noqa: BLE001 - report to the UI; never echo keys
            events.append({"type": "error", "message": sec.redact(f"{type(e).__name__}: {str(e)[:300]}", api_key)})
        finally:
            JOBS[job]["done"] = True
            events.append({"type": "end", "run_id": run_id})
            shutil.rmtree(work, ignore_errors=True)

    # Keep a reference: an unreferenced task can be garbage-collected mid-run.
    JOBS[job]["task"] = asyncio.create_task(go())
    log.info("run %s started client=%s via=%s", run_id, sec.fingerprint(ip), "own-key" if api_key else who.via)
    return {"job_id": job, "run_id": run_id, "run_token": run_token, "session": session, "expires_at": exp,
            "max_episodes": max_episodes, "capped": capped}


def _forget_old_jobs(keep: int = 50) -> None:
    done = [k for k, j in JOBS.items() if j["done"]]
    for k in done[: max(0, len(done) - keep)]:
        JOBS.pop(k, None)


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    if not sec.principal(request).can_read(job["run_id"]):
        raise HTTPException(401, "Access code required")

    async def stream():
        i = 0
        while True:
            if await request.is_disconnected():
                return
            while i < len(job["events"]):
                e = job["events"][i]
                i += 1
                yield {"data": json.dumps(e)}
                if e.get("type") == "end":
                    return
            await asyncio.sleep(0.1)

    return EventSourceResponse(stream())


# --------------------------------------------------------------------------- frontend


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        f = (WEB / path).resolve()
        if path and f.is_file() and f.is_relative_to(WEB.resolve()):  # no ../ escapes
            return FileResponse(f)
        return FileResponse(WEB / "index.html")
