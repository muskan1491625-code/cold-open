"""Access control and upload safety for the review API.

Why this shape:
- The demo runs contain a client's scripts, so reading them needs proof of the
  access code. The browser trades the code once for a short-lived signed session
  token (``POST /api/access``) and sends ``Authorization: Bearer``. The code
  itself is never kept by the page, never put in a URL (URLs end up in server
  logs and browser history) and never stored on disk.
- Tokens are stateless HMAC-SHA256 signatures. The signing key is derived from
  ``SESSION_SECRET`` *and* the access code, so rotating the code revokes every
  session at once without a database.
- No cookies on purpose: on a Hugging Face Space the app runs in a cross-site
  iframe, where browsers block or partition third-party cookies. A bearer
  header works the same in the iframe and on the direct URL, and is immune to
  CSRF because browsers never attach it automatically.
- Wrong codes are throttled per client and globally, so guessing is slow even
  if an attacker rotates IP addresses.
- Someone who runs a show with their own API key gets a run-scoped token for
  that run only. The server keeps a SHA-256 hash of it, never the token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException, Request

log = logging.getLogger("coldopen.security")

SESSION_TTL = int(os.environ.get("COLDOPEN_SESSION_TTL", 8 * 3600))  # one working day
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MODEL_RE = re.compile(r"^[a-z0-9_-]{2,30}/[A-Za-z0-9._:/-]{1,120}$")

# Upload limits: a whole microdrama season is well under 2 MB of text.
MAX_UPLOAD_BYTES = int(os.environ.get("COLDOPEN_MAX_UPLOAD_MB", 20)) * 1024 * 1024
MAX_ZIP_ENTRIES = 500
MAX_SCRIPTS = 200
MAX_SCRIPT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_RATIO = 100  # compressed-to-real size; text compresses ~4x, zip bombs ~1000x


def access_code() -> str | None:
    return os.environ.get("DEMO_ACCESS_CODE") or None


def require_code() -> bool:
    """Hosted images set COLDOPEN_REQUIRE_CODE=1: a missing DEMO_ACCESS_CODE then locks the
    client runs instead of opening them (fail closed). Locally, no code means open access."""
    return os.environ.get("COLDOPEN_REQUIRE_CODE", "") not in ("", "0", "false")


def code_required() -> bool:
    return bool(access_code()) or require_code()


def public_runs() -> set[str]:
    """Runs anyone may open without a code: only invented sample shows, never client data."""
    return {r.strip() for r in os.environ.get("COLDOPEN_PUBLIC_RUNS", "").split(",") if r.strip()}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    """Issues and verifies ``v1.<payload>.<signature>`` tokens."""

    def __init__(self, secret: str | None = None):
        # A random per-process secret is safe (sessions just end on restart);
        # SESSION_SECRET (or the Space-provided SPACE_SIGNING_SECRET) keeps them across restarts.
        raw = secret or os.environ.get("SESSION_SECRET") or os.environ.get("SPACE_SIGNING_SECRET") or secrets.token_hex(32)
        self._secret = hashlib.sha256(raw.encode()).digest()

    def _key(self) -> bytes:
        code = access_code() or ""
        return hmac.new(self._secret, b"code:" + code.encode(), hashlib.sha256).digest()

    def issue(self, scope: str, ttl: int = SESSION_TTL) -> tuple[str, int]:
        now = int(time.time())
        exp = now + ttl
        payload = _b64(json.dumps({"s": scope, "i": now, "e": exp, "n": secrets.token_hex(4)},
                                  separators=(",", ":")).encode())
        sig = _b64(hmac.new(self._key(), payload.encode(), hashlib.sha256).digest())
        return f"v1.{payload}.{sig}", exp

    def verify(self, token: str) -> dict | None:
        try:
            ver, payload, sig = token.split(".")
        except ValueError:
            return None
        if ver != "v1":
            return None
        want = _b64(hmac.new(self._key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        try:
            data = json.loads(_unb64(payload))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("e", 0) < time.time():
            return None
        return data


class Throttle:
    """Sliding-window failure counter, per key and across all keys."""

    def __init__(self, per_key: int, window: float, global_limit: int):
        self.per_key, self.window, self.global_limit = per_key, window, global_limit
        self._hits: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and q[0] <= now - self.window:
            q.popleft()

    def retry_after(self, key: str) -> int:
        """Seconds until ``key`` may try again; 0 when allowed."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key, deque())
            self._trim(q, now)
            self._trim(self._all, now)
            waits = []
            if len(q) >= self.per_key:
                waits.append(q[0] + self.window - now)
            if len(self._all) >= self.global_limit:
                waits.append(self._all[0] + self.window - now)
            return max(1, int(max(waits)) + 1) if waits else 0

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._hits.setdefault(key, deque()).append(now)
            self._all.append(now)
            if len(self._hits) > 10_000:  # bound memory under a spray of addresses
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - self.window}

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_ip(request: Request) -> str:
    """The caller's address.

    Behind a reverse proxy the socket peer is the proxy, so set
    COLDOPEN_PROXY_HOPS to the number of trusted proxies and we read the address
    that the outermost trusted proxy appended to X-Forwarded-For (right to
    left). Left-most entries are client-supplied and can be forged.
    """
    hops = int(os.environ.get("COLDOPEN_PROXY_HOPS", "0") or 0)
    if hops > 0:
        chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return request.client.host if request.client else "unknown"


