import { memo, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { api } from "../api/client";
import type { TranscriptView, TranscriptWord } from "../api/types";
import { clock, useI18n } from "../i18n";
import type { Live } from "../screens/JobPage";
import { ErrorBanner } from "./ErrorBanner";
import { addVariant, vocabKey } from "./Vocabulary";

/** Same rule as the server's captions (tools/captions.py SENTENCE_END). */
const SENTENCE_END = /[.!?…]['"»«”“)]*$/;
export const MAX_WORD = 60;

/** Ask the player to show the reel at `t` (output seconds) and play from there. */
export const SEEK_EVENT = "videocut:seek";
export function seekPlayer(t: number) {
  window.dispatchEvent(new CustomEvent(SEEK_EVENT, { detail: { t } }));
}

/** Words grouped into sentences (a sentence ends at . ! ? …), in transcript order. */
export function sentences(words: TranscriptWord[]): TranscriptWord[][] {
  const out: TranscriptWord[][] = [];
  let cur: TranscriptWord[] = [];
  for (const w of words) {
    cur.push(w);
    if (SENTENCE_END.test(w.w)) {
      out.push(cur);
      cur = [];
    }
  }
  if (cur.length) out.push(cur);
  return out;
}

/** The words shown: by default only what the reel keeps (when there is a reel). */
export function visibleWords(view: TranscriptView, all: boolean): TranscriptWord[] {
  return !view.has_reel || all ? view.words : view.words.filter((w) => w.out_t0 !== null);
}

const match = (w: TranscriptWord, q: string) => q !== "" && (w.w.toLowerCase().includes(q) || (w.asr ?? "").toLowerCase().includes(q));

interface SentenceProps {
  words: TranscriptWord[];
  hasReel: boolean;
  query: string;
  /** The word being edited when it is in this sentence, else -1 (keeps other rows memoized). */
  editing: number;
  editor: ReactNode;
}

/** One sentence: its time and its words as small buttons (one click handler per list). */
const Sentence = memo(function Sentence({ words, hasReel, query, editing, editor }: SentenceProps) {
  const { t } = useI18n();
  const first = words[0];
  const at = hasReel && first.out_t0 !== null ? first.out_t0 : first.t0;
  return (
    <p className="ts-sentence">
      <span className="ts-time" title={hasReel && first.out_t0 !== null ? t("transcript.time.reel") : t("transcript.time.source")}>
        {clock(at)}
      </span>{" "}
      {words.map((w) =>
        w.i === editing ? (
          <span key={w.i}>{editor} </span>
        ) : (
          <span key={w.i}>
            <button
              type="button"
              data-i={w.i}
              className={[
                "ts-word",
                w.edited ? "ts-edited" : "",
                w.hidden ? "ts-hidden" : "",
                hasReel && w.out_t0 === null ? "ts-cut" : "",
                match(w, query) ? "ts-hit" : "",
              ].join(" ")}
              title={w.edited ? t("transcript.was", { w: w.asr ?? "" }) : undefined}
            >
              {w.w}
            </button>{" "}
          </span>
        ),
      )}
    </p>
  );
});

/** The job page's "Transkript": read the words, fix single words, undo, search. */
export function TranscriptPanel({ live }: { live: Live }) {
  const { t } = useI18n();
  const job = live.job!;
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<TranscriptView | null>(null);
  const [all, setAll] = useState(false);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<TranscriptWord | null>(null);
  const [draft, setDraft] = useState("");
  const [always, setAlways] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    api.transcript(job.id).then(setView).catch(setErr);
  }, [open, job.id, live.tick]);
  useEffect(() => input.current?.focus(), [editing]);

  const words = useMemo(() => (view ? visibleWords(view, all) : []), [view, all]);
  const q = query.trim().toLowerCase();
  const groups = useMemo(() => {
    const s = sentences(words);
    return q ? s.filter((g) => g.some((w) => match(w, q))) : s;
  }, [words, q]);
  const hits = useMemo(() => (q ? words.filter((w) => match(w, q)).length : 0), [words, q]);

  const start = (ev: MouseEvent<HTMLDivElement>) => {
    const el = (ev.target as HTMLElement).closest<HTMLElement>("[data-i]");
    if (!el || !view || live.running || busy) return;
    const w = view.words.find((x) => x.i === Number(el.dataset.i));
    if (!w) return;
    setEditing(w);
    setDraft(w.hidden ? "" : w.w);
    setAlways(false);
    setNote("");
    if (w.out_t0 !== null) seekPlayer(w.out_t0);
  };

  const act = async (fn: () => Promise<void>) => {
    setErr(null);
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  /** "Immer so schreiben": the pair goes into the brand's Wörterbuch (the job's terms when
   * the job has no brand of its own), then the vocabulary is applied to the transcript. */
  const remember = async (term: string, heard: string) => {
    const bid = job.brand && job.brand !== "_example" ? job.brand : null;
    if (bid) {
      const b = await api.brand(bid);
      const { files: _f, ...tokens } = b;
      void _f;
      await api.saveBrand(bid, { ...tokens, vocabulary: addVariant((b.vocabulary ?? []).map((e) => ({ ...e })), term, heard) });
    } else {
      const v = await api.vocabulary(job.id);
      await api.saveVocabulary(job.id, addVariant(v.vocabulary, term, heard));
    }
    await api.applyVocabulary(job.id);
  };

  const save = () => {
    const w = editing;
    if (!w) return;
    const text = draft.trim().replace(/\s+/g, " ");
    if (text === (w.hidden ? "" : w.w)) {
      setEditing(null);
      return;
    }
    void act(async () => {
      // the edit is saved first; the Wörterbuch is a second step that may fail on its own
      setView(await api.editWord(job.id, w.i, text, w.t0));
      setEditing(null);
      void live.reload();
      const heard = w.asr ?? w.w;
      if (always && text && vocabKey(text) !== vocabKey(heard)) {
        try {
          await remember(text, heard);
          setNote(t("transcript.remembered", { term: text, heard }));
        } finally {
          setView(await api.transcript(job.id));
          void live.reload();
        }
      }
    });
  };

  const onKey = (ev: KeyboardEvent<HTMLInputElement>) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      save();
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      setEditing(null);
    }
  };

  const undo = () =>
    act(async () => {
      setNote("");
      setView(await api.undoWord(job.id));
      setEditing(null);
      void live.reload();
    });

  const editor = editing && (
    <span className="ts-edit">
      <input
        ref={input}
        aria-label={t("transcript.edit.label", { w: editing.asr ?? editing.w })}
        value={draft}
        maxLength={MAX_WORD}
        disabled={busy}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
        size={Math.max(4, draft.length + 1)}
      />
      <label className="ts-always">
        <input type="checkbox" checked={always} disabled={busy} onChange={(e) => setAlways(e.target.checked)} /> {t("transcript.always")}
      </label>
      <button type="button" className="btn btn-small btn-primary" disabled={busy} onClick={save}>
        {t("common.save")}
      </button>
      <button type="button" className="btn btn-small" disabled={busy} onClick={() => setEditing(null)}>
        {t("common.cancel")}
      </button>
    </span>
  );

  return (
    <details className="details panel" onToggle={(e) => setOpen(e.currentTarget.open)} data-testid="job-transcript">
      <summary>{t("transcript.title")}</summary>
      {open && view && (
        <div className="stack-sm">
          <p className="hint">{t("transcript.explain")}</p>
          <div className="ts-tools">
            <input
              type="search"
              className="ts-search"
              placeholder={t("transcript.search")}
              aria-label={t("transcript.search")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {view.has_reel && (
              <label className="ts-all">
                <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> {t("transcript.all")}
              </label>
            )}
            <button type="button" className="btn btn-small" disabled={!view.can_undo || busy || live.running} onClick={() => void undo()}>
              {t("transcript.undo")}
            </button>
          </div>
          <p className="muted" role="status">
            {q ? t("transcript.hits", { n: hits }) : t("transcript.count", { n: words.length, e: view.edits })}
            {note && ` · ${note}`}
          </p>
          {view.drift > 0 && (
            <p className="banner banner-warn" data-testid="transcript-drift">
              {t("transcript.drift", { n: view.drift })}
            </p>
          )}
          <div className="ts-text" onClick={start} data-testid="transcript-text">
            {groups.map((g) => (
              <Sentence
                key={g[0].i}
                words={g}
                hasReel={view.has_reel}
                query={q}
                editing={editing && g.some((w) => w.i === editing.i) ? editing.i : -1}
                editor={editing && g.some((w) => w.i === editing.i) ? editor : null}
              />
            ))}
          </div>
          <ErrorBanner error={err} />
        </div>
      )}
      {open && !view && !err && <p className="muted">{t("common.loading")}</p>}
      {open && !view && <ErrorBanner error={err} />}
    </details>
  );
}

/** After transcript edits: set the captions of the finished reel again, without Claude. */
export function CaptionsRefreshBanner({ live }: { live: Live }) {
  const { t } = useI18n();
  const job = live.job!;
  const [err, setErr] = useState<unknown>(null);
  if (!job.captions_stale || live.running) return null;
  const go = async () => {
    setErr(null);
    try {
      live.begin("overlays");
      await api.refreshCaptions(job.id);
    } catch (e) {
      setErr(e);
      void live.reload();
    }
  };
  return (
    <div className="banner banner-info stack-sm" data-testid="captions-refresh">
      <p>{t("transcript.stale")}</p>
      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={() => void go()}>
          {t("vocab.overlays.redo")}
        </button>
        <span className="muted">{t("transcript.refresh.hint")}</span>
      </div>
      <ErrorBanner error={err} />
    </div>
  );
}
