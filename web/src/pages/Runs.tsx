import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AccessGate } from "../components/AccessGate";
import { get, isUnlocked } from "../lib/api";
import { useAuth } from "../lib/auth";
import { usd } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { RunListItem } from "../lib/types";

/**
 * The landing page. Anyone can open the invented sample show straight away; the
 * client's runs sit behind the access code, which is asked for inline (or arrives
 * by itself through a #code= link) so nobody has to guess where to log in.
 */
export function RunsPage({ linkError }: { linkError?: string | null }) {
  const { required } = useAuth();
  const unlocked = !required || isUnlocked();
  const { data, error } = useApi<RunListItem[]>("/api/runs");
  const [locked, setLocked] = useState(0);
  useEffect(() => {
    get<{ locked_runs: number }>("/api/session", true).then((s) => setLocked(s.locked_runs)).catch(() => setLocked(0));
  }, [unlocked]);

  const runs = data ?? [];
  return (
    <div className="grid gap-10 lg:grid-cols-12">
      <section className="lg:col-span-7" aria-labelledby="runs-h">
        <h2 id="runs-h" className="text-xl font-semibold">Runs</h2>
        <p className="mt-1 text-sm text-fog">Each run reads one show and proposes a launch slate of promos.</p>
        {error && <p className="mt-4 text-rec-ink">{error.message}</p>}
        {data && runs.length === 0 && (
          <div className="mt-6 rounded-panel border border-dashed border-rule p-8 text-center">
            <p>{unlocked ? "No runs yet." : "Unlock with the access code to see the runs."}</p>
            <Link to="/new" className="mt-3 inline-block rounded-chip bg-tungsten px-4 py-2 text-sm font-semibold text-ink">
              Start a run
            </Link>
          </div>
        )}
        <ul className="mt-6 flex flex-col border-t border-rule">
          {runs.map((r) => (
            <li key={r.id}>
              <Link
                to={`/run/${r.id}/map`}
                className="grid gap-2 border-b border-rule px-1 py-4 transition-colors hover:bg-console/70 sm:grid-cols-[1fr_auto] sm:px-3"
              >
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 font-semibold">
                    <span className="truncate">{r.show}</span>
                    {r.public && (
                      <span className="rounded-chip border border-teal/70 px-1.5 py-0.5 font-cond text-xs font-normal text-teal">
                        Sample show, open to everyone
                      </span>
                    )}
                  </p>
                  <p className="font-cond text-xs text-fog">
                    {r.id}, {new Date(r.date).toLocaleString()}
                    {r.models.provider !== "live" ? `, ${r.models.provider === "mock" ? "offline stand-in models" : r.models.provider}` : ""}
                  </p>
                </div>
                <div className="flex gap-6 font-cond text-sm tnum">
                  <span><span className="text-fog">Promos </span>{r.valid}</span>
                  <span><span className="text-fog">Calls </span>{r.llm_calls}</span>
                  <span><span className="text-fog">Cost </span>{usd(r.cost_usd)}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      {!unlocked && (
        <aside className="lg:col-span-5" aria-label="Unlock the client runs">
          <div className="rounded-panel border border-rule bg-console">
            <AccessGate
              compact
              initialError={linkError ?? undefined}
              note={
                locked > 0
                  ? `${locked} more run${locked === 1 ? "" : "s"} on the client's real scripts ${locked === 1 ? "needs" : "need"} the access code.`
                  : "The client's runs need the access code."
              }
            />
          </div>
        </aside>
      )}
    </div>
  );
}
