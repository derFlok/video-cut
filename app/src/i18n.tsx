// Every visible string of the UI, German (default) and English.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { Lang } from "./api/types";

const de = {
  "app.name": "Video Cut",
  "nav.jobs": "Videos",
  "nav.brands": "Marken",
  "nav.lang": "English",
  "nav.lang.label": "Sprache wechseln",
  "nav.skip": "Zum Inhalt springen",
  "common.loading": "Lädt …",
  "common.retry": "Erneut versuchen",
  "common.cancel": "Abbrechen",
  "common.save": "Speichern",
  "common.close": "Schließen",
  "common.back": "Zurück",
  "common.delete": "Löschen",
  "common.none": "—",
  "error.network": "Keine Verbindung zur App. Läuft `videocut ui` noch?",
  "error.forbidden": "Sitzung abgelaufen. Bitte die App über `videocut ui` neu öffnen.",
  "error.busy": "Claude arbeitet gerade an einem anderen Schritt. Bitte warten, bis er fertig ist.",
  "error.stale": "Der Plan wurde inzwischen geändert. Bitte neu laden und die Änderung erneut machen.",
  "error.generic": "Etwas ist schiefgegangen: {msg}",
  "claude.missing": "Claude ist nicht angemeldet. Bitte `videocut setup` ausführen.",

  "home.title": "Deine Videos",
  "home.new": "Neues Video",
  "home.empty": "Noch keine Videos. Leg mit „Neues Video“ los.",
  "home.untitled": "Ohne Titel",
  "home.created": "Angelegt {date}",

  "status.new": "Neu",
  "status.running": "Läuft",
  "status.waiting": "Wartet auf dich",
  "status.done": "Fertig",
  "status.error": "Fehler",

  "stage.prep": "Vorbereiten",
  "stage.plan": "Plan",
  "stage.approval": "Freigabe",
  "stage.cut": "Schnitt",
  "stage.overlays": "Einblendungen",
  "stage.verify": "Prüfung",
  "stage.done": "Fertig",
  "stage.revise": "Korrektur",
  "stage.plan_revise": "Plan wird überarbeitet",
  "stage.error": "Fehler",

  "new.title": "Neues Video",
  "new.type": "1. Videoart",
  "new.files": "2. Aufnahmen",
  "new.camera": "Kamera-Aufnahme",
  "new.camera.hint": "Pflicht. Video hierher ziehen oder klicken. Tipp: 4K quer oder direkt hochkant aufnehmen.",
  "new.screen": "Bildschirmaufnahme",
  "new.screen.hint": "Optional. Für Produkt-Walkthroughs.",
  "new.assets": "Screenshots",
  "new.assets.hint": "Optional, mehrere. Reihenfolge mit den Pfeilen ändern.",
  "new.drop": "Datei hierher ziehen oder klicken",
  "new.replace": "Ersetzen",
  "new.remove": "Entfernen",
  "new.up": "Nach oben",
  "new.down": "Nach unten",
  "new.look": "3. Aussehen",
  "new.brand": "Marke",
  "new.brand.none": "Neutral (ohne Marke)",
  "new.format": "Format",
  "new.format.auto": "Automatisch",
  "new.format.auto.hint.screen": "Automatisch wählt 16:9, weil eine Bildschirmaufnahme oder Screenshots dabei sind.",
  "new.format.auto.hint.camera": "Automatisch wählt 9:16 (hochkant), weil nur eine Kamera-Aufnahme dabei ist.",
  "new.brief": "4. Anweisung an Claude (eine Zeile, optional)",
  "new.brief.placeholder": "z. B. „Kurz halten, Fokus auf die neue Funktion“",
  "new.submit": "Hochladen und starten",
  "new.uploading": "Lädt hoch … {pct} %",
  "new.starting": "Starte …",
  "new.err.type": "Bitte eine Videoart wählen.",
  "new.err.camera": "Bitte eine Kamera-Aufnahme hinzufügen.",
  "new.err.video": "„{name}“ ist kein Video (erlaubt: {ext}).",
  "new.err.image": "„{name}“ ist kein Bild (erlaubt: {ext}).",
  "new.err.needs": "Diese Videoart braucht: {what}.",

  "job.title": "Video",
  "job.elapsed": "Laufzeit {t}",
  "job.cost": "Kosten bisher {c}",
  "job.log": "Protokoll",
  "job.log.show": "Protokoll anzeigen",
  "job.log.hide": "Protokoll ausblenden",
  "job.log.empty": "Noch keine Einträge.",
  "job.start": "Starten",
  "job.continue": "Weiter",
  "job.failed": "Der letzte Lauf ist fehlgeschlagen.",
  "job.delete": "Video löschen",
  "job.delete.confirm": "Dieses Video wirklich löschen? Es wird in den Papierkorb von Video Cut verschoben.",
  "job.working": "Claude arbeitet …",
  "job.idle": "Bereit.",

  "plan.title": "Der Plan",
  "plan.hook": "Einstieg (Hook)",
  "plan.core": "Kernaussage",
  "plan.keep": "Was bleibt drin",
  "plan.views": "Bildausschnitte",
  "plan.overlays": "Einblendungen",
  "plan.risks": "Risiken",
  "plan.why": "Warum",
  "plan.zoom": "Zoom",
  "plan.markdown": "Plan als Text",
  "plan.edit": "Plan bearbeiten",
  "plan.edit.save": "Plan speichern",
  "plan.edit.hint": "Du bearbeitest den Plan direkt. Nach dem Speichern musst du ihn neu freigeben.",
  "plan.ask": "Was soll Claude am Plan ändern?",
  "plan.ask.placeholder": "z. B. „Die Frage zu den Anrufen weglassen, dafür das Beispiel länger“",
  "plan.ask.send": "An Claude schicken",
  "plan.ask.running": "Claude überarbeitet den Plan …",
  "plan.changed": "Geändert",
  "plan.approve": "Plan freigeben und schneiden",
  "plan.none": "Noch kein Plan.",
  "plan.approved": "Freigegeben",

  "pd.title": "Persönliche Daten im Video",
  "pd.intro": "Claude hat Stellen gefunden, die persönliche Daten zeigen könnten. Sicher ist: alles schwärzen. Wenn es nur Demodaten sind, kannst du sie sichtbar lassen.",
  "pd.default": "Voreinstellung: alles wird geschwärzt.",
  "pd.mode.redact_all": "Alles schwärzen",
  "pd.mode.demo": "Alles Demodaten (nichts schwärzen)",
  "pd.mode.redact": "Einzeln wählen",
  "pd.row.redact": "schwärzen",
  "pd.kind.email": "E-Mail",
  "pd.kind.phone": "Telefonnummer",
  "pd.kind.name": "Name",
  "pd.kind.customer": "Kundendaten",
  "pd.kind.other": "Sonstiges",
  "pd.where.asset": "Screenshot {name}",
  "pd.count": "{n} von {total} werden geschwärzt.",
  "pd.confirm": "Freigeben",

  "result.title": "Ergebnis",
  "result.verify.ok": "Die automatische Prüfung hat nichts gefunden.",
  "result.verify.bad": "Die automatische Prüfung hat {n} Stelle(n) markiert:",
  "result.verify.cut": "Schnitt bei {t}: {flags}",
  "result.verify.overlay": "Einblendung {scene} bei {t}: {flags}",
  "result.verify.screen": "Bildschirm: {flags}",
  "result.verify.check": "Prüfung „{name}“ nicht bestanden",
  "result.feedback": "Was soll anders werden?",
  "result.feedback.placeholder": "z. B. „Hier weniger rauszoomen“ oder „Die Frage zu den Anrufen weglassen“",
  "result.feedback.at": "bei aktueller Stelle ({t})",
  "result.feedback.send": "Korrektur schicken",
  "result.feedback.running": "Claude setzt die Korrektur um …",
  "result.history": "Versionen",
  "result.history.empty": "Noch keine Korrekturen.",
  "result.history.at": "bei {t}",
  "result.history.play": "Abspielen",
  "result.history.current": "Aktuell",
  "result.download.mp4": "MP4 herunterladen",
  "result.download.cover": "Cover herunterladen",
  "result.none": "Noch kein fertiges Video.",

  "brands.title": "Marken",
  "brands.new": "Neue Marke",
  "brands.new.name": "Name der neuen Marke",
  "brands.new.hint": "Startet mit den neutralen Beispielwerten.",
  "brands.empty": "Noch keine Marken.",
  "brands.preview": "Vorschau",
  "brands.edit": "Bearbeiten",
  "brands.editor": "Marke bearbeiten",
  "brands.saved": "Gespeichert.",
  "brands.errors": "Die Marke ist so nicht gültig:",
  "brands.name": "Name",
  "brands.colors": "Farben",
  "brands.color.bg": "Hintergrund",
  "brands.color.surface": "Fläche",
  "brands.color.text": "Text",
  "brands.color.muted": "Nebentext",
  "brands.color.accent": "Akzent",
  "brands.fonts": "Schriften",
  "brands.font.display": "Überschriften",
  "brands.font.body": "Fließtext",
  "brands.font.system": "Systemschrift",
  "brands.font.upload": "Schrift hochladen (woff2, ttf, otf)",
  "brands.pace": "Untertitel-Tempo",
  "brands.pace.calm": "ruhig",
  "brands.pace.normal": "normal",
  "brands.pace.fast": "schnell",
  "brands.address": "Anrede",
  "brands.address.du": "du",
  "brands.address.sie": "Sie",
  "brands.logo": "Logo",
  "brands.logo.upload": "Logo hochladen (SVG, PNG)",
  "brands.logo.light": "für helle Flächen",
  "brands.logo.dark": "für dunkle Flächen",
  "brands.logo.placement": "Platzierung",
  "brands.logo.intro": "am Anfang",
  "brands.logo.outro": "am Ende",
  "brands.logo.corner": "in der Ecke",
  "brands.logo.none": "keins",
  "brands.files": "Dateien",
  "brands.uploaded": "„{name}“ hochgeladen.",
  "brands.readonly": "Die neutrale Beispielmarke kann nicht bearbeitet werden. Leg eine neue Marke an.",

  "time.ago": "vor {t}",
};

