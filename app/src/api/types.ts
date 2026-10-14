// Shapes of the shared API contract (prefix /api). Optional fields may be absent.

export type Lang = "de" | "en";
export type Format = "9:16" | "16:9" | "1:1" | "4:5";
export type FormatChoice = "auto" | Format;
export const FORMATS: Format[] = ["9:16", "16:9", "1:1", "4:5"];

/** How tightly the reel is cut; "auto" lets the server pick (calm with screen material). */
export type CutStyle = "calm" | "standard" | "tight";
export type CutStyleChoice = "auto" | CutStyle;
export const CUT_STYLES: CutStyleChoice[] = ["auto", "calm", "standard", "tight"];

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
  files?: string[];
  [k: string]: unknown;
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
  thumb_url: string | null;
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
  brief?: string | null;
  created?: string;
  /** The stored choice (absent: automatic) and what it resolves to. */
  cut_style?: CutStyle | "auto" | null;
  cut_style_resolved?: CutStyle | null;
  /** The current reel was cut with another style or format than the resolved one. */
  cut_stale?: boolean;
  /** Length of the camera recording (work/probe.json), for the duration hints. */
  source_duration_s?: number | null;
  has_screen?: boolean;
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
  [k: string]: unknown;
}

export interface Plan {
  markdown: string;
  plan: PlanJson | null;
  sha: string;
}

export type JobEvent =
  | { t: "stage"; stage: Stage }
  | { t: "log"; text: string }
  | { t: "tool"; text: string }
  | { t: "gate"; gate: "plan" | "result" }
  | { t: "done"; ok: boolean }
  | { t: "error"; text: string };
