import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { Badges, StructureIcon } from "../components/Bits";
import { ColdViewerPanel } from "../components/ColdViewerPanel";
import { DecisionBar } from "../components/DecisionBar";
import { PhoneAnimatic, type Clip } from "../components/PhoneAnimatic";
import { SegmentEditor, validateClip } from "../components/SegmentEditor";
import { useRun } from "../components/Shell";
import { useToast } from "../components/Toast";
import { download, invalidate, post } from "../lib/api";
import { ENGINE, eps, STRUCTURE } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { AtomView, Decision, PromoResp } from "../lib/types";

const VERB: Record<string, string> = { approve_paid: "Approved for paid", organic_only: "Marked organic only", reject: "Rejected" };

export function CutRoomPage() {
  const { runId, slate, reloadSlate } = useRun();
  const { pid } = useParams();
  if (!pid) return <Navigate to={`/run/${runId}/promo/${slate.slate.hero}`} replace />;
  return <CutRoom key={pid} runId={runId} pid={pid} order={slate.promos.map((p) => p.id)} onSaved={reloadSlate} />;
}

function CutRoom({ runId, pid, order, onSaved }: { runId: string; pid: string; order: string[]; onSaved: () => void }) {
  const { data, error } = useApi<PromoResp>(`/api/runs/${runId}/promos/${pid}`);
  const toast = useToast();
  const [clip, setClip] = useState<Clip | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);

  useEffect(() => {
    if (!data) return;
    const trims = data.decision?.trims;
    setClip(trims && trims.length === data.promo.segments.length ? trims : data.promo.segments.map(({ episode, start, end }) => ({ episode, start, end })));
    setDecision(data.decision);
  }, [data]);

  const atomsByEp = useMemo(() => {
    const m = new Map<number, AtomView[]>();
    data?.context.forEach((c) => m.set(c.episode, [...(m.get(c.episode) ?? []), ...c.atoms.filter((a) => !(m.get(c.episode) ?? []).some((b) => b.id === a.id))]));
    return m;
  }, [data]);
  const allAtoms = useMemo(() => new Map([...atomsByEp.values()].flat().map((a) => [a.id, a])), [atomsByEp]);

  const idx = order.indexOf(pid);
  const prev = idx > 0 ? order[idx - 1] : null;
  const next = idx >= 0 && idx < order.length - 1 ? order[idx + 1] : null;

  const decide = useCallback(
    async (action: "approve_paid" | "organic_only" | "reject", reason?: string) => {
      if (!data || !clip) return;
      const original = data.promo.segments;
      const trimmed = clip.some((s, i) => Math.abs(s.start - original[i].start) > 0.01 || Math.abs(s.end - original[i].end) > 0.01);
      try {
        const rec = await post<Decision>(`/api/runs/${runId}/promos/${pid}/decision`, { action, reason, trims: trimmed ? clip : null });
        setDecision(rec);
        invalidate(`/api/runs/${runId}/promos/${pid}`);
        onSaved();
        toast(VERB[action] + (reason ? `: ${reason}` : ""), action === "approve_paid" ? "teal" : action === "reject" ? "rec" : "tungsten");
      } catch (e) {
        toast(`Could not save: ${(e as Error).message}`, "rec");
      }
    },
    [data, clip, runId, pid, onSaved, toast],
  );

  if (error) return <p className="text-rec-ink">{error.message}</p>;
  if (!data || !clip) return <div aria-busy className="h-[600px] rounded-panel bg-console/60" />;

  const p = data.promo;
  const hook = allAtoms.get(p.validation.hook_id);
  const cliff = allAtoms.get(p.validation.cliffhanger_id);
  const check = validateClip(clip, atomsByEp, hook, cliff);
  const blocked = check.errors.length ? "Fix the trims before approving: the cut breaks a hard rule." : null;

  return (
    <div className="grid gap-8 lg:grid-cols-12">
      <div className="lg:col-span-4 xl:col-span-3">
        <div className="lg:sticky lg:top-6">
          <PhoneAnimatic clip={clip} atomsByEp={atomsByEp} hookAt={check.hookAt} />
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-6 lg:col-span-8 xl:col-span-9">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="flex items-center gap-2 font-cond text-xs text-fog">
              <StructureIcon structure={p.structure} />
              {p.id}, {STRUCTURE[p.structure]}, {eps(p.segments)}
            </p>
            <h2 className="text-xl font-semibold">{ENGINE[p.engine]}</h2>
            <p className="measure mt-1 text-sm text-fog">{data.summary.rationale}</p>
          </div>
          <nav aria-label="Other promos" className="flex items-center gap-2 text-sm">
            {prev && (
              <Link to={`/run/${runId}/promo/${prev}`} className="rounded-chip border border-rule px-3 py-1.5 hover:bg-raised">
                Previous
              </Link>
            )}
            {next && (
              <Link to={`/run/${runId}/promo/${next}`} className="rounded-chip border border-rule px-3 py-1.5 hover:bg-raised">
                Next promo
              </Link>
            )}
          </nav>
        </header>

        <section aria-labelledby="seg-h" className="rounded-panel border border-rule bg-console p-4 md:p-5">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h3 id="seg-h" className="font-semibold">Segments</h3>
            <p className="font-cond text-sm tnum" aria-live="polite">
              <span className={check.duration < 20 || check.duration > 60 ? "text-rec-ink" : ""}>{check.duration.toFixed(1)} s</span>
              <span className="text-fog"> total, hook at </span>
              <span className={check.hookAt > 3 ? "text-rec-ink" : ""}>{check.hookAt.toFixed(1)} s</span>
            </p>
          </div>
          <div className="flex flex-col gap-4">
            {clip.map((s, i) => {
              const ctx = data.context[i];
              return (
                <SegmentEditor
                  key={i}
                  index={i}
                  count={clip.length}
                  seg={s}
                  original={p.segments[i]}
                  atoms={atomsByEp.get(s.episode) ?? []}
                  ekg={ctx.ekg}
                  episodeDuration={ctx.duration}
                  hook={i === 0 ? hook : undefined}
                  onChange={(ns) => setClip(clip.map((x, k) => (k === i ? ns : x)))}
                />
              );
            })}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-fog">
            <span>Drag a handle or use the arrow keys; edges snap to line and shot boundaries.</span>
            <button
              onClick={() => setClip(p.segments.map(({ episode, start, end }) => ({ episode, start, end })))}
              className="text-tungsten underline-offset-4 hover:underline"
            >
              Reset trims
            </button>
          </div>
          {check.errors.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1 text-sm text-rec-ink" aria-live="polite">
              {check.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </section>

        <div className="grid gap-6 xl:grid-cols-2">
          <section aria-labelledby="cv-h" className="rounded-panel border border-rule bg-console p-4 md:p-5">
            <h3 id="cv-h" className="mb-3 font-semibold">Cold Viewer</h3>
            <ColdViewerPanel v={p.viewer} />
          </section>
          <section aria-labelledby="risk-h" className="flex flex-col gap-4 rounded-panel border border-rule bg-console p-4 md:p-5">
            <h3 id="risk-h" className="font-semibold">Risk and landing</h3>
            <Badges p={data.summary} />
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="font-cond text-xs text-fog">Lands on</dt>
                <dd>Episode {p.landing_episode}</dd>
              </div>
              <div>
                <dt className="font-cond text-xs text-fog">Free episodes before the paywall</dt>
                <dd className="tnum">{p.free_runway}</dd>
              </div>
              <div>
                <dt className="font-cond text-xs text-fog">Policy flags</dt>
                <dd>{p.policy_reasons.length ? p.policy_reasons.map((r) => r.replace(/_/g, " ")).join(", ") : "None"}</dd>
              </div>
              <div>
                <dt className="font-cond text-xs text-fog">Reveals given away</dt>
                <dd>{p.reveals_disclosed.length ? p.reveals_disclosed.join(", ") : "None"}</dd>
              </div>
            </dl>
            {p.continuity_warnings.length > 0 && (
              <ul className="flex flex-col gap-1 border-l-2 border-tungsten pl-3 text-sm">
                {p.continuity_warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {p.ungrounded_refs.length > 0 && (
              <p className="text-sm">
                <span className="text-fog">Names a stranger cannot place: </span>
                {p.ungrounded_refs.join(", ")}
              </p>
            )}
          </section>
        </div>

        <section aria-label="Decision" className="rounded-panel border border-rule bg-console p-4 md:p-5">
          <DecisionBar onDecide={decide} blocked={blocked} onExport={() => download(runId, `${pid}.edl`)} current={decision?.action} />
          {decision && (
            <p className="mt-3 text-xs text-fog">
              Last decision: {VERB[decision.action]}
              {decision.reason ? ` (${decision.reason})` : ""}
              {decision.trims ? ", with trims" : ""}, {new Date(decision.at).toLocaleString()}.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
