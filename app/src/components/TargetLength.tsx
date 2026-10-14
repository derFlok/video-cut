import { useEffect, useState } from "react";
import { api } from "../api/client";
import { TARGET_MAX_S, TARGET_MIN_S, TARGET_PRESETS, type PlanJson, type TargetChoice, type TargetRange } from "../api/types";
import { clock, useI18n, type T } from "../i18n";
import type { Live } from "../screens/JobPage";
import { ErrorBanner } from "./ErrorBanner";

/** "2:30" -> 150, "90" -> 90 (seconds); null when unreadable or out of range. */
export function parseMmss(text: string): number | null {
  const v = text.trim();
  let secs: number | null = null;
  const m = v.match(/^(\d{1,3}):([0-5]\d)$/);
  if (m) secs = Number(m[1]) * 60 + Number(m[2]);
  else if (/^\d{1,5}$/.test(v)) secs = Number(v);
  return secs !== null && secs >= TARGET_MIN_S && secs <= TARGET_MAX_S ? secs : null;
}

/** 150 -> "2:30". */
export function formatMmss(secs: number): string {
  return `${Math.floor(secs / 60)}:${String(Math.round(secs % 60)).padStart(2, "0")}`;
}

/** 60 -> "60 s", 300 -> "5 Min", 150 -> "2:30 Min", null -> "Automatisch". */
export function targetLabel(secs: number | null | undefined, t: T): string {
  if (secs === null || secs === undefined) return t("target.auto");
  if (secs < 120) return t("target.secs", { n: Math.round(secs) });
  if (secs % 60 === 0) return t("target.mins", { n: secs / 60 });
  const [m, s] = formatMmss(secs).split(":");
  return t("target.minsecs", { m, s });
}

/** The range the plan aims for, in plain words. */
export function rangeText(range: TargetRange | null | undefined, t: T): string {
  if (!range) return t("target.range.none");
  if (range.max_s < 120) return t("target.range", { min: range.min_s, max: t("target.secs", { n: range.max_s }) });
  const [m, s] = formatMmss(range.max_s).split(":");
  return t("target.range", { min: formatMmss(range.min_s), max: t("target.minsecs", { m, s }) });
}

/** Automatic, the presets, or a custom mm:ss. `value` undefined: a custom entry that is not
 * valid (yet); onChange reports it the same way. */
export function TargetSelect({
  id,
  value,
  disabled,
  onChange,
}: {
  id: string;
  value: TargetChoice | undefined;
  disabled?: boolean;
  onChange: (v: TargetChoice | undefined) => void;
}) {
  const { t } = useI18n();
  const preset = value === null || (value !== undefined && TARGET_PRESETS.includes(value));
  const [custom, setCustom] = useState(!preset);
  const [text, setText] = useState(value !== null && value !== undefined && !preset ? formatMmss(value) : "");
  const mode = custom ? "custom" : value === null || value === undefined ? "auto" : String(value);
  const invalid = custom && text.trim() !== "" && parseMmss(text) === null;

  return (
    <div className="stack-sm">
      <select
        id={id}
        value={mode}
        disabled={disabled}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "custom") {
            setCustom(true);
            onChange(parseMmss(text) ?? undefined);
            return;
          }
          setCustom(false);
          onChange(v === "auto" ? null : Number(v));
        }}
      >
        <option value="auto">{t("target.auto")}</option>
        {TARGET_PRESETS.map((s) => (
          <option key={s} value={String(s)}>
            {targetLabel(s, t)}
          </option>
        ))}
        <option value="custom">{t("target.custom")}</option>
      </select>
      {custom && (
        <div className="field">
          <label htmlFor={`${id}-custom`}>{t("target.custom.label")}</label>
          <input
            id={`${id}-custom`}
            type="text"
            inputMode="numeric"
            value={text}
            disabled={disabled}
            placeholder={t("target.custom.placeholder")}
            aria-invalid={invalid}
            onChange={(e) => {
              setText(e.target.value);
              onChange(parseMmss(e.target.value) ?? undefined);
            }}
          />
          {invalid && (
            <p className="hint hint-error" role="alert">
              {t("target.custom.invalid")}
            </p>
          )}
        </div>
      )}
      <p className="hint" data-testid="target-hint">
        {t("target.auto.hint")}
      </p>
    </div>
  );
}

/** On the plan: the target, the range the plan aims for and its estimate. Before approval
 * the target can change; a plan made for another target is stale and offers a new plan
 * (`onReplan`, the plan-feedback path). */
export function TargetPanel({
  live,
  plan,
  onReplan,
}: {
  live: Live;
  plan: PlanJson | null;
  onReplan: (text: string) => void;
}) {
  const { t } = useI18n();
  const job = live.job!;
  const current = job.target_s ?? null;
  const [draft, setDraft] = useState<TargetChoice | undefined>(current);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => setDraft(job.target_s ?? null), [job.target_s]);

  const busy = live.running || job.status === "running" || job.stage === "plan_revise";
  const editable = !job.approved && !busy;
  const estimate = plan?.estimated_s;

  const apply = async () => {
    if (draft === undefined) return;
    setErr(null);
    setSaving(true);
    try {
      await api.setTarget(job.id, draft);
      await live.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="stack-sm target" aria-labelledby="target-title">
      {editable ? (
        <label id="target-title" htmlFor="target" className="h3">
          {t("target.title")}
        </label>
      ) : (
        <h3 id="target-title" data-testid="target-value">
          {t("target.title")}: {targetLabel(current, t)}
        </h3>
      )}
      <p className="muted" data-testid="target-summary">
        {rangeText(job.target_resolved, t)}
        {estimate ? ` ${t("target.estimate", { t: clock(estimate) })}` : ""}
      </p>
      {editable && (
        <>
          <TargetSelect id="target" value={draft} disabled={saving} onChange={setDraft} />
          {draft !== current && (
            <div className="stack-sm">
              <p className="hint">{t("target.change.note")}</p>
              <div className="actions">
                <button type="button" className="btn" disabled={saving || draft === undefined} onClick={() => void apply()}>
                  {t("target.apply")}
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {job.plan_target_stale && !busy && (
        <div className="banner banner-info" data-testid="target-stale">
          <p>{t("target.stale", { old: targetLabel(job.plan_target_s, t), new: targetLabel(current, t) })}</p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onReplan(t("target.replan.text", { len: targetLabel(current, t) }))}
          >
            {t("target.replan")}
          </button>
        </div>
      )}
      <ErrorBanner error={err} />
    </section>
  );
}
