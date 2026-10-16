// API client. Authentication is the HttpOnly session cookie set by the server (the UI never
// sees the token); writes are same-origin, so the browser sends the Origin header the
// server checks. A Transport makes the client testable: the real one uses fetch, XHR (for
// upload progress) and EventSource; the mock one (src/mock) answers from memory.

import type {
  Brand,
  BrandSummary,
  CutStyleChoice,
  RedactStyle,
  Format,
  FormatChoice,
  JobDetail,
  JobEvent,
  JobSummary,
  JobVocabulary,
  Plan,
  RedactionDecision,
  Session,
  SlidesMode,
  TargetChoice,
  TargetRange,
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

  brands: () => call<BrandSummary[]>("GET", "/api/brands"),
  brand: (id: string) => call<Brand>("GET", `/api/brands/${enc(id)}`),
  saveBrand: (id: string, tokens: Brand) => call<Brand>("PUT", `/api/brands/${enc(id)}`, tokens),
  newBrand: (name: string) => call<{ id: string }>("POST", "/api/brands", { name }),
  brandFile: (id: string, kind: "logo" | "font", file: File, role?: string) => {
    const form = new FormData();
    form.append(kind, file);
    if (role) form.append("role", role);
    return call<Brand>("POST", `/api/brands/${enc(id)}/files`, form);
  },
  sheetUrl: (id: string, format: Format, bust = "") =>
    transport.url(`/api/brands/${enc(id)}/sheet?format=${enc(format)}${bust ? `&v=${enc(bust)}` : ""}`),

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

  stillUrl: (id: string, path: string) =>
    transport.url(`${J(id)}/stills/${path.split("/").map(enc).join("/")}`),
  mediaUrl: (id: string, kind: "reel" | "preview" | "cover" | "screen_proxy" | "camera_proxy", bust = "") =>
    transport.url(`${J(id)}/media/${kind}${bust ? `?v=${enc(bust)}` : ""}`),
  downloadUrl: (id: string) => transport.url(`${J(id)}/download`),
  url: (path: string) => transport.url(path),
};
