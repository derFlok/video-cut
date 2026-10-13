import { useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Revision, VerifySummary } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { LiveLog } from "../components/LiveLog";
import { clock, date, money, useI18n, type T } from "../i18n";
import type { Live } from "./JobPage";

const words = (flags: string[]) => flags.map((f) => f.replace(/_/g, " ")).join(", ");

/** The verify summary as plain sentences. */
export function verifyLines(v: VerifySummary, t: T): string[] {
  const out: string[] = [];
  for (const c of v.flagged ?? []) out.push(t("result.verify.cut", { t: clock(c.t), flags: words(c.flags) }));
  for (const o of v.overlay_flagged ?? [])
    out.push(t("result.verify.overlay", { scene: o.scene ?? "", t: clock(o.t0), flags: words(o.flags) }));
  for (const s of v.screen_flagged ?? []) out.push(t("result.verify.screen", { flags: words(s.flags ?? []) }));
  for (const [name, c] of Object.entries(v.checks ?? {})) if (!c.ok) out.push(t("result.verify.check", { name: name.replace(/_/g, " ") }));
  return out;
}

export function Result({ live }: { live: Live }) {
  const { t, lang } = useI18n();
  const job = live.job!;
  const player = useRef<HTMLVideoElement>(null);
  const [text, setText] = useState("");
  const [atHere, setAtHere] = useState(false);
  const [now, setNow] = useState(0);
  const [err, setErr] = useState<unknown>(null);
  const [sending, setSending] = useState(false);
  const [playing, setPlaying] = useState<Revision | null>(null);

  const bust = String(job.revisions.length);
  const current = api.mediaUrl(job.id, "reel", bust);
  const cover = job.outputs?.cover ? api.mediaUrl(job.id, "cover", bust) : undefined;
  const revising = sending || job.stage === "revise" || (live.running && job.approved);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const body = text.trim();
    if (!body) return;
    setErr(null);
    setSending(true);
    const at = atHere && player.current ? Math.round(player.current.currentTime * 10) / 10 : undefined;
    try {
      live.begin("revise");
      await api.feedback(job.id, body, at);
      setText("");
      setAtHere(false);
    } catch (e2) {
      setErr(e2);
      void live.reload();
    } finally {
      setSending(false);
    }
  };

  const lines = job.verify ? verifyLines(job.verify, t) : [];

  return (
    <section className="panel stack" aria-labelledby="result-title">
      <h2 id="result-title">{t("result.title")}</h2>
      <div className={`player player-${(job.format_resolved ?? "9:16").replace(":", "x")}`}>
        <video
          ref={player}
          key={playing?.video_url ?? current}
          src={playing?.video_url ? api.url(playing.video_url) : current}
          poster={cover}
          controls
          preload="metadata"
          playsInline
          onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)}
        />
      </div>
      {playing && (
        <p className="hint">
          {t("result.history")}: „{playing.text}“ ·{" "}
          <button type="button" className="linkbtn" onClick={() => setPlaying(null)}>
            {t("result.history.current")}
          </button>
        </p>
      )}

      <div className="actions">
        <a className="btn btn-primary" href={api.downloadUrl(job.id)} download>
          {t("result.download.mp4")}
        </a>
        {cover && (
          <a className="btn" href={cover} download>
            {t("result.download.cover")}
          </a>
        )}
      </div>

      {job.verify && (
        <div className={`banner ${lines.length ? "banner-warn" : "banner-ok"}`}>
          {lines.length === 0 ? (
            <p>{t("result.verify.ok")}</p>
          ) : (
            <>
              <p>{t("result.verify.bad", { n: lines.length })}</p>
              <ul>
                {lines.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      <form className="askbox" onSubmit={send}>
        <label htmlFor="fb-text">{t("result.feedback")}</label>
        <textarea
          id="fb-text"
          rows={2}
          value={text}
          disabled={revising}
          placeholder={t("result.feedback.placeholder")}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="askbox-row">
          <label className="check">
            <input type="checkbox" checked={atHere} onChange={(e) => setAtHere(e.target.checked)} />
            {t("result.feedback.at", { t: clock(now) })}
          </label>
          <button type="submit" className="btn" disabled={revising || !text.trim()}>
            {t("result.feedback.send")}
          </button>
        </div>
      </form>
      <ErrorBanner error={err} />
      {revising && (
        <div className="stack-sm" role="status">
          <p className="working">{t("result.feedback.running")}</p>
          <LiveLog lines={live.lines} defaultOpen />
        </div>
      )}

      <div className="stack-sm">
        <h3>{t("result.history")}</h3>
        {job.revisions.length === 0 ? (
          <p className="muted">{t("result.history.empty")}</p>
        ) : (
          <ol className="history" reversed>
            {[...job.revisions].reverse().map((r, i) => (
              <li key={`${r.time}-${i}`}>
                <div className="history-text">
                  <p>„{r.text}“</p>
                  <p className="muted">
                    {date(r.time, lang)}
                    {r.at_s !== undefined && r.at_s !== null && ` · ${t("result.history.at", { t: clock(r.at_s) })}`}
                    {r.cost !== undefined && r.cost !== null && ` · ${money(r.cost, lang)}`}
                  </p>
                </div>
                {r.video_url && (
                  <button type="button" className="btn btn-small" onClick={() => setPlaying(r)}>
                    {t("result.history.play")}
                  </button>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