type Key = keyof typeof de;
const en: Record<Key, string> = {
  "app.name": "Video Cut",
  "nav.jobs": "Videos",
  "nav.brands": "Brands",
  "nav.lang": "Deutsch",
  "nav.lang.label": "Switch language",
  "nav.skip": "Skip to content",
  "common.loading": "Loading …",
  "common.retry": "Try again",
  "common.cancel": "Cancel",
  "common.save": "Save",
  "common.close": "Close",
  "common.back": "Back",
  "common.delete": "Delete",
  "common.none": "—",
  "error.network": "Cannot reach the app. Is `videocut ui` still running?",
  "error.forbidden": "Session expired. Please reopen the app with `videocut ui`.",
  "error.busy": "Claude is busy with another step. Please wait until it is done.",
  "error.stale": "The plan changed in the meantime. Please reload and make your change again.",
  "error.generic": "Something went wrong: {msg}",
  "claude.missing": "Claude is not signed in. Please run `videocut setup`.",

  "home.title": "Your videos",
  "home.new": "New video",
  "home.empty": "No videos yet. Start with “New video”.",
  "home.untitled": "Untitled",
  "home.created": "Created {date}",

  "status.new": "New",
  "status.running": "Running",
  "status.waiting": "Waiting for you",
  "status.done": "Done",
  "status.error": "Error",

  "stage.prep": "Prepare",
  "stage.plan": "Plan",
  "stage.approval": "Approval",
  "stage.cut": "Cut",
  "stage.overlays": "Overlays",
  "stage.verify": "Check",
  "stage.done": "Done",
  "stage.revise": "Revision",
  "stage.plan_revise": "Revising the plan",
  "stage.error": "Error",

  "new.title": "New video",
  "new.type": "1. Video type",
  "new.files": "2. Recordings",
  "new.camera": "Camera recording",
  "new.camera.hint": "Required. Drop a video here or click. Tip: record 4K landscape or straight in portrait.",
  "new.screen": "Screen recording",
  "new.screen.hint": "Optional. For product walkthroughs.",
  "new.assets": "Screenshots",
  "new.assets.hint": "Optional, several. Change the order with the arrows.",
  "new.drop": "Drop a file here or click",
  "new.replace": "Replace",
  "new.remove": "Remove",
  "new.up": "Move up",
  "new.down": "Move down",
  "new.look": "3. Look",
  "new.brand": "Brand",
  "new.brand.none": "Neutral (no brand)",
  "new.format": "Format",
  "new.format.auto": "Automatic",
  "new.format.auto.hint.screen": "Automatic picks 16:9 because a screen recording or screenshots are included.",
  "new.format.auto.hint.camera": "Automatic picks 9:16 (portrait) because there is only a camera recording.",
  "new.brief": "4. Instruction for Claude (one line, optional)",
  "new.brief.placeholder": "e.g. “Keep it short, focus on the new feature”",
  "new.submit": "Upload and start",
  "new.uploading": "Uploading … {pct} %",
  "new.starting": "Starting …",
  "new.err.type": "Please pick a video type.",
  "new.err.camera": "Please add a camera recording.",
  "new.err.video": "“{name}” is not a video (allowed: {ext}).",
  "new.err.image": "“{name}” is not an image (allowed: {ext}).",
  "new.err.needs": "This video type needs: {what}.",

  "job.title": "Video",
  "job.elapsed": "Elapsed {t}",
  "job.cost": "Cost so far {c}",
  "job.log": "Log",
  "job.log.show": "Show log",
  "job.log.hide": "Hide log",
  "job.log.empty": "No entries yet.",
  "job.start": "Start",
  "job.continue": "Continue",
  "job.failed": "The last run failed.",
  "job.delete": "Delete video",
  "job.delete.confirm": "Really delete this video? It is moved to the Video Cut trash.",
  "job.working": "Claude is working …",
  "job.idle": "Ready.",

  "plan.title": "The plan",
  "plan.hook": "Hook",
  "plan.core": "Core message",
  "plan.keep": "What stays in",
  "plan.views": "Framing",
  "plan.overlays": "Overlays",
  "plan.risks": "Risks",
  "plan.why": "Why",
  "plan.zoom": "Zoom",
  "plan.markdown": "Plan as text",
  "plan.edit": "Edit plan",
  "plan.edit.save": "Save plan",
  "plan.edit.hint": "You are editing the plan directly. After saving you need to approve it again.",
  "plan.ask": "What should Claude change in the plan?",
  "plan.ask.placeholder": "e.g. “Drop the question about the calls, make the example longer”",
  "plan.ask.send": "Send to Claude",
  "plan.ask.running": "Claude is revising the plan …",
  "plan.changed": "Changed",
  "plan.approve": "Approve plan and cut",
  "plan.none": "No plan yet.",
  "plan.approved": "Approved",

  "pd.title": "Personal data in the video",
  "pd.intro": "Claude found places that may show personal data. The safe choice is to redact everything. If it is only demo data, you can leave it visible.",
  "pd.default": "Default: everything is redacted.",
  "pd.mode.redact_all": "Redact everything",
  "pd.mode.demo": "All demo data (redact nothing)",
  "pd.mode.redact": "Choose per item",
  "pd.row.redact": "redact",
  "pd.kind.email": "Email",
  "pd.kind.phone": "Phone number",
  "pd.kind.name": "Name",
  "pd.kind.customer": "Customer data",
  "pd.kind.other": "Other",
  "pd.where.asset": "Screenshot {name}",
  "pd.count": "{n} of {total} will be redacted.",
  "pd.confirm": "Approve",

  "result.title": "Result",
  "result.verify.ok": "The automatic check found nothing.",
  "result.verify.bad": "The automatic check flagged {n} place(s):",
  "result.verify.cut": "Cut at {t}: {flags}",
  "result.verify.overlay": "Overlay {scene} at {t}: {flags}",
  "result.verify.screen": "Screen: {flags}",
  "result.verify.check": "Check “{name}” failed",
  "result.feedback": "What should change?",
  "result.feedback.placeholder": "e.g. “Zoom out less here” or “Drop the question about the calls”",
  "result.feedback.at": "at the current position ({t})",
  "result.feedback.send": "Send correction",
  "result.feedback.running": "Claude is applying the correction …",
  "result.history": "Versions",
  "result.history.empty": "No corrections yet.",
  "result.history.at": "at {t}",
  "result.history.play": "Play",
  "result.history.current": "Current",
  "result.download.mp4": "Download MP4",
  "result.download.cover": "Download cover",
  "result.none": "No finished video yet.",

  "brands.title": "Brands",
  "brands.new": "New brand",
  "brands.new.name": "Name of the new brand",
  "brands.new.hint": "Starts with the neutral example values.",
  "brands.empty": "No brands yet.",
  "brands.preview": "Preview",
  "brands.edit": "Edit",
  "brands.editor": "Edit brand",
  "brands.saved": "Saved.",
  "brands.errors": "The brand is not valid like this:",
  "brands.name": "Name",
  "brands.colors": "Colours",
  "brands.color.bg": "Background",
  "brands.color.surface": "Surface",
  "brands.color.text": "Text",
  "brands.color.muted": "Secondary text",
  "brands.color.accent": "Accent",
  "brands.fonts": "Fonts",
  "brands.font.display": "Headings",
  "brands.font.body": "Body",
  "brands.font.system": "System font",
  "brands.font.upload": "Upload a font (woff2, ttf, otf)",
  "brands.pace": "Caption pace",
  "brands.pace.calm": "calm",
  "brands.pace.normal": "normal",
  "brands.pace.fast": "fast",
  "brands.address": "Address form",
  "brands.address.du": "informal (du)",
  "brands.address.sie": "formal (Sie)",
  "brands.logo": "Logo",
  "brands.logo.upload": "Upload a logo (SVG, PNG)",
  "brands.logo.light": "for light surfaces",
  "brands.logo.dark": "for dark surfaces",
  "brands.logo.placement": "Placement",
  "brands.logo.intro": "at the start",
  "brands.logo.outro": "at the end",
  "brands.logo.corner": "in the corner",
  "brands.logo.none": "none",
  "brands.files": "Files",
  "brands.uploaded": "“{name}” uploaded.",
  "brands.readonly": "The neutral example brand cannot be edited. Create a new brand.",

  "time.ago": "{t} ago",
};

