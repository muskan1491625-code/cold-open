import { Link } from "react-router-dom";
import { useRun } from "../components/Shell";
import { pct, secs, usd } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { QualityResp } from "../lib/types";

export function QualityPage() {
  const { runId } = useRun();
  const { data, error } = useApi<QualityResp>(`/api/runs/${runId}/quality`);
  if (error) return <p className="text-rec-ink">{error.message}</p>;
  if (!data) return <div aria-busy className="h-[600px] rounded-panel bg-console/60" />;
  const ev = data.eval;
  return (
    <div className="grid gap-8 lg:grid-cols-12">
      <section aria-labelledby="funnel-h" className="lg:col-span-7">
        <h2 id="funnel-h" className="mb-1 text-lg font-semibold">From candidates to slate</h2>
        <p className="mb-4 text-sm text-fog">Every candidate the model proposed, and where the code stopped it.</p>
        <ConstraintFunnel q={data} />
      </section>
      <section aria-labelledby="ledger-h" className="lg:col-span-5">
        <h2 id="ledger-h" className="mb-1 text-lg font-semibold">Results</h2>
        <p className="mb-4 text-sm text-fog">
          {ev ? "Measured against hand-labelled gold promos." : "Gold labels not scored yet: run coldopen eval to fill the gold rows."}
        </p>
        <ResultsLedger q={data} />
      </section>
      {ev && (
        <section aria-labelledby="iou-h" className="lg:col-span-5">
          <h2 id="iou-h" className="mb-1 text-lg font-semibold">Overlap with gold</h2>
          <p className="mb-4 text-sm text-fog">Best overlap per gold promo. A hit needs 0.5 or more.</p>
          <IoUStrip matches={ev.gold_matches} />
        </section>
      )}
      {ev && Object.keys(ev.ablations).length > 0 && (
        <section aria-labelledby="abl-h" className="lg:col-span-7">
          <h2 id="abl-h" className="mb-1 text-lg font-semibold">What each part buys</h2>
          <p className="mb-4 text-sm text-fog">Invalid outputs per variant. The full system should show zeros.</p>
          <AblationMultiples ablations={ev.ablations} />
        </section>
      )}
      <section aria-labelledby="gal-h" className="lg:col-span-12">
        <h2 id="gal-h" className="mb-1 text-lg font-semibold">Where it fails</h2>
        <p className="mb-4 text-sm text-fog">Real cases from this run, so the weak spots are visible, not hidden.</p>
        <FailureGallery q={data} runId={runId} />
      </section>
    </div>
  );
}

function ConstraintFunnel({ q }: { q: QualityResp }) {
  const c = q.report.counts;
  const v = q.validation;
  const raw = c.raw_candidates ?? v.candidates_checked;
  const schema = Object.entries(v.rejected_by_reason).filter(([k]) => k === "schema").reduce((a, [, n]) => a + n, 0);
  const passedIds = raw - schema;
  const afterRepair = (c.valid ?? 0) + (v.duplicates ?? 0);
  const steps = [
    { label: "Proposed by the miner", n: raw, drop: null as string | null },
    { label: "Well-formed JSON", n: passedIds, drop: schema ? `${schema} malformed` : null },
    {
      label: "Passed every hard rule",
      n: afterRepair,
      drop: [
        v.repaired ? `${v.repaired} fixed by one repair round` : "",
        ...Object.entries(v.rejected_by_reason).filter(([k]) => k !== "schema").map(([k, n]) => `${n} rejected: ${k.replace(/_/g, " ")}`),
      ]
        .filter(Boolean)
        .join("; ") || null,
    },
    { label: "Unique (overlap removed)", n: c.valid ?? 0, drop: v.duplicates ? `${v.duplicates} near-duplicates` : null },
    { label: "On the launch slate", n: c.slate ?? 0, drop: c.bench ? `${c.bench} on the bench` : null },
  ];
  const max = Math.max(1, raw);
  return (
    <ol className="flex flex-col gap-2">
      {steps.map((s, i) => (
        <li key={s.label} className="grid grid-cols-[minmax(0,1fr)_minmax(0,14rem)] items-center gap-4 md:grid-cols-[12rem_1fr_minmax(0,16rem)]">
          <span className="text-sm md:order-1">{s.label}</span>
          <div className="order-3 col-span-2 flex items-center gap-2 md:order-2 md:col-span-1">
            <div
              className="h-6 rounded-r-[4px]"
              style={{ width: `${Math.max(1.5, (s.n / max) * 100)}%`, background: i === steps.length - 1 ? "rgb(var(--tungsten))" : "rgb(var(--teal) / 0.75)" }}
              aria-hidden
            />
            <span className="font-cond text-sm tnum">{s.n}</span>
          </div>
          <span className="order-2 text-xs text-fog md:order-3">{s.drop ?? ""}</span>
        </li>
      ))}
    </ol>
  );
}

