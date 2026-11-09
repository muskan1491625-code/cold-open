/**
 * API client and session handling.
 *
 * The access code is typed once, traded for a short-lived signed token
 * (POST /api/access), and then forgotten: the page never keeps the code.
 * Tokens live in sessionStorage, so they survive a reload but die with the tab,
 * and they expire on their own (8 h). Every call sends `Authorization: Bearer`;
 * nothing secret ever goes into a URL (URLs end up in logs and history), so
 * downloads and the progress stream use fetch with headers.
 */

type Session = { token: string; exp: number };

const DEMO_KEY = "coldopen.session.v1";
const RUNS_KEY = "coldopen.runs.v1";
const listeners = new Set<() => void>();

function read<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null; // private mode or blocked storage: fall back to memory only
  }
}
function write(key: string, value: unknown) {
  try {
    if (value == null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* memory-only is fine */
  }
}

let demo: Session | null = read<Session>(DEMO_KEY);
let runs: Record<string, Session> = read<Record<string, Session>>(RUNS_KEY) ?? {};
let expiryTimer: number | undefined;

const live = (s: Session | null | undefined): s is Session => !!s && s.exp * 1000 > Date.now() + 5_000;

function changed() {
  cache.clear();
  window.clearTimeout(expiryTimer);
  if (live(demo)) {
    // Lock the page the moment the session expires instead of failing call by call.
    expiryTimer = window.setTimeout(signOut, Math.min(demo.exp * 1000 - Date.now(), 2 ** 31 - 1));
  }
  listeners.forEach((l) => l());
}

export const isUnlocked = () => live(demo);
export const sessionExpiry = () => (live(demo) ? new Date(demo.exp * 1000) : null);
export const hasRunAccess = (runId: string) => live(demo) || live(runs[runId]);

export function onAuthChange(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function signOut() {
  demo = null;
  runs = {};
  write(DEMO_KEY, null);
  write(RUNS_KEY, null);
  changed();
}

function rememberRun(runId: string, token: string, exp: number) {
  runs = { ...runs, [runId]: { token, exp } };
  write(RUNS_KEY, runs);
  changed();
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter?: number) {
    super(message);
  }
}

function tokenFor(path: string): string | null {
  if (live(demo)) return demo.token;
  const m = path.match(/^\/api\/runs\/([^/?]+)/);
  const s = m ? runs[decodeURIComponent(m[1])] : undefined;
  return live(s) ? s.token : null;
}

function headers(path: string, extra: Record<string, string> = {}) {
  const h: Record<string, string> = { ...extra };
  const t = tokenFor(path);
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

const cache = new Map<string, Promise<unknown>>();

async function fail(r: Response, sentToken: boolean): Promise<never> {
  const body = await r.json().catch(() => ({}));
  if (r.status === 401 && sentToken && live(demo)) signOut(); // the code was rotated or the token was revoked
  const retry = Number(r.headers.get("Retry-After")) || undefined;
  throw new ApiError(r.status, typeof body.detail === "string" ? body.detail : r.statusText, retry);
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const h = headers(path, (init.headers as Record<string, string>) ?? {});
  const r = await fetch(path, { ...init, headers: h, credentials: "omit" });
  return r.ok ? ((await r.json()) as T) : fail(r, !!h.Authorization);
}

export function get<T>(path: string, fresh = false): Promise<T> {
  if (!fresh && cache.has(path)) return cache.get(path) as Promise<T>;
  const p = call<T>(path);
  p.catch(() => cache.delete(path));
  cache.set(path, p);
  return p;
}

export function post<T>(path: string, body: unknown): Promise<T> {
  return call<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export function invalidate(prefix: string) {
  for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k);
}

/** Trade the code for a session. Resolves with an error message, or null on success. */
export async function unlock(code: string): Promise<{ error: string; retryAfter?: number } | null> {
  try {
    const r = await fetch("/api/access", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
      credentials: "omit",
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const retryAfter = Number(r.headers.get("Retry-After")) || undefined;
      return { error: typeof body.detail === "string" ? body.detail : "That code did not match.", retryAfter };
    }
    demo = { token: body.token, exp: body.expires_at };
    write(DEMO_KEY, demo);
    changed();
    return null;
  } catch {
    return { error: "Could not reach the server. Check your connection and try again." };
  }
}

/** Download an export with the session header, then hand the file to the browser. */
export async function download(runId: string, name: string, filename = name) {
  const path = `/api/runs/${encodeURIComponent(runId)}/export/${encodeURIComponent(name)}`;
  const h = headers(path);
  const r = await fetch(path, { headers: h, credentials: "omit" });
  if (!r.ok) await fail(r, !!h.Authorization);
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

export type Created = { job_id: string; run_id: string; run_token: string; session: string; expires_at: number };

export async function upload(form: FormData): Promise<Created> {
  const job = await call<Created>("/api/runs", { method: "POST", body: form });
  rememberRun(job.run_id, job.session, job.expires_at); // so someone using their own key can open their run
  return job;
}

/**
 * Server-sent events over fetch (EventSource cannot send an Authorization
 * header). Calls `onEvent` for each `data:` message until the stream ends.
 */
export async function streamJob(job: Created, onEvent: (e: Record<string, unknown>) => void, signal?: AbortSignal) {
  const h = live(demo) ? { Authorization: `Bearer ${demo.token}` } : { Authorization: `Bearer ${job.session}` };
  const r = await fetch(`/api/jobs/${job.job_id}/events`, { headers: { ...h, Accept: "text/event-stream" }, signal });
  if (!r.ok || !r.body) await fail(r, true);
  const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value.replace(/\r\n/g, "\n");
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = block
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trimStart())
        .join("\n");
      if (data) onEvent(JSON.parse(data));
    }
  }
}

export type Health = { ok: boolean; access_code_required: boolean; public_runs: string[]; code_hint: string };

/**
 * One-click sign-in: a link ending in #code=... unlocks the tab, then the code is wiped
 * from the address bar. The part after # is never sent to any server, so it stays out of
 * logs; the code itself is still swapped for a short-lived token like a typed one.
 */
export async function unlockFromLink(): Promise<string | null> {
  const m = window.location.hash.match(/(?:^#|&)code=([^&]+)/);
  if (!m) return null;
  const code = decodeURIComponent(m[1]);
  history.replaceState(null, "", window.location.pathname + window.location.search);
  if (isUnlocked()) return null;
  const res = await unlock(code);
  return res ? res.error : null;
}

export function health(): Promise<Health> {
  return fetch("/api/health", { credentials: "omit" }).then((r) => r.json());
}
