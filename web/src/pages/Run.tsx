import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { isUnlocked, streamJob, unlock, upload } from "../lib/api";
import { useAuthRequired } from "../lib/auth";
import { usd } from "../lib/format";
import { useApi } from "../lib/hooks";

const STAGES = ["parse", "bible", "lexicon", "ekg", "mine", "snap", "viewer", "risk", "slate"];
const LABEL: Record<string, string> = {
  parse: "Parse", bible: "Bible", lexicon: "Lexicon", ekg: "EKG", mine: "Mining", snap: "Validate",
  viewer: "Cold Viewer", risk: "Risk", slate: "Slate",
};
type Ev = { type: string; stage?: string; status?: string; done?: number; total?: number; message?: string; llm_calls?: number; tokens_in?: number; tokens_out?: number; cost_usd?: number | null; elapsed?: number; run_id?: string };

export function RunPage() {
  const models = useApi<{ roles: Record<string, string>; options: string[]; server_key_max_episodes: number }>("/api/models");
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [provider, setProvider] = useState<"live" | "mock">("live");
  const [apiKey, setApiKey] = useState("");
  const [code, setCode] = useState("");
  const required = useAuthRequired();
  const unlocked = !required || isUnlocked();
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [paused, setPaused] = useState<string | null>(null);
  const [events, setEvents] = useState<Record<string, Ev>>({});
  const [runId, setRunId] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (models.data) setRoles(models.data.roles);
  }, [models.data]);

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files[0];
    if (f) pick(f);
  }
  function pick(f: File) {
    if (!f.name.toLowerCase().endsWith(".zip")) {
      setError("Choose a .zip of episode .txt files (zip the folder first).");
      return;
    }
    setError(null);
    setFile(f);
  }

  async function start(e: FormEvent) {
    e.preventDefault();
    if (!file) {
      setError("Choose a .zip of episode scripts first.");
      return;
    }
    if (!unlocked && !(provider === "live" && apiKey.trim())) {
      if (!code.trim()) {
        setError(provider === "mock" ? "The offline stand-ins need the access code." : "Enter the access code, or your own API key.");
        return;
      }
      const res = await unlock(code.trim());
      if (res) {
        setError(res.error);
        return;
      }
      setCode("");
    }
    const form = new FormData();
    form.append("file", file);
    form.append("provider", provider);
    if (title) form.append("title", title);
    if (provider === "live") {
      Object.entries(roles).forEach(([k, v]) => form.append(k, v));
      if (apiKey) form.append("api_key", apiKey);
    }
    setBusy(true);
    setError(null);
    setEvents({});
    setWarnings([]);
    setPaused(null);
    setDone(false);
    try {
      const job = await upload(form);
      setRunId(job.run_id);
      setApiKey(""); // the key is used for this job only; drop it from memory
      await streamJob(job, (raw) => {
        const ev = raw as Ev;
        if (ev.type === "stage" && ev.stage) setEvents((x) => ({ ...x, [ev.stage!]: ev }));
        if (ev.type === "error") setError(ev.message ?? "The run failed.");
        if (ev.type === "warning" && ev.message) setWarnings((w) => [...w, ev.message!]);
        if (ev.type === "limit") setPaused(ev.message ?? "Paused by an API limit.");
        if (ev.type === "end") setDone(true);
      });
      setBusy(false);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const last = Object.values(events).sort((a, b) => (b.elapsed ?? 0) - (a.elapsed ?? 0))[0];

  return (
    <div className="grid gap-8 lg:grid-cols-12">
      <form onSubmit={start} className="flex flex-col gap-5 lg:col-span-6" aria-labelledby="new-h">
        <div>
          <h2 id="new-h" className="text-xl font-semibold">Start a run</h2>
          <p className="mt-1 text-sm text-fog">Upload a show and get a ranked launch slate with cut lists.</p>
        </div>

        <div
          onDragOver={(e) => (e.preventDefault(), setDrag(true))}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
          className={`flex flex-col items-center justify-center rounded-panel border border-dashed px-6 py-10 text-center transition-colors ${drag ? "border-tungsten bg-tungsten/5" : "border-rule"}`}
        >
          {file ? (
            <p>
              <span className="font-semibold">{file.name}</span>{" "}
              <span className="text-fog">({(file.size / 1024).toFixed(0)} KB)</span>
            </p>
          ) : (
            <p>Drop a folder of episode scripts to start a run.</p>
          )}
          <p className="mt-1 text-xs text-fog">Zip the folder of .txt files first; each file is one episode.</p>
          <button type="button" onClick={() => input.current?.click()} className="mt-4 rounded-chip border border-rule px-4 py-2 text-sm hover:bg-raised">
            {file ? "Choose a different file…" : "Choose a .zip…"}
          </button>
          <input ref={input} type="file" accept=".zip" className="sr-only" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} aria-label="Episode scripts zip" />
        </div>

        <label className="flex flex-col gap-1 text-sm">
          Show title <span className="text-xs text-fog">Optional; defaults to the folder name in the zip.</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} name="show-title" autoComplete="off" className="rounded-chip border border-rule bg-console px-3 py-2 text-md" />
        </label>

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm">Models</legend>
          <div className="flex gap-4 text-sm">
            {(["live", "mock"] as const).map((p) => (
              <label key={p} className="flex cursor-pointer items-center gap-2">
                <input type="radio" name="provider" checked={provider === p} onChange={() => setProvider(p)} />
                {p === "live" ? "Real models" : "Offline stand-ins (no keys, heuristic answers)"}
              </label>
            ))}
          </div>
          {provider === "live" && (
            <div className="grid gap-3 sm:grid-cols-3">
              {["miner", "judge", "classifier"].map((r) => (
                <label key={r} className="flex flex-col gap-1 text-sm">
                  <span>{r === "miner" ? "Miner" : r === "judge" ? "Cold Viewer" : "Classifier"}</span>
                  <input
                    list="model-options"
                    value={roles[r] ?? ""}
                    onChange={(e) => setRoles({ ...roles, [r]: e.target.value })}
                    spellCheck={false}
                    autoComplete="off"
                    className="rounded-chip border border-rule bg-console px-2 py-1.5 font-cond text-sm"
                  />
                </label>
              ))}
              <datalist id="model-options">{models.data?.options.map((o) => <option key={o} value={o} />)}</datalist>
            </div>
          )}
        </fieldset>

        {provider === "live" && (
          <label className="flex flex-col gap-1 text-sm">
            Use my own API key{" "}
            <span className="text-xs text-fog">
              Sent with this run only, never stored or logged. It must work for every model above (for example, three Gemini models with a Gemini key).
            </span>
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off" spellCheck={false} name="provider-key" className="rounded-chip border border-rule bg-console px-3 py-2 text-md" />
          </label>
        )}
        {unlocked ? (
          required && <p className="text-sm text-fog">Unlocked with the access code: the server's own keys are available.</p>
        ) : (
          <label className="flex flex-col gap-1 text-sm">
            Access code <span className="text-xs text-fog">Needed for the server's own keys and the offline stand-ins.</span>
            <input type="password" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" name="access-code" className="rounded-chip border border-rule bg-console px-3 py-2 text-md" />
          </label>
        )}

        {provider === "live" && !apiKey.trim() && (models.data?.server_key_max_episodes ?? 0) > 0 && (
          <p className="text-sm text-fog">
            Runs on the server's keys cover the first {models.data!.server_key_max_episodes} episodes: they share a free API tier
            with every visitor. Add your own key above to run a whole show.
          </p>
        )}
        {error && <p className="text-sm text-rec-ink" role="alert">{error}</p>}
        <button type="submit" disabled={busy} className="self-start rounded-chip bg-tungsten px-5 py-2.5 font-semibold text-ink hover:opacity-90 disabled:opacity-50">
          {busy ? "Running…" : "Start the run"}
        </button>
      </form>

      <section aria-labelledby="console-h" className="lg:col-span-6">
        <h2 id="console-h" className="mb-3 text-lg font-semibold">Progress</h2>
        <ol className="flex flex-col rounded-panel border border-rule bg-console" aria-live="polite">
          {STAGES.map((s) => {
            const e = events[s];
            const st = e?.status ?? "waiting";
            return (
              <li key={s} className="grid grid-cols-[1.25rem_7rem_1fr_auto] items-center gap-3 border-b border-rule/60 px-4 py-2.5 last:border-0">
                <span aria-hidden className={`h-2 w-2 rounded-full ${st === "done" ? "bg-teal" : st === "running" ? "bg-tungsten" : "bg-rule"}`} />
                <span className={st === "waiting" ? "text-fog" : ""}>{LABEL[s]}</span>
                <span className="truncate font-cond text-xs text-fog">
                  {st === "running" && e?.total ? `${e.done} of ${e.total}` : e?.message ?? (st === "waiting" ? "Waiting" : "")}
                </span>
                <span className="font-cond text-xs text-fog tnum">{e?.elapsed != null ? `${e.elapsed.toFixed(1)} s` : ""}</span>
              </li>
            );
          })}
        </ol>
        {last && (
          <p className="mt-3 font-cond text-sm text-fog tnum">
            {last.llm_calls} model calls, {((last.tokens_in ?? 0) + (last.tokens_out ?? 0)).toLocaleString()} tokens, cost {usd(last.cost_usd)}
          </p>
        )}
        {paused && (
          <div role="status" className="mt-4 border-l-2 border-tungsten bg-console px-4 py-3 text-sm">
            <p className="font-semibold">Paused by an API limit, not an error</p>
            <p className="mt-1 text-fog">{paused}</p>
            <p className="mt-2 text-fog">
              Meanwhile, try <span className="text-bone">Offline stand-ins</span> to see the whole pipeline, or open a finished
              run from the Runs list.
            </p>
          </div>
        )}
        {warnings.length > 0 && (
          <div role="status" className="mt-4 border-l-2 border-tungsten bg-console px-4 py-3 text-sm">
            <p className="font-semibold">Finished with partial results</p>
            <ul className="mt-1 list-disc pl-4 text-fog">
              {warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        )}
        {done && runId && !error && !paused && (
          <Link to={`/run/${runId}/map`} className="mt-4 inline-block rounded-chip bg-tungsten px-4 py-2 text-sm font-semibold text-ink">
            Open the tension map
          </Link>
        )}
      </section>
    </div>
  );
}