function ResultsLedger({ q }: { q: QualityResp }) {
  const ev = q.eval;
  const cp = q.report.constraint_pass;
  const allPass = Object.values(cp).length ? Math.min(...Object.values(cp)) : null;
  const rows: [string, string][] = [
    ["Hit rate (top 20, overlap 0.5+)", ev ? pct(ev.gold.hit_rate) : "—"],
    ["Gold promos found", ev ? `${ev.gold.hits} of ${ev.gold.gold}` : "—"],
    ["Median start error", ev ? secs(ev.gold.median_start_error_s) : "—"],
    ["Median end error", ev ? secs(ev.gold.median_end_error_s) : "—"],
    ["Precision@10 (human rating 4+)", ev ? pct(ev.precision_at_10) : "—"],
    ["Cold Viewer agreement (human audit)", ev?.viewer_agreement.agreement != null ? pct(ev.viewer_agreement.agreement) : "—"],
    ["Comprehension, median", q.comprehension.median != null ? q.comprehension.median.toFixed(2) : "—"],
    ["Constraint pass, final set", pct(allPass)],
    ["Valid on first try", pct(q.validation.first_pass_valid_rate)],
    ["LLM calls", String(q.report.llm_calls)],
    ["Tokens", (q.report.tokens_in + q.report.tokens_out).toLocaleString()],
    ["Cost", usd(q.report.cost_usd)],
  ];
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-b border-rule/60 last:border-0">
            <th scope="row" className="py-1.5 pr-4 text-left font-normal text-fog">{k}</th>
            <td className="py-1.5 text-right font-cond tnum">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function IoUStrip({ matches }: { matches: NonNullable<QualityResp["eval"]>["gold_matches"] }) {
  const W = 100;
  return (
    <div>
      <div className="relative h-20" role="img" aria-label={`Best overlap for ${matches.length} gold promos; ${matches.filter((m) => m.hit).length} at or above 0.5`}>
        <div className="absolute inset-x-0 top-10 h-px bg-rule" />
        <div className="absolute top-2 h-16 w-px bg-tungsten" style={{ left: "50%" }} />
        <span className="absolute top-0 -translate-x-1/2 font-cond text-xs text-tungsten" style={{ left: "50%" }}>0.5</span>
        {matches.map((m, i) => (
          <span
            key={m.gold}
            title={`${m.gold} E${m.episode}: ${m.note} (${m.iou.toFixed(2)})`}
            className={`absolute h-2.5 w-2.5 -translate-x-1/2 rounded-full ring-2 ring-bay ${m.hit ? "bg-teal" : "bg-rec"}`}
            style={{ left: `${(m.iou / 1) * W}%`, top: 36 + ((i % 3) - 1) * 9 }}
          />
        ))}
      </div>
      <div className="flex justify-between font-cond text-xs text-fog">
        <span>0, no overlap</span>
        <span>1, exact</span>
      </div>
    </div>
  );
}

const VIOLATIONS: [string, string][] = [
  ["timestamps_off_atom_boundary", "Times not on a line boundary"],
  ["midline_cuts", "Cuts through a spoken line"],
  ["durations_out_of_range", "Length outside 20–60 s"],
  ["hook_latency_over_3s", "Hook later than 3 s"],
  ["quotes_not_in_script", "Quotes not in the script"],
];
const ORDER = ["rules", "llm_only", "no_viewer", "full"];
const LABEL: Record<string, string> = { rules: "Rules only", llm_only: "LLM only", no_viewer: "No viewer", full: "Full" };

function AblationMultiples({ ablations }: { ablations: NonNullable<QualityResp["eval"]>["ablations"] }) {
  const names = ORDER.filter((n) => n in ablations);
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {[["hit_rate", "Hit rate"] as const, ...VIOLATIONS].map(([key, label]) => {
        const vals = names.map((n) => (key === "hit_rate" ? (ablations[n].gold.hit_rate ?? 0) : ablations[n].violations[key] ?? 0));
        const max = Math.max(1e-9, ...vals);
        return (
          <figure key={key} className="rounded-panel border border-rule bg-console p-3">
            <figcaption className="mb-2 text-sm">{label}</figcaption>
            <ul className="flex flex-col gap-1">
              {names.map((n, i) => (
                <li key={n} className="grid grid-cols-[5.5rem_1fr] items-center gap-2 font-cond text-xs">
                  <span className="text-fog">{LABEL[n]}</span>
                  <span className="flex items-center gap-1.5">
                    <span
                      className={`h-3 rounded-r-[3px] ${n === "full" ? "bg-tungsten" : "bg-fog/60"}`}
                      style={{ width: `${vals[i] ? Math.max(2, (vals[i] / max) * 80) : 0}%` }}
                      aria-hidden
                    />
                    <span className="tnum">{key === "hit_rate" ? pct(vals[i]) : vals[i]}</span>
                  </span>
                </li>
              ))}
            </ul>
          </figure>
        );
      })}
    </div>
  );
}

function FailureGallery({ q, runId }: { q: QualityResp; runId: string }) {
  if (!q.gallery.length) return <p className="text-sm text-fog">No failures recorded for this run.</p>;
  return (
    <div className="overflow-x-auto rounded-panel border border-rule">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="border-b border-rule bg-console">
          <tr>
            {["Category", "Where", "What went wrong", ""].map((h) => (
              <th key={h} scope="col" className="px-3 py-2 text-left font-cond text-xs font-normal text-fog">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {q.gallery.map((g, i) => (
            <tr key={i} className="border-b border-rule/60 last:border-0">
              <td className="whitespace-nowrap px-3 py-2">{g.category}</td>
              <td className="whitespace-nowrap px-3 py-2 font-cond">{g.where}</td>
              <td className="px-3 py-2 text-fog">{g.what}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right">
                {g.promo_id && (
                  <Link to={`/run/${runId}/promo/${g.promo_id}`} className="text-tungsten underline-offset-4 hover:underline">
                    Open {g.promo_id}
                  </Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
