import { useState } from "react";
import { Link } from "react-router-dom";
import { StructureIcon } from "../components/Bits";
import { useRun } from "../components/Shell";
import { HeatLegend, TensionMap } from "../components/TensionMap";
import { ENGINE, eps } from "../lib/format";
import type { Bible } from "../lib/types";

export function MapPage() {
  const { runId, show, slate } = useRun();
  const [focus, setFocus] = useState<string | null>(null);
  const durations = Object.fromEntries(show.episodes.map((e) => [e.number, e.duration]));
  return (
    <div className="grid gap-6 lg:grid-cols-12">
      <aside className="order-2 lg:order-1 lg:col-span-3">
        <BiblePanel bible={show.bible} />
      </aside>
      <section className="order-1 min-w-0 lg:order-2 lg:col-span-6" aria-labelledby="map-h">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="map-h" className="text-lg font-semibold">Tension across {show.episodes.length} episodes</h2>
          <p className="text-sm text-fog">Hover a row to read the line. Select a bracket to open it.</p>
        </div>
        <TensionMap
          runId={runId}
          ekg={show.ekg}
          durations={durations}
          promos={slate.promos}
          lines={show.lines}
          focusId={focus}
          onFocus={setFocus}
        />
        <div className="mt-3">
          <HeatLegend />
        </div>
      </section>
      <aside className="order-3 lg:col-span-3" aria-labelledby="rail-h">
        <h2 id="rail-h" className="mb-3 text-md font-semibold">Launch slate</h2>
        <ol className="flex flex-col gap-1">
          {[slate.slate.hero, ...slate.slate.challengers].map((id, i) => {
            const p = slate.promos.find((x) => x.id === id)!;
            return (
              <li key={id}>
                <Link
                  to={`/run/${runId}/promo/${id}`}
                  onMouseEnter={() => setFocus(id)}
                  onMouseLeave={() => setFocus(null)}
                  onFocus={() => setFocus(id)}
                  onBlur={() => setFocus(null)}
                  className={`grid grid-cols-[1.5rem_1fr_auto] items-center gap-x-3 rounded-panel border px-3 py-2 transition-colors ${
                    focus === id ? "border-tungsten/60 bg-raised" : "border-transparent hover:bg-raised"
                  }`}
                >
                  <span className={`font-cond text-sm tnum ${i === 0 ? "text-tungsten" : "text-fog"}`}>{i + 1}</span>
                  <span className="min-w-0">
                    <span className="flex items-center gap-2 text-sm">
                      <StructureIcon structure={p.structure} className="text-fog" />
                      <span className="font-cond">{eps(p.segments)}</span>
                    </span>
                    <span className="block truncate text-xs text-fog">{ENGINE[p.engine]}</span>
                  </span>
                  <span className="font-cond text-sm tnum">{p.scores.total.toFixed(2)}</span>
                </Link>
              </li>
            );
          })}
        </ol>
        <Link to={`/run/${runId}/slate`} className="mt-3 inline-block text-sm text-tungsten underline-offset-4 hover:underline">
          Review the full slate
        </Link>
        <p className="mt-6 text-xs text-fog">
          {slate.promos.length} valid promos found; {slate.slate.bench.length} more on the bench.
        </p>
      </aside>
    </div>
  );
}

export function BiblePanel({ bible }: { bible: Bible }) {
  const conflicts = bible.continuity_conflicts;
  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="premise-h">
        <h2 id="premise-h" className="mb-2 text-md font-semibold">Show bible</h2>
        <p className="text-sm text-fog">{bible.premise}</p>
      </section>
      {conflicts.length > 0 && <ContinuityCard conflicts={conflicts} />}
      <section aria-labelledby="chars-h">
        <h3 id="chars-h" className="mb-2 text-sm font-semibold">Characters</h3>
        <ul tabIndex={0} aria-label="Characters" className="flex max-h-[520px] flex-col gap-3 overflow-y-auto pr-1">
          {bible.characters.map((c) => {
            const aka = c.aliases.filter((a) => a.label.toUpperCase() !== c.canonical.toUpperCase());
            return (
              <li key={c.canonical}>
                <p className="text-sm font-semibold">{c.canonical}</p>
                <p className="text-xs text-fog">{c.role}</p>
                {aka.length > 0 && (
                  <ul className="mt-1 flex flex-wrap gap-1">
                    {aka.map((a) => (
                      <li key={a.label + a.first_ep} className="rounded-chip border border-rule px-1.5 py-0.5 font-cond text-xs text-fog">
                        aka {titleCase(a.label)}, E{a.first_ep}
                        {a.last_ep !== a.first_ep ? `–${a.last_ep}` : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

function ContinuityCard({ conflicts }: { conflicts: Bible["continuity_conflicts"] }) {
  return (
    <section aria-labelledby="cont-h" className="rounded-panel border border-rule border-l-rec bg-console p-3" style={{ borderLeftWidth: 3 }}>
      <h3 id="cont-h" className="text-sm font-semibold">Continuity</h3>
      <ul className="mt-2 flex flex-col gap-2">
        {conflicts.map((c) => (
          <li key={c.entity} className="text-xs">
            <p className="text-bone">
              {c.entity} spelled {c.variants.length} ways
            </p>
            <p className="mt-0.5 font-cond text-fog">
              {c.variants.map((v, i) => (
                <span key={v.spelling}>
                  {i > 0 && ", "}
                  <span className="text-bone">{v.spelling}</span> {rangeText(v.episodes)}
                </span>
              ))}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function titleCase(s: string) {
  return s.toLowerCase().replace(/\b\w/g, (m) => m.toUpperCase());
}

function rangeText(e: number[]) {
  if (!e.length) return "";
  const s = [...e].sort((a, b) => a - b);
  const parts: string[] = [];
  let a = s[0], b = s[0];
  for (const x of s.slice(1).concat(Infinity)) {
    if (x === b + 1) b = x;
    else {
      parts.push(a === b ? `E${a}` : `E${a}–${b}`);
      a = b = x;
    }
  }
  return parts.join(", ");
}
