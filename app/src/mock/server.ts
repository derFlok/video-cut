// In-memory fake of the shared API contract: development (`npm run dev:mock`), tests and
// screenshots. It simulates runs with timers and streams events like the real server.

import { setTransport, type Transport, type UploadProgress } from "../api/client";
import type {
  Brand,
  BrandSettings,
  ClaudeDetection,
  Disclosure,
  LabelSettings,
  SetupStatus,
  ThemeView,
  JobDetail,
  JobEvent,
  JobSummary,
  MaterialMode,
  MaterialSettings,
  MaterialView,
  MaterialWish,
  PdCandidate,
  SecretAccount,
  Plan,
  PlanJson,
  Stage,
  TargetRange,
  TranscriptWord,
  VideoType,
  VocabEntry,
  VocabSuggestion,
} from "../api/types";
import { brandSheet, coverPicture, personStill, screenStill } from "./pictures";

export interface MockOptions {
  /** Milliseconds per simulated step (0 in tests). */
  step?: number;
  /** Start with the demo jobs (true) or empty. */
  seed?: boolean;
  /** The first-start state (default: done, local mode, a signed-in claude program). */
  setup?: Partial<Omit<SetupStatus, "claude">> & { claude?: Partial<ClaudeDetection> };
  /** The answer of "Verbindung prüfen". */
  testOk?: boolean;
  /** Default brand and theme switch (default: none, following). */
  brandSettings?: Partial<BrandSettings>;
  /** Material settings (default: none, no generator, both stock libraries, no keys). */
  materialSettings?: Partial<MaterialSettings>;
}

interface MockJob {
  detail: JobDetail;
  created: string;
  title: string | null;
  plan: Plan | null;
  events: JobEvent[];
  /** The style the current reel was cut with. */
  cutWith?: string;
  /** The target length the plan was made for (null: automatic). */
  planTarget?: number | null;
  /** The job's own transcript vocabulary, what was applied, and words heard close to a term. */
  vocabulary?: VocabEntry[];
  vocabApplied?: number;
  heard?: VocabSuggestion[];
  /** The transcript (made on first use) and the edits an undo can take back. */
  words?: TranscriptWord[];
  edits?: { i: number; before: TranscriptWord }[];
  /** Material candidates and choices (after approval of a plan with wishes). */
  material?: MaterialView;
  /** work/disclosure.json of the last full render (absent: none yet). */
  disclosure?: Disclosure;
}

const WISHES: MaterialWish[] = [
  { n: 1, kind: "broll", src_from: 13.0, src_to: 19.5, query: "team meeting office calm", prompt: null, style: "photo", why: "Zeigt den Alltag, über den gesprochen wird." },
  { n: 2, kind: "still", src_from: 33.0, src_to: 38.0, query: "laptop desk morning light", prompt: "A tidy desk with a laptop in soft morning light", style: "photo", why: "Ruhiges Bild zum zweiten Beispiel." },
];

/** Candidates the mock "finds" for a wish (pictures from mock/pictures). */
function mockCandidates(id: string, w: MaterialWish, mode: MaterialMode, seed = 0): MaterialView["wishes"][number]["candidates"] {
  const out: MaterialView["wishes"][number]["candidates"] = [];
  const base = `/api/jobs/${id}/material/files/${w.n}`;
  if (mode !== "generated" && w.kind !== "icon")
    for (let k = 1; k <= 3; k++)
      out.push({
        id: `c${k}`, kind: w.kind === "broll" && k < 3 ? "video" : "image", thumb_url: `${base}/c${k + seed}_sm.jpg`, file_url: `${base}/c${k}.mp4`,
        width: 1080, height: 1920, duration_s: w.kind === "broll" && k < 3 ? 8 + k : null, source: "pexels",
        source_url: "https://www.pexels.com/", author: `Beispiel ${k}`, licence: "Pexels License", attribution: `Video by Beispiel ${k} on Pexels`,
        ai_generated: false, model: null, watermark: "none",
        // the third one comes from Openverse: a CC BY photo with its licence link
        ...(k === 3 && {
          source: "openverse", source_url: "https://commons.wikimedia.org/", licence: "CC BY 2.0", width: 960, height: 1280, small: true,
          licence_url: "https://creativecommons.org/licenses/by/2.0/", attribution: "Lake by Beispiel 3, CC BY 2.0, via Openverse/wikimedia",
        }),
      });
  if (mode !== "stock" && w.kind !== "broll")
    for (let k = out.length + 1, i = 0; i < 2; i++, k++)
      out.push({
        id: `c${k}`, kind: "image", thumb_url: `${base}/c${k + seed}_sm.jpg`, file_url: `${base}/c${k}.png`, width: 1088, height: 1920,
        duration_s: null, source: "gemini", source_url: null, author: null, licence: "generated", attribution: "AI-generated (gemini-nano-banana-2.1)",
        ai_generated: true, model: "gemini-nano-banana-2.1", watermark: "synthid",
      });
  return out;
}

const SAMPLE = "Heute zeige ich euch, wie Zentrowia den Ablauf einfacher macht. Ähm, also, zuerst öffnet ihr die Übersicht. Dann wählt ihr den Bericht aus und klickt auf Senden. Das war's schon!";

/** A transcript for the mock: the sample repeated, one word every 0.4 s; every 7th sentence is cut. */
function mockWords(n = 4): TranscriptWord[] {
  const out: TranscriptWord[] = [];
  let t = 0;
  let out_t = 0;
  let sentence = 0;
  for (let r = 0; r < n; r++)
    for (const w of SAMPLE.split(" ")) {
      const cut = sentence % 7 === 6 || w.startsWith("Ähm");
      out.push({ i: out.length, w, t0: +t.toFixed(2), t1: +(t + 0.35).toFixed(2), out_t0: cut ? null : +out_t.toFixed(2), out_t1: cut ? null : +(out_t + 0.35).toFixed(2) });
      t += 0.4;
      if (!cut) out_t += 0.4;
      if (/[.!?]$/.test(w)) sentence++;
    }
  return out;
}

export interface MockState {
  types: VideoType[];
  brands: Map<string, Brand>;
  jobs: Map<string, MockJob>;
  active: string | null;
  setup: SetupStatus;
  apiKey: string | null;
  stopped: boolean;
  brandSettings: BrandSettings;
  materialSettings: MaterialSettings;
  labels: LabelSettings;
  /** Every request the fake saw (method, path, body), newest last. */
  calls: { method: string; path: string; body: unknown }[];
}

const TYPES: VideoType[] = [
  { id: "thought", name_de: "Gedanke des Tages", name_en: "Thought of the day", desc_de: "30–45 s, eine These, Kernaussage groß eingeblendet.", desc_en: "30–45 s, one thesis, the core message shown large.", needs: ["camera"], optional: [] },
  { id: "myth", name_de: "Mythos vs. Realität", name_en: "Myth vs. reality", desc_de: "Gegenüberstellung als Split-Grafik.", desc_en: "Side-by-side split graphic.", needs: ["camera"], optional: [] },
  { id: "tips", name_de: "Drei Tipps", name_en: "Three tips", desc_de: "Liste mit animierter Nummer pro Punkt.", desc_en: "A list with an animated number per point.", needs: ["camera"], optional: [] },
  { id: "walkthrough", name_de: "Produkt-Walkthrough", name_en: "Product walkthrough", desc_de: "Bildschirmaufnahme mit Zoom auf die besprochene Stelle.", desc_en: "Screen recording zoomed onto the part you talk about.", needs: ["camera"], optional: ["screen", "assets"] },
  { id: "process", name_de: "Prozess als Bildstrecke", name_en: "Process as a picture series", desc_de: "Mehrere Screenshots als Klickstrecke.", desc_en: "Several screenshots as a click-through.", needs: ["camera"], optional: ["assets"] },
];

