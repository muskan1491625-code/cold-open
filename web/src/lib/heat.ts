import { interpolateLab, scaleLinear } from "d3";

/** Read a token ("59 166 160") from :root as an rgb() string. */
export function token(name: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim().split(/\s+/).map(Number);
  return `rgb(${v[0]}, ${v[1]}, ${v[2]})`;
}

let ramp: ((t: number) => string) | null = null;

/** The data scale is the colour grade: teal (calm) -> tungsten (rising) -> rec (peak), in Lab. */
export function rebuildHeat() {
  const s = scaleLinear<string>().domain([0, 0.55, 1]).range([token("teal"), token("tungsten"), token("rec")]).interpolate(interpolateLab);
  ramp = (t) => s(t);
}

export function heat(t: number): string {
  if (!ramp) rebuildHeat();
  return ramp!(Math.max(0, Math.min(1, t)));
}

/**
 * Opacity that rises with value. The three-hue ramp is not monotonic in lightness,
 * so on the dark bay calm cells would read as loud as peaks; fading them restores
 * "more = brighter" while keeping the brief's colour grade.
 */
export function heatAlpha(t: number): number {
  return 0.16 + 0.84 * Math.pow(Math.max(0, Math.min(1, t)), 1.4);
}
