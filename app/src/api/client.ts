// API client. Authentication is the HttpOnly session cookie set by the server (the UI never
// sees the token); writes are same-origin, so the browser sends the Origin header the
// server checks. A Transport makes the client testable: the real one uses fetch, XHR (for
// upload progress) and EventSource; the mock one (src/mock) answers from memory.

import type {
  Brand,
  BrandSettings,
  BrandSummary,
  CutStyleChoice,
  Disclosure,
  RedactStyle,
  SfxKind,
  SfxLevel,
  SfxSettings,
  Format,
  FormatChoice,
  JobDetail,
  JobEvent,
  JobSummary,
  JobVocabulary,
  ImageModelStatus,
  MaterialMode,
  MaterialSettings,
  MaterialView,
  LabelSettings,
  Plan,
  SecretAccount,
  RedactionDecision,
  Session,
  SetupStatus,
  SetupTest,
  AuthMode,
  SlidesMode,
  TargetChoice,
  TargetRange,
  ThemeView,
  TranscriptView,
  VideoType,
  VocabEntry,
} from "./types";

/** POST /api/jobs/{id}/vocabulary/apply. */
export type VocabularyApplied = JobVocabulary & {
  result: { applied: number; terms: Record<string, number>; caption_words: number; overlays_stale: boolean };
};

/** PUT /api/jobs/{id}/target. */
export interface TargetResult {
  id: string;
  target_s: number | null;
  target_resolved: TargetRange | null;
  previous: number | null;
  plan_stale: boolean;
  approval_revoked: boolean;
  reel_stale: boolean;
  approved: boolean;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
  /** The conflict kind of a 409 ({detail: {code}}): busy | stale | state | exists | read_only. */
  get code(): string | null {
    const d = (this.detail as { detail?: { code?: unknown } } | null)?.detail;
    return d && typeof d === "object" && typeof d.code === "string" ? d.code : null;
  }
  /** Validation messages the server sent (brand check, upload check), if any. */
  get errors(): string[] {
    const d = this.detail as { errors?: unknown; detail?: unknown } | undefined;
    const list = d?.errors ?? (d && typeof d.detail === "object" ? (d.detail as { errors?: unknown }).errors : null);
    return Array.isArray(list) ? list.map(String) : [];
  }
}

export interface UploadProgress {
  loaded: number;
  total: number;
}

export interface Transport {
  request(method: string, path: string, body?: unknown): Promise<Response>;
  upload(path: string, form: FormData, onProgress: (p: UploadProgress) => void, signal?: AbortSignal): Promise<Response>;
  events(path: string, onEvent: (ev: JobEvent) => void, onError: () => void): () => void;
  /** URL for <img>/<video>; the mock turns it into a data URL. */
  url(path: string): string;
}

export const realTransport: Transport = {
  request(method, path, body) {
    const init: RequestInit = { method, credentials: "same-origin", headers: {} };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) {
      init.body = JSON.stringify(body);
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    }
    return fetch(path, init);
  },
  upload(path, form, onProgress, signal) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", path);
      xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => onProgress({ loaded: e.loaded, total: e.lengthComputable ? e.total : 0 });
      xhr.onload = () =>
        resolve(new Response(xhr.responseText, { status: xhr.status, headers: { "Content-Type": "application/json" } }));
      xhr.onerror = () => reject(new ApiError(0, "network"));
      xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
      signal?.addEventListener("abort", () => xhr.abort());
      xhr.send(form);
    });
  },
  events(path, onEvent, onError) {
    const es = new EventSource(path, { withCredentials: true });
    es.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data) as JobEvent);
      } catch {
        /* ignore malformed lines */
      }
    };
    es.onerror = () => onError();
    return () => es.close();
  },
  url: (path) => path,
};