const EXAMPLE: Brand = {
  name: "Example (neutral)",
  colors: { bg: "#F4F4F4", surface: "#FFFFFF", text: "#141414", muted: "#6B6B6B", accent: "#141414", accent_usage: "sparsam" },
  fonts: { display: "system-ui", body: "system-ui", weights: [400, 600, 800] },
  logo: { light: null, dark: null, placement: "none" },
  captions: { style: "box", case: "normal", highlight: "underline", position: "center", pace: "normal" },
  motion: { character: "ruhig", spring: { w: 13, z: 0.9 }, transitions: "blur" },
  icons: "none",
  voice: { address: "du", tone: "neutral", forbidden_words: [], cta: "" },
  music: { mood: "neutral", max_bpm: 110 },
  legal: { footer: "" },
  files: ["brand.json"],
};

function contrastOf(a = "#000000", b = "#FFFFFF"): number {
  const lum = (hex: string) => {
    const n = (i: number) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * n(1) + 0.7152 * n(3) + 0.0722 * n(5);
  };
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const sha = (s: string) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0).toString(16).padStart(8, "0");
};

function planFor(title: string, variant = 0, note?: string): Plan {
  const keep = [
    { n: 1, src: [3.2, 7.9] as [number, number], text: "Einstieg: die häufigste Frage in einem Satz.", why: "Starker Hook ohne Begrüßung." },
    { n: 2, src: [12.4, 24.0] as [number, number], text: "Die Antwort mit dem Beispiel aus dem Alltag.", why: "Kernaussage, ruhig erklärt." },
    { n: 3, src: [31.5, 44.8] as [number, number], text: "Zweites Beispiel mit der Bildschirmaufnahme.", why: "Zeigt den Ablauf konkret." },
    { n: 4, src: [52.0, 58.6] as [number, number], text: "Abschluss mit Aufruf zum Weiterleiten.", why: "Klarer Schluss." },
  ];
  if (variant > 0) keep.splice(2, 1, { n: 3, src: [33.0, 49.5], text: "Zweites Beispiel, länger, ohne die Zwischenfrage.", why: "Auf Wunsch: Beispiel länger, Frage weg." });
  const plan: PlanJson = {
    title,
    format: "16:9",
    hook: { text: "Die eine Frage, die fast jeder stellt – und warum die Antwort einfacher ist.", src: [0.4, 3.1] },
    core_message: "Ein klarer Ablauf spart jede Woche Zeit.",
    keep_list: keep,
    views: [
      { n: 1, src: [3.2, 7.9], layout: "face", still: "work/plan_stills/view-1.jpg" },
      { n: 2, src: [12.4, 24.0], layout: "pip", zoom_box: [0.1, 0.2, 0.6, 0.6], still: "work/plan_stills/view-2.jpg" },
      { n: 3, src: [31.5, 44.8], layout: "screen", zoom_box: [0.3, 0.1, 0.9, 0.5], still: "work/plan_stills/view-3.jpg" },
      { n: 4, src: [52.0, 58.6], layout: "split", still: "work/plan_stills/view-4.jpg" },
    ],
    overlays: [
      { scene: "keyword", t0: 1.0, text: "Die häufigste Frage" },
      { scene: "number_card", t0: 9.5, text: "3 Schritte" },
      { scene: "cta_end", t0: 38.0, text: "Schick das jemandem, der das braucht." },
    ],
    personal_data: [
      { id: 1, kind: "email", t0: 18.0, t1: 24.1, box: [0.55, 0.3, 0.92, 0.38], reason: "E-Mail-Adresse in der Kontaktliste sichtbar.", still: "work/plan_stills/pd-1.jpg" },
      { id: 2, kind: "name", asset: "input/shot-1.png", box: [0.06, 0.42, 0.45, 0.5], reason: "Vor- und Nachname in der Kopfzeile.", still: "work/plan_stills/pd-2.jpg" },
    ],
    slides: [
      { id: "s1", template: "steps", content: { steps: ["Anfrage", "Prüfen", "Antworten"] }, t0: 13.0, t1: 19.5, layout: "slide", why: "Der Ablauf in drei Schritten.", source: "auto", still: "work/slides/stills/s1.jpg" },
      { id: "s2", template: "image", content: { asset: "input/slides/page_02.png" }, t0: 52.0, t1: 56.0, layout: "slide_full", why: "Deine Folie zum Schluss.", source: "upload", still: "work/slides/stills/s2.jpg" },
    ],
    risks: ["Bildschirmaufnahme bei 0:31 ist klein; Zoom auf höchstens 1,6×.", "Ton am Ende leicht hallig."],
    estimated_s: variant > 0 ? 44 : 41,
  };
  if (note) plan.change_note = note;
  const md = [
    `# ${title}`,
    "",
    `**Hook:** ${plan.hook}`,
    "",
    `**Kernaussage:** ${plan.core_message}`,
    "",
    "## Streichliste / Behalten",
    "",
    "| # | Quelle | Inhalt |",
    "|---|---|---|",
    ...keep.map((k) => `| ${k.n} | ${k.src[0]}–${k.src[1]} s | ${k.text} |`),
    "",
    "## Risiken",
    "",
    ...(plan.risks as string[]).map((r) => `- ${r}`),
    "",
  ].join("\n");
  return { markdown: md, plan, sha: sha(md) };
}

const CANDIDATES: PdCandidate[] = [
  { id: 1, kind: "email", t0: 18.0, t1: 24.1, box: [0.55, 0.3, 0.92, 0.38], reason: "E-Mail-Adresse in der Kontaktliste sichtbar.", text: "erika.mustermann@example.com", measured: "ok" },
  { id: 2, kind: "name", asset: "input/shot-1.png", box: [0.06, 0.42, 0.45, 0.5], reason: "Vor- und Nachname in der Kopfzeile." },
];

function baseDetail(id: string, over: Partial<JobDetail>): JobDetail {
  const d: JobDetail = {
    id,
    type: "thought",
    brand: null,
    format: "auto",
    format_resolved: "9:16",
    status: "new",
    stage: "prep",
    approved: false,
    plan_sha: null,
    redaction: { candidates: [], decision: null },
    outputs: null,
    verify: null,
    runs: [],
    revisions: [],
    cut_style: "auto",
    cut_style_resolved: over.has_screen ? "calm" : "standard",
    redact_style: "auto",
    redact_style_default: true,
    cut_stale: false,
    source_duration_s: 64.2,
    has_screen: false,
    target_s: null,
    plan_target_stale: false,
    plan_target_s: null,
    ...over,
  };
  d.target_resolved = resolveTarget(d);
  return d;
}

