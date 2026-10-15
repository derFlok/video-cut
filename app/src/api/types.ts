// Shapes of the shared API contract (prefix /api). Optional fields may be absent.

export type Lang = "de" | "en";
export type Format = "9:16" | "16:9" | "1:1" | "4:5";
export type FormatChoice = "auto" | Format;
export const FORMATS: Format[] = ["9:16", "16:9", "1:1", "4:5"];

/** How tightly the reel is cut; "auto" lets the server pick (calm with screen material). */
export type CutStyle = "calm" | "standard" | "tight";
export type CutStyleChoice = "auto" | CutStyle;
export const CUT_STYLES: CutStyleChoice[] = ["auto", "calm", "standard", "tight"];

/** How personal data on the screen is hidden: auto (cut off full-width bars at the top or
 * bottom of the screen, blur the rest), blur, crop (cut off bands along an edge) or block. */
export type RedactStyle = "auto" | "blur" | "crop" | "block";
export const REDACT_STYLES: RedactStyle[] = ["auto", "blur", "crop", "block"];

/** Target length of the video in seconds; null (or "auto" in requests) lets the server
 * decide: by type for talking heads, as long as needed for demos. */
export type TargetChoice = number | null;
/** The range the plan aims for; null: no fixed length. */
export interface TargetRange {
  min_s: number;
  max_s: number;
}
export const TARGET_PRESETS = [30, 60, 90, 120, 180, 300, 600];
export const TARGET_MIN_S = 5;
export const TARGET_MAX_S = 3 * 3600;

export type Stage =
  | "prep"
  | "plan"
  | "approval"
  | "cut"
  | "overlays"
  | "verify"
  | "done"
  | "revise"
  | "plan_revise"
  | "error";

export type Status = "new" | "running" | "waiting" | "done" | "error";

export interface Session {
  version: string;
  lang: Lang;
  claude: { ok: boolean; how: "subscription" | "api_key" | null };
}

export interface VideoType {
  id: string;
  name_de: string;
  name_en: string;
  desc_de: string;
  desc_en: string;
  needs: string[];
  optional: string[];
}

export interface BrandSummary {
  id: string;
  name: string;
  preview_url: string;
}

/** brand.json plus the files in the brand folder. Unknown fields are kept as they are. */
export interface Brand {
  name: string;
  colors: Record<string, string>;
  fonts: { display?: string; body?: string; weights?: number[]; [k: string]: unknown };
  logo: { light?: string | null; dark?: string | null; placement?: string; [k: string]: unknown };
  captions: { pace?: string; style?: string; [k: string]: unknown };
  voice: { address?: string; [k: string]: unknown };
  /** Transcript vocabulary: canonical spellings and their known misspellings. */
  vocabulary?: VocabEntry[];
  files?: string[];
  [k: string]: unknown;
}

/** One transcript vocabulary entry (brand.json / job.json "vocabulary"). */
export interface VocabEntry {
  term: string;
  variants: string[];
  note?: string;
}

/** A possible misspelling `videocut tool vocab suggest` found (never applied by itself). */
export interface VocabSuggestion {
  term: string;
  heard: string;
  distance: number;
  count: number;
  times: number[];
  conf: number | null;
}

/** GET /api/jobs/{id}/vocabulary. */
export interface JobVocabulary {
  id: string;
  vocabulary: VocabEntry[];
  brand: string | null;
  brand_vocabulary: VocabEntry[];
  effective: VocabEntry[];
  applied: { applied: number; terms: Record<string, number> } | null;
  has_transcript: boolean;
  suggestions: VocabSuggestion[];
  overlays_stale: boolean;
}

export interface JobSummary {
  id: string;
  created: string;
  type: string;
  brand: string | null;
  format: FormatChoice | null;
  status: Status;
  stage: Stage;
  title: string | null;
  /** The id of the job this one was duplicated from. */
  copy_of?: string | null;
  thumb_url: string | null;
  target_s?: number | null;
}

export type PdKind = "email" | "phone" | "name" | "customer" | "other";

export interface PdCandidate {
  id: number;
  kind: PdKind;
  t0?: number;
  t1?: number;
  asset?: string;
  box: [number, number, number, number];
  reason: string;
  still?: string;
  /** The exact text shown; its position is measured, not estimated. */
  text?: string;
  /** "ok": the text is covered where it is; otherwise (some of) the whole area is. */
  measured?: "ok" | "partial" | "failed";
  note?: string;
  /** The boxes drawn on `still` (what will be covered at that moment). */
  still_boxes?: [number, number, number, number][];
}