def fingerprint(value: str) -> str:
    """Short non-reversible tag for logs (never log raw IPs or secrets)."""
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def owner_matches(stored: str, token: str) -> bool:
    if stored.startswith("sha256:"):
        return hmac.compare_digest(stored, hash_token(token))
    return hmac.compare_digest(stored, token)  # runs created before hashing


@dataclass
class Principal:
    """Who is calling: ``demo`` scope (holds the access code) and/or owned runs."""

    demo: bool = False
    runs: set[str] = field(default_factory=set)
    expires_at: int | None = None
    via: str = "none"

    def can_read(self, run_id: str) -> bool:
        return self.demo or run_id in self.runs or run_id in public_runs()


SIGNER = Signer()
LOGIN_THROTTLE = Throttle(per_key=8, window=15 * 60, global_limit=100)
CREATE_THROTTLE = Throttle(per_key=6, window=60 * 60, global_limit=60)


def check_code(request: Request, code: str | None) -> bool:
    """Constant-time code check with throttling. Raises 429 while locked out."""
    expected = access_code()
    if not expected:
        return not require_code()  # hosted without a code: nothing unlocks
    ip = client_ip(request)
    wait = LOGIN_THROTTLE.retry_after(ip)
    if wait:
        raise HTTPException(429, f"Too many wrong codes. Try again in {wait} s.", headers={"Retry-After": str(wait)})
    ok = bool(code) and hmac.compare_digest(code.encode(), expected.encode())
    if ok:
        LOGIN_THROTTLE.reset(ip)
    else:
        LOGIN_THROTTLE.hit(ip)
        log.warning("access denied client=%s", fingerprint(ip))
    return ok


def principal(request: Request) -> Principal:
    """Resolve every credential the request carries into one Principal."""
    p = Principal()
    if not code_required():
        p.demo, p.via = True, "open"  # local use without a code configured
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        data = SIGNER.verify(auth[7:].strip())
        if data:
            scope = str(data.get("s", ""))
            p.expires_at = int(data["e"])
            if scope == "demo":
                p.demo, p.via = True, "session"
            elif scope.startswith("run:"):
                p.runs.add(scope[4:])
                p.via = p.via if p.demo else "run-session"
    code = request.headers.get("x-access-code")
    if code and not p.demo:  # header form kept for scripts and the CLI
        if check_code(request, code):
            p.demo, p.via = True, "code"
    return p


def run_dir(runs_root: Path, run_id: str) -> Path:
    """Validate a run id and keep the path inside the runs folder."""
    if not RUN_ID_RE.match(run_id):
        raise HTTPException(400, "Bad run id")
    d = (runs_root / run_id).resolve()
    if d.parent != runs_root.resolve():
        raise HTTPException(400, "Bad run id")
    return d


def require_run(request: Request, runs_root: Path, run_id: str) -> Path:
    d = run_dir(runs_root, run_id)
    if not (d / "run_report.json").exists():
        raise HTTPException(404, f"No finished run named {run_id}")
    p = principal(request)
    if p.can_read(run_id):
        return d
    token = request.headers.get("x-run-token")
    owner = d / ".owner_token"
    if token and owner.exists() and owner_matches(owner.read_text().strip(), token):
        return d
    raise HTTPException(401, "Access code required")


async def read_upload(upload, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read an upload in chunks and stop as soon as it passes the size limit."""
    chunks, size = [], 0
    while chunk := await upload.read(1 << 20):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"The zip is larger than {limit // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


def inspect_zip(path: Path) -> int:
    """Reject archives that are malformed, encrypted, path-escaping or zip bombs.

    Returns the number of episode scripts (.txt files). Nothing is extracted to
    disk; the parser reads members in memory, bounded by these checks.
    """
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise HTTPException(422, "Upload a .zip of episode .txt files") from None
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise HTTPException(422, f"The zip has {len(infos)} entries; the limit is {MAX_ZIP_ENTRIES}")
        scripts, total = 0, 0
        for i in infos:
            name = i.filename
            if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
                raise HTTPException(422, f"Unsafe path in zip: {name[:80]}")
            if i.flag_bits & 0x1:
                raise HTTPException(422, "Encrypted zips are not supported")
            if i.is_dir() or not name.lower().endswith(".txt") or "__MACOSX" in name:
                continue
            if i.file_size > MAX_SCRIPT_BYTES:
                raise HTTPException(422, f"{Path(name).name} is over {MAX_SCRIPT_BYTES // 1024 // 1024} MB")
            if i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
                raise HTTPException(422, f"{Path(name).name} looks like a zip bomb")
            total += i.file_size
            scripts += 1
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(422, "The scripts are too large in total")
        if scripts == 0:
            raise HTTPException(422, "The zip has no .txt episode scripts")
        if scripts > MAX_SCRIPTS:
            raise HTTPException(422, f"The zip has {scripts} scripts; the limit is {MAX_SCRIPTS}")
        return scripts


def redact(text: str, *secrets_: str | None) -> str:
    """Remove secrets (and anything that looks like a provider key) from a message."""
    for s in secrets_:
        if s:
            text = text.replace(s, "[redacted]")
    return re.sub(r"(AIza[0-9A-Za-z_-]{20,}|gsk_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z_-]{20,}|hf_[0-9A-Za-z]{20,})",
                  "[redacted]", text)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    # The UI is fully self-hosted: no third-party scripts, fonts or calls.
    # Hugging Face shows a Space inside an iframe on huggingface.co.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'self' https://huggingface.co https://*.hf.space"
    ),
}
