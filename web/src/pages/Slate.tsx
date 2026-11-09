import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badges, PolicyBadge, sameLine, ScriptCard, ScriptLine, SegmentStrip, StructureIcon, VUMeter } from "../components/Bits";
import { useRun } from "../components/Shell";
import { ENGINE, eps, STRUCTURE } from "../lib/format";
import { useKeys } from "../lib/hooks";
import type { Decision, PromoSummary } from "../lib/types";

const DECISION_LABEL: Record<string, string> = { approve_paid: "Approved for paid", organic_only: "Organic only", reject: "Rejected" };

function DecisionTag({ d }: { d?: Decision }) {
  if (!d) return null;
  const cls = d.action === "approve_paid" ? "text-teal" : d.action === "reject" ? "text-rec-ink" : "text-tungsten";
  return <span className={`font-cond text-xs ${cls}`}>{DECISION_LABEL[d.action]}</span>;
}

export function SlatePage() {
  const { runId, slate, show } = useRun();
  const noViewer = (show.report?.notes ?? []).some((n) => n.startsWith("Slate ranked without the Cold Viewer"));
  const navigate = useNavigate();
  const byId = useMemo(() => new Map(slate.promos.map((p) => [p.id, p])), [slate]);
  const hero = byId.get(slate.slate.hero)!;
  const challengers = slate.slate.challengers.map((id) => byId.get(id)!);
  const bench = slate.slate.bench.map((id) => byId.get(id)!);
  const order = [hero, ...challengers, ...bench];
  const [sel, setSel] = useState(0);

  useKeys(
    {
      j: () => setSel((i) => Math.min(order.length - 1, i + 1)),
      k: () => setSel((i) => Math.max(0, i - 1)),
      Enter: () => navigate(`/run/${runId}/promo/${order[sel].id}`),
    },
    [sel, order],
  );

  return (
    <div className="flex flex-col gap-10">
      <section aria-labelledby="hero-h">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="hero-h" className="text-xl font-semibold">Hero promo</h2>
          <p className="kbd-hint font-cond text-xs text-fog">
            <kbd className="rounded-chip border border-rule px-1">j</kbd> <kbd className="rounded-chip border border-rule px-1">k</kbd> to move,{" "}
            <kbd className="rounded-chip border border-rule px-1">Enter</kbd> to open in the cut room
          </p>
        </div>
        <HeroSpread p={hero} runId={runId} selected={sel === 0} decision={slate.decisions[hero.id]} noViewer={noViewer} />
      </section>

      <section aria-labelledby="ch-h">
        <h2 id="ch-h" className="mb-3 text-lg font-semibold">Challengers</h2>
        <ol className="flex flex-col border-t border-rule">
          {challengers.map((p, i) => (
            <li key={p.id}>
              <ChallengerRow p={p} n={i + 1} runId={runId} selected={sel === i + 1} decision={slate.decisions[p.id]} noViewer={noViewer} />
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="bench-h">
        <h2 id="bench-h" className="mb-3 text-lg font-semibold">Bench</h2>
        <BenchTable noViewer={noViewer} rows={bench} runId={runId} selectedId={sel > challengers.length ? order[sel]?.id : undefined} decisions={slate.decisions} />
      </section>
    </div>
  );
}

function HeroSpread({ p, runId, selected, decision, noViewer }: { p: PromoSummary; runId: string; selected: boolean; decision?: Decision; noViewer: boolean }) {
  return (
    <article
      className={`grid gap-6 rounded-panel border bg-console p-5 md:p-6 lg:grid-cols-12 ${selected ? "border-tungsten/70" : "border-rule"}`}
      aria-label={`Hero ${p.id}`}
    >
      <div className="flex flex-col gap-5 lg:col-span-7">
        <ScriptCard p={p} />
        <SegmentStrip segments={p.segments} duration={p.duration} />
        <p className="measure text-md">{p.why}</p>
      </div>
      <div className="flex flex-col gap-5 lg:col-span-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-cond text-xs text-fog">{p.id}</p>
            <p className="text-lg font-semibold">{ENGINE[p.engine]}</p>
          </div>
          <div className="text-right">
            <p className="font-cond text-xs text-fog">Total score</p>
            <p className="font-cond text-2xl tnum">{p.scores.total.toFixed(2)}</p>
          </div>
        </div>
        <VUMeter scores={p.scores} noViewer={noViewer} />
        <dl className="grid grid-cols-3 gap-3 border-t border-rule pt-4 text-sm">
          <div>
            <dt className="font-cond text-xs text-fog">Structure</dt>
            <dd className="flex items-center gap-2">
              <StructureIcon structure={p.structure} className="text-fog" />
              {STRUCTURE[p.structure]}
            </dd>
          </div>
          <div>
            <dt className="font-cond text-xs text-fog">Length</dt>
            <dd className="tnum">{p.duration.toFixed(1)} s</dd>
          </div>
          <div>
            <dt className="font-cond text-xs text-fog">Lands on</dt>
            <dd className="tnum">
              E{p.landing_episode}, {p.free_runway} free
            </dd>
          </div>
        </dl>
        <Badges p={p} />
        <p className="text-sm text-fog">{p.rationale}</p>
        <div className="flex items-center gap-4">
          <Link to={`/run/${runId}/promo/${p.id}`} className="rounded-chip bg-tungsten px-4 py-2 text-sm font-semibold text-ink hover:opacity-90">
            Open in cut room
          </Link>
          <DecisionTag d={decision} />
        </div>
      </div>
    </article>
  );
}

function ChallengerRow({ p, n, runId, selected, decision, noViewer }: { p: PromoSummary; n: number; runId: string; selected: boolean; decision?: Decision; noViewer: boolean }) {
  return (
    <Link
      to={`/run/${runId}/promo/${p.id}`}
      aria-current={selected ? "true" : undefined}
      className={`relative grid gap-4 border-b border-rule py-5 pl-4 pr-2 transition-colors hover:bg-console/70 md:grid-cols-12 md:pl-5 ${selected ? "bg-console/70 before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-tungsten" : ""}`}
    >
      <div className="flex items-start gap-4 md:col-span-3">
        <span className="font-cond text-2xl leading-none text-fog tnum">{n + 1}</span>
        <div className="min-w-0">
          <p className="font-semibold">{ENGINE[p.engine]}</p>
          <p className="mt-0.5 flex items-center gap-2 font-cond text-xs text-fog">
            <StructureIcon structure={p.structure} />
            {eps(p.segments)}, {p.duration.toFixed(0)} s
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <PolicyBadge policy={p.policy} />
            <DecisionTag d={decision} />
          </div>
        </div>
      </div>
      <div className={`grid gap-4 md:col-span-5 ${sameLine(p) ? "" : "sm:grid-cols-2"}`}>
        <ScriptLine cue={p.hook_cue} text={p.hook_text} size="sm" />
        {!sameLine(p) && <ScriptLine cue={p.cliff_cue} text={p.cliffhanger_text} size="sm" />}
      </div>
      <div className="flex flex-col gap-2 md:col-span-4">
        <VUMeter scores={p.scores} compact noViewer={noViewer} />
        <p className="text-xs text-fog">{p.rationale.replace(/^Challenger \d+: /, "")}</p>
      </div>
    </Link>
  );
}

type SortKey = "total" | "hook" | "cliff" | "viewer" | "duration" | "landing_episode";

function BenchTable({ rows, runId, selectedId, decisions, noViewer }: { rows: PromoSummary[]; runId: string; selectedId?: string; decisions: Record<string, Decision>; noViewer: boolean }) {
  const [key, setKey] = useState<SortKey>("total");
  const [desc, setDesc] = useState(true);
  const val = (p: PromoSummary) => (key === "duration" ? p.duration : key === "landing_episode" ? p.landing_episode : p.scores[key]);
  const sorted = [...rows].sort((a, b) => (desc ? val(b) - val(a) : val(a) - val(b)));
  const head = (k: SortKey, label: string) => (
    <th scope="col" className="px-3 py-2 text-right font-normal" aria-sort={key === k ? (desc ? "descending" : "ascending") : "none"}>
      <button
        className={`font-cond text-xs ${key === k ? "text-bone" : "text-fog"} hover:text-bone`}
        onClick={() => (key === k ? setDesc(!desc) : (setKey(k), setDesc(true)))}
      >
        {label}
        {key === k ? (desc ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );
  return (
    <>
    <ul className="flex flex-col border-t border-rule md:hidden" aria-label="Bench, sorted by total score">
      {sorted.map((p) => (
        <li key={p.id}>
          <Link to={`/run/${runId}/promo/${p.id}`} className={`grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 border-b border-rule px-1 py-3 font-cond text-sm ${selectedId === p.id ? "bg-console/70" : ""}`}>
            <span className="truncate">
              <span className="text-tungsten">{p.id}</span> {ENGINE[p.engine]}
            </span>
            <span className="text-right tnum">{p.scores.total.toFixed(2)}</span>
            <span className="text-xs text-fog tnum">
              {eps(p.segments)}, {p.duration.toFixed(0)} s, lands E{p.landing_episode} <DecisionTag d={decisions[p.id]} />
            </span>
            <span className="justify-self-end"><PolicyBadge policy={p.policy} /></span>
          </Link>
        </li>
      ))}
    </ul>
    <div className="hidden overflow-x-auto rounded-panel border border-rule md:block">
      <table className="w-full min-w-[720px] font-cond text-sm">
        <thead className="border-b border-rule bg-console">
          <tr>
            <th scope="col" className="px-3 py-2 text-left font-cond text-xs font-normal text-fog">Promo</th>
            <th scope="col" className="px-3 py-2 text-left font-cond text-xs font-normal text-fog">Engine</th>
            <th scope="col" className="px-3 py-2 text-left font-cond text-xs font-normal text-fog">Episodes</th>
            {head("duration", "Length")}
            {head("hook", "Hook")}
            {head("cliff", "Cliff")}
            {head("viewer", "Viewer")}
            {head("landing_episode", "Lands")}
            {head("total", "Total")}
            <th scope="col" className="px-3 py-2 text-left font-cond text-xs font-normal text-fog">Policy</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => (
            <tr key={p.id} className={`border-b border-rule/60 last:border-0 hover:bg-raised ${selectedId === p.id ? "bg-raised" : ""}`}>
              <td className="px-3 py-2">
                <Link to={`/run/${runId}/promo/${p.id}`} className="text-tungsten underline-offset-4 hover:underline">
                  {p.id}
                </Link>{" "}
                <DecisionTag d={decisions[p.id]} />
              </td>
              <td className="px-3 py-2">{ENGINE[p.engine]}</td>
              <td className="px-3 py-2">{eps(p.segments)}</td>
              <td className="px-3 py-2 text-right tnum">{p.duration.toFixed(1)}</td>
              <td className="px-3 py-2 text-right tnum">{(p.scores.hook * 100).toFixed(0)}</td>
              <td className="px-3 py-2 text-right tnum">{(p.scores.cliff * 100).toFixed(0)}</td>
              <td className="px-3 py-2 text-right tnum">{noViewer ? <span className="text-fog">n/a</span> : (p.scores.viewer * 100).toFixed(0)}</td>
              <td className="px-3 py-2 text-right tnum">E{p.landing_episode}</td>
              <td className="px-3 py-2 text-right tnum">{p.scores.total.toFixed(2)}</td>
              <td className="px-3 py-2">
                <PolicyBadge policy={p.policy} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </>
  );
}
