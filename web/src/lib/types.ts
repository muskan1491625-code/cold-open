export type Seg = { episode: number; start: number; end: number; start_tc: string; end_tc: string; atom_ids?: string[] };
export type Scores = {
  hook: number; cliff: number; viewer: number; tension: number; quality: number;
  policy_penalty: number; spoiler_penalty: number; paywall_penalty: number; referent_penalty: number; total: number;
};
export type Cue = { label: string | null; tags: string[]; kind: string; descriptor: string | null } | null;
export type PromoSummary = {
  id: string; slot: "hero" | "challenger" | "bench" | "pool"; structure: string; engine: string; segments: Seg[];
  duration: number; hook_text: string; cliffhanger_text: string; hook_speaker: string | null; hook_at: number;
  why: string; policy: "paid_safe" | "review" | "organic_only"; landing_episode: number; free_runway: number;
  scores: Scores; rationale: string; reveals_disclosed: string[]; ungrounded_refs: string[];
  continuity_warnings: string[]; hook_cue?: Cue; cliff_cue?: Cue;
};
export type Decision = {
  promo_id: string; action: "approve_paid" | "organic_only" | "reject"; reason?: string | null;
  trims?: { episode: number; start: number; end: number }[] | null; at: string;
};
export type SlateResp = {
  slate: { hero: string; challengers: string[]; bench: string[]; rationale: Record<string, string> };
  promos: PromoSummary[]; decisions: Record<string, Decision>;
};
export type Character = { canonical: string; aliases: { label: string; first_ep: number; last_ep: number }[]; role: string; visual_descriptor: string };
export type Bible = {
  premise: string; characters: Character[]; arcs: { name: string; first_ep: number; last_ep: number; summary: string }[];
  key_reveals: { id: string; description: string; episode: number }[];
  continuity_conflicts: { entity: string; variants: { spelling: string; episodes: number[] }[]; note: string }[];
};
export type RunReport = {
  run_id: string; show_title: string; started_at: string; finished_at: string; models: Record<string, string>;
  llm_calls: number; tokens_in: number; tokens_out: number; cost_usd: number | null; stage_seconds: Record<string, number>;
  counts: Record<string, number>; constraint_pass: Record<string, number>; notes: string[];
};
export type EKGRow = { episode: number; values: number[]; hotspots: { t: number; value: number; atom_ids: string[] }[] };
export type ShowResp = {
  title: string; stats: Record<string, number>; report: RunReport;
  episodes: { number: number; logline: string; duration: number }[]; bible: Bible;
  ekg: EKGRow[]; lines: Record<string, [number, number, string, string][]>;
};
export type AtomView = {
  id: string; kind: "camera" | "action" | "dialogue" | "sfx"; start: number; end: number; text: string;
  speaker: string | null; label: string | null; tags: string[]; tc: string; descriptor: string | null; perceived: string;
};
export type ViewerResult = {
  stage_a: { stop: number; reason: string };
  stage_b: { main_character: string; what_they_want: string; conflict: string; left_wondering: string; need_next: number; confusing: string[] };
  grader: { who: number; want: number; conflict: number; open_question: number; notes: string };
  first_three_seconds: string; ungrounded_refs: string[];
};
export type FullPromo = PromoSummary & {
  segments: (Seg & { atom_ids: string[] })[]; viewer: ViewerResult | Record<string, never>;
  validation: Record<string, any>; policy_reasons: string[];
};
export type PromoResp = {
  promo: FullPromo; summary: PromoSummary;
  context: { episode: number; duration: number; atoms: AtomView[]; ekg: number[] }[];
  decision: Decision | null;
};
export type RunListItem = {
  id: string; show: string; date: string; models: Record<string, string>; cost_usd: number | null;
  valid: number; llm_calls: number; seconds: number; public?: boolean;
};
export type GalleryItem = { category: string; promo_id: string | null; candidate_id?: string; where: string; what: string; episode: number | null };
export type QualityResp = {
  report: RunReport;
  eval: null | {
    gold: { gold: number; hits: number; hit_rate: number | null; median_start_error_s: number | null; median_end_error_s: number | null };
    gold_matches: { gold: string; episode: number; note: string; iou: number; hit: boolean; promo: string | null }[];
    precision_at_10: number | null; viewer_agreement: { n: number; agreement?: number };
    comprehension: { n: number; median?: number }; constraint_pass: Record<string, number>;
    ablations: Record<string, { gold: { hit_rate: number | null }; violations: Record<string, number>; promos: number }>;
  };
  validation: {
    candidates_checked: number; rejected: number; repaired: number; duplicates: number;
    rejected_by_reason: Record<string, number>; repaired_from_reason: Record<string, number>; first_pass_valid_rate: number;
  };
  gallery: GalleryItem[];
  comprehension: { n: number; median?: number };
};
