import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { MOTION_MODES, MOTION_WISH_MAX, type MotionBeat, type MotionMode, type PlanMotion } from "../api/types";
import { clockFine, useI18n, type T, type TKey } from "../i18n";

/** Problems of the motion part of the new-job form (empty: fine). */
export function motionErrors(mode: MotionMode, wish: string, t: T): string[] {
  const errs: string[] = [];
  if (mode === "wish" && !wish.trim()) errs.push(t("motion.err.nowish"));
  if (wish.length > MOTION_WISH_MAX) errs.push(t("motion.err.wish", { n: MOTION_WISH_MAX }));
  return errs;
}

/** "Animationen" in the new-job form: off, Claude proposes them, following a wish. */
export function MotionPicker({
  mode,
  onMode,
  wish,
  onWish,
  disabled,
}: {
  mode: MotionMode;
  onMode: (m: MotionMode) => void;
  wish: string;
  onWish: (w: string) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  return (
    <fieldset className="stack" data-testid="new-motion">
      <legend className="h2">{t("motion.title")}</legend>
      <p className="hint">{t("motion.hint")}</p>
      <div className="segmented segmented-wrap" role="radiogroup" aria-label={t("motion.title")}>
        {MOTION_MODES.map((m) => (
          <label key={m} className={`seg${mode === m ? " seg-on" : ""}`}>
            <input
              type="radio"
              name="motion-mode"
              className="sr-only"
              value={m}
              checked={mode === m}
              disabled={disabled}
              onChange={() => onMode(m)}
            />
            <span>{t(`motion.mode.${m}` as TKey)}</span>
          </label>
        ))}
      </div>
      {mode !== "off" && <p className="hint">{t(`motion.mode.${mode}.hint` as TKey)}</p>}
      {mode === "wish" && (
        <div className="field">
          <label htmlFor="motion-wish">{t("motion.wish")}</label>
          <textarea
            id="motion-wish"
            rows={2}
            value={wish}
            maxLength={MOTION_WISH_MAX}
            disabled={disabled}
            placeholder={t("motion.wish.placeholder")}
            onChange={(e) => onWish(e.target.value)}
          />
        </div>
      )}
    </fieldset>
  );
}

/** A component name of the catalogue in plain words (the id when there is no translation). */
export function componentName(c: string, t: T): string {
  const key = `motion.comp.${c}` as TKey;
  const s = t(key);
  return s === key ? c : s;
}

/** One beat in plain words: "a1: „anmelden“ (2.)". */
export function beatText(id: string, b: MotionBeat, t: T): string {
  const n = b.n && b.n > 1 ? ` (${b.n}.)` : "";
  const edge = b.edge === "end" ? ` ${t("motion.beat.end")}` : "";
  return `${id}: „${b.word}“${n}${edge}`;
}

/** One card per planned motion moment at the plan gate: where the band goes (still), the
 *  component(s), what the band shows, the beat list; a feedback line (a plan feedback run). */
export function MotionCards({
  jobId,
  moments,
  canAsk,
  onAsk,
}: {
  jobId: string;
  moments: PlanMotion[];
  canAsk: boolean;
  onAsk: (text: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="stack-sm" data-testid="plan-motion">
      <h3>{t("motion.plan")}</h3>
      <p className="hint">{t("motion.plan.hint")}</p>
      <ul className="viewcards slidecards">
        {moments.map((m) => (
          <MotionCard key={m.id} jobId={jobId} m={m} canAsk={canAsk} onAsk={onAsk} />
        ))}
      </ul>
    </div>
  );
}

function MotionCard({ jobId, m, canAsk, onAsk }: { jobId: string; m: PlanMotion; canAsk: boolean; onAsk: (text: string) => void }) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const fid = `motion-fb-${m.id}`;
  const send = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    onAsk(`${t("motion.plan.about", { id: m.id })} ${text.trim()}`);
    setText("");
  };
  const beats = Object.entries(m.beats ?? {});
  return (
    <li className="viewcard slidecard" data-motion={m.id}>
      <div className="viewcard-still">
        {m.still ? (
          <img src={api.stillUrl(jobId, m.still)} alt={t("motion.plan.still")} loading="lazy" />
        ) : (
          <span className="thumb-empty">{t("motion.plan.nostill")}</span>
        )}
      </div>
      <div className="viewcard-meta stack-sm">
        <span className="badges">
          {(m.components ?? []).map((c) => (
            <span key={c} className="badge badge-accent">
              {componentName(c, t)}
            </span>
          ))}
        </span>
        <span>
          {t("motion.plan.shows")}: {m.shows}
        </span>
        <span className="muted">
          {t("motion.plan.at")} {clockFine(m.src[0])} – {clockFine(m.src[1])}
        </span>
        {beats.length > 0 && (
          <details>
            <summary>{t("motion.plan.beats", { n: beats.length })}</summary>
            <ul className="beatlist" data-testid={`beats-${m.id}`}>
              {beats.map(([id, b]) => (
                <li key={id}>{beatText(id, b, t)}</li>
              ))}
            </ul>
          </details>
        )}
        {m.why && (
          <span className="muted">
            {t("plan.why")}: {m.why}
          </span>
        )}
        {canAsk && (
          <>
            <form className="slidecard-ask" onSubmit={send}>
              <label htmlFor={fid} className="sr-only">
                {t("motion.plan.feedback")}
              </label>
              <input
                id={fid}
                type="text"
                value={text}
                maxLength={500}
                placeholder={t("motion.plan.feedback.placeholder")}
                onChange={(e) => setText(e.target.value)}
              />
              <button type="submit" className="btn btn-small" disabled={!text.trim()}>
                {t("slides.plan.send")}
              </button>
            </form>
            <button type="button" className="btn btn-small btn-ghost" onClick={() => onAsk(t("motion.plan.remove.text", { id: m.id }))}>
              {t("motion.plan.remove")}
            </button>
          </>
        )}
      </div>
    </li>
  );
}