const resolveStyle = (d: JobDetail) =>
  d.cut_style && d.cut_style !== "auto" ? d.cut_style : d.has_screen ? "calm" : "standard";

/** Like tools/_length.py: target ± 15 %; automatic: none for demos, else by type. */
const resolveTarget = (d: JobDetail): TargetRange | null => {
  const t = d.target_s;
  if (t !== null && t !== undefined) return { min_s: Math.floor(t * 0.85 + 1e-6), max_s: Math.ceil(t * 1.15 - 1e-6) };
  if (d.has_screen || (d as { assets?: string[] }).assets?.length) return null;
  return d.type === "thought" ? { min_s: 30, max_s: 45 } : { min_s: 30, max_s: 60 };
};

/** A plan run made the plan for the current target. */
const planned = (job: MockJob) => {
  job.planTarget = job.detail.target_s ?? null;
  job.detail.plan_target_stale = false;
  job.detail.plan_target_s = null;
};

/** How long the seeded running jobs have been in their stage. */
export const RUNNING_FOR_MS = 125_000;

function seedJobs(): Map<string, MockJob> {
  const jobs = new Map<string, MockJob>();
  const add = (id: string, title: string | null, created: string, d: Partial<JobDetail>, plan: Plan | null) => {
    // a running job has been in its stage for a while (the page was reloaded meanwhile)
    const at = d.status === "running" ? new Date(Date.now() - RUNNING_FOR_MS).toISOString() : undefined;
    jobs.set(id, {
      detail: baseDetail(id, { title, created, ...d, stage_since: at ?? null, plan_sha: plan?.sha ?? null }),
      created,
      title,
      plan,
      events: [{ t: "stage", stage: d.stage ?? "prep", ...(at ? { at } : {}) }],
      cutWith: d.outputs?.reel ? (d.has_screen ? "calm" : "standard") : undefined,
    });
  };
  const p1 = planFor("Warum ein fester Ablauf Zeit spart");
  add(
    "job-approval",
    "Warum ein fester Ablauf Zeit spart",
    "2026-10-12T09:14:00",
    {
      type: "walkthrough",
      brand: "light",
      format: "auto",
      format_resolved: "16:9",
      status: "waiting",
      stage: "approval",
      has_screen: true,
      source_duration_s: 251.8,
      brief: "Kurz halten, Fokus auf den Ablauf",
      redaction: { candidates: clone(CANDIDATES), decision: null },
      runs: [
        { phase: "prep", wall_s: 184, turns: null, cost_usd: null },
        { phase: "plan", wall_s: 141, turns: 22, cost_usd: 0.41 },
      ],
    },
    p1,
  );
  add(
    "job-done",
    "Drei Tipps für den Start",
    "2026-10-10T16:40:00",
    {
      type: "tips",
      brand: "dark",
      format: "9:16",
      format_resolved: "9:16",
      status: "done",
      stage: "done",
      approved: true,
      outputs: { reel: "out/reel.mp4", preview: "out/preview.mp4", cover: "out/cover.jpg" },
      slides: { mode: "auto", wish: "Die drei Tipps als Aufzählung", pages: 0, stale: false, marks: [{ id: "s1", t0: 8.2, t1: 14.6, layout: "slide" }] },
      verify: {
        ok: false,
        checks: { loudness: { ok: true }, duration: { ok: true } },
        flagged: [{ cut: 7, t: 21.4, flags: ["blink_at_cut"] }],
        overlay_flagged: [],
        screen_flagged: [],
      },
      runs: [
        { phase: "prep", wall_s: 160, turns: null, cost_usd: null },
        { phase: "plan", wall_s: 120, turns: 18, cost_usd: 0.36 },
        { phase: "cut", wall_s: 302, turns: 41, cost_usd: 0.88 },
        { phase: "overlays", wall_s: 210, turns: 30, cost_usd: 0.52 },
      ],
      revisions: [
        { n: 1, time: "2026-10-10T17:20:00", text: "Bei 0:12 weniger rauszoomen.", at_s: 12.0, ok: true, changed: { segments: [3] }, cost: 0.21, version: 2, video_url: "/api/jobs/job-done/media/history/reel-2.mp4" },
        { n: 2, time: "2026-10-10T17:41:00", text: "Die Frage zu den Anrufen weglassen.", ok: true, changed: { segments: [5, 6] }, cost: 0.18, version: 3, video_url: "/api/jobs/job-done/media/reel" },
      ],
      versions: [
        { version: 3, revision: 2, text: "Die Frage zu den Anrufen weglassen.", video_url: "/api/jobs/job-done/media/reel", current: true },
        { version: 2, revision: 1, text: "Bei 0:12 weniger rauszoomen.", video_url: "/api/jobs/job-done/media/history/reel-2.mp4", current: false },
        { version: 1, revision: null, text: null, video_url: "/api/jobs/job-done/media/history/reel-1.mp4", current: false },
      ],
    },
    planFor("Drei Tipps für den Start"),
  );
  jobs.get("job-done")!.heard = [
    { term: "Zentrovia", heard: "Sentrovia", distance: 1, count: 2, times: [3.2, 18.4], conf: 0.41 },
  ];
  add("job-running", "Neue Funktion in 40 Sekunden", "2026-10-13T08:02:00", { type: "walkthrough", status: "running", stage: "prep", format: "16:9", format_resolved: "16:9", has_screen: true, source_duration_s: 251.8, runs: [] }, null);
  add(
    "job-overlays",
    "Ablauf in drei Schritten",
    "2026-10-13T10:30:00",
    {
      type: "walkthrough",
      status: "running",
      stage: "overlays",
      approved: true,
      has_screen: true,
      format: "16:9",
      format_resolved: "16:9",
      source_duration_s: 596.1,
      outputs: { reel: "out/reel.mp4", preview: null, cover: null },
      runs: [
        { phase: "plan", wall_s: 254, turns: 30, cost_usd: 1.42 },
        { phase: "cut", wall_s: 1459, turns: 88, cost_usd: 2.49 },
      ],
    },
    planFor("Ablauf in drei Schritten"),
  );
  add("job-error", null, "2026-10-09T11:00:00", { type: "myth", status: "error", stage: "error", failed_stage: "prep", runs: [{ phase: "prep", wall_s: 12, turns: null, cost_usd: null }] }, null);
  const running = jobs.get("job-running")!;
  running.events.push(
    { t: "log", text: "  ingest --denoise   41.2s  {'denoised': True}" },
    { t: "log", text: "  sync               3.1s  {'offset_s': 1.24, 'method': 'audio', 'confidence': 0.91}" },
    { t: "log", text: "  screen             22.4s  {'clicks': 14, 'zoom_targets': 6}" },
    { t: "log", text: "  redact text        30.2s  {'texts': 88, 'suggest': 2}" },
    { t: "log", text: "  face               18.9s  {'found_pct': 98.4, 'blinks': 51}" },
  );
  // waiting at the material gate: the plan with two wishes is approved, candidates fetched
  const matPlan = planFor("Ablauf mit Bildern");
  matPlan.plan!.material_wishes = clone(WISHES);
  add("job-material", "Ablauf mit Bildern", "2026-10-14T10:00:00", {
    type: "thought", status: "waiting", stage: "material", gate: "material", approved: true, source_duration_s: 64.0,
    material: { mode: "both", wish: "ruhige Büroszenen", stale: false, wishes: 2, pending: [1, 2], fetched: true },
    runs: [{ phase: "plan", wall_s: 80, turns: 14, cost_usd: 0.3 }, { phase: "material", wall_s: 21, turns: null, cost_usd: 0.07 }],
  }, matPlan);
  const mj = jobs.get("job-material")!;
  mj.material = {
    mode: "both", image_provider: "gemini", budget_usd: 1, cost_usd: 0.07, fetched: true, pending: [1, 2],
    wishes: WISHES.map((w) => ({ ...w, decided: false, chosen: null, candidates: mockCandidates("job-material", w, "both") })),
  };
  jobs.get("job-overlays")!.events.push(
    { t: "log", text: "overlays: Claude is working" },
    { t: "tool", text: "Bash: videocut tool captions --job job-overlays" },
  );
  return jobs;
}

