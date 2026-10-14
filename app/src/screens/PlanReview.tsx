import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type { PdCandidate, Plan, PlanJson, PlanKeep, RedactionDecision } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { LiveLog } from "../components/LiveLog";
import { Markdown } from "../components/Markdown";
import { clockFine, useI18n } from "../i18n";
import type { Live } from "./JobPage";
import { PersonalDataDialog } from "./PersonalData";

const keepKey = (k: PlanKeep) => `${k.src[0]}-${k.src[1]}-${k.text}`;

/** Keep-list entries of `next` that were not in `prev` (shown as changed). */
export function changedKeeps(prev: PlanJson | null, next: PlanJson | null): Set<number> {
  const out = new Set<number>();
  if (!prev?.keep_list || !next?.keep_list) return out;
  const before = new Set(prev.keep_list.map(keepKey));
  for (const k of next.keep_list) if (!before.has(keepKey(k))) out.add(k.n);
  return out;
}

/** Personal-data candidates with the still (and the boxes drawn on it) from plan.json
 *  when the job list has none. */
function withStills(cands: PdCandidate[], plan: PlanJson | null): PdCandidate[] {
  const byId = new Map((plan?.personal_data ?? []).map((p) => [p.id, p]));
  return cands.map((c) => {
    if (c.still) return c;
    const p = byId.get(c.id);
    return { ...c, still: p?.still ?? undefined, still_boxes: p?.still_boxes ?? c.still_boxes };
  });
}