export const STRINGS: Record<Lang, Record<Key, string>> = { de, en };
export type TKey = Key;
export type T = (key: Key, vars?: Record<string, string | number>) => string;

function format(s: string, vars?: Record<string, string | number>): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m));
}

interface Ctx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: T;
}

const I18n = createContext<Ctx | null>(null);

const STORE = "videocut.lang";

function initialLang(): Lang {
  try {
    const v = localStorage.getItem(STORE);
    if (v === "de" || v === "en") return v;
  } catch {
    /* storage may be blocked */
  }
  return "de";
}

export function I18nProvider({ children, lang: fixed }: { children: ReactNode; lang?: Lang }) {
  const [lang, setLangState] = useState<Lang>(fixed ?? initialLang);
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(STORE, l);
    } catch {
      /* ignore */
    }
    document.documentElement.lang = l;
  }, []);
  const value = useMemo<Ctx>(
    () => ({ lang, setLang, t: (key, vars) => format(STRINGS[lang][key] ?? key, vars) }),
    [lang, setLang],
  );
  return <I18n.Provider value={value}>{children}</I18n.Provider>;
}

export function useI18n(): Ctx {
  const ctx = useContext(I18n);
  if (!ctx) throw new Error("useI18n outside I18nProvider");
  return ctx;
}

/** 75.3 -> "1:15" (output time in the player and the logs). */
export function clock(s: number): string {
  const v = Math.max(0, Math.floor(s));
  const m = Math.floor(v / 60);
  return `${m}:${String(v % 60).padStart(2, "0")}`;
}

/** 75.3 -> "1:15.3" (source times in the plan). */
export function clockFine(s: number): string {
  const v = Math.max(0, s);
  const m = Math.floor(v / 60);
  const rest = (v - m * 60).toFixed(1).padStart(4, "0");
  return `${m}:${rest}`;
}

export function money(usd: number | null | undefined, lang: Lang): string {
  if (usd === null || usd === undefined) return "—";
  return new Intl.NumberFormat(lang === "de" ? "de-DE" : "en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(usd);
}

export function date(iso: string | undefined | null, lang: Lang): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(lang === "de" ? "de-DE" : "en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}
