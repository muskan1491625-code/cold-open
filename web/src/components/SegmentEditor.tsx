import { useRef } from "react";
import { tc } from "../lib/format";
import { heat } from "../lib/heat";
import { useWidth } from "../lib/hooks";
import type { AtomView } from "../lib/types";
import type { Clip } from "./PhoneAnimatic";

const H = 64;
const PAD = 16;
const EPS = 0.01;

export type Check = { duration: number; hookAt: number; errors: string[] };

/** Same hard rules as the Python validator, re-run live while trimming. */
export function validateClip(clip: Clip, atomsByEp: Map<number, AtomView[]>, hook?: AtomView, cliff?: AtomView): Check {
  const errors: string[] = [];
  const duration = clip.reduce((a, s) => a + (s.end - s.start), 0);
  if (duration < 20 - EPS) errors.push(`Too short: ${duration.toFixed(1)} s (minimum 20 s).`);
  if (duration > 60 + EPS) errors.push(`Too long: ${duration.toFixed(1)} s (maximum 60 s).`);
  const s0 = clip[0];
  const hookAt = hook ? hook.start - s0.start : 0;
  if (hook && (hook.start < s0.start - EPS || hook.start >= s0.end)) errors.push("The hook line is no longer in the first segment.");
  else if (hookAt > 3 + EPS) errors.push(`The hook starts ${hookAt.toFixed(1)} s in; it must start within 3 s.`);
  const last = clip[clip.length - 1];
  if (cliff && (cliff.end > last.end + EPS || cliff.start < last.start - EPS)) errors.push("The cliffhanger line is cut off.");
  for (const s of clip) {
    if (s.end - s.start <= 0.2) errors.push("A segment has no length.");
    for (const d of atomsByEp.get(s.episode) ?? []) {
      if (d.kind !== "dialogue") continue;
      for (const b of [s.start, s.end]) {
        if (d.start < b - EPS && d.end > b + EPS) errors.push(`Cuts through a line at ${tc(b)} in E${s.episode}.`);
      }
    }
  }
  return { duration, hookAt, errors: [...new Set(errors)] };
}

function snapper(atoms: AtomView[]) {
  const cams = [...new Set(atoms.filter((a) => a.kind === "camera").flatMap((a) => [a.start, a.end]))].sort((a, b) => a - b);
  const all = [...new Set(atoms.flatMap((a) => [a.start, a.end]))].sort((a, b) => a - b);
  const nearest = (xs: number[], t: number) => xs.reduce((best, x) => (Math.abs(x - t) < Math.abs(best - t) ? x : best), xs[0] ?? t);
  return {
    snap(t: number) {
      const c = nearest(cams, t);
      return Math.abs(c - t) <= 0.5 ? c : nearest(all, t);
    },
    step(t: number, dir: 1 | -1) {
      const xs = dir > 0 ? all.filter((x) => x > t + EPS) : all.filter((x) => x < t - EPS).reverse();
      return xs[0] ?? t;
    },
  };
}

type Props = {
  index: number;
  count: number;
  seg: Clip[number];
  original: Clip[number];
  atoms: AtomView[];
  ekg: number[];
  episodeDuration: number;
  hook?: AtomView;
  onChange: (s: Clip[number]) => void;
};