let transport: Transport = realTransport;
export function setTransport(t: Transport): void {
  transport = t;
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const d = data as { detail?: unknown; error?: unknown } | null;
    const msg =
      (typeof d?.detail === "string" && d.detail) ||
      (typeof (d?.detail as { message?: unknown } | undefined)?.message === "string" &&
        (d!.detail as { message: string }).message) ||
      (typeof d?.error === "string" && d.error) ||
      (typeof data === "string" && data) ||
      res.statusText ||
      `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, data);
  }
  return data as T;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await transport.request(method, path, body);
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, "network");
  }
  return parse<T>(res);
}

const enc = encodeURIComponent;
const J = (id: string) => `/api/jobs/${enc(id)}`;

export const api = {
  session: () => call<Session>("GET", "/api/session"),
  types: () => call<VideoType[]>("GET", "/api/types"),

  setupStatus: (refresh = false) => call<SetupStatus>("GET", `/api/setup/status${refresh ? "?refresh=1" : ""}`),
  /** Choose the mode; an API key goes to the keychain on the server and never comes back. */
  setupAuth: (mode: AuthMode, apiKey?: string) =>
    call<SetupStatus>("POST", "/api/setup/auth", apiKey ? { mode, api_key: apiKey } : { mode }),
  setupTest: () => call<SetupTest>("POST", "/api/setup/test", {}),
  /** The "Beenden" button: the server stops (409 busy while a run is active, unless force). */
  shutdown: (force = false) => call<{ stopping: boolean }>("POST", "/api/shutdown", { force }),

  brands: () => call<BrandSummary[]>("GET", "/api/brands"),
  brand: (id: string) => call<Brand>("GET", `/api/brands/${enc(id)}`),
  saveBrand: (id: string, tokens: Brand) => call<Brand>("PUT", `/api/brands/${enc(id)}`, tokens),
  newBrand: (name: string) => call<{ id: string }>("POST", "/api/brands", { name }),
  deleteBrand: (id: string) => call<{ deleted: string }>("DELETE", `/api/brands/${enc(id)}`),
  theme: () => call<ThemeView>("GET", "/api/theme"),
  /** Only the fields given change; answers with the theme that follows from them. */
  saveBrandSettings: (changes: Partial<BrandSettings>) => call<ThemeView>("PUT", "/api/settings/brand", changes),
  /** The theme of an edited, unsaved brand (live preview, contrast hints). */
  previewTheme: (id: string, tokens: Brand) => call<ThemeView>("POST", `/api/brands/${enc(id)}/theme`, tokens),
  brandFile: (id: string, kind: "logo" | "font", file: File, role?: string) => {
    const form = new FormData();
    form.append(kind, file);
    if (role) form.append("role", role);
    return call<Brand>("POST", `/api/brands/${enc(id)}/files`, form);
  },
  sheetUrl: (id: string, format: Format, bust = "") =>
    transport.url(`/api/brands/${enc(id)}/sheet?format=${enc(format)}${bust ? `&v=${enc(bust)}` : ""}`),

  materialSettings: () => call<MaterialSettings>("GET", "/api/settings/material"),
  /** Only the fields given change. */
  saveMaterialSettings: (
    changes: Partial<Pick<MaterialSettings, "material_default_mode" | "image_provider" | "stock_providers" | "material_budget_usd">>,
  ) => call<MaterialSettings>("PUT", "/api/settings/material", changes),
  /** The optional local image model: state, sizes, progress while it installs. */
  imageModel: () => call<ImageModelStatus>("GET", "/api/models/image"),
  installImageModel: () => call<ImageModelStatus>("POST", "/api/models/image/install"),
  cancelImageModel: () => call<ImageModelStatus>("POST", "/api/models/image/cancel"),
  removeImageModel: () => call<ImageModelStatus>("DELETE", "/api/models/image"),
  /** Which keys are stored (never their values). */
  secrets: () => call<{ secrets: Record<SecretAccount, boolean> }>("GET", "/api/settings/secrets"),
  /** Write-only: the key goes to the keychain; "" removes it. */
  setSecret: (account: SecretAccount, value: string) =>
    call<{ secrets: Record<SecretAccount, boolean> }>("PUT", "/api/settings/secrets", { account, value }),
  labelSettings: () => call<LabelSettings>("GET", "/api/settings/labels"),
  saveLabelSettings: (s: LabelSettings) => call<LabelSettings>("PUT", "/api/settings/labels", s),
  disclosure: (id: string) => call<Disclosure>("GET", `${J(id)}/disclosure`),
  sfxSettings: () => call<SfxSettings>("GET", "/api/settings/sfx"),
  saveSfxDefault: (sfx_default: SfxLevel) => call<SfxSettings>("PUT", "/api/settings/sfx", { sfx_default }),
  fetchSfx: (kind?: SfxKind) => call<SfxSettings>("POST", "/api/settings/sfx/fetch", kind ? { kind } : {}),
  uploadSfx: (kind: SfxKind, file: File, title?: string) => {
    const form = new FormData();
    form.append("sound", file);
    if (title) form.append("title", title);
    return call<SfxSettings>("POST", `/api/settings/sfx/${kind}`, form);
  },
  deleteSfx: (kind: SfxKind, id: string) => call<SfxSettings>("DELETE", `/api/settings/sfx/${kind}/${id}`),
  sfxAudioUrl: (kind: SfxKind, id: string) => `/api/settings/sfx/${kind}/${id}/audio`,

  jobs: () => call<JobSummary[]>("GET", "/api/jobs"),
  job: (id: string) => call<JobDetail>("GET", J(id)),
  createJob: async (
    fields: {
      camera: File;
      screen?: File | null;
      assets?: File[];
      type: string;
      brand?: string;
      format?: FormatChoice;
      brief?: string;
      cut_style?: CutStyleChoice;
      redact_style?: RedactStyle;
      /** Seconds, or null for automatic (sent as "auto"). */
      target_s?: TargetChoice;
      /** The video's own transcript vocabulary. */
      vocabulary?: VocabEntry[];
      /** Slides and animations: mode, wish, and the human's own pages (PDF or images). */
      slides_mode?: SlidesMode;
      slides_wish?: string;
      slides?: File[];
      /** Material: stock footage and/or AI-generated stills, with a free-text wish. */
      material_mode?: MaterialMode;
      material_wish?: string;
    },
    onProgress: (p: UploadProgress) => void,
    signal?: AbortSignal,
  ) => {
    const form = new FormData();
    form.append("type", fields.type);
    if (fields.brand) form.append("brand", fields.brand);
    if (fields.format) form.append("format", fields.format);
    if (fields.brief) form.append("brief", fields.brief);
    if (fields.cut_style) form.append("cut_style", fields.cut_style);
    if (fields.redact_style) form.append("redact_style", fields.redact_style);
    if (fields.target_s !== undefined) form.append("target_s", fields.target_s === null ? "auto" : String(fields.target_s));
    if (fields.vocabulary?.length) form.append("vocabulary", JSON.stringify(fields.vocabulary));
    if (fields.slides_mode && fields.slides_mode !== "none") form.append("slides_mode", fields.slides_mode);
    if (fields.slides_wish) form.append("slides_wish", fields.slides_wish);
    for (const f of fields.slides ?? []) form.append("slides", f);
    if (fields.material_mode && fields.material_mode !== "none") form.append("material_mode", fields.material_mode);
    if (fields.material_wish) form.append("material_wish", fields.material_wish);
    form.append("camera", fields.camera);
    if (fields.screen) form.append("screen", fields.screen);
    for (const a of fields.assets ?? []) form.append("assets", a);
    let res: Response;
    try {
      res = await transport.upload("/api/jobs", form, onProgress, signal);
    } catch (e) {
      if (e instanceof ApiError || (e instanceof DOMException && e.name === "AbortError")) throw e;
      throw new ApiError(0, "network");
    }
    return parse<{ id: string }>(res);
  },
  start: (id: string) => call<unknown>("POST", `${J(id)}/start`),
  remove: (id: string) => call<unknown>("DELETE", J(id)),
  /** Copy the job; keep_approval takes over the approval and the personal-data decision. */
  duplicate: (id: string, body: { title?: string; keep_approval: boolean }) =>
    call<JobSummary>("POST", `${J(id)}/duplicate`, body),
  setTitle: (id: string, title: string) => call<{ id: string; title: string }>("PUT", `${J(id)}/title`, { title }),
  events: (id: string, onEvent: (ev: JobEvent) => void, onError: () => void) =>
    transport.events(`${J(id)}/events`, onEvent, onError),

  plan: (id: string) => call<Plan>("GET", `${J(id)}/plan`),
  savePlan: (id: string, markdown: string, sha: string) =>
    call<Plan>("PUT", `${J(id)}/plan`, { markdown, sha }),
  /** `slide`: the id of the plan's slide the feedback is about. */
  planFeedback: (id: string, text: string, slide?: string) =>
    call<unknown>("POST", `${J(id)}/plan/feedback`, slide ? { text, slide } : { text }),
  approve: (id: string, plan_sha: string, redaction: RedactionDecision | null) =>
    call<unknown>("POST", `${J(id)}/approve`, redaction ? { plan_sha, redaction } : { plan_sha }),
  setCutStyle: (id: string, cut_style: CutStyleChoice) => call<JobDetail>("PUT", `${J(id)}/cut-style`, { cut_style }),
  /** How personal data is hidden; a reel that hid it otherwise goes stale (recut). */
  setJobSfx: (id: string, sfx: SfxLevel | "default") =>
    call<{ sfx: SfxLevel | null; sfx_resolved: SfxLevel; cut_stale: boolean }>("PUT", `${J(id)}/sfx`, { sfx }),
  setRedactStyle: (id: string, redact_style: RedactStyle) =>
    call<{ redact_style: RedactStyle; cut_stale: boolean }>("PUT", `${J(id)}/redact-style`, { redact_style }),
  recut: (id: string) => call<unknown>("POST", `${J(id)}/recut`),
  /** Store the target length; a plan made for another one goes stale (plan again). */
  setTarget: (id: string, target_s: TargetChoice) =>
    call<TargetResult>("PUT", `${J(id)}/target`, { target_s: target_s === null ? "auto" : target_s }),
  vocabulary: (id: string) => call<JobVocabulary>("GET", `${J(id)}/vocabulary`),
  saveVocabulary: (id: string, vocabulary: VocabEntry[]) =>
    call<JobVocabulary>("PUT", `${J(id)}/vocabulary`, { vocabulary }),
  /** Apply brand + job vocabulary to the existing transcript (and captions). */
  applyVocabulary: (id: string) => call<VocabularyApplied>("POST", `${J(id)}/vocabulary/apply`),
  /** Set captions and overlays again on the kept cut (after the vocabulary changed them). */
  redoOverlays: (id: string) => call<unknown>("POST", `${J(id)}/overlays`),
  transcript: (id: string) => call<TranscriptView>("GET", `${J(id)}/transcript`),
  /** One word's new text ("" hides it from the captions); `t0` guards against a changed transcript. */
  editWord: (id: string, i: number, text: string, t0?: number) =>
    call<TranscriptView>("PATCH", `${J(id)}/transcript`, t0 === undefined ? { i, text } : { i, text, t0 }),
  undoWord: (id: string) => call<TranscriptView>("POST", `${J(id)}/transcript/undo`),
  /** Set the captions again with the edited transcript (tool steps, no Claude run). */
  refreshCaptions: (id: string) => call<unknown>("POST", `${J(id)}/captions/refresh`),
  feedback: (id: string, text: string, at_s?: number) =>
    call<unknown>("POST", `${J(id)}/feedback`, at_s === undefined ? { text } : { text, at_s }),

  material: (id: string) => call<MaterialView>("GET", `${J(id)}/material`),
  /** Fetch candidates (all wishes, or one with an edited query / prompt; generate: images only). */
  fetchMaterial: (id: string, body: { wish?: number; query?: string; prompt?: string; generate?: boolean } = {}) =>
    call<{ id: string; run: string; state: string }>("POST", `${J(id)}/material/fetch`, body),
  /** The human's pick for a wish; null = no material there. */
  chooseMaterial: (id: string, n: number, candidate: string | null) =>
    call<MaterialView>("POST", `${J(id)}/material/choose`, { n, candidate }),

  stillUrl: (id: string, path: string) =>
    transport.url(`${J(id)}/stills/${path.split("/").map(enc).join("/")}`),
  mediaUrl: (id: string, kind: "reel" | "preview" | "cover" | "screen_proxy" | "camera_proxy", bust = "") =>
    transport.url(`${J(id)}/media/${kind}${bust ? `?v=${enc(bust)}` : ""}`),
  downloadUrl: (id: string) => transport.url(`${J(id)}/download`),
  url: (path: string) => transport.url(path),
};
