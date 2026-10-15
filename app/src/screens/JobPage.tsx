import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { JobDetail, JobEvent, Stage } from "../api/types";
import { CutStylePanel } from "../components/CutStyle";
import { ErrorBanner } from "../components/ErrorBanner";
import { LiveLog, type LogLine } from "../components/LiveLog";
import { StatusChip } from "../components/StatusChip";
import { StatusLine } from "../components/StatusLine";
import { JobVocabularyPanel, OverlaysStaleBanner } from "../components/Vocabulary";
import { Stepper } from "../components/Stepper";
import { clock, money, useI18n } from "../i18n";
import { kindOf, type RunKind } from "../progress";
import { go } from "../router";
import { PlanReview } from "./PlanReview";
import { Result } from "./Result";

const MAX_LINES = 600;
const GATES: Stage[] = ["approval", "done", "error"];

export interface Live {
  job: JobDetail | null;
  error: unknown;
  lines: LogLine[];
  running: boolean;
  /** Increments whenever a run finishes or reaches a gate (screens reload their data). */
  tick: number;
  lastOk: boolean | null;
  /** The last stage before an error (the stepper marks it as failed). */
  lastStage: Stage | undefined;
  reload: () => Promise<void>;
  /** Mark a run as started by this page (before the first event arrives). */
  begin: (stage: Stage) => void;
  /** The kind of run the current stage belongs to (a revision also ends in "verify"). */
  kind: RunKind;
  /** When the current stage began (ms, this page's clock) and its lines so far. */
  stageSince: number;
  stageLines: LogLine[];
}

