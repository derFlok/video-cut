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

/** Slides and animations of a job (job.json "slides"). */
export type SlidesMode = "none" | "upload" | "auto" | "both";
export const SLIDES_MODES: SlidesMode[] = ["none", "upload", "auto", "both"];
export const SLIDE_WISH_MAX = 2000;
export type SlideLayout = "slide" | "slide_split" | "slide_full";

export interface PlanSlide {
  id: string;
  template: string;
  content: Record<string, unknown>;
  /** Source seconds of the words the slide illustrates. */
  t0: number;
  t1: number;
  layout: SlideLayout | string;
  why?: string;
  source?: "auto" | "upload";
  still?: string | null;
}

export interface JobSlides {
  mode: SlidesMode;
  wish: string;
  pages: number;
  /** The plan was made for other slide settings: plan again. */
  stale: boolean;
  /** Where the reel shows each slide (output seconds). */
  marks: { id: string; t0: number; t1: number; layout?: string | null }[];
}
/** Material of a job (job.json "material"): stock footage, AI-generated stills, both. */
export type MaterialMode = "none" | "stock" | "generated" | "both";
export const MATERIAL_MODES: MaterialMode[] = ["none", "stock", "generated", "both"];
export const MATERIAL_WISH_MAX = 2000;
export type ImageProviderName = "none" | "gemini" | "openai" | "local";
export const IMAGE_PROVIDERS: ImageProviderName[] = ["none", "gemini", "openai", "local"];
export type StockProviderName = "pixabay" | "pexels" | "coverr" | "openverse";
export const STOCK_PROVIDERS: StockProviderName[] = ["pixabay", "pexels", "coverr", "openverse"];
/** Keychain accounts of the material providers' API keys (write-only from the app). */
export type SecretAccount = "pixabay-api-key" | "pexels-api-key" | "coverr-api-key" | "gemini-api-key" | "openai-api-key";
export const SECRET_ACCOUNTS: SecretAccount[] = ["pixabay-api-key", "pexels-api-key", "coverr-api-key", "gemini-api-key", "openai-api-key"];

/** GET/PUT /api/settings/material. */
export interface MaterialSettings {
  material_default_mode: MaterialMode;
  image_provider: ImageProviderName;
  stock_providers: StockProviderName[];
  material_budget_usd: number;
  image_providers: { name: Exclude<ImageProviderName, "none">; available: boolean; reason: string; registered: boolean }[];
  secrets: Record<SecretAccount, boolean>;
}

export type MaterialKind = "broll" | "still" | "icon";
export type MaterialStyle = "photo" | "illustration";

/** A material wish of the plan (plan.json "material_wishes"). */
export interface MaterialWish {
  n: number;
  kind: MaterialKind;
  src_from: number;
  src_to: number;
  query?: string | null;
  prompt?: string | null;
  style?: MaterialStyle;
  why?: string;
}

export interface MaterialCandidate {
  id: string;
  kind: "video" | "image";
  thumb_url: string;
  file_url: string;
  width: number;
  height: number;
  /** Under 1280 px on the long side: it will be upscaled. */
  small?: boolean;
  duration_s?: number | null;
  source: string;
  source_url?: string | null;
  author?: string | null;
  licence: string;
  /** Link to the licence text (CC licences from Openverse). */
  licence_url?: string | null;
  attribution?: string | null;
  ai_generated: boolean;
  model?: string | null;
  watermark?: string | null;
}

/** GET /api/jobs/{id}/material (also the answer of a choice). */
export interface MaterialView {
  mode: MaterialMode;
  image_provider: ImageProviderName;
  budget_usd: number;
  cost_usd: number;
  wishes: (MaterialWish & { decided: boolean; chosen: string | null; candidates: MaterialCandidate[] })[];
  pending: number[];
  fetched: boolean;
}

/** Job detail "material". */
export interface JobMaterial {
  mode: MaterialMode;
  wish: string;
  stale: boolean;
  wishes: number;
  pending: number[];
  fetched: boolean;
}

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
  | "material"
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

/** What the official claude program says about itself (GET /api/setup/status). */
export interface ClaudeDetection {
  state: "checking" | "done";
  installed?: boolean;
  version?: string | null;
  signed_in?: boolean;
  method?: string | null;
  /** The account e-mail, only when the official program prints one (never stored). */
  account?: string | null;
  subscription?: string | null;
  error?: string | null;
}

