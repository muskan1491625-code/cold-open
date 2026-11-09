export function tc(seconds: number): string {
  const cs = Math.round(Math.max(0, seconds) * 100);
  const m = Math.floor(cs / 6000);
  const s = Math.floor((cs % 6000) / 100);
  const c = cs % 100;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(c).padStart(2, "0")}`;
}

export const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);
export const usd = (x: number | null | undefined) => (x == null ? "unknown" : `$${x.toFixed(x < 1 ? 4 : 2)}`);
export const secs = (x: number | null | undefined, d = 1) => (x == null ? "—" : `${x.toFixed(d)} s`);

export const ENGINE: Record<string, string> = {
  humiliation_reversal: "Humiliation reversal",
  dangerous_protector: "Dangerous protector",
  hidden_identity: "Hidden identity",
  sacrifice_ultimatum: "Sacrifice ultimatum",
  framed_condemned: "Framed and condemned",
  family_betrayal: "Family betrayal",
  forbidden_scandal: "Forbidden scandal",
  other: "Other",
};
export const STRUCTURE: Record<string, string> = { straight: "Straight", flash_forward: "Flash-forward", arc_bridge: "Arc bridge" };
export const POLICY: Record<string, string> = { paid_safe: "Paid-safe", review: "Review", organic_only: "Organic only" };

export function eps(segs: { episode: number }[]): string {
  const e = [...new Set(segs.map((s) => s.episode))].sort((a, b) => a - b);
  return e.map((x) => `E${x}`).join(" + ");
}

/** Strip the script's straight quotes for display inside our own quote marks. */
export const unquote = (t: string) => t.trim().replace(/^["“]/, "").replace(/["”]$/, "");
export const curly = (t: string) => `“${unquote(t)}”`;
