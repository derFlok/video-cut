import { useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";
import { SLIDES_MODES, SLIDE_WISH_MAX, type PlanKeep, type PlanSlide, type SlidesMode } from "../api/types";
import { clock, clockFine, useI18n, type T, type TKey } from "../i18n";
import { DropZone, hasExt } from "./DropZone";

export const SLIDE_EXT = [".pdf", ".png", ".jpg", ".jpeg"];
export const MAX_SLIDE_FILES = 30;
export const MAX_SLIDE_BYTES = 200 * 1024 * 1024;

export const needsFiles = (m: SlidesMode) => m === "upload" || m === "both";
export const claudeMakes = (m: SlidesMode) => m === "auto" || m === "both";

/** Problems of the slides part of the new-job form (empty: fine). */
export function slideErrors(mode: SlidesMode, files: File[], wish: string, t: T): string[] {
  const errs: string[] = [];
  if (mode === "upload" && files.length === 0) errs.push(t("slides.err.none"));
  for (const f of files) {
    if (!hasExt(f.name, SLIDE_EXT)) errs.push(t("slides.err.type", { name: f.name }));
    else if (f.size > MAX_SLIDE_BYTES) errs.push(t("slides.err.size", { name: f.name }));
  }
  if (files.length > MAX_SLIDE_FILES) errs.push(t("slides.err.count", { n: MAX_SLIDE_FILES }));
  if (wish.length > SLIDE_WISH_MAX) errs.push(t("slides.err.wish", { n: SLIDE_WISH_MAX }));
  return errs;
}

/** "Folien & Animationen" in the new-job form: none, own pages, Claude makes them, both;
 *  the human's files and a free-text wish. */
export function SlidesPicker({
  mode,
  onMode,
  files,
  onFiles,
  wish,
  onWish,
  invalid,
  disabled,
  fileRows,
}: {
  mode: SlidesMode;
  onMode: (m: SlidesMode) => void;
  files: File[];
  onFiles: (f: File[]) => void;
  wish: string;
  onWish: (w: string) => void;
  invalid?: boolean;
  disabled?: boolean;
  fileRows?: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <fieldset className="stack" data-testid="new-slides">
      <legend className="h2">{t("slides.title")}</legend>
      <p className="hint">{t("slides.hint")}</p>
      <div className="segmented segmented-wrap" role="radiogroup" aria-label={t("slides.title")}>
        {SLIDES_MODES.map((m) => (
          <label key={m} className={`seg${mode === m ? " seg-on" : ""}`}>
            <input
              type="radio"
              name="slides-mode"
              className="sr-only"
              value={m}
              checked={mode === m}
              disabled={disabled}
              onChange={() => onMode(m)}
            />
            <span>{t(`slides.mode.${m}` as TKey)}</span>
          </label>
        ))}
      </div>
      {mode !== "none" && <p className="hint">{t(`slides.mode.${mode}.hint` as TKey)}</p>}
      {needsFiles(mode) && (
        <DropZone
          label={t("slides.files")}
          hint={t("slides.files.hint")}
          accept={SLIDE_EXT}
          multiple
          required={mode === "upload"}
          invalid={invalid && mode === "upload" && files.length === 0}
          onFiles={(f) => onFiles([...files, ...f])}
        >
          {fileRows}
        </DropZone>
      )}
      {mode !== "none" && (
        <div className="field">
          <label htmlFor="slides-wish">{t("slides.wish")}</label>
          <textarea
            id="slides-wish"
            rows={2}
            value={wish}
            maxLength={SLIDE_WISH_MAX}
            disabled={disabled}
            placeholder={t(claudeMakes(mode) ? "slides.wish.placeholder" : "slides.wish.placeholder.upload")}
            onChange={(e) => onWish(e.target.value)}
          />
        </div>
      )}
    </fieldset>
  );
}

/** A slide template id in plain words. */
export function templateName(template: string, t: T): string {
  const key = `slides.tpl.${template}` as TKey;
  const s = t(key);
  return s === key ? template : s;
}

export function layoutName(layout: string, t: T): string {
  const key = `slides.layout.${layout}` as TKey;
  const s = t(key);
  return s === key ? layout : s;
}

/** The keep row whose words a slide illustrates (the most overlap), for "at which moment". */
export function spokenAt(slide: PlanSlide, keep: PlanKeep[] | undefined): PlanKeep | undefined {
  let best: PlanKeep | undefined;
  let most = 0;
  for (const k of keep ?? []) {
    const o = Math.min(k.src[1], slide.t1) - Math.max(k.src[0], slide.t0);
    if (o > most) [best, most] = [k, o];
  }
  return best;
}

/** One card per planned slide at the plan gate: still, template in plain words, the spoken
 *  moment, layout; "Folie entfernen" and a feedback line (both a plan feedback run). */
export function SlideCards({
  jobId,
  slides,
  keep,
  canAsk,
  onAsk,
}: {
  jobId: string;
  slides: PlanSlide[];
  keep?: PlanKeep[];
  canAsk: boolean;
  onAsk: (text: string, slide: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="stack-sm" data-testid="plan-slides">
      <h3>{t("slides.plan")}</h3>
      <ul className="viewcards slidecards">
        {slides.map((s) => (
          <SlideCard key={s.id} jobId={jobId} slide={s} said={spokenAt(s, keep)} canAsk={canAsk} onAsk={onAsk} />
        ))}
      </ul>
    </div>
  );
}

function SlideCard({
  jobId,
  slide,
  said,
  canAsk,
  onAsk,
}: {
  jobId: string;
  slide: PlanSlide;
  said?: PlanKeep;
  canAsk: boolean;
  onAsk: (text: string, slide: string) => void;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const fid = `slide-fb-${slide.id}`;
  const send = (e: FormEvent) => {
    e.preventDefault();
    if (text.trim()) onAsk(text.trim(), slide.id);
  };
  return (
    <li className="viewcard slidecard" data-slide={slide.id}>
      <div className="viewcard-still">
        {slide.still ? (
          <img src={api.stillUrl(jobId, slide.still)} alt={templateName(slide.template, t)} loading="lazy" />
        ) : (
          <span className="thumb-empty">{t("slides.plan.nostill")}</span>
        )}
      </div>
      <div className="viewcard-meta stack-sm">
        <span className="badges">
          <span className="badge badge-accent">{templateName(slide.template, t)}</span>
          <span className="badge">{layoutName(String(slide.layout), t)}</span>
        </span>
        <span className="muted">
          {t("slides.plan.at")} {clockFine(slide.t0)} – {clockFine(slide.t1)}
        </span>
        {said && (
          <span>
            {t("slides.plan.said")}: „{said.text}“
          </span>
        )}
        {slide.why && (
          <span className="muted">
            {t("plan.why")}: {slide.why}
          </span>
        )}
        {canAsk && (
          <>
            <form className="slidecard-ask" onSubmit={send}>
              <label htmlFor={fid} className="sr-only">
                {t("slides.plan.feedback")}
              </label>
              <input
                id={fid}
                type="text"
                value={text}
                maxLength={500}
                placeholder={t("slides.plan.feedback.placeholder")}
                onChange={(e) => setText(e.target.value)}
              />
              <button type="submit" className="btn btn-small" disabled={!text.trim()}>
                {t("slides.plan.send")}
              </button>
            </form>
            <button
              type="button"
              className="btn btn-small btn-ghost"
              onClick={() => onAsk(t("slides.plan.remove.text", { id: slide.id }), slide.id)}
            >
              {t("slides.plan.remove")}
            </button>
          </>
        )}
      </div>
    </li>
  );
}

/** Slide moments on a strip under the player (output seconds); a click seeks there. */
export function SlideMarks({
  marks,
  duration,
  onSeek,
}: {
  marks: { id: string; t0: number; t1: number }[];
  duration: number;
  onSeek: (t: number) => void;
}) {
  const { t } = useI18n();
  if (!marks.length || !(duration > 0)) return null;
  return (
    <div className="slidemarks" role="group" aria-label={t("slides.marks")} data-testid="slide-marks">
      {marks.map((m) => {
        const left = Math.min(100, (m.t0 / duration) * 100);
        const width = Math.max(0.8, Math.min(100 - left, ((m.t1 - m.t0) / duration) * 100));
        return (
          <button
            key={`${m.id}-${m.t0}`}
            type="button"
            className="slidemark"
            style={{ left: `${left}%`, width: `${width}%` }}
            title={t("slides.mark", { id: m.id, t: clock(m.t0) })}
            aria-label={t("slides.mark", { id: m.id, t: clock(m.t0) })}
            onClick={() => onSeek(m.t0)}
          />
        );
      })}
    </div>
  );
}
