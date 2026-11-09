import { curly, POLICY, tc, unquote } from "../lib/format";
import { heat } from "../lib/heat";
import type { Cue, PromoSummary, Scores, Seg } from "../lib/types";

/** Tiny glyph per structure: one take, a flash then a take, or two takes joined. */
export function StructureIcon({ structure, className = "" }: { structure: string; className?: string }) {
  const title = structure === "flash_forward" ? "Flash-forward" : structure === "arc_bridge" ? "Arc bridge" : "Straight";
  return (
    <svg role="img" aria-label={title} width="22" height="10" viewBox="0 0 22 10" className={`shrink-0 ${className}`}>
      <title>{title}</title>
      {structure === "straight" && <rect x="1" y="3" width="20" height="4" rx="1" fill="currentColor" />}
      {structure === "flash_forward" && (
        <>
          <rect x="1" y="3" width="4" height="4" rx="1" className="fill-tungsten" />
          <rect x="7" y="3" width="14" height="4" rx="1" fill="currentColor" />
        </>
      )}
      {structure === "arc_bridge" && (
        <>
          <rect x="1" y="1" width="8" height="4" rx="1" fill="currentColor" />
          <path d="M9 3 C12 3 10 7 13 7" stroke="currentColor" fill="none" strokeWidth="1" />
          <rect x="13" y="5" width="8" height="4" rx="1" fill="currentColor" />
        </>
      )}
    </svg>
  );
}

const POLICY_CLASS: Record<string, string> = {
  paid_safe: "border-teal text-teal",
  review: "border-tungsten text-tungsten",
  organic_only: "border-rec text-rec-ink",
};

export function PolicyBadge({ policy }: { policy: string }) {
  return (
    <span className={`inline-flex items-center rounded-chip border px-2 py-0.5 font-cond text-xs ${POLICY_CLASS[policy]}`}>
      {POLICY[policy]}
    </span>
  );
}

export function Badges({ p }: { p: PromoSummary }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <PolicyBadge policy={p.policy} />
      {p.reveals_disclosed.length > 0 && (
        <span className="rounded-chip border border-rec/70 px-2 py-0.5 font-cond text-xs text-rec-ink">
          Spoils {p.reveals_disclosed.join(", ")}
        </span>
      )}
      {p.ungrounded_refs.length > 0 && (
        <span className="rounded-chip border border-tungsten/70 px-2 py-0.5 font-cond text-xs text-tungsten" title="Names a stranger cannot place">
          Unexplained {p.ungrounded_refs.join(", ")}
        </span>
      )}
      {p.continuity_warnings.length > 0 && (
        <span className="rounded-chip border border-tungsten/70 px-2 py-0.5 font-cond text-xs text-tungsten" title={p.continuity_warnings.join("\n")}>
          Continuity
        </span>
      )}
    </div>
  );
}

const METRICS: [keyof Scores, string][] = [
  ["hook", "Hook"],
  ["cliff", "Cliffhanger"],
  ["viewer", "Stranger gets it"],
  ["tension", "Tension"],
  ["quality", "Cut quality"],
];

/** VU meters: 10 LED segments per metric, lit segments coloured by their ramp position. */
export function VUMeter({ scores, compact = false, noViewer = false }: { scores: Scores; compact?: boolean; noViewer?: boolean }) {
  return (
    <dl className={`grid gap-y-1.5 ${compact ? "text-xs" : "text-sm"}`}>
      {METRICS.map(([k, label]) => {
        const v = scores[k] as number;
        const na = noViewer && k === "viewer"; // not judged (API limit or the no_viewer variant), not a zero
        const lit = na ? 0 : Math.round(v * 10);
        return (
          <div key={k} className="grid grid-cols-[minmax(0,7.5rem)_1fr_2.5rem] items-center gap-3">
            <dt className="truncate font-cond text-fog">{label}</dt>
            <dd className="flex gap-[3px]" aria-label={na ? `${label} not judged` : `${label} ${Math.round(v * 100)} out of 100`}>
              {Array.from({ length: 10 }, (_, i) => (
                <span
                  key={i}
                  aria-hidden
                  className={`${compact ? "h-2" : "h-2.5"} flex-1 rounded-[2px]`}
                  style={{ background: i < lit ? heat(i / 9) : "rgb(var(--rule) / 0.6)" }}
                />
              ))}
            </dd>
            <dd className={`text-right font-cond tnum ${na ? "text-fog" : ""}`} title={na ? "Not judged by the Cold Viewer" : undefined}>
              {na ? "n/a" : (v * 100).toFixed(0)}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** A line set as a screenplay: cue centred in caps, parenthetical in italics, dialogue 35ch. */
export function ScriptLine({ cue, text, size = "md" }: { cue?: Cue; text: string; size?: "md" | "sm" }) {
  const cls = size === "sm" ? "text-sm" : "";
  if (!cue || cue.kind !== "dialogue") {
    return <p className={`font-script ${size === "sm" ? "text-sm" : "text-md"} leading-snug`}>{unquote(text)}</p>;
  }
  const tags = cue.tags.filter(Boolean);
  return (
    <div className={`screenplay ${cls}`}>
      <div className="cue">{cue.label}</div>
      {tags.length > 0 && <div className="paren">({tags.join(", ")})</div>}
      <div className="line">{curly(text)}</div>
    </div>
  );
}

export const sameLine = (p: PromoSummary) => p.hook_text.trim() === p.cliffhanger_text.trim();

export function ScriptCard({ p }: { p: PromoSummary }) {
  if (sameLine(p)) {
    return (
      <section aria-label="Hook and cliffhanger" className="rounded-panel border border-rule bg-bay/60 p-5">
        <h3 className="mb-3 font-cond text-xs text-fog">Hook at {p.hook_at.toFixed(1)} s, and the last line before the cut</h3>
        <ScriptLine cue={p.hook_cue} text={p.hook_text} />
      </section>
    );
  }
  return (
    <div className="grid gap-5 rounded-panel border border-rule bg-bay/60 p-5 sm:grid-cols-2">
      <section aria-label="Hook">
        <h3 className="mb-3 font-cond text-xs text-fog">Hook, {p.hook_at.toFixed(1)} s in</h3>
        <ScriptLine cue={p.hook_cue} text={p.hook_text} />
      </section>
      <section aria-label="Cliffhanger" className="sm:border-l sm:border-rule sm:pl-5">
        <h3 className="mb-3 font-cond text-xs text-fog">Last line before the cut</h3>
        <ScriptLine cue={p.cliff_cue} text={p.cliffhanger_text} />
      </section>
    </div>
  );
}

/** Segments laid end to end at their true proportion of the promo. */
export function SegmentStrip({ segments, duration }: { segments: Seg[]; duration: number }) {
  return (
    <div className="flex flex-wrap gap-0.5" aria-label="Segments in playback order">
      {segments.map((s, i) => {
        const d = s.end - s.start;
        return (
          <div key={i} className="min-w-[10.5rem] rounded-chip border border-rule bg-console px-2 py-1" style={{ flexGrow: d / duration, flexBasis: 0 }}>
            <div className="truncate font-cond text-xs">
              <span className="font-semibold">E{s.episode}</span>{" "}
              <span className="font-script text-fog">
                {tc(s.start)}–{tc(s.end)}
              </span>
            </div>
            <div className="font-cond text-xs text-fog tnum">{d.toFixed(1)} s</div>
          </div>
        );
      })}
    </div>
  );
}
