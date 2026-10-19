// What a job is doing right now, in plain words: derived from the SSE stage and the log/tool
// lines, so nobody needs the log to understand the state. Pure functions (tested directly).

import type { JobDetail, Run, Stage } from "./api/types";
import type { LogLine } from "./components/LiveLog";
import type { T, TKey } from "./i18n";

/** What kind of run produced the current stage: a revision ends in "verify" too. */
export type RunKind = "prep" | "plan" | "cut" | "revise" | null;

export function kindOf(stage: Stage, prev: RunKind): RunKind {
  if (stage === "prep") return "prep";
  if (stage === "plan" || stage === "plan_revise") return "plan";
  if (stage === "cut" || stage === "overlays") return "cut";
  if (stage === "revise") return "revise";
  return prev;
}

// The local preparation steps in run order (src/videocut/run.py PREP / DEMO_PREP). run.py
// logs a step when it is finished, so the one running is the first not yet logged.
const PREP_STEPS: { id: string; screen: boolean }[] = [
  { id: "ingest", screen: false },
  { id: "sync", screen: true },
  { id: "screen", screen: true },
  { id: "redact text", screen: true },
  { id: "face", screen: false },
  { id: "transcribe", screen: false },
  { id: "sheet", screen: false },
];

/** The preparation step running now, from the lines of the current run. */
export function prepStep(lines: LogLine[], withScreen: boolean): string {
  const done = new Set<string>();
  for (const l of lines) {
    if (l.kind !== "log") continue;
    const s = l.text.trim();
    for (const p of PREP_STEPS) if (s === p.id || s.startsWith(`${p.id} `)) done.add(p.id);
  }
  const steps = PREP_STEPS.filter((p) => withScreen || !p.screen);
  return steps.find((p) => !done.has(p.id))?.id ?? "sheet";
}

/** The plain-language "what is happening" line. */
export function activity(job: JobDetail, running: boolean, kind: RunKind, lines: LogLine[], t: T): string {
  const stage = job.stage;
  if (!running) {
    if (stage === "approval") return t("now.approval");
    if (stage === "material") return t("now.material.choose");
    if (stage === "done") return job.cut_stale ? t("now.stale") : t("now.done");
    if (stage === "error" || job.status === "error") {
      const at = job.failed_stage && job.failed_stage !== "error" ? t(`stage.${job.failed_stage}` as TKey) : null;
      return at ? t("now.error.at", { stage: at }) : t("now.error");
    }
    if (job.status === "new") return t("now.new");
    return t("now.idle");
  }
  switch (stage) {
    case "prep": {
      const step = prepStep(lines, job.has_screen ?? false);
      return t(`now.prep.${step.replace(" ", "_")}` as TKey);
    }
    case "plan":
      return t("now.plan");
    case "plan_revise":
      return t("now.plan_revise");
    case "cut": {
      const last = [...lines].reverse().find((l) => l.kind === "tool");
      return last && /\brender\b/.test(last.text) ? t("now.cut.render") : t("now.cut");
    }
    case "overlays":
      return t("now.overlays");
    case "material":
      return t("now.material");
    case "verify":
      return kind === "revise" ? t("now.verify.revise") : t("now.verify");
    case "revise":
      return t("now.revise");
    default:
      return t("now.working");
  }
}

/** Rough wall time of a stage from the recording length (seconds), fitted on real runs. */
export function expectedSeconds(stage: Stage, duration_s: number | null | undefined): number | null {
  if (!duration_s || duration_s <= 0) return null;
  const d = duration_s;
  switch (stage) {
    case "prep":
      return 30 + 0.5 * d;
    case "plan":
    case "plan_revise":
      return 60 + 0.35 * d;
    case "cut":
      return 60 + 1.8 * d;
    case "overlays":
      return 120 + 1.4 * d;
    case "verify":
      return 20 + 0.2 * d;
    case "material":
      return 60;
    case "revise":
      return 60 + 0.6 * d;
    default:
      return null;
  }
}

/** "ca. 12 Min." for an expected duration (whole minutes, at least one). */
export function aboutMinutes(s: number, t: T): string {
  return t("now.about", { m: Math.max(1, Math.round(s / 60)) });
}

/** Why the correction box is locked, in words that match what runs; null when it is open. */
export function lockReason(job: JobDetail, running: boolean, kind: RunKind, sending: boolean, interim: boolean, t: T): string | null {
  if (sending) return t("lock.sending");
  if (running) {
    switch (job.stage) {
      case "revise":
        return t("lock.revise");
      case "verify":
        return kind === "revise" ? t("lock.verify.revise") : t("lock.verify");
      case "cut":
        return t("lock.cut");
      case "overlays":
        return t("lock.overlays");
      case "prep":
      case "plan":
      case "plan_revise":
      case "material":
        return t("lock.plan");
      default:
        return t("lock.busy");
    }
  }
  if (interim) return t("lock.interim");
  if (job.cut_stale) return t("lock.stale");
  return null;
}

/** The reel on disk is only a step on the way (cut without overlays, or not yet checked):
 * a cut run is still going, or it stopped before overlays were done. */
export function isInterim(job: JobDetail, running: boolean, kind: RunKind): boolean {
  if (running) return ["cut", "overlays", "verify"].includes(job.stage) && kind !== "revise";
  return job.status === "error" && (job.failed_stage === "cut" || job.failed_stage === "overlays");
}

/** Time and cost of cutting again (cut + overlays), from the last runs of each phase. */
export function recutEstimate(job: JobDetail): { seconds: number | null; cost: number | null } {
  const last = (phase: string): Run | undefined => [...job.runs].reverse().find((r) => r.phase === phase);
  const runs = [last("cut"), last("overlays")].filter((r): r is Run => !!r);
  const costs = runs.map((r) => r.cost_usd).filter((c): c is number => typeof c === "number");
  const fromRuns = runs.length === 2 ? runs.reduce((s, r) => s + (r.wall_s || 0), 0) : null;
  const guess = ["cut", "overlays", "verify"].reduce<number | null>((s, st) => {
    const e = expectedSeconds(st as Stage, job.source_duration_s);
    return s === null || e === null ? null : s + e;
  }, 0);
  return { seconds: fromRuns || guess, cost: costs.length ? costs.reduce((a, b) => a + b, 0) : null };
}
