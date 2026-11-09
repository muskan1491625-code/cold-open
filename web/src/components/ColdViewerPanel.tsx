import type { ViewerResult } from "../lib/types";

function Mark({ v }: { v: number }) {
  if (v >= 1)
    return (
      <span role="img" className="text-teal" aria-label="Understood">
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden><path d="M2.5 7.5 L5.5 10.5 L11.5 3.5" stroke="currentColor" strokeWidth="2" fill="none" /></svg>
      </span>
    );
  if (v > 0)
    return (
      <span role="img" className="text-tungsten" aria-label="Partly understood">
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden><circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" fill="none" /><path d="M7 2 A5 5 0 0 1 7 12 Z" fill="currentColor" /></svg>
      </span>
    );
  return (
    <span role="img" className="text-rec-ink" aria-label="Missed">
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden><path d="M3 3 L11 11 M11 3 L3 11" stroke="currentColor" strokeWidth="2" /></svg>
    </span>
  );
}

/** The model "stranger": what it understood, in its own words, graded against the bible. */
export function ColdViewerPanel({ v }: { v: ViewerResult | Record<string, never> }) {
  if (!("stage_b" in v)) {
    return <p className="text-sm text-fog">This promo was not shown to the Cold Viewer.</p>;
  }
  const b = v.stage_b, g = v.grader;
  const rows: [string, number, string][] = [
    ["Who it is about", g.who, b.main_character],
    ["What they want", g.want, b.what_they_want],
    ["What stands in the way", g.conflict, b.conflict],
    ["Left wanting the next episode", g.open_question, b.left_wondering],
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <p className="text-sm">
          <span className="text-fog">Would stop scrolling </span>
          <span className="font-cond text-lg tnum">{v.stage_a.stop}</span>
          <span className="text-fog"> of 5</span>
        </p>
        <p className="text-sm italic text-fog">“{v.stage_a.reason}”</p>
      </div>
      <ul className="flex flex-col gap-2">
        {rows.map(([label, score, words]) => (
          <li key={label} className="grid grid-cols-[1.25rem_1fr] items-baseline gap-x-2 gap-y-0.5 text-sm sm:grid-cols-[1.25rem_minmax(0,11rem)_1fr]">
            <Mark v={score} />
            <span className="text-fog">{label}</span>
            <span className="col-start-2 italic sm:col-start-auto">“{words}”</span>
          </li>
        ))}
      </ul>
      <p className="text-sm">
        <span className="text-fog">Wants the next episode </span>
        <span className="font-cond tnum">{b.need_next}</span>
        <span className="text-fog"> of 5</span>
      </p>
      {b.confusing.length > 0 && (
        <div>
          <p className="text-sm text-fog">Confused by</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {b.confusing.map((c) => (
              <li key={c} className="rounded-chip border border-rule px-2 py-0.5 text-xs">
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}
      {g.notes && <p className="text-xs text-fog">Grader: {g.notes}</p>}
    </div>
  );
}
