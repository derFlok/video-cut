// Shapes of the shared API contract (prefix /api). Optional fields may be absent.

export type Lang = "de" | "en";
export type Format = "9:16" | "16:9" | "1:1" | "4:5";
export type FormatChoice = "auto" | Format;
export const FORMATS: Format[] = ["9:16", "16:9", "1:1", "4:5"];

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

export interface Revision {
  n?: number;
  time: string;
  text: string;
  at_s?: number | null;
  changed?: Record<string, unknown>;
  cost?: number | null;
  video_url?: string;
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
  title?: string | null;
  brief?: string | null;
  created?: string;
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
  hook?: string;
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
