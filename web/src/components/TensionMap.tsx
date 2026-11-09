import { rgb } from "d3";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { curly, ENGINE, tc } from "../lib/format";
import { heat, heatAlpha, rebuildHeat } from "../lib/heat";
import { useReducedMotion, useWidth } from "../lib/hooks";
import type { EKGRow, PromoSummary } from "../lib/types";

const GAP = 3;
/** Row height adapts: 50 episodes fit ~650 px, a short show gets taller rows (10-22 px). */
const rowHeight = (n: number) => Math.max(10, Math.min(22, Math.floor(620 / Math.max(1, n)) - GAP));
const LABEL_W = 38;
const DUR_W = 46;
const TOP = 22; // room for the hero tab above the first row
const STAGGER = 18;
const FADE = 320;

type Props = {
  runId: string;
  ekg: EKGRow[];
  durations: Record<number, number>;
  promos: PromoSummary[];
  lines: Record<string, [number, number, string, string][]>;
  focusId?: string | null;
  onFocus?: (id: string | null) => void;
};

type Hover = { row: number; ep: number; t: number; x: number; y: number };

/** Show-wide tension heatmap: one row per episode, each row normalised to its own duration. */
export function TensionMap({ runId, ekg, durations, promos, lines, focusId, onFocus }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const canvas = useRef<HTMLCanvasElement>(null);
  const reduced = useReducedMotion();
  const navigate = useNavigate();
  const [hover, setHover] = useState<Hover | null>(null);
  const developed = useRef(false);

  const ROW = rowHeight(ekg.length);
  const plotW = Math.max(120, width - LABEL_W - DUR_W);
  const height = TOP + ekg.length * (ROW + GAP) + 4;
  const rowOf = useMemo(() => new Map(ekg.map((e, i) => [e.episode, i])), [ekg]);
  const yOf = (i: number) => TOP + i * (ROW + GAP);
  const xOf = (ep: number, t: number) => LABEL_W + (t / (durations[ep] || 1)) * plotW;

  // Pre-computed cell colours, so the develop animation only blends them.
  const cells = useMemo(() => {
    rebuildHeat();
    return ekg.map((e) => e.values.map((v) => ({ c: rgb(heat(v)), a: heatAlpha(v) })));
  }, [ekg]);

  useEffect(() => {
    const cv = canvas.current;
    if (!cv || !width) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(width * dpr);
    cv.height = Math.round(height * dpr);
    const ctx = cv.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const draw = (elapsed: number) => {
      ctx.clearRect(0, 0, width, height);
      ekg.forEach((e, i) => {
        const p = Math.max(0, Math.min(1, (elapsed - i * STAGGER) / FADE));
        if (p <= 0) return;
        const dur = durations[e.episode] || e.values.length;
        const cw = plotW / dur;
        const y = yOf(i);
        cells[i].forEach(({ c, a }, t) => {
          // "Developing": saturation and density come up together, like film exposure.
          const g = 0.299 * c.r + 0.587 * c.g + 0.114 * c.b;
          const r = g + (c.r - g) * p, gg = g + (c.g - g) * p, b = g + (c.b - g) * p;
          ctx.fillStyle = `rgba(${r | 0},${gg | 0},${b | 0},${a * p})`;
          ctx.fillRect(LABEL_W + t * cw, y, Math.ceil(cw + 0.5), ROW);
        });
      });
    };

    if (reduced || developed.current) {
      draw(Infinity);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const total = ekg.length * STAGGER + FADE;
    const tick = (now: number) => {
      const el = now - t0;
      draw(el);
      if (el < total) raf = requestAnimationFrame(tick);
      else developed.current = true;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [width, height, cells, reduced, plotW]);

  const brackets = promos.filter((p) => p.slot !== "pool");

  function onMove(ev: React.PointerEvent<SVGSVGElement>) {
    const box = (ev.currentTarget as SVGSVGElement).getBoundingClientRect();
    const x = ev.clientX - box.left;
    const y = ev.clientY - box.top;
    const row = Math.floor((y - TOP) / (ROW + GAP));
    if (row < 0 || row >= ekg.length || x < LABEL_W || x > LABEL_W + plotW) {
      setHover(null);
      return;
    }
    const ep = ekg[row].episode;
    const t = ((x - LABEL_W) / plotW) * (durations[ep] || 1);
    setHover({ row, ep, t, x, y: yOf(row) });
  }

  const hoverLine = hover ? (lines[String(hover.ep)] ?? []).find(([s, e]) => s <= hover.t && hover.t < e) : undefined;
  const hoverValue = hover ? ekg[hover.row].values[Math.min(ekg[hover.row].values.length - 1, Math.floor(hover.t))] : 0;

  return (
    <div ref={ref} className="relative select-none">
      <canvas ref={canvas} style={{ width, height }} className="block" aria-hidden />
      <svg
        width={width}
        height={height}
        className="absolute inset-0"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
        role="group"
        aria-label="Tension map: one row per episode. Brackets mark the launch slate."
      >
        {ekg.map((e, i) => (
          <g key={e.episode} aria-hidden>
            <text x={LABEL_W - 8} y={yOf(i) + ROW / 2 + 4} textAnchor="end" className="fill-fog font-cond" fontSize={11}>
              E{e.episode}
            </text>
            <text x={LABEL_W + plotW + 8} y={yOf(i) + ROW / 2 + 4} className="fill-fog font-cond tnum" fontSize={11}>
              {tc(durations[e.episode] ?? 0).slice(0, 5)}
            </text>
          </g>
        ))}

        {/* Arc-bridge connectors between a promo's segments. */}
        {brackets.filter((p) => p.slot !== "bench" || p.id === focusId).map((p) =>
          p.segments.slice(1).map((s, k) => {
            const a = p.segments[k];
            const ia = rowOf.get(a.episode), ib = rowOf.get(s.episode);
            if (ia == null || ib == null) return null;
            const x1 = xOf(a.episode, a.end), y1 = yOf(ia) + ROW / 2;
            const x2 = xOf(s.episode, s.start), y2 = yOf(ib) + ROW / 2;
            const strong = p.slot === "hero" || p.id === focusId;
            return (
              <path
                key={`${p.id}-c${k}`}
                d={`M${x1},${y1} C${x1 + 24},${y1} ${x2 - 24},${y2} ${x2},${y2}`}
                fill="none"
                className={strong ? "stroke-tungsten" : "stroke-fog"}
                strokeWidth={1}
                opacity={strong ? 0.9 : 0.5}
                aria-hidden
              />
            );
          }),
        )}

        {brackets.map((p) => {
          const label = `${p.slot === "hero" ? "Hero" : p.slot === "challenger" ? "Challenger" : "Bench"} ${p.id}, ${ENGINE[p.engine]}, ${p.segments.map((s) => `E${s.episode} ${s.start_tc} to ${s.end_tc}`).join(", then ")}`;
          const focused = p.id === focusId;
          // The tab marks the longest segment (a flash-forward opens on a 3 s peak).
          const tabSeg = p.segments.reduce((bi, s, i, arr) => (s.end - s.start > arr[bi].end - arr[bi].start ? i : bi), 0);
          return (
            <a
              key={p.id}
              href={`/run/${runId}/promo/${p.id}`}
              aria-label={label}
              onClick={(e) => {
                e.preventDefault();
                navigate(`/run/${runId}/promo/${p.id}`);
              }}
              onMouseEnter={() => onFocus?.(p.id)}
              onMouseLeave={() => onFocus?.(null)}
              onFocus={() => onFocus?.(p.id)}
              onBlur={() => onFocus?.(null)}
              className="cursor-pointer"
            >
              {p.segments.map((s, k) => {
                const i = rowOf.get(s.episode);
                if (i == null) return null;
                const x = xOf(s.episode, s.start), w = Math.max(3, xOf(s.episode, s.end) - x);
                const y = yOf(i) - 2;
                const cls =
                  p.slot === "hero"
                    ? "stroke-tungsten"
                    : p.slot === "challenger"
                      ? "stroke-bone"
                      : "stroke-fog";
                return (
                  <g key={k}>
                    <rect x={x - 3} y={y - 3} width={w + 6} height={ROW + 10} fill="transparent" />
                    <rect
                      x={x}
                      y={y}
                      width={w}
                      height={ROW + 4}
                      rx={2}
                      fill={focused ? "rgb(var(--bone) / 0.12)" : "transparent"}
                      className={cls}
                      strokeWidth={p.slot === "hero" ? 2 : p.slot === "challenger" ? 1.5 : 1}
                      strokeDasharray={p.slot === "challenger" ? "4 3" : undefined}
                      opacity={p.slot === "bench" && !focused ? 0.75 : 1}
                    />
                    {p.slot === "hero" && k === tabSeg && (
                      <g aria-hidden>
                        <rect x={x} y={y - 15} width={34} height={13} rx={2} className="fill-tungsten" />
                        <text x={x + 17} y={y - 5} textAnchor="middle" className="fill-ink font-cond" fontSize={10} fontWeight={600}>
                          Hero
                        </text>
                      </g>
                    )}
                  </g>
                );
              })}
            </a>
          );
        })}

        {hover && (
          <line
            x1={hover.x}
            x2={hover.x}
            y1={hover.y - 3}
            y2={hover.y + ROW + 3}
            className="stroke-bone"
            strokeWidth={1.5}
            pointerEvents="none"
            aria-hidden
          />
        )}
      </svg>

      {hover && (
        <div
          role="tooltip"
          className="pointer-events-none absolute z-10 w-72 rounded-panel border border-rule bg-console p-3 text-sm"
          style={{
            left: Math.min(Math.max(0, hover.x - 144), Math.max(0, width - 288)),
            top: hover.y + ROW + 10 + (hover.row > ekg.length - 12 ? -(ROW + 130) : 0),
          }}
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-semibold">Episode {hover.ep}</span>
            <span className="font-script tnum">{tc(hover.t)}</span>
          </div>
          {hoverLine ? (
            <p className="mt-1 font-script text-sm leading-snug">
              <span className="text-fog">{hoverLine[2]}: </span>
              {curly(hoverLine[3])}
            </p>
          ) : (
            <p className="mt-1 text-xs text-fog">No one is speaking here.</p>
          )}
          <div className="mt-2 flex items-center gap-2 font-cond text-xs text-fog">
            <span aria-hidden className="inline-block h-2 w-6 rounded-sm" style={{ background: heat(hoverValue) }} />
            Tension {hoverValue.toFixed(2)}
          </div>
        </div>
      )}
    </div>
  );
}

/** Scale legend for the heat ramp (required for a multi-hue magnitude scale). */
export function HeatLegend() {
  const stops = [0, 0.25, 0.55, 0.8, 1].map((t) => `${heat(t)} ${t * 100}%`).join(", ");
  return (
    <div className="flex items-center gap-3 font-cond text-xs text-fog" aria-label="Tension scale from calm to peak">
      <span>Calm</span>
      <span aria-hidden className="h-2 w-40 rounded-sm" style={{ background: `linear-gradient(90deg, ${stops})` }} />
      <span>Peak</span>
      <span className="ml-4 flex items-center gap-1.5">
        <span aria-hidden className="inline-block h-2.5 w-5 rounded-sm border-2 border-tungsten" /> Hero
      </span>
      <span className="flex items-center gap-1.5">
        <span aria-hidden className="inline-block h-2.5 w-5 rounded-sm border border-dashed border-bone" /> Challenger
      </span>
      <span className="flex items-center gap-1.5">
        <span aria-hidden className="inline-block h-2.5 w-5 rounded-sm border border-fog" /> Bench
      </span>
    </div>
  );
}