export interface RedactionDecision {
  mode: "demo" | "redact_all" | "redact";
  ids: number[];
}

export interface Run {
  phase: string;
  wall_s: number;
  turns: number | null;
  cost_usd: number | null;
}

/** One line of work/revisions.jsonl. A successful revision made `version`; `video_url`
 * plays that version while its file is kept. */
export interface Revision {
  n?: number;
  time: string;
  text: string;
  at_s?: number | null;
  ok?: boolean;
  error?: string | null;
  summary?: string | null;
  changed?: Record<string, unknown>;
  verify_ok?: boolean;
  cost?: number | null;
  turns?: number | null;
  wall_s?: number | null;
  version?: number;
  previous_version?: number;
  video_url?: string;
}

/** A playable state of the reel: version 1 is the first cut, each successful revision
 * makes the next one. `video_url` is null once out/history dropped the file. */
export interface Version {
  version: number;
  revision: number | null;
  text: string | null;
  video_url: string | null;
  current: boolean;
}

export interface VerifySummary {
  ok: boolean;
  checks?: Record<string, { ok: boolean; [k: string]: unknown }>;
  flagged?: { cut: number; t: number; flags: string[] }[];
  overlay_flagged?: { scene: string | null; t0: number; t1: number; flags: string[] }[];
  screen_flagged?: { flags: string[]; [k: string]: unknown }[];
  warnings?: string[];
  [k: string]: unknown;
}

export interface JobDetail {
  id: string;
  type: string;
  brand: string | null;
  format: FormatChoice | null;
  format_resolved: Format | null;
  status: Status;
  stage: Stage;
  approved: boolean;
  plan_sha: string | null;
  redaction: { candidates: PdCandidate[]; decision: Record<string, unknown> | null };
  outputs: { reel?: string | null; preview?: string | null; cover?: string | null } | null;
  verify: VerifySummary | null;
  runs: Run[];
  revisions: Revision[];
  versions?: Version[];
  title?: string | null;
  copy_of?: string | null;
  brief?: string | null;
  created?: string;
  /** The stored choice (absent: automatic) and what it resolves to. */
  cut_style?: CutStyle | "auto" | null;
  cut_style_resolved?: CutStyle | null;
  /** How personal data is hidden (resolved: the job's choice or the brand's default). */
  redact_style?: RedactStyle | null;
  redact_style_default?: boolean;
  /** The current reel was cut with another style or format than the resolved one. */
  cut_stale?: boolean;
  /** A vocabulary changed the captions of the finished reel: set them again (/overlays). */
  overlays_stale?: boolean;
  /** The chosen target length (null: automatic) and the range the plan aims for (null: no
   * fixed length, as long as the content needs). */
  target_s?: number | null;
  target_resolved?: TargetRange | null;
  /** The plan was made for another target length (plan_target_s; null: automatic): it
   * must be planned again before it can be approved. */
  plan_target_stale?: boolean;
  plan_target_s?: number | null;
  /** Length of the camera recording (work/probe.json), for the duration hints. */
  source_duration_s?: number | null;
  has_screen?: boolean;
  /** When the running stage started (ISO); null when nothing runs. */
  stage_since?: string | null;
  /** The stage a failed run stopped in. */
  failed_stage?: Stage | null;
}

export interface PlanKeep {
  n: number;
  src: [number, number];
  text: string;
  why?: string;
}

export interface PlanView {
  n: number;
  src: [number, number];
  layout: string;
  zoom_box?: number[] | null;
  still?: string | null;
}

export interface PlanJson {
  title?: string;
  format?: string;
  hook?: string | { text?: string; src?: [number, number] };
  core_message?: string;
  keep_list?: PlanKeep[];
  views?: PlanView[];
  overlays?: { scene?: string; t0?: number; t1?: number; text?: string; [k: string]: unknown }[];
  personal_data?: PdCandidate[];
  risks?: (string | { text?: string; [k: string]: unknown })[];
  change_note?: string;
  /** The target the plan was made for and its estimate of the output length (seconds). */
  target_s?: number;
  estimated_s?: number;
  [k: string]: unknown;
}

export interface Plan {
  markdown: string;
  plan: PlanJson | null;
  sha: string;
}

export type JobEvent =
  /** `at`: when the stage started (ISO); a replayed event keeps its original time. */
  | { t: "stage"; stage: Stage; at?: string }
  | { t: "log"; text: string }
  | { t: "tool"; text: string }
  | { t: "gate"; gate: "plan" | "result" }
  | { t: "done"; ok: boolean }
  | { t: "error"; text: string };