export function PlanReview({ live }: { live: Live }) {
  const { t } = useI18n();
  const job = live.job!;
  const [plan, setPlan] = useState<Plan | null>(null);
  const [prev, setPrev] = useState<PlanJson | null>(null);
  const [lastAsk, setLastAsk] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [conflict, setConflict] = useState(false);
  const [ask, setAsk] = useState("");
  const [asking, setAsking] = useState(false);
  const [pdOpen, setPdOpen] = useState(false);
  const [showMd, setShowMd] = useState(false);
  const [approving, setApproving] = useState(false);
  const planRef = useRef<Plan | null>(null);
  planRef.current = plan;

  const load = async () => {
    try {
      const p = await api.plan(job.id);
      setPlan(p);
      setErr(null);
      return p;
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) setErr(e);
      return null;
    }
  };

  // Reload after every finished run or gate (a revised plan arrives that way).
  useEffect(() => {
    void load().then(() => setAsking(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.id, live.tick]);

  const revising = asking || job.stage === "plan_revise";
  const approvable = !job.approved && !live.running && !revising && plan !== null && job.status !== "running";

  const sendAsk = async (e: FormEvent) => {
    e.preventDefault();
    const text = ask.trim();
    if (!text) return;
    setErr(null);
    setPrev(planRef.current?.plan ?? null);
    setLastAsk(text);
    setAsking(true);
    try {
      live.begin("plan_revise");
      await api.planFeedback(job.id, text);
      setAsk("");
    } catch (e2) {
      setAsking(false);
      setErr(e2);
      void live.reload();
    }
  };

  const save = async () => {
    if (!plan) return;
    setErr(null);
    try {
      const p = await api.savePlan(job.id, draft, plan.sha);
      setPlan(p);
      setEditing(false);
      setConflict(false);
      void live.reload();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict(true);
      setErr(e);
    }
  };

  const approve = async (redaction: RedactionDecision | null) => {
    if (!plan) return;
    setApproving(true);
    setErr(null);
    try {
      await api.approve(job.id, plan.sha, redaction);
      setPdOpen(false);
      live.begin("cut");
      void live.reload();
    } catch (e) {
      setErr(e);
      setPdOpen(false);
      if (e instanceof ApiError && e.status === 409) void load();
    } finally {
      setApproving(false);
    }
  };

  const candidates = withStills(job.redaction?.candidates ?? [], plan?.plan ?? null);
  const onApprove = () => (candidates.length ? setPdOpen(true) : void approve(null));
  const changed = changedKeeps(prev, plan?.plan ?? null);

  return (
    <section className="panel stack" aria-labelledby="plan-title">
      <div className="titlebar">
        <h2 id="plan-title">{t("plan.title")}</h2>
        {job.approved && <span className="chip chip-done">{t("plan.approved")}</span>}
      </div>
      <ErrorBanner error={err} />
      {conflict && (
        <button
          type="button"
          className="btn btn-small"
          onClick={async () => {
            const p = await load();
            if (p) setDraft(p.markdown);
            setConflict(false);
            setErr(null);
          }}
        >
          {t("common.retry")}
        </button>
      )}

      {!plan && !revising && <p className="muted">{t("plan.none")}</p>}

      {plan && lastAsk && !revising && (
        <div className="banner banner-change" role="status">
          <strong>{t("plan.changed")}:</strong> {plan.plan?.change_note || lastAsk}
        </div>
      )}

      {plan && !editing && (plan.plan ? <StructuredPlan jobId={job.id} plan={plan.plan} changed={changed} /> : <Markdown source={plan.markdown} />)}

      {plan && plan.plan && !editing && (
        <details className="details" onToggle={(e) => setShowMd(e.currentTarget.open)}>
          <summary>{t("plan.markdown")}</summary>
          {showMd && <Markdown source={plan.markdown} />}
        </details>
      )}

      {editing && plan && (
        <div className="stack-sm">
          <p className="hint">{t("plan.edit.hint")}</p>
          <label htmlFor="plan-md" className="sr-only">
            {t("plan.edit")}
          </label>
          <textarea id="plan-md" className="editor" value={draft} onChange={(e) => setDraft(e.target.value)} rows={24} spellCheck />
          <div className="actions">
            <button type="button" className="btn btn-primary" onClick={save}>
              {t("plan.edit.save")}
            </button>
            <button type="button" className="btn" onClick={() => (setEditing(false), setConflict(false))}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      )}

      {!job.approved && plan && !editing && (
        <>
          <form className="askbox" onSubmit={sendAsk}>
            <label htmlFor="plan-ask">{t("plan.ask")}</label>
            <div className="askbox-row">
              <textarea
                id="plan-ask"
                rows={2}
                value={ask}
                placeholder={t("plan.ask.placeholder")}
                disabled={revising}
                onChange={(e) => setAsk(e.target.value)}
              />
              <button type="submit" className="btn" disabled={revising || live.running || !ask.trim()}>
                {t("plan.ask.send")}
              </button>
            </div>
          </form>
          {revising && (
            <div className="stack-sm" role="status">
              <p className="working">{t("plan.ask.running")}</p>
              <LiveLog lines={live.lines} defaultOpen />
            </div>
          )}
          <div className="actions">
            <button type="button" className="btn btn-primary" disabled={!approvable || approving} onClick={onApprove}>
              {t("plan.approve")}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={revising || live.running}
              onClick={() => {
                setDraft(plan.markdown);
                setEditing(true);
              }}
            >
              {t("plan.edit")}
            </button>
          </div>
        </>
      )}

      {pdOpen && (
        <PersonalDataDialog
          jobId={job.id}
          candidates={candidates}
          busy={approving}
          onCancel={() => setPdOpen(false)}
          onConfirm={(d) => void approve(d)}
        />
      )}
    </section>
  );
}

function riskText(r: PlanJson["risks"] extends (infer U)[] | undefined ? U : never): string {
  return typeof r === "string" ? r : (r?.text ?? JSON.stringify(r));
}

export function StructuredPlan({ jobId, plan, changed }: { jobId: string; plan: PlanJson; changed: Set<number> }) {
  const { t } = useI18n();
  const core = plan.core_message ?? (plan as { core?: string }).core;
  const hook = typeof plan.hook === "string" ? plan.hook : plan.hook?.text;
  return (
    <div className="plan stack">
      {(hook || core) && (
        <div className="grid-2">
          {hook && (
            <div className="callout">
              <span className="label">{t("plan.hook")}</span>
              <p className="callout-text">{hook}</p>
            </div>
          )}
          {core && (
            <div className="callout">
              <span className="label">{t("plan.core")}</span>
              <p className="callout-text">{core}</p>
            </div>
          )}
        </div>
      )}

      {plan.keep_list && plan.keep_list.length > 0 && (
        <div className="stack-sm">
          <h3>{t("plan.keep")}</h3>
          <ol className="timeline">
            {plan.keep_list.map((k) => (
              <li key={k.n} className={changed.has(k.n) ? "tl-changed" : undefined}>
                <span className="tl-time">
                  {clockFine(k.src[0])} – {clockFine(k.src[1])}
                </span>
                <div className="tl-body">
                  <p>
                    {k.text}
                    {changed.has(k.n) && <span className="chip chip-change">{t("plan.changed")}</span>}
                  </p>
                  {k.why && (
                    <p className="muted">
                      {t("plan.why")}: {k.why}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {plan.views && plan.views.length > 0 && (
        <div className="stack-sm">
          <h3>{t("plan.views")}</h3>
          <ul className="viewcards">
            {plan.views.map((v) => (
              <li key={v.n} className="viewcard">
                <div className="viewcard-still">
                  {v.still ? <img src={api.stillUrl(jobId, v.still)} alt="" loading="lazy" /> : <span className="thumb-empty" />}
                </div>
                <div className="viewcard-meta">
                  <span className="muted">
                    {clockFine(v.src[0])} – {clockFine(v.src[1])}
                  </span>
                  <span className="badges">
                    <span className="badge">{v.layout}</span>
                    {v.zoom_box && <span className="badge badge-accent">{t("plan.zoom")}</span>}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {plan.overlays && plan.overlays.length > 0 && (
        <div className="stack-sm">
          <h3>{t("plan.overlays")}</h3>
          <ul className="plainlist">
            {plan.overlays.map((o, i) => (
              <li key={i}>
                {o.t0 !== undefined && <span className="muted">{clockFine(o.t0)} </span>}
                {o.scene && <span className="badge">{o.scene}</span>} {o.text ?? (o as { label?: string }).label ?? ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {plan.risks && plan.risks.length > 0 && (
        <div className="stack-sm">
          <h3>{t("plan.risks")}</h3>
          <ul className="plainlist risks">
            {plan.risks.map((r, i) => (
              <li key={i}>{riskText(r)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