export function SegmentEditor({ index, count, seg, original, atoms, ekg, episodeDuration, hook, onChange }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const svg = useRef<SVGSVGElement>(null);
  const lo = Math.max(0, Math.min(seg.start, original.start) - 15);
  const hi = Math.min(episodeDuration || original.end + 15, Math.max(seg.end, original.end) + 15);
  const W = Math.max(200, width);
  const x = (t: number) => PAD + ((t - lo) / (hi - lo)) * (W - 2 * PAD);
  const tOf = (px: number) => lo + ((px - PAD) / (W - 2 * PAD)) * (hi - lo);
  const y = (v: number) => 8 + (1 - v) * (H - 20);
  const { snap, step } = snapper(atoms);

  const drag = (edge: "start" | "end") => (e: React.PointerEvent) => {
    e.preventDefault();
    const el = e.currentTarget as Element;
    el.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const box = svg.current!.getBoundingClientRect();
      const t = snap(Math.max(0, Math.min(episodeDuration, tOf(ev.clientX - box.left))));
      if (edge === "start" && t < seg.end - 0.5) onChange({ ...seg, start: t });
      if (edge === "end" && t > seg.start + 0.5) onChange({ ...seg, end: t });
    };
    const up = () => {
      el.removeEventListener("pointermove", move as EventListener);
      el.removeEventListener("pointerup", up);
    };
    el.addEventListener("pointermove", move as EventListener);
    el.addEventListener("pointerup", up);
  };

  const key = (edge: "start" | "end") => (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const t = step(seg[edge], e.key === "ArrowRight" ? 1 : -1);
    if (edge === "start" && t < seg.end - 0.5) onChange({ ...seg, start: t });
    if (edge === "end" && t > seg.start + 0.5) onChange({ ...seg, end: t });
  };

  const pieces = [];
  for (let i = Math.floor(lo); i < Math.min(Math.ceil(hi), ekg.length - 1); i++) {
    const a = ekg[i], b = ekg[i + 1];
    pieces.push(<line key={i} x1={x(i)} y1={y(a)} x2={x(i + 1)} y2={y(b)} stroke={heat((a + b) / 2)} strokeWidth={2} strokeLinecap="round" />);
  }
  const dialogue = atoms.filter((a) => a.kind === "dialogue" && a.end > lo && a.start < hi);
  const cams = atoms.filter((a) => a.kind === "camera" && a.start > lo && a.start < hi);
  const changed = Math.abs(seg.start - original.start) > EPS || Math.abs(seg.end - original.end) > EPS;

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-3 font-cond text-xs">
        <span>
          <span className="font-semibold text-bone">
            Segment {index + 1}
            {count > 1 ? ` of ${count}` : ""}, E{seg.episode}
          </span>{" "}
          <span className="font-script text-fog">
            {tc(seg.start)}–{tc(seg.end)}
          </span>
        </span>
        <span className="text-fog tnum">
          {(seg.end - seg.start).toFixed(1)} s{changed ? ", trimmed" : ""}
        </span>
      </div>
      <div ref={ref} className="rounded-panel border border-rule bg-bay/60">
        <svg ref={svg} width={W} height={H + 14} className="block touch-none select-none">
          <rect x={x(seg.start)} y={2} width={Math.max(1, x(seg.end) - x(seg.start))} height={H} fill="rgb(var(--bone) / 0.07)" />
          {cams.map((c) => (
            <line key={c.id} x1={x(c.start)} x2={x(c.start)} y1={2} y2={6} className="stroke-rule" strokeWidth={1} />
          ))}
          {pieces}
          {dialogue.map((d) => (
            <rect key={d.id} x={x(Math.max(lo, d.start))} y={H - 4} width={Math.max(1, x(Math.min(hi, d.end)) - x(Math.max(lo, d.start)))} height={3} rx={1} className="fill-fog" opacity={0.45}>
              <title>{`${d.label}: ${d.text}`}</title>
            </rect>
          ))}
          {hook && hook.start >= lo && hook.start <= hi && (
            <path d={`M${x(hook.start)} ${H + 3} l-5 8 h10 z`} className="fill-tungsten">
              <title>Hook</title>
            </path>
          )}
          {index === count - 1 && <path d={`M${x(seg.end)} ${H + 11} l-5 -8 h10 z`} className="fill-rec"><title>Final cut</title></path>}
          {(["start", "end"] as const).map((edge) => (
            <g
              key={edge}
              role="slider"
              tabIndex={0}
              aria-label={`Segment ${index + 1} ${edge}`}
              aria-valuemin={0}
              aria-valuemax={episodeDuration}
              aria-valuenow={seg[edge]}
              aria-valuetext={tc(seg[edge])}
              onPointerDown={drag(edge)}
              onKeyDown={key(edge)}
              className="cursor-ew-resize focus:outline-none [&:focus-visible>rect:last-child]:fill-bone"
            >
              <rect x={x(seg[edge]) - 8} y={0} width={16} height={H + 4} fill="transparent" />
              <rect x={x(seg[edge]) - 1.5} y={0} width={3} height={H + 4} rx={1.5} className="fill-tungsten" />
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}