/** The checks of tools/vocab.py check that the UI can trip over. */
function vocabErrors(list: VocabEntry[] | undefined, path = "$.vocabulary"): string[] {
  const errors: string[] = [];
  if (list === undefined) return errors;
  const seen = new Set<string>();
  list.forEach((e, i) => {
    if (!e.term?.trim()) errors.push(`${path}[${i}].term: required, a non-empty string`);
    e.variants.forEach((v, j) => {
      const k = v.toLowerCase();
      if (/[\\^$|*?[\](){}]/.test(v)) errors.push(`${path}[${i}].variants[${j}]: '${v}' must be plain text, not a pattern`);
      else if (seen.has(k)) errors.push(`${path}[${i}].variants[${j}]: '${v}' is already used`);
      seen.add(k);
    });
  });
  return errors;
}

export function createMock(opts: MockOptions = {}) {
  const step = opts.step ?? 700;
  const state: MockState = {
    types: TYPES,
    brands: new Map<string, Brand>([
      ["_example", clone(EXAMPLE)],
      ...(opts.seed === false
        ? []
        : ([
            ["light", { ...clone(EXAMPLE), name: "Beispiel hell", colors: { ...EXAMPLE.colors, accent: "#2F6FDE", surface: "#FFFFFF" } }],
            ["dark", { ...clone(EXAMPLE), name: "Beispiel dunkel", colors: { bg: "#121417", surface: "#1E2228", text: "#F2F2F2", muted: "#9AA0A8", accent: "#E8B23A", accent_usage: "frei" }, vocabulary: [{ term: "Zentrovia", variants: ["Zentrowia"] }] }],
          ] as [string, Brand][])),
    ]),
    jobs: opts.seed === false ? new Map() : seedJobs(),
    active: null,
    setup: {
      done: true,
      mode: "local",
      api_key_stored: false,
      console_url: "https://console.anthropic.com/settings/keys",
      ...opts.setup,
      claude: { state: "done", installed: true, version: "2.0.0", signed_in: true, method: "claude.ai", account: "person@example.com", subscription: "pro", ...opts.setup?.claude },
    },
    apiKey: null,
    stopped: false,
    brandSettings: { default_brand: null, theme_follows_brand: true, ...opts.brandSettings },
    materialSettings: {
      material_default_mode: "none",
      image_provider: "gemini",
      stock_providers: ["pixabay", "pexels", "coverr", "openverse"],
      material_budget_usd: 1,
      image_providers: [
        { name: "gemini", available: false, reason: "no key stored (gemini-api-key)", registered: true },
        { name: "openai", available: false, reason: "no key stored (openai-api-key)", registered: true },
        { name: "local", available: false, reason: "the image model is not downloaded", registered: true },
      ],
      secrets: { "pixabay-api-key": false, "pexels-api-key": false, "coverr-api-key": false, "gemini-api-key": false, "openai-api-key": false },
      ...opts.materialSettings,
    },
    labels: { ai_label_illustrations: true },
    calls: [],
  };
  const listeners = new Map<string, Set<(ev: JobEvent) => void>>();
  let counter = 1;

  const emit = (id: string, ev: JobEvent) => {
    const job = state.jobs.get(id);
    if (!job) return;
    if (ev.t === "stage") {
      ev = { ...ev, at: ev.at ?? new Date().toISOString() };
      job.events = job.events.filter((e) => e.t !== "stage");
      job.detail.stage = ev.stage;
      job.detail.stage_since = ev.at;
    }
    job.events.push(ev);
    listeners.get(id)?.forEach((cb) => cb(ev));
  };
  const later = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

  /** A simulated Claude run: stages with log lines, then a gate or done. */
  async function simulate(id: string, stages: Stage[], end: "plan" | "material" | "result", after: () => void) {
    const job = state.jobs.get(id)!;
    state.active = id;
    job.detail.status = "running";
    const t0 = Date.now();
    for (const s of stages) {
      emit(id, { t: "stage", stage: s });
      await later(step);
      emit(id, { t: "log", text: `${s}: läuft …` });
      emit(id, { t: "tool", text: `Bash: videocut tool ${s === "prep" ? "ingest" : s} ${id}` });
      await later(step);
    }
    after();
    job.detail.runs.push({ phase: stages[stages.length - 1], wall_s: Math.round((Date.now() - t0) / 1000) + 60, turns: 12, cost_usd: 0.27 });
    state.active = null;
    if (end === "plan") {
      job.detail.status = "waiting";
      emit(id, { t: "stage", stage: "approval" });
      emit(id, { t: "gate", gate: "plan" });
    } else if (end === "material") {
      job.detail.status = "waiting";
      job.detail.gate = "material";
      emit(id, { t: "stage", stage: "material" });
      emit(id, { t: "gate", gate: "material" });
    } else {
      job.detail.status = "done";
      emit(id, { t: "stage", stage: "done" });
      emit(id, { t: "gate", gate: "result" });
      emit(id, { t: "done", ok: true });
    }
  }

  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  function summary(j: MockJob): JobSummary {
    const d = j.detail;
    return {
      id: d.id,
      created: j.created,
      type: d.type,
      brand: d.brand,
      format: d.format,
      status: d.status,
      stage: d.stage,
      title: j.title,
      copy_of: d.copy_of ?? null,
      thumb_url: d.status === "new" ? null : `/api/jobs/${d.id}/stills/work/plan_stills/view-1.jpg`,
      target_s: d.target_s ?? null,
    };
  }

  /** A small stand-in for videocut/theme.py: brand colours onto the main tokens, and the
   * same contrast notes for text (4.5:1) and accent (3:1) on the background. */
  function mockTheme(bid: string, br: Brand): ThemeView {
    const c = br.colors ?? {};
    const notes: ThemeView["notes"] = [];
    if (contrastOf(c.text, c.bg) < 4.5) notes.push({ code: "text", scheme: "light", ratio: +contrastOf(c.text, c.bg).toFixed(2) });
    if (contrastOf(c.accent, c.bg) < 3) notes.push({ code: "accent", scheme: "light", ratio: +contrastOf(c.accent, c.bg).toFixed(2) });
    const css = `html:root{--bg:${c.bg};--surface:${c.surface ?? c.bg};--text:${c.text};--primary:${c.accent}}`;
    const logo = typeof br.logo?.light === "string" ? `/api/brands/${bid}/assets/${br.logo.light}` : null;
    return { brand_id: bid, name: br.name, css, logo: { light: logo, dark: logo }, notes };
  }

  function themeView(): ThemeView {
    const s = { ...state.brandSettings };
    const br = s.default_brand ? state.brands.get(s.default_brand) : undefined;
    if (!br || !s.theme_follows_brand || !s.default_brand)
      return { brand_id: null, name: null, css: "", logo: { light: null, dark: null }, notes: [], settings: s };
    return { ...mockTheme(s.default_brand, br), settings: s };
  }

  function route(method: string, path: string, body: unknown): Response {
    const url = new URL(path, "http://127.0.0.1");
    const p = url.pathname.split("/").filter(Boolean).map(decodeURIComponent); // ["api", ...]
    if (p[0] !== "api") return json(404, { detail: "not found" });
    const [, a, id, b, c] = p;

    if (a === "session") return json(200, { version: "0.0.1", lang: "de", claude: { ok: true, how: "subscription" } });
    if (a === "types") return json(200, state.types);

    if (a === "setup") {
      if (id === "status" && method === "GET") return json(200, { ...state.setup, api_key_stored: state.apiKey !== null || state.setup.api_key_stored });
      if (id === "auth" && method === "POST") {
        const b = body as { mode?: string; api_key?: string };
        if (b.mode !== "local" && b.mode !== "apikey") return json(422, { detail: { message: "invalid request: mode" } });
        if (b.mode === "apikey") {
          if (b.api_key && !/^sk-ant-[A-Za-z0-9_-]{20,300}$/.test(b.api_key))
            return json(422, { detail: { message: "That does not look like an Anthropic API key.", code: "bad_key" } });
          if (b.api_key) state.apiKey = b.api_key;
          else if (state.apiKey === null && !state.setup.api_key_stored) return json(422, { detail: { message: "No API key given.", code: "no_key" } });
        }
        state.setup = { ...state.setup, mode: b.mode, done: true };
        return json(200, { ...state.setup, api_key_stored: state.apiKey !== null || state.setup.api_key_stored });
      }
      if (id === "test" && method === "POST")
        return json(200, opts.testOk === false ? { ok: false, message: "Invalid API key", seconds: 1.2, mode: state.setup.mode } : { ok: true, message: "OK", seconds: 3.4, mode: state.setup.mode });
    }
    if (a === "shutdown" && method === "POST") {
      if (state.active && !(body as { force?: boolean })?.force) return json(409, { detail: { message: "a run is queued or active", code: "busy" } });
      state.stopped = true;
      return json(200, { stopping: true });
    }

    if (a === "settings" && id === "material") {
      const m = state.materialSettings;
      if (method === "GET") return json(200, m);
      if (method === "PUT") {
        const b = body as Partial<MaterialSettings>;
        if (b.material_default_mode !== undefined && !["none", "stock", "generated", "both"].includes(b.material_default_mode))
          return json(400, { detail: { message: `unknown material mode '${b.material_default_mode}'` } });
        if (b.image_provider !== undefined && !["none", "gemini", "openai", "local"].includes(b.image_provider))
          return json(400, { detail: { message: `unknown image provider '${b.image_provider}'` } });
        if (b.material_budget_usd !== undefined && !(b.material_budget_usd >= 0 && b.material_budget_usd <= 100))
          return json(400, { detail: { message: "the cost limit must be between 0 and 100" } });
        for (const k of ["material_default_mode", "image_provider", "stock_providers", "material_budget_usd"] as const)
          if (b[k] !== undefined) (m as unknown as Record<string, unknown>)[k] = b[k];
        return json(200, m);
      }
    }
    if (a === "settings" && id === "secrets") {
      const m = state.materialSettings;
      if (method === "GET") return json(200, { secrets: m.secrets });
      if (method === "PUT") {
        const b = body as { account?: string; value?: string };
        if (!b.account || !(b.account in m.secrets)) return json(400, { detail: { message: `unknown account '${b.account}'` } });
        m.secrets = { ...m.secrets, [b.account as SecretAccount]: !!b.value };
        const prov = b.account.replace("-api-key", "");
        m.image_providers = m.image_providers.map((x) =>
          x.name === prov ? { ...x, available: !!b.value, reason: b.value ? "" : `no key stored (${b.account})` } : x,
        );
        return json(200, { secrets: m.secrets });
      }
    }

    if (a === "theme" && method === "GET") return json(200, themeView());
    if (a === "settings" && id === "brand" && method === "PUT") {
      const b = body as Partial<BrandSettings>;
      if (b.default_brand && !state.brands.has(b.default_brand)) return json(404, { detail: { message: `no brand '${b.default_brand}'` } });
      if (b.default_brand !== undefined) state.brandSettings.default_brand = b.default_brand;
      if (typeof b.theme_follows_brand === "boolean") state.brandSettings.theme_follows_brand = b.theme_follows_brand;
      return json(200, themeView());
    }
    if (a === "settings" && id === "labels") {
      if (method === "PUT") state.labels = { ai_label_illustrations: !!(body as LabelSettings).ai_label_illustrations };
      return json(200, state.labels);
    }
    if (a === "brands") {
      if (!id && method === "GET")
        return json(200, [...state.brands.entries()].map(([bid, br]) => ({ id: bid, name: br.name, preview_url: `/api/brands/${bid}/sheet?format=9:16`, default: bid === state.brandSettings.default_brand })));
      if (!id && method === "POST") {
        const name = String((body as { name?: string })?.name ?? "Neue Marke");
        const bid = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || `brand-${counter++}`;
        state.brands.set(bid, { ...clone(EXAMPLE), name });
        return json(201, { id: bid });
      }
      const br = state.brands.get(id);
      if (!br) return json(404, { detail: `no brand ${id}` });
      if (!b && method === "GET") return json(200, br);
      if (!b && method === "PUT") {
        if (id === "_example") return json(409, { detail: { message: "the bundled example brand is read-only; create a new brand", code: "read_only" } });
        const tokens = body as Brand;
        const errors: string[] = [];
        if (!tokens.name || !String(tokens.name).trim()) errors.push("$.name: required, a non-empty string");
        for (const k of ["bg", "text", "accent", "surface", "muted"]) {
          const v = tokens.colors?.[k];
          if (v !== undefined && !/^#[0-9a-fA-F]{6}$/.test(v)) errors.push(`$.colors.${k}: '${v}' is not a hex colour like #RRGGBB`);
        }
        errors.push(...vocabErrors(tokens.vocabulary));
        if (errors.length) return json(422, { detail: { message: "brand is invalid", errors } });
        state.brands.set(id, { ...tokens, files: br.files });
        return json(200, state.brands.get(id));
      }
      if (!b && method === "DELETE") {
        if (id === "_example") return json(409, { detail: { message: "the bundled example brand is read-only", code: "read_only" } });
        state.brands.delete(id);
        if (state.brandSettings.default_brand === id) state.brandSettings.default_brand = null;
        return json(200, { deleted: id });
      }
      if (b === "theme" && method === "POST") return json(200, { ...mockTheme(id, body as Brand), brand_id: id });
      if (b === "files" && method === "POST") {
        const form = body as FormData;
        const file = (form.get("logo") ?? form.get("font")) as File | null;
        if (!file) return json(400, { detail: "no file" });
        const name = form.get("logo") ? `logo-${form.get("role") ?? "light"}${file.name.slice(file.name.lastIndexOf("."))}` : `fonts/${file.name}`;
        br.files = [...(br.files ?? []), name];
        if (form.get("logo")) br.logo = { ...br.logo, [String(form.get("role") ?? "light")]: name };
        return json(200, br);
      }
    }

    if (a === "jobs") {
      if (!id && method === "GET")
        return json(200, [...state.jobs.values()].sort((x, y) => (x.created < y.created ? 1 : -1)).map(summary));
      const job = state.jobs.get(id);
      if (!job) return json(404, { detail: `no job ${id}` });
      const d = job.detail;
      if (!b && method === "GET") return json(200, d);
      if (!b && method === "DELETE") {
        state.jobs.delete(id);
        return json(200, { ok: true });
      }
      if (b === "duplicate" && method === "POST") {
        if (state.active === id) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        const req = body as { title?: string; keep_approval?: boolean };
        const base = id.replace(/-copy-\d+$/, "");
        let n = 1;
        while (state.jobs.has(`${base}-copy-${n}`)) n++;
        const nid = `${base}-copy-${n}`;
        const copy: MockJob = clone(job);
        const keep = !!req.keep_approval && d.approved;
        copy.created = new Date().toISOString();
        copy.title = req.title?.trim() || job.title;
        copy.events = [];
        Object.assign(copy.detail, { id: nid, title: copy.title, copy_of: id, approved: keep });
        if (!keep) copy.detail.redaction = { ...copy.detail.redaction, decision: null };
        state.jobs.set(nid, copy);
        return json(200, summary(copy));
      }
      if (b === "disclosure" && method === "GET") return json(200, job.disclosure ? { ...job.disclosure, ready: true } : { ready: false });
      if (b === "title" && method === "PUT") {
        const title = String((body as { title?: string })?.title ?? "").trim();
        if (!title) return json(422, { detail: "the title is empty" });
        job.title = title;
        d.title = title;
        return json(200, { id, title });
      }
      if (b === "start" && method === "POST") {
        if (state.active) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        if (d.stage === "material" && job.material) {
          if (job.material.pending.length) return json(409, { detail: { message: "choose material for every wish first", code: "state" } });
          d.gate = null;
          void simulate(id, ["cut", "overlays", "verify"], "result", () => {
            d.outputs = { reel: "out/reel.mp4", preview: "out/preview.mp4", cover: "out/cover.jpg" };
            d.verify = { ok: true, checks: { loudness: { ok: true } }, flagged: [], overlay_flagged: [], screen_flagged: [] };
          });
          return json(202, { ok: true });
        }
        void simulate(id, ["prep", "plan"], "plan", () => {
          job.plan = planFor(job.title ?? "Neues Video");
          if (d.material && d.material.mode !== "none" && job.plan.plan) job.plan.plan.material_wishes = clone(WISHES);
          d.plan_sha = job.plan.sha;
          d.redaction = { candidates: clone(CANDIDATES), decision: null };
          planned(job);
        });
        return json(202, { ok: true });
      }
      if (b === "plan" && !c && method === "GET") return job.plan ? json(200, job.plan) : json(404, { detail: "no plan yet" });
      if (b === "plan" && !c && method === "PUT") {
        const { markdown, sha: s } = body as { markdown: string; sha: string };
        if (!job.plan || s !== job.plan.sha) return json(409, { detail: { message: "the plan changed since you read it; read it again", code: "stale" } });
        job.plan = { markdown, plan: job.plan.plan, sha: sha(markdown) };
        d.plan_sha = job.plan.sha;
        d.approved = false;
        return json(200, job.plan);
      }
      if (b === "plan" && c === "feedback" && method === "POST") {
        if (state.active) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        const text = String((body as { text?: string }).text ?? "");
        const slide = (body as { slide?: string }).slide;
        if (slide && !(job.plan?.plan?.slides ?? []).some((s) => s.id === slide))
          return json(400, { detail: { message: `the plan has no slide '${slide}'` } });
        d.approved = false;
        void simulate(id, ["plan_revise"], "plan", () => {
          job.plan = planFor(job.title ?? "Neues Video", 1, `Auf deinen Wunsch: ${text}`);
          planned(job);
          d.plan_sha = job.plan.sha;
        });
        return json(202, { ok: true });
      }
      if (b === "approve" && method === "POST") {
        if (state.active) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        const req = body as { plan_sha: string; redaction?: { mode: string; ids: number[] } };
        if (!job.plan || req.plan_sha !== job.plan.sha) return json(409, { detail: { message: "the plan changed since you read it; read it again", code: "stale" } });
        if (d.plan_target_stale) return json(409, { detail: { message: "the plan was made for another target length: plan again", code: "state" } });
        d.approved = true;
        d.redaction.decision = req.redaction ? { ...req.redaction } : null;
        const wishes = job.plan.plan?.material_wishes ?? [];
        if (d.material && d.material.mode !== "none" && wishes.length) {
          job.material = { mode: d.material.mode, image_provider: state.materialSettings.image_provider, budget_usd: state.materialSettings.material_budget_usd, cost_usd: 0,
            wishes: wishes.map((w) => ({ ...w, decided: false, chosen: null, candidates: [] })), pending: wishes.map((w) => w.n), fetched: false };
          d.material = { ...d.material, wishes: wishes.length, pending: job.material.pending, fetched: false };
          void simulate(id, ["material"], "material", () => {
            const mv = job.material!;
            mv.wishes = mv.wishes.map((w) => ({ ...w, candidates: mockCandidates(id, w, mv.mode) }));
            mv.fetched = true;
            mv.cost_usd = mv.mode === "stock" ? 0 : 0.13;
            d.material = { ...d.material!, fetched: true };
          });
          return json(202, { ok: true });
        }
        void simulate(id, ["cut", "overlays", "verify"], "result", () => {
          job.cutWith = resolveStyle(d);
          d.cut_stale = false;
          d.outputs = { reel: "out/reel.mp4", preview: "out/preview.mp4", cover: "out/cover.jpg" };
          d.verify = { ok: true, checks: { loudness: { ok: true } }, flagged: [], overlay_flagged: [], screen_flagged: [] };
        });
        return json(202, { ok: true });
      }
      if (b === "material") {
        const mv = job.material;
        if (!c && method === "GET")
          return mv ? json(200, mv) : json(200, { mode: d.material?.mode ?? "none", image_provider: state.materialSettings.image_provider, budget_usd: state.materialSettings.material_budget_usd, cost_usd: 0, wishes: [], pending: [], fetched: false });
        if (!mv) return json(409, { detail: { message: "the approved plan has no material wishes", code: "state" } });
        if (c === "fetch" && method === "POST") {
          if (state.active) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
          const req = (body ?? {}) as { wish?: number; query?: string; prompt?: string; generate?: boolean };
          if (req.wish !== undefined && !mv.wishes.some((w) => w.n === req.wish)) return json(400, { detail: { message: `no material wish ${req.wish}` } });
          void simulate(id, ["material"], "material", () => {
            mv.wishes = mv.wishes.map((w) => {
              if (req.wish !== undefined && w.n !== req.wish) return w;
              const nw = { ...w, query: req.query ?? w.query, prompt: req.prompt ?? w.prompt };
              const fresh = mockCandidates(id, nw, mv.mode, req.wish !== undefined ? 3 : 0);
              const cands = req.generate ? [...w.candidates.filter((x) => !x.ai_generated), ...fresh.filter((x) => x.ai_generated)] : fresh;
              return { ...nw, candidates: cands, decided: false, chosen: null };
            });
            mv.pending = mv.wishes.filter((w) => !w.decided).map((w) => w.n);
            mv.fetched = true;
            d.material = { ...d.material!, fetched: true, pending: mv.pending };
          });
          return json(202, { id, run: "material", state: "queued" });
        }
        if (c === "choose" && method === "POST") {
          const req = body as { n: number; candidate: string | null };
          const w = mv.wishes.find((x) => x.n === req.n);
          if (!w) return json(400, { detail: { message: `no material wish ${req.n}` } });
          if (req.candidate !== null && !w.candidates.some((x) => x.id === req.candidate))
            return json(400, { detail: { message: `wish ${req.n} has no candidate '${req.candidate}'` } });
          w.decided = true;
          w.chosen = req.candidate;
          mv.pending = mv.wishes.filter((x) => !x.decided).map((x) => x.n);
          d.material = { ...d.material!, pending: mv.pending };
          return json(200, mv);
        }
      }
      if (b === "cut-style" && method === "PUT") {
        const v = String((body as { cut_style?: string })?.cut_style ?? "");
        if (!["auto", "calm", "standard", "tight"].includes(v)) return json(422, { detail: `unknown cut style '${v}'` });
        if (state.active === id) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        d.cut_style = v as JobDetail["cut_style"];
        d.cut_style_resolved = resolveStyle(d);
        d.cut_stale = !!d.outputs?.reel && d.approved && job.cutWith !== d.cut_style_resolved;
        return json(200, d);
      }
      if (b === "redact-style" && method === "PUT") {
        const v = String((body as { redact_style?: string })?.redact_style ?? "");
        if (!["auto", "blur", "crop", "block"].includes(v)) return json(422, { detail: `unknown redact style '${v}'` });
        if (state.active === id) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        d.redact_style = v as JobDetail["redact_style"];
        d.redact_style_default = false;
        return json(200, { id, redact_style: d.redact_style, cut_stale: !!d.cut_stale });
      }
      if (b === "target" && method === "PUT") {
        const raw = (body as { target_s?: unknown })?.target_s;
        const v = raw === null || raw === undefined || raw === "auto" ? null : Number(raw);
        if (v !== null && !(Number.isFinite(v) && v >= 5 && v <= 10800)) return json(400, { detail: `invalid target length '${String(raw)}'` });
        if (state.active === id) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        const previous = d.target_s ?? null;
        d.target_s = v;
        d.target_resolved = resolveTarget(d);
        const stale = !!job.plan && (job.planTarget ?? null) !== v;
        const revoked = stale && d.approved;
        d.plan_target_stale = stale;
        d.plan_target_s = stale ? (job.planTarget ?? null) : null;
        if (stale) d.approved = false;
        return json(200, { id, target_s: v, target_resolved: d.target_resolved, previous, plan_stale: stale, approval_revoked: revoked, reel_stale: !!d.outputs?.reel && v !== previous, approved: d.approved });
      }
      if (b === "vocabulary") {
        const view = (extra: Record<string, unknown> = {}) => {
          const brandWords = (d.brand && state.brands.get(d.brand)?.vocabulary) || [];
          const own = job.vocabulary ?? [];
          const effective = [...brandWords.filter((e) => !own.some((o) => o.term.toLowerCase() === e.term.toLowerCase())), ...own];
          const known = new Set(effective.flatMap((e) => [e.term, ...e.variants]).map((x) => x.toLowerCase()));
          return json(200, {
            id,
            vocabulary: own,
            brand: d.brand,
            brand_vocabulary: brandWords,
            effective,
            applied: job.vocabApplied ? { applied: job.vocabApplied, terms: {} } : null,
            has_transcript: d.status !== "new",
            suggestions: (job.heard ?? []).filter((s) => !known.has(s.heard.toLowerCase())),
            overlays_stale: !!d.overlays_stale,
            ...extra,
          });
        };
        if (!c && method === "GET") return view();
        if (state.active === id) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        if (!c && method === "PUT") {
          const list = ((body as { vocabulary?: VocabEntry[] })?.vocabulary ?? []) as VocabEntry[];
          const errors = vocabErrors(list, "$.vocabulary");
          if (errors.length) return json(422, { detail: { message: "the vocabulary has problems", errors } });
          job.vocabulary = list;
          return view();
        }
        if (c === "apply" && method === "POST") {
          if (d.status === "new") return json(409, { detail: { message: "work/words.json is missing", code: "state" } });
          const brandWords = (d.brand && state.brands.get(d.brand)?.vocabulary) || [];
          const words = [...brandWords, ...(job.vocabulary ?? [])];
          const heard = new Set(words.flatMap((e) => e.variants.map((v) => v.toLowerCase())));
          const applied = (job.heard ?? []).filter((s) => heard.has(s.heard.toLowerCase())).reduce((n, s) => n + s.count, 0);
          const captions = d.outputs?.reel ? applied : 0;
          job.vocabApplied = applied;
          if (captions) d.overlays_stale = true;
          return view({ result: { applied, terms: {}, caption_words: captions, overlays_stale: captions > 0 } });
        }
      }
      if (b === "transcript") {
        if (d.status === "new") return json(409, { detail: { message: "the job has no transcript yet", code: "state" } });
        job.words ??= mockWords();
        job.edits ??= [];
        const words = job.words;
        const view = () =>
          json(200, {
            id, total: words.length, offset: 0, words, has_reel: !!d.outputs?.reel, edits: words.filter((w) => w.edited).length,
            can_undo: job.edits!.length > 0, edits_at: null, drift: 0, captions_stale: !!d.captions_stale, overlays_stale: !!d.overlays_stale,
          });
        if (!c && method === "GET") return view();
        if (state.active === id) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        if (!c && method === "PATCH") {
          const { i, text } = body as { i: number; text: string };
          if (typeof text !== "string" || text.length > 60 || /[\u0000-\u001f]/.test(text)) return json(422, { detail: { message: "invalid text" } });
          const w = words[i];
          if (!w) return json(422, { detail: { message: `no word ${i}` } });
          job.edits.push({ i, before: { ...w } });
          const asr = w.asr ?? w.w;
          words[i] = text ? { ...w, w: text, edited: true, asr, hidden: undefined } : { ...w, w: asr, edited: true, asr, hidden: true };
          if (d.outputs?.reel && d.brand) d.captions_stale = d.overlays_stale = true;
          return view();
        }
        if (c === "undo" && method === "POST") {
          const last = job.edits.pop();
          if (!last) return json(409, { detail: { message: "there is no edit to undo", code: "state" } });
          words[last.i] = last.before;
          return view();
        }
      }
      if (b === "captions" && c === "refresh" && method === "POST") {
        if (state.active) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        if (!d.overlays_stale) return json(409, { detail: { message: "the captions of this job are up to date", code: "state" } });
        void simulate(id, ["overlays", "verify"], "result", () => {
          d.captions_stale = d.overlays_stale = false;
        });
        return json(202, { ok: true });
      }
      if (b === "overlays" && method === "POST") {
        if (state.active) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        if (!d.overlays_stale) return json(409, { detail: { message: "the overlays of this job are not stale", code: "state" } });
        void simulate(id, ["overlays", "verify"], "result", () => {
          d.overlays_stale = false;
        });
        return json(202, { ok: true });
      }
      if (b === "recut" && method === "POST") {
        if (state.active) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        if (!d.approved || !job.plan) return json(409, { detail: { message: "the plan is not approved", code: "state" } });
        void simulate(id, ["cut", "overlays", "verify"], "result", () => {
          job.cutWith = resolveStyle(d);
          d.cut_stale = false;
          d.outputs = { reel: "out/reel.mp4", preview: "out/preview.mp4", cover: "out/cover.jpg" };
        });
        return json(202, { ok: true });
      }
      if (b === "feedback" && method === "POST") {
        if (state.active) return json(409, { detail: { message: "a run of this job is queued or active", code: "busy" } });
        const fb = body as { text: string; at_s?: number };
        const n = d.revisions.length + 1;
        void simulate(id, ["revise", "verify"], "result", () => {
          // Like the server: the reel before revision n is kept as out/history/reel-<n>.mp4,
          // so version k (k < latest) plays history/reel-k, the latest one the current reel.
          const version = d.revisions.filter((r) => r.ok !== false).length + 2;
          d.revisions.push({ n, time: new Date().toISOString(), text: fb.text, at_s: fb.at_s ?? null, ok: true, changed: { segments: [2] }, cost: 0.19, previous_reel: `out/history/reel-${version - 1}.mp4`, version } as never);
          const reel = `/api/jobs/${id}/media/reel`;
          d.versions = [
            ...d.revisions.filter((r) => r.version).map((r) => ({ version: r.version!, revision: r.n ?? null, text: r.text, video_url: r.version === version ? reel : `/api/jobs/${id}/media/history/reel-${r.version}.mp4`, current: r.version === version })).reverse(),
            { version: 1, revision: null, text: null, video_url: `/api/jobs/${id}/media/history/reel-1.mp4`, current: false },
          ];
          for (const r of d.revisions) if (r.version) r.video_url = d.versions.find((v) => v.version === r.version)?.video_url ?? undefined;
        });
        return json(202, { ok: true });
      }
    }
    return json(404, { detail: "not found" });
  }

  const transport: Transport = {
    async request(method, path, body) {
      state.calls.push({ method, path, body });
      return route(method, path, body);
    },
    async upload(path, form, onProgress: (p: UploadProgress) => void) {
      state.calls.push({ method: "POST", path, body: form });
      const files = ["camera", "screen", "assets"].flatMap((k) => form.getAll(k)).filter((f): f is File => f instanceof File);
      const total = files.reduce((s, f) => s + f.size, 0) || 1;
      for (let i = 1; i <= 10; i++) {
        await later(step / 5);
        onProgress({ loaded: (total * i) / 10, total });
      }
      const camera = form.get("camera");
      if (!(camera instanceof File)) return json(422, { detail: "camera recording missing" });
      const id = `job-${Date.now().toString(36)}-${counter++}`;
      const withScreen = form.get("screen") instanceof File || form.getAll("assets").length > 0;
      const fmt = String(form.get("format") ?? "auto");
      const title = camera.name.replace(/\.[^.]+$/, "");
      const style = String(form.get("cut_style") ?? "auto");
      const redact = String(form.get("redact_style") ?? "auto");
      const target = String(form.get("target_s") ?? "auto");
      const words = JSON.parse(String(form.get("vocabulary") ?? "[]")) as VocabEntry[];
      const wordErrors = vocabErrors(words, "$.vocabulary");
      if (wordErrors.length) return json(422, { detail: { message: "the vocabulary has problems", errors: wordErrors } });
      state.jobs.set(id, {
        detail: baseDetail(id, {
          has_screen: withScreen,
          target_s: target === "auto" ? null : Number(target),
          cut_style: style as JobDetail["cut_style"],
          redact_style: redact as JobDetail["redact_style"],
          redact_style_default: redact === "auto",
          cut_style_resolved: (style === "auto" ? (withScreen ? "calm" : "standard") : style) as JobDetail["cut_style_resolved"],
          type: String(form.get("type")),
          brand: (form.get("brand") as string) || null,
          format: fmt as JobDetail["format"],
          format_resolved: (fmt === "auto" ? (withScreen ? "16:9" : "9:16") : fmt) as JobDetail["format_resolved"],
          brief: (form.get("brief") as string) || null,
          title,
          material: {
            mode: (String(form.get("material_mode") ?? "none") as MaterialMode),
            wish: String(form.get("material_wish") ?? ""),
            stale: false,
            wishes: 0,
            pending: [],
            fetched: false,
          },
        }),
        created: new Date().toISOString(),
        title,
        plan: null,
        events: [{ t: "stage", stage: "prep" }],
        vocabulary: words,
      });
      return json(201, { id });
    },
    events(path, onEvent) {
      const id = decodeURIComponent(path.split("/")[3]);
      const set = listeners.get(id) ?? new Set();
      listeners.set(id, set);
      set.add(onEvent);
      // Replay the current state, like the server does on connect.
      const replay = [...(state.jobs.get(id)?.events ?? [])];
      setTimeout(() => replay.forEach(onEvent), 0);
      return () => set.delete(onEvent);
    },
    url(path) {
      const u = new URL(path, "http://127.0.0.1");
      const p = u.pathname.split("/").filter(Boolean).map(decodeURIComponent);
      if (p[1] === "brands" && p[3] === "sheet") {
        const br = state.brands.get(p[2]);
        return brandSheet(br?.colors ?? {}, u.searchParams.get("format") ?? "9:16");
      }
      if (p[1] === "jobs" && p[3] === "stills") {
        const name = p.slice(4).join("/");
        const m = name.match(/(\d+)/);
        const n = m ? Number(m[1]) : 1;
        const fmt = state.jobs.get(p[2])?.detail.format_resolved ?? "16:9";
        if (name.includes("pd-") || name.includes("view-3")) return screenStill(n);
        return personStill(n, fmt);
      }
      if (p[1] === "jobs" && p[3] === "material" && p[4] === "files") {
        const m = (p[6] ?? "").match(/c(\d+)/);
        const n = m ? Number(m[1]) : 1;
        return p[6]?.endsWith(".mp4") ? "data:video/mp4;base64," : personStill(n + 10 * Number(p[5] ?? 1), "9:16");
      }
      if (p[1] === "jobs" && p[3] === "media" && p[4] === "cover") return coverPicture(state.jobs.get(p[2])?.detail.format_resolved ?? "9:16");
      if (p[1] === "jobs" && (p[3] === "media" || p[3] === "download")) return "data:video/mp4;base64,";
      return path;
    },
  };

  return { state, transport, emit };
}

export function installMock(opts: MockOptions = {}) {
  const mock = createMock(opts);
  setTransport(mock.transport);
  return mock;
}
