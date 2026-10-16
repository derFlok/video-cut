import { useEffect, useState, type KeyboardEvent } from "react";
import { api, type VocabularyApplied } from "../api/client";
import type { JobVocabulary, VocabEntry, VocabSuggestion } from "../api/types";
import { useI18n } from "../i18n";
import type { Live } from "../screens/JobPage";
import { ErrorBanner } from "./ErrorBanner";

/** Same matching key as the server (tools/vocab.py key): letters and digits, case-folded. */
export function vocabKey(text: string): string {
  return text
    .normalize("NFC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]/gu, "");
}

/** Entries as the server wants them: empty rows dropped, text trimmed. */
export function cleanVocabulary(entries: VocabEntry[]): VocabEntry[] {
  return entries
    .map((e) => ({ ...e, term: e.term.trim(), variants: e.variants.map((v) => v.trim()).filter(Boolean) }))
    .filter((e) => e.term !== "");
}

/** Add a misspelling to a term (a new entry when the term is not there yet). */
export function addVariant(entries: VocabEntry[], term: string, variant: string): VocabEntry[] {
  const k = vocabKey(term);
  const i = entries.findIndex((e) => vocabKey(e.term) === k);
  if (i < 0) return [...entries, { term, variants: [variant] }];
  const e = entries[i];
  if (e.variants.some((v) => vocabKey(v) === vocabKey(variant))) return entries;
  return entries.map((x, j) => (j === i ? { ...x, variants: [...x.variants, variant] } : x));
}

/** Rows of canonical spellings, each with its known misspellings as chips. */
export function VocabEditor({
  id,
  value,
  onChange,
  disabled,
  errors,
}: {
  id: string;
  value: VocabEntry[];
  onChange: (v: VocabEntry[]) => void;
  disabled?: boolean;
  errors?: string[];
}) {
  const { t } = useI18n();
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const update = (i: number, e: VocabEntry) => onChange(value.map((x, j) => (j === i ? e : x)));
  const addChip = (i: number) => {
    const text = (drafts[i] ?? "").trim().replace(/,$/, "").trim();
    if (!text) return;
    const e = value[i];
    if (!e.variants.some((v) => vocabKey(v) === vocabKey(text))) update(i, { ...e, variants: [...e.variants, text] });
    setDrafts((d) => ({ ...d, [i]: "" }));
  };
  const onKey = (i: number, ev: KeyboardEvent<HTMLInputElement>) => {
    if (ev.key === "Enter" || ev.key === ",") {
      ev.preventDefault();
      addChip(i);
    } else if (ev.key === "Backspace" && !(drafts[i] ?? "") && value[i].variants.length) {
      update(i, { ...value[i], variants: value[i].variants.slice(0, -1) });
    }
  };

  return (
    <div className="stack-sm vocab">
      <p className="hint">{t("vocab.explain")}</p>
      {value.length > 0 && (
        <ul className="vocab-rows">
          {value.map((e, i) => (
            <li key={i} className="vocab-row">
              <div className="field">
                <label htmlFor={`${id}-term-${i}`}>{t("vocab.term")}</label>
                <input
                  id={`${id}-term-${i}`}
                  value={e.term}
                  disabled={disabled}
                  placeholder={t("vocab.term.placeholder")}
                  onChange={(x) => update(i, { ...e, term: x.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor={`${id}-var-${i}`}>{t("vocab.variants")}</label>
                <div className="vchips">
                  {e.variants.map((v, k) => (
                    <span key={`${v}-${k}`} className="vchip">
                      {v}
                      {!disabled && (
                        <button
                          type="button"
                          className="vchip-x"
                          aria-label={t("vocab.variant.remove", { v })}
                          onClick={() => update(i, { ...e, variants: e.variants.filter((_, j) => j !== k) })}
                        >
                          ×
                        </button>
                      )}
                    </span>
                  ))}
                  <input
                    id={`${id}-var-${i}`}
                    className="vchip-input"
                    value={drafts[i] ?? ""}
                    disabled={disabled}
                    placeholder={t("vocab.variants.placeholder")}
                    onChange={(x) => setDrafts((d) => ({ ...d, [i]: x.target.value }))}
                    onKeyDown={(x) => onKey(i, x)}
                    onBlur={() => addChip(i)}
                  />
                </div>
              </div>
              {!disabled && (
                <button
                  type="button"
                  className="btn btn-icon"
                  aria-label={t("vocab.remove", { term: e.term || "—" })}
                  onClick={() => onChange(value.filter((_, j) => j !== i))}
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!disabled && (
        <div>
          <button type="button" className="btn btn-small" onClick={() => onChange([...value, { term: "", variants: [] }])}>
            + {t("vocab.add")}
          </button>
        </div>
      )}
      {errors && errors.length > 0 && (
        <ul className="field-error">
          {errors.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** After a vocabulary changed the captions of a finished reel: set them again (the overlay
 * stage runs on the kept cut; no new approval). */
export function OverlaysStaleBanner({ live }: { live: Live }) {
  const { t } = useI18n();
  const job = live.job!;
  const [err, setErr] = useState<unknown>(null);
  // transcript edits: the Transkript section offers its own refresh (no Claude run)
  if (!job.overlays_stale || job.captions_stale || live.running) return null;
  const redo = async () => {
    setErr(null);
    try {
      live.begin("overlays");
      await api.redoOverlays(job.id);
    } catch (e) {
      setErr(e);
      void live.reload();
    }
  };
  return (
    <div className="banner banner-info stack-sm" data-testid="vocab-overlays">
      <p>{t("vocab.overlays.stale")}</p>
      <div>
        <button type="button" className="btn btn-primary" onClick={() => void redo()}>
          {t("vocab.overlays.redo")}
        </button>
      </div>
      <ErrorBanner error={err} />
    </div>
  );
}

/** The job page's "Begriffe für dieses Video": the job's own terms, apply them to the
 * existing transcript, and the suggestions ("Did you mean …?"). */
export function JobVocabularyPanel({ live }: { live: Live }) {
  const { t } = useI18n();
  const job = live.job!;
  const [data, setData] = useState<JobVocabulary | null>(null);
  const [draft, setDraft] = useState<VocabEntry[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const show = (v: JobVocabulary) => {
    setData(v);
    setDraft(v.vocabulary);
  };
  useEffect(() => {
    if (!open) return;
    api.vocabulary(job.id).then(show).catch(setErr);
  }, [open, job.id, live.tick]);

  const act = async (fn: () => Promise<void>) => {
    setErr(null);
    setNote("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const applied = (v: VocabularyApplied) => {
    show(v);
    const n = v.result.applied;
    setNote(v.result.caption_words ? t("vocab.applied.captions", { n, c: v.result.caption_words }) : t("vocab.applied", { n }));
    void live.reload();
  };
  const save = () =>
    act(async () => {
      const v = await api.saveVocabulary(job.id, cleanVocabulary(draft));
      show(v);
      if (v.has_transcript) applied(await api.applyVocabulary(job.id));
      else setNote(t("vocab.saved"));
    });
  const adopt = (s: VocabSuggestion) =>
    act(async () => {
      const inBrand = !!data?.brand && data.brand !== "_example" && data.brand_vocabulary.some((e) => vocabKey(e.term) === vocabKey(s.term));
      if (inBrand && data?.brand) {
        const b = await api.brand(data.brand);
        const list = (b.vocabulary ?? []).map((e) => ({ ...e }));
        const { files: _f, ...tokens } = b;
        void _f;
        await api.saveBrand(data.brand, { ...tokens, vocabulary: addVariant(list, s.term, s.heard) });
      } else {
        await api.saveVocabulary(job.id, addVariant(cleanVocabulary(draft), s.term, s.heard));
      }
      applied(await api.applyVocabulary(job.id));
    });
  const dirty = data !== null && JSON.stringify(cleanVocabulary(draft)) !== JSON.stringify(data.vocabulary);
  const locked = busy || live.running;

  return (
    <details className="details panel" onToggle={(e) => setOpen(e.currentTarget.open)} data-testid="job-vocab">
      <summary>{t("vocab.job.title")}</summary>
      {open && data && (
        <div className="stack-sm">
          {data.effective.length > data.vocabulary.length && data.brand && (
            <p className="hint">{t("vocab.job.brand", { n: data.brand_vocabulary.length })}</p>
          )}
          <VocabEditor id="jv" value={draft} onChange={setDraft} disabled={locked} />
          <div className="actions">
            <button type="button" className="btn btn-primary" disabled={locked || !dirty} onClick={() => void save()}>
              {data.has_transcript ? t("vocab.save.apply") : t("common.save")}
            </button>
            {data.has_transcript && !dirty && (
              <button type="button" className="btn" disabled={locked} onClick={() => void act(async () => applied(await api.applyVocabulary(job.id)))}>
                {t("vocab.apply")}
              </button>
            )}
          </div>
          {data.applied && data.applied.applied > 0 && !note && <p className="muted">{t("vocab.applied", { n: data.applied.applied })}</p>}
          {note && (
            <p className="muted" role="status">
              {note}
            </p>
          )}
          {data.suggestions.length > 0 && (
            <div className="stack-sm" data-testid="vocab-suggestions">
              <h3 className="h3">{t("vocab.suggest.title")}</h3>
              <ul className="vocab-suggest">
                {data.suggestions.map((s) => (
                  <li key={`${s.term}-${s.heard}`}>
                    <span>{t("vocab.suggest.line", { heard: s.heard, term: s.term, n: s.count })}</span>
                    <button type="button" className="btn btn-small" disabled={locked} onClick={() => void adopt(s)}>
                      {t("vocab.suggest.add")}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <ErrorBanner error={err} />
        </div>
      )}
      {open && !data && !err && <p className="muted">{t("common.loading")}</p>}
      {open && !data && <ErrorBanner error={err} />}
    </details>
  );
}
