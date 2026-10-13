import { useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { JobDetail, Revision, VerifySummary, Version } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { LiveLog } from "../components/LiveLog";
import { clock, date, money, STRINGS, useI18n, type T, type TKey } from "../i18n";
import type { Live } from "./JobPage";

/** A known id ("blink_at_cut") in plain words; an unknown one as it is, readable. */
function known(t: T, prefix: "flag" | "check", id: string): string {
  const key = `${prefix}.${id}`;
  return key in STRINGS.de ? t(key as TKey) : id.replace(/_/g, " ");
}

const words = (t: T, flags: string[]) => flags.map((f) => known(t, "flag", f)).join(", ");

/** The verify summary as plain sentences. */
export function verifyLines(v: VerifySummary, t: T): string[] {
  const out: string[] = [];
  for (const c of v.flagged ?? []) out.push(t("result.verify.cut", { t: clock(c.t), flags: words(t, c.flags) }));
  for (const o of v.overlay_flagged ?? [])
    out.push(t("result.verify.overlay", { scene: o.scene ?? "", t: clock(o.t0), flags: words(t, o.flags) }));
  for (const s of v.screen_flagged ?? []) out.push(t("result.verify.screen", { flags: words(t, s.flags ?? []) }));
  for (const [name, c] of Object.entries(v.checks ?? {}))
    if (!c.ok) out.push(t("result.verify.check", { name: known(t, "check", name) }));
  return out;
}

/** The playable versions, newest first: from the server, or (an older server) version 1
 * plus one per revision that carries a video. */
export function versionsOf(job: JobDetail): Version[] {
  if (job.versions?.length) return job.versions;
  const good = job.revisions.filter((r) => r.ok !== false && !r.error);
  const out: Version[] = [{ version: 1, revision: null, text: null, video_url: null, current: good.length === 0 }];
  good.forEach((r, i) =>
    out.push({ version: i + 2, revision: r.n ?? null, text: r.text, video_url: r.video_url ?? null, current: i === good.length - 1 }),
  );
  return out.reverse();
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
  const [playing, setPlaying] = useState<Version | null>(null);

  const bust = String(job.revisions.length);
  const versions = versionsOf(job);
  const byVersion = new Map<number, Revision>();
  for (const r of job.revisions) if (r.version) byVersion.set(r.version, r);
  const failed = job.revisions.filter((r) => r.ok === false || r.error);
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
          data-version={playing?.version ?? versions[0]?.version ?? 1}
          poster={cover}
          controls
          preload="metadata"
          playsInline
          onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)}
        />
      </div>
      {playing && (
        <p className="hint" role="status">
          {t("result.version.playing", { n: playing.version })} ·{" "}
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
        {versions.length <= 1 && failed.length === 0 && <p className="muted">{t("result.history.empty")}</p>}
        <ol className="history" reversed>
          {versions.map((v) => {
            const r = byVersion.get(v.version);
            const isPlaying = playing ? playing.version === v.version : v.current;
            return (
              <li key={v.version} aria-current={isPlaying ? "true" : undefined}>
                <div className="history-text">
                  <p>
                    <strong>{v.version === 1 ? t("result.version.first") : t("result.version", { n: v.version })}</strong>
                    {v.current && ` · ${t("result.version.current")}`}
                  </p>
                  {v.text && <p>„{v.text}“</p>}
                  {r && (
                    <p className="muted">
                      {date(r.time, lang)}
                      {r.at_s !== undefined && r.at_s !== null && ` · ${t("result.history.at", { t: clock(r.at_s) })}`}
                      {r.cost !== undefined && r.cost !== null && ` · ${money(r.cost, lang)}`}
                    </p>
                  )}
                  {r?.summary && <p className="muted">{r.summary}</p>}
                  {!v.video_url && <p className="muted">{t("result.version.gone")}</p>}
                </div>
                {v.video_url && !isPlaying && (
                  <button
                    type="button"
                    className="btn btn-small"
                    aria-label={`${t("result.history.play")}: ${t("result.version", { n: v.version })}`}
                    onClick={() => setPlaying(v.current ? null : v)}
                  >
                    {t("result.history.play")}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
        {failed.length > 0 && (
          <ul className="plainlist">
            {failed.map((r, i) => (
              <li key={`${r.time}-${i}`} className="muted">
                {t("result.revision.failed")}: „{r.text}“ · {date(r.time, lang)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