/** Job detail plus its live event stream (the server replays the current state on connect). */
export function useLiveJob(id: string): Live {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const [tick, setTick] = useState(0);
  const [lastOk, setLastOk] = useState<boolean | null>(null);
  const [lastStage, setLastStage] = useState<Stage | undefined>(undefined);
  const stageRef = useRef<Stage | undefined>(undefined);
  const [kind, setKind] = useState<RunKind>(null);
  const [stageSince, setStageSince] = useState(() => Date.now());
  const [stageFrom, setStageFrom] = useState(0);
  const seq = useRef(0);
  const eventStage = useRef<Stage | undefined>(undefined);
  const enterStage = useCallback((stage: Stage, at?: string | null) => {
    // The server's start time (from the event or the job) survives a reload of the page;
    // without one the step starts now.
    const started = at ? Date.parse(at) : NaN;
    if (eventStage.current === stage) {
      if (!Number.isNaN(started)) setStageSince(Math.min(started, Date.now()));
      return;
    }
    eventStage.current = stage;
    setKind((k) => kindOf(stage, k));
    setStageSince(Number.isNaN(started) ? Date.now() : Math.min(started, Date.now()));
    setStageFrom(seq.current);
  }, []);

  const reload = useCallback(async () => {
    try {
      const j = await api.job(id);
      stageRef.current = j.stage;
      if (j.status === "running" && j.stage_since) enterStage(j.stage, j.stage_since);
      setJob(j);
      setRunning(j.status === "running");
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [id, enterStage]);

  useEffect(() => {
    void reload();
    const add = (l: LogLine) => setLines((prev) => [...prev.slice(-(MAX_LINES - 1)), { ...l, n: seq.current++ }]);
    const close = api.events(
      id,
      (ev: JobEvent) => {
        switch (ev.t) {
          case "stage": {
            // A replay of the stage the job is already in says nothing new; a change means a
            // run moved on (gates, done and error end it).
            const moved = stageRef.current !== undefined && stageRef.current !== ev.stage;
            stageRef.current = ev.stage;
            enterStage(ev.stage, ev.at);
            if (ev.stage !== "error") setLastStage(ev.stage);
            setJob((j) => (j ? { ...j, stage: ev.stage } : j));
            if (ev.stage === "error") setRunning(false);
            else if (moved && !GATES.includes(ev.stage)) setRunning(true);
            if (moved) void reload();
            break;
          }
          case "log":
            add({ kind: "log", text: ev.text });
            break;
          case "tool":
            add({ kind: "tool", text: ev.text });
            break;
          case "gate":
            setRunning(false);
            setTick((n) => n + 1);
            void reload();
            break;
          case "done":
            setRunning(false);
            setLastOk(ev.ok);
            setTick((n) => n + 1);
            void reload();
            break;
          case "error":
            add({ kind: "error", text: ev.text });
            setLastOk(false);
            break;
        }
      },
      () => undefined, // EventSource reconnects by itself
    );
    return close;
  }, [id, reload, enterStage]);

  const begin = useCallback((stage: Stage) => {
    stageRef.current = stage;
    eventStage.current = undefined;
    setKind(null);
    enterStage(stage);
    setRunning(true);
    setLastOk(null);
    setJob((j) => (j ? { ...j, stage, status: "running" } : j));
  }, [enterStage]);

  const stageLines = lines.filter((l) => (l.n ?? 0) >= stageFrom);
  return { job, error, lines, running, tick, lastOk, lastStage, reload, begin, kind, stageSince, stageLines };
}

function useElapsed(running: boolean): number {
  const [secs, setSecs] = useState(0);
  const started = useRef<number | null>(null);
  useEffect(() => {
    if (!running) {
      started.current = null;
      setSecs(0);
      return;
    }
    started.current = Date.now();
    const timer = setInterval(() => setSecs((Date.now() - (started.current ?? Date.now())) / 1000), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return secs;
}

export function JobPage({ id }: { id: string }) {
  const { t, lang } = useI18n();
  const live = useLiveJob(id);
  const { job, running } = live;
  const [actionErr, setActionErr] = useState<unknown>(null);
  const [planOpen, setPlanOpen] = useState(false);
  const live_s = useElapsed(running);

  if (live.error && !job) return <ErrorBanner error={live.error} onRetry={() => void live.reload()} />;
  if (!job) return <p className="muted">{t("common.loading")}</p>;

  const wall = job.runs.reduce((s, r) => s + (r.wall_s || 0), 0) + live_s;
  const cost = job.runs.reduce((s, r) => s + (r.cost_usd || 0), 0);
  const hasCost = job.runs.some((r) => r.cost_usd !== null && r.cost_usd !== undefined);

  const start = async () => {
    setActionErr(null);
    try {
      live.begin("prep");
      await api.start(id);
    } catch (e) {
      setActionErr(e);
      void live.reload();
    }
  };
  const remove = async () => {
    if (!window.confirm(t("job.delete.confirm"))) return;
    try {
      await api.remove(id);
      go({ name: "home" });
    } catch (e) {
      setActionErr(e);
    }
  };

  const atGateOrLater = ["approval", "cut", "overlays", "verify", "done", "revise", "plan_revise"].includes(job.stage) || job.approved;
  const showPlan = atGateOrLater && !(job.outputs?.reel && job.approved && job.stage !== "plan_revise");
  const canStart = !running && (job.status === "new" || job.status === "error" || (job.stage === "prep" && job.status !== "waiting"));

  return (
    <div className="stack-lg">
      <div className="titlebar">
        <div>
          <h1>{job.title || t("home.untitled")}</h1>
          {job.brief && <p className="muted">„{job.brief}“</p>}
        </div>
        <StatusChip status={running ? "running" : job.status} stage={job.stage} />
      </div>

      <section className="panel stack">
        <Stepper stage={job.stage} status={running ? "running" : job.status} lastGood={live.lastStage} />
        <StatusLine live={live} />
        <div className="meta-row">
          <span>{t("job.elapsed", { t: clock(wall) })}</span>
          <span>{t("job.cost", { c: hasCost ? money(cost, lang) : "—" })}</span>
        </div>
        <LiveLog lines={live.lines} />
        {canStart && (
          <div className="actions">
            <button type="button" className="btn btn-primary" onClick={start}>
              {job.status === "new" ? t("job.start") : t("job.continue")}
            </button>
          </div>
        )}
        <ErrorBanner error={actionErr} />
      </section>

      {atGateOrLater && <CutStylePanel live={live} />}
      <OverlaysStaleBanner live={live} />
      <JobVocabularyPanel live={live} />
      {showPlan && <PlanReview live={live} />}
      {job.outputs?.reel && job.approved && job.stage !== "plan_revise" && (
        <>
          <Result live={live} />
          <details className="details panel" onToggle={(e) => setPlanOpen(e.currentTarget.open)}>
            <summary>{t("job.plan.show")}</summary>
            {planOpen && <PlanReview live={live} />}
          </details>
        </>
      )}

      <div className="actions actions-end">
        <button type="button" className="btn btn-ghost btn-danger" onClick={remove} disabled={running}>
          {t("job.delete")}
        </button>
      </div>
    </div>
  );
}