export type AuthMode = "local" | "apikey";

export interface SetupStatus {
  done: boolean;
  mode: AuthMode;
  api_key_stored: boolean;
  claude: ClaudeDetection;
  console_url: string;
}

export interface SetupTest {
  ok: boolean;
  message: string;
  seconds: number | null;
  mode?: AuthMode;
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
  /** The default brand (settings.json default_brand). */
  default?: boolean;
}

export interface BrandSettings {
  default_brand: string | null;
  theme_follows_brand: boolean;
}

/** A token the contrast guard replaced with the neutral one (theme.py). */
export interface ThemeNote {
  code: "text" | "muted" | "accent" | "surface";
  scheme: "light" | "dark";
  ratio: number;
}

/** The app's look from a brand: GET /api/theme, PUT /api/settings/brand, and (without
 * settings) the editor's preview POST /api/brands/{id}/theme. css "" is the neutral UI. */
export interface ThemeView {
  brand_id: string | null;
  name: string | null;
  css: string;
  logo: { light: string | null; dark: string | null };
  notes: ThemeNote[];
  invalid?: boolean;
  settings?: BrandSettings | null;
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
  /** The human edited the transcript since the reel's captions were set: set them again
   * without Claude (/captions/refresh). */
  captions_stale?: boolean;
  /** The chosen target length (null: automatic) and the range the plan aims for (null: no
   * fixed length, as long as the content needs). */
  target_s?: number | null;
  target_resolved?: TargetRange | null;
  /** The plan was made for another target length (plan_target_s; null: automatic): it
   * must be planned again before it can be approved. */
  plan_target_stale?: boolean;
  plan_target_s?: number | null;
  slides?: JobSlides;
  material?: JobMaterial;
  /** Where the job waits for the human (status "waiting"). */
  gate?: "plan" | "material" | "result" | null;
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
  slides?: PlanSlide[];
  material_wishes?: MaterialWish[];
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
  | { t: "gate"; gate: "plan" | "material" | "result" }
  | { t: "done"; ok: boolean }
  | { t: "error"; text: string };

/** One word of GET /api/jobs/{id}/transcript: source time, and the time in the finished
 * reel when the cut keeps it (null otherwise). */
export interface TranscriptWord {
  i: number;
  w: string;
  t0: number;
  t1: number;
  out_t0: number | null;
  out_t1: number | null;
  /** The human changed it; `asr` is what the transcriber wrote. */
  edited?: boolean;
  asr?: string;
  /** Hidden from the captions (an empty edit). */
  hidden?: boolean;
}

/** GET /api/jobs/{id}/transcript (also the answer of an edit or an undo). */
export interface TranscriptView {
  id: string;
  total: number;
  offset: number;
  words: TranscriptWord[];
  has_reel: boolean;
  edits: number;
  can_undo: boolean;
  edits_at: string | null;
  /** Words that differ from the human's edit record (set back on the next refresh). */
  drift: number;
  captions_stale: boolean;
  overlays_stale: boolean;
}

/** GET /api/jobs/{id}/disclosure: what to tick and credit when publishing (written by the
 * last full render; `ready` false before one). Texts are in the job's language. */
export type DisclosureAi = {
  n: number;
  kind: string;
  t0: number;
  t1: number;
  model: string | null;
  prompt: string | null;
  style: "photo" | "illustration";
  labelled: boolean;
};
export type DisclosureStock = {
  n: number;
  source: string | null;
  author: string | null;
  licence: string | null;
  attribution: string | null;
  source_url: string | null;
};
export type DisclosurePlatform = { id: string; label: string; status: "required" | "not needed"; why: string };
export type Disclosure = {
  ready: boolean;
  lang?: string;
  ai?: DisclosureAi[];
  stock?: DisclosureStock[];
  platforms?: DisclosurePlatform[];
  snippet?: string;
  comment?: string | null;
  c2pa?: "signed" | "skipped" | "failed" | null;
};

/** GET/PUT /api/settings/labels. */
export type LabelSettings = { ai_label_illustrations: boolean };
