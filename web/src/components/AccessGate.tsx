import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { unlock } from "../lib/api";
import { useAuth } from "../lib/auth";

/**
 * The first screen a visitor sees, set as a screenplay title page.
 * The code is exchanged for a short-lived session token and then dropped;
 * after too many wrong tries the server locks this client out for a while,
 * and the form counts down instead of letting people hammer it.
 */
export function AccessGate({ onDone, note, compact = false, initialError }: { onDone?: () => void; note?: string; compact?: boolean; initialError?: string }) {
  const { hint } = useAuth();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(initialError ? `The sign-in link did not work: ${initialError}` : null);
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);

  useEffect(() => {
    if (wait <= 0) return;
    const t = window.setTimeout(() => setWait((w) => w - 1), 1000);
    return () => window.clearTimeout(t);
  }, [wait]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const value = code.trim();
    if (!value) {
      setError("Enter the access code you were given.");
      return;
    }
    setBusy(true);
    const res = await unlock(value);
    setBusy(false);
    if (res) {
      setError(res.retryAfter ? "Too many wrong codes from this network." : res.error);
      if (res.retryAfter) setWait(res.retryAfter);
      return;
    }
    setCode("");
    onDone?.();
  }

  const locked = wait > 0;
  const form = (
    <form onSubmit={submit} className={compact ? "mt-5 w-full" : "mx-auto mt-10 w-full max-w-sm"} noValidate>
      <label htmlFor="code" className="block font-cond text-xs text-fog">Access code</label>
      <div className="mt-1 flex gap-2">
        <input
          id="code"
          name="access-code"
          type="password"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          autoFocus={!compact}
          maxLength={200}
          value={code}
          disabled={locked}
          onChange={(e) => setCode(e.target.value)}
          aria-invalid={!!error}
          aria-describedby={[error ? "code-error" : "", hint ? "code-hint" : ""].filter(Boolean).join(" ") || undefined}
          className="min-w-0 flex-1 rounded-chip border border-rule bg-bay px-3 py-2.5 font-script text-md disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || locked}
          className="shrink-0 rounded-chip bg-tungsten px-4 py-2.5 text-sm font-semibold text-ink transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {busy ? "Checking…" : locked ? `Wait ${wait} s` : "Open"}
        </button>
      </div>
      <p id="code-error" role="alert" className="mt-2 min-h-5 text-sm text-rec-ink">
        {error}
        {locked && ` Try again in ${wait} s.`}
      </p>
      {hint && <p id="code-hint" className="mt-1 text-sm text-fog">{hint}</p>}
      {!compact && (
        <p className="mt-4 text-sm text-fog">
          Or <Link to="/" className="text-bone underline decoration-rule underline-offset-4 hover:decoration-bone">open the sample show</Link>, which needs no code,
          or <Link to="/new" className="text-bone underline decoration-rule underline-offset-4 hover:decoration-bone">run your own show</Link> with your own model key.
        </p>
      )}
    </form>
  );

  if (compact) {
    return (
      <div className="p-5 sm:p-6">
        <h3 className="font-script text-lg uppercase tracking-[0.06em]">Client runs</h3>
        <p className="mt-2 text-sm text-fog">{note}</p>
        {form}
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-[70vh] max-w-xl flex-col justify-center px-2 py-10 sm:px-6">
      <div className="font-script">
        <p className="text-sm text-fog">FADE IN:</p>
        <h1 className="mt-10 text-center text-3xl uppercase tracking-[0.08em] underline decoration-1 underline-offset-[10px]">
          Cold Open
        </h1>
        <p className="mt-6 text-center text-md">a launch-slate finder for microdrama scripts</p>
        <p className="measure mx-auto mt-8 max-w-[34ch] text-center text-sm leading-relaxed text-fog">
          {note ?? "The demo runs hold a client's scripts, so they sit behind an access code."}
        </p>
      </div>

      {form}

      <p className="mt-12 text-right font-script text-sm text-fog">CUT TO:</p>
    </div>
  );
}
