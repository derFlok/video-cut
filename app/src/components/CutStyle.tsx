import { useState } from "react";
import { api } from "../api/client";
import { CUT_STYLES, type CutStyleChoice } from "../api/types";
import { money, useI18n } from "../i18n";
import { aboutMinutes, recutEstimate } from "../progress";
import type { Live } from "../screens/JobPage";
import { ErrorBanner } from "./ErrorBanner";

/** The cut style select with a one-line explanation of the chosen style. */
export function CutStyleSelect({
  id,
  value,
  resolved,
  disabled,
  onChange,
}: {
  id: string;
  value: CutStyleChoice;
  /** What "auto" stands for here, when known. */
  resolved?: string | null;
  disabled?: boolean;
  onChange: (v: CutStyleChoice) => void;
}) {
  const { t } = useI18n();
  const name = (v: string) => t(`style.${v as CutStyleChoice}`);
  return (
    <div className="stack-sm">
      <select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as CutStyleChoice)}>
        {CUT_STYLES.map((s) => (
          <option key={s} value={s}>
            {s === "auto" && resolved ? t("style.auto.is", { style: name(resolved) }) : name(s)}
          </option>
        ))}
      </select>
      <p className="hint" data-testid="style-desc">
        {t(`style.${value}.desc`)}
      </p>
    </div>
  );
}

/** The job's cut style: shown on the plan and the result, changeable; a cut made with
 * another style offers "cut again" with what it costs. */
export function CutStylePanel({ live }: { live: Live }) {
  const { t, lang } = useI18n();
  const job = live.job!;
  const [err, setErr] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const value: CutStyleChoice = (job.cut_style as CutStyleChoice | null | undefined) ?? "auto";
  const hasCut = !!job.outputs?.reel && job.approved;

  const change = async (v: CutStyleChoice) => {
    setErr(null);
    setSaved(false);
    setSaving(true);
    try {
      await api.setCutStyle(job.id, v);
      await live.reload();
      setSaved(true);
    } catch (e) {
      setErr(e);
    } finally {
      setSaving(false);
    }
  };
  const recut = async () => {
    setErr(null);
    setSaved(false);
    try {
      live.begin("cut");
      await api.recut(job.id);
    } catch (e) {
      setErr(e);
      void live.reload();
    }
  };

  const est = recutEstimate(job);
  const hint = t("style.recut.hint", {
    time: est.seconds ? aboutMinutes(est.seconds, t) : t("style.recut.time.unknown"),
    cost: est.cost !== null ? t("style.recut.hint.cost", { c: money(est.cost, lang) }) : "",
  });

  return (
    <section className="panel stack-sm cutstyle" aria-labelledby="cutstyle-title">
      <label id="cutstyle-title" htmlFor="cutstyle" className="h3">
        {t("style.title")}
      </label>
      <CutStyleSelect
        id="cutstyle"
        value={value}
        resolved={job.cut_style_resolved}
        disabled={saving || live.running}
        onChange={(v) => void change(v)}
      />
      {saved && !job.cut_stale && !hasCut && (
        <p className="hint" role="status">
          {t("style.saved")}
        </p>
      )}
      {hasCut && job.cut_stale && !live.running && (
        <div className="banner banner-info" data-testid="recut">
          <p>{t("style.stale")}</p>
          <p className="hint">{hint}</p>
          <button type="button" className="btn btn-primary" onClick={() => void recut()}>
            {t("style.recut")}
          </button>
        </div>
      )}
      <ErrorBanner error={err} />
    </section>
  );
}
