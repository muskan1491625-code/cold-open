import { useEffect, useMemo, useRef, useState } from "react";
import { tc, unquote } from "../lib/format";
import { useKeys, useReducedMotion } from "../lib/hooks";
import type { AtomView } from "../lib/types";

export type Clip = { episode: number; start: number; end: number }[];
export type Event = { t0: number; t1: number; seg: number; atom: AtomView };

/** Lay a promo's atoms on the playback clock (segments end to end). */
export function timeline(clip: Clip, atomsByEp: Map<number, AtomView[]>): { events: Event[]; total: number; cuts: number[] } {
  const events: Event[] = [];
  const cuts: number[] = [];
  let offset = 0;
  clip.forEach((s, k) => {
    if (k > 0) cuts.push(offset);
    for (const a of atomsByEp.get(s.episode) ?? []) {
      if (a.end <= s.start + 0.05 || a.start >= s.end - 0.05) continue;
      events.push({ t0: offset + Math.max(0, a.start - s.start), t1: offset + Math.min(a.end, s.end) - s.start, seg: k, atom: a });
    }
    offset += s.end - s.start;
  });
  events.sort((a, b) => a.t0 - b.t0);
  return { events, total: offset, cuts };
}

const RING = 2 * Math.PI * 13;

/** The 9:16 phone: what a scroller would see and hear, played in real time. */
export function PhoneAnimatic({ clip, atomsByEp, hookAt }: { clip: Clip; atomsByEp: Map<number, AtomView[]>; hookAt: number }) {
  const { events, total, cuts } = useMemo(() => timeline(clip, atomsByEp), [clip, atomsByEp]);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const raf = useRef(0);
  const last = useRef(0);
  const reduced = useReducedMotion();

  useEffect(() => {
    if (!playing) return;
    last.current = performance.now();
    const tick = (now: number) => {
      const dt = (now - last.current) / 1000;
      last.current = now;
      setT((x) => {
        const n = x + dt;
        if (n >= total) {
          setPlaying(false);
          return total;
        }
        return n;
      });
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, total]);

  useEffect(() => setT((x) => Math.min(x, total)), [total]);

  const toggle = () => {
    if (!playing && t >= total - 0.05) setT(0);
    setPlaying((p) => !p);
  };
  useKeys({ " ": (e) => (e.preventDefault(), toggle()) }, [playing, t, total]);

  const active = events.filter((e) => e.t0 <= t && t < e.t1);
  const camera = [...active].reverse().find((e) => e.atom.kind === "camera");
  const action = [...active].reverse().find((e) => e.atom.kind === "action");
  const line = [...active].reverse().find((e) => e.atom.kind === "dialogue");
  const sfx = active.find((e) => e.atom.kind === "sfx" && t - e.t0 < 1.6);
  const flash = !reduced && cuts.some((c) => t >= c && t - c < 0.08);
  const ringP = Math.min(t, 3) / 3;
  const hooked = t >= hookAt;

  return (
    <div className="flex flex-col items-center gap-3">
      <div
        className="phone-screen relative overflow-hidden rounded-phone border border-rule bg-[#0d161c]"
        style={{ width: 300, height: 533 }}
        aria-label="Phone preview of the promo"
        role="img"
      >
        <div className="absolute inset-x-0 top-0 p-4 pr-14">
          {camera && <p className="text-xs italic leading-snug text-fog">{stage(camera.atom.perceived)}</p>}
          {action && <p className="mt-1.5 text-xs italic leading-snug text-bone/80">{stage(action.atom.perceived)}</p>}
        </div>

        <svg className="absolute right-3 top-3" width="32" height="32" viewBox="0 0 32 32" aria-hidden>
          <circle cx="16" cy="16" r="13" fill="none" stroke="rgb(var(--rule))" strokeWidth="3" />
          <circle
            cx="16"
            cy="16"
            r="13"
            fill="none"
            stroke={t === 0 ? "rgb(var(--rule))" : hooked ? "rgb(var(--teal))" : "rgb(var(--rec))"}
            strokeWidth="3"
            strokeDasharray={RING}
            strokeDashoffset={RING * (1 - ringP)}
            transform="rotate(-90 16 16)"
            strokeLinecap="round"
          />
          <text x="16" y="20" textAnchor="middle" fontSize="10" className="fill-fog font-cond">3s</text>
        </svg>

        {sfx && (
          <p className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap font-cond text-xs text-fog">
            [{unquote(sfx.atom.text).slice(0, 40)}]
          </p>
        )}

        <div className="absolute inset-x-0 bottom-0 p-5 pb-8 text-center">
          {line && (
            <>
              <p className="mb-1 font-cond text-xs text-fog">{line.atom.descriptor}</p>
              <p className="font-script text-md leading-snug text-bone" style={{ textShadow: "0 1px 2px rgb(0 0 0 / 0.6)" }}>
                {unquote(line.atom.text)}
              </p>
            </>
          )}
        </div>

        {flash && <div className="absolute inset-0 bg-bone/10" aria-hidden />}
        <p className="absolute bottom-2 left-3 font-script text-xs text-fog tnum" aria-hidden>
          {tc(t)}
        </p>
      </div>

      <div className="flex w-[300px] items-center gap-3">
        <button
          onClick={toggle}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-rule hover:bg-raised"
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? (
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden><rect x="2" y="1" width="3" height="10" fill="currentColor" /><rect x="7" y="1" width="3" height="10" fill="currentColor" /></svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden><path d="M3 1 L11 6 L3 11 Z" fill="currentColor" /></svg>
          )}
        </button>
        <input
          type="range"
          min={0}
          max={total}
          step={0.01}
          value={t}
          onChange={(e) => {
            setPlaying(false);
            setT(Number(e.target.value));
          }}
          aria-label="Scrub through the promo"
          aria-valuetext={`${tc(t)} of ${tc(total)}`}
          className="w-full accent-[rgb(var(--tungsten))]"
        />
        <span className="font-script text-xs text-fog tnum">{tc(total).slice(0, 5)}</span>
      </div>
      <p className="kbd-hint font-cond text-xs text-fog">
        <kbd className="rounded-chip border border-rule px-1">Space</kbd> plays or pauses
      </p>
    </div>
  );
}

const stage = (p: string) => p.replace(/^\[(On screen|Action|Sound): /, "").replace(/\]$/, "");
