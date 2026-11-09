"""API: access control, run data, decisions, exports, upload safety and a mock upload job."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coldopen.config import Config
from coldopen.llm.mockbrain import MockBrain
from coldopen.llm.provider import LLMClient
from coldopen.pipeline import Pipeline
from tests.helpers import FIX

CFG = Config(models={"miner": "m/a", "judge": "m/b", "classifier": "m/c"}, rpm=100000, concurrency=8)


@pytest.fixture
def api(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    out = runs / "mini"
    asyncio.run(Pipeline(FIX / "mini_show", out, CFG, LLMClient(CFG, provider=MockBrain(), prices={}, run_dir=out)).run())
    import coldopen.api.security as sec
    import coldopen.api.server as server

    monkeypatch.setattr(server, "RUNS", runs)
    monkeypatch.setenv("DEMO_ACCESS_CODE", "letmein")
    # Fresh throttles per test so lockouts never leak between tests.
    monkeypatch.setattr(sec, "LOGIN_THROTTLE", sec.Throttle(per_key=8, window=900, global_limit=100))
    monkeypatch.setattr(sec, "CREATE_THROTTLE", sec.Throttle(per_key=6, window=3600, global_limit=60))
    # One client session = one event loop, like uvicorn; background jobs survive between requests.
    with TestClient(server.app) as client:
        yield client, {"X-Access-Code": "letmein"}


def test_access_code_required(api):
    c, h = api
    assert c.get("/api/runs/mini/slate").status_code == 401
    assert c.get("/api/runs").json() == []
    assert c.post("/api/access", headers={"X-Access-Code": "nope"}).status_code == 401
    assert [r["id"] for r in c.get("/api/runs", headers=h).json()] == ["mini"]


def test_show_slate_promo(api):
    c, h = api
    show = c.get("/api/runs/mini/show", headers=h).json()
    assert len(show["ekg"]) == 3 and show["bible"]["characters"]
    sl = c.get("/api/runs/mini/slate", headers=h).json()
    hero = sl["slate"]["hero"]
    assert sl["promos"][0]["id"] == hero and sl["promos"][0]["slot"] == "hero"
    pr = c.get(f"/api/runs/mini/promos/{hero}", headers=h).json()
    assert pr["context"][0]["atoms"] and "perceived" in pr["context"][0]["atoms"][0]


def test_decision_persists_and_trims_edl(api):
    c, h = api
    hero = c.get("/api/runs/mini/slate", headers=h).json()["slate"]["hero"]
    seg = c.get(f"/api/runs/mini/promos/{hero}", headers=h).json()["promo"]["segments"]
    trims = [{"episode": s["episode"], "start": s["start"] + 1.0, "end": s["end"]} for s in seg]
    r = c.post(f"/api/runs/mini/promos/{hero}/decision", headers=h, json={"action": "approve_paid", "trims": trims})
    assert r.status_code == 200
    assert c.get("/api/runs/mini/slate", headers=h).json()["decisions"][hero]["action"] == "approve_paid"
    edl = c.get(f"/api/runs/mini/export/{hero}.edl", headers=h).text
    assert edl.startswith(f"TITLE: {hero}") and "FCM: NON-DROP FRAME" in edl
    assert c.post(f"/api/runs/mini/promos/{hero}/decision", headers=h, json={"action": "bogus"}).status_code == 422
    assert c.get("/api/runs/mini/export/promos.csv", headers=h).status_code == 200
    # Credentials in the query string are ignored on purpose (they end up in logs).
    assert c.get("/api/runs/mini/export/promos.csv", params={"code": "letmein"}).status_code == 401


def test_server_rejects_trims_that_break_hard_rules(api):
    c, h = api
    hero = c.get("/api/runs/mini/slate", headers=h).json()["slate"]["hero"]
    seg = c.get(f"/api/runs/mini/promos/{hero}", headers=h).json()["promo"]["segments"]
    url = f"/api/runs/mini/promos/{hero}/decision"
    too_short = [{"episode": s["episode"], "start": s["start"], "end": s["start"] + 2.0} for s in seg]
    r = c.post(url, headers=h, json={"action": "approve_paid", "trims": too_short})
    assert r.status_code == 422 and "20-60" in r.json()["detail"]
    late = [{"episode": s["episode"], "start": s["start"] + 8.0, "end": s["end"]} for s in seg]
    assert c.post(url, headers=h, json={"action": "approve_paid", "trims": late}).status_code == 422
    wrong_count = [{"episode": seg[0]["episode"], "start": 0, "end": 30}] * (len(seg) + 1)
    assert c.post(url, headers=h, json={"action": "approve_paid", "trims": wrong_count}).status_code == 422
    assert c.post(url, headers=h, json={"action": "reject", "reason": "x" * 501}).status_code == 422


def test_session_token_flow(api):
    c, _ = api
    r = c.post("/api/access", json={"code": "letmein"})
    assert r.status_code == 200
    tok = r.json()["token"]
    bearer = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/runs/mini/slate", headers=bearer).status_code == 200
    assert c.get("/api/session", headers=bearer).json()["demo"] is True
    assert c.get("/api/session").json()["demo"] is False
    # A tampered signature and a garbage token both fail closed.
    assert c.get("/api/runs/mini/slate", headers={"Authorization": f"Bearer {tok[:-2]}xx"}).status_code == 401
    assert c.get("/api/runs/mini/slate", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_rotating_the_code_revokes_sessions(api, monkeypatch):
    c, _ = api
    tok = c.post("/api/access", json={"code": "letmein"}).json()["token"]
    monkeypatch.setenv("DEMO_ACCESS_CODE", "a-new-code")
    assert c.get("/api/runs/mini/slate", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_expired_token_is_refused(api):
    import coldopen.api.security as sec

    c, _ = api
    tok, _ = sec.SIGNER.issue("demo", ttl=-1)
    assert c.get("/api/runs/mini/slate", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_wrong_codes_lock_out(api):
    c, _ = api
    for _ in range(8):
        assert c.post("/api/access", json={"code": "guess"}).status_code == 401
    r = c.post("/api/access", json={"code": "letmein"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_security_headers_and_no_store(api):
    c, h = api
    r = c.get("/api/runs/mini/slate", headers=h)
    assert r.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_bad_run_ids_are_rejected(api):
    c, h = api
    assert c.get("/api/runs/..%2F..%2Fetc/slate", headers=h).status_code in (400, 404)
    assert c.get("/api/runs/.hidden/slate", headers=h).status_code == 400


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_upload_safety(api):
    c, h = api

    def post(data: bytes, **form):
        return c.post("/api/runs", files={"file": ("s.zip", data, "application/zip")},
                      data={"provider": "mock", **form}, headers=h)

    assert post(b"not a zip").status_code == 422
    assert post(_zip({"../evil.txt": b"x"})).status_code == 422
    assert post(_zip({"notes.md": b"x"})).status_code == 422
    assert "zip bomb" in post(_zip({"E1.txt": b"0" * 1_000_000})).json()["detail"]
    assert post(_zip({"E1.txt": b"x"}), miner="evil/../../x").status_code == 422
    # No code and no own key: refused, even for the offline stand-ins.
    r = c.post("/api/runs", files={"file": ("s.zip", _zip({"E1.txt": b"x"}), "application/zip")},
               data={"provider": "mock"})
    assert r.status_code == 401


def test_upload_runs_a_mock_job(api):
    c, h = api
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in (FIX / "mini_show").glob("*.txt"):
            z.write(f, f"Mini/{f.name}")
    r = c.post("/api/runs", files={"file": ("show.zip", buf.getvalue(), "application/zip")},
               data={"provider": "mock"}, headers=h)
    assert r.status_code == 200, r.text
    job = r.json()
    assert c.get(f"/api/jobs/{job['job_id']}/events").status_code == 401
    with c.stream("GET", f"/api/jobs/{job['job_id']}/events", headers=h) as s:
        events = [json.loads(line[5:]) for line in s.iter_lines() if line.startswith("data:")]
    assert events[-1]["type"] == "end" and any(e.get("stage") == "slate" for e in events)
    assert c.get(f"/api/runs/{job['run_id']}/slate", headers={"X-Run-Token": job["run_token"]}).status_code == 200
    own = {"Authorization": f"Bearer {job['session']}"}
    assert c.get(f"/api/runs/{job['run_id']}/slate", headers=own).status_code == 200
    # The run-scoped session opens that run only, and the token is stored hashed.
    assert c.get("/api/runs/mini/slate", headers=own).status_code == 401
    import coldopen.api.server as server

    stored = (server.RUNS / job["run_id"] / ".owner_token").read_text()
    assert stored.startswith("sha256:") and job["run_token"] not in stored


def test_server_reads_dotenv_at_import():
    """The access code must be active from the first request, not after the first config read."""
    import coldopen.api.server as server

    assert "load_dotenv(override=False)" in Path(server.__file__).read_text()


def test_public_sample_runs_open_without_a_code(api, monkeypatch):
    c, h = api
    monkeypatch.setenv("COLDOPEN_PUBLIC_RUNS", "mini")
    assert c.get("/api/runs/mini/slate").status_code == 200
    assert [r["public"] for r in c.get("/api/runs").json()] == [True]
    assert c.get("/api/health").json()["public_runs"] == ["mini"]
    # Writing decisions on a public sample is allowed (it is invented data); the session still reports locks.
    monkeypatch.setenv("COLDOPEN_PUBLIC_RUNS", "")
    assert c.get("/api/session").json()["locked_runs"] == 1


def test_hosted_without_a_code_fails_closed(api, monkeypatch):
    """A deploy that forgot DEMO_ACCESS_CODE must not publish the client's runs."""
    c, _ = api
    monkeypatch.delenv("DEMO_ACCESS_CODE")
    monkeypatch.setenv("COLDOPEN_REQUIRE_CODE", "1")
    assert c.get("/api/health").json()["access_code_required"] is True
    assert c.get("/api/runs/mini/slate").status_code == 401
    assert c.post("/api/access", json={"code": "anything"}).status_code == 401
    monkeypatch.setenv("COLDOPEN_PUBLIC_RUNS", "mini")  # invented samples can still be public
    assert c.get("/api/runs/mini/slate").status_code == 200
