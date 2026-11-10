import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { SFX_LEVELS, type SfxKind, type SfxLevel, type SfxSettings } from "../api/types";
import { useI18n, type TKey } from "../i18n";
import type { Live } from "../screens/JobPage";
import { ErrorBanner } from "./ErrorBanner";

type Choice = SfxLevel | "default";

/** The job's sound-effect level (off | subtle | normal | as in settings). A finished video
 *  mixed at another level offers "cut again" (the render mixes work/sfx.wav anew). */
export function SfxPanel({ live }: { live: Live }) {
  const { t } = useI18n();
  const job = live.job!;
  const [err, setErr] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);
  const [deflt, setDeflt] = useState<SfxLevel | null>(null);
  useEffect(() => {
    api
      .sfxSettings()
      .then((s) => setDeflt(s.sfx_default))
      .catch(() => setDeflt(null));
  }, []);
  const value: Choice = job.sfx ?? "default";
  const level = (l: SfxLevel) => t(`sfx.${l}` as TKey);
  const shown: SfxLevel | null = job.sfx ?? deflt ?? job.sfx_resolved ?? null;

  const change = async (v: Choice) => {
    setErr(null);
    setSaving(true);
    try {
      const r = await api.setJobSfx(job.id, v);
      setStale(r.cut_stale);
      await live.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setSaving(false);
    }
  };
  const recut = async () => {
    setErr(null);
    try {
      live.begin("cut");
      await api.recut(job.id);
      setStale(false);
    } catch (e) {
      setErr(e);
      void live.reload();
    }
  };

  return (
    <section className="panel stack-sm" aria-labelledby="sfx-title" data-testid="job-sfx">
      <label id="sfx-title" htmlFor="sfx-level" className="h3">
        {t("sfx.label")}
      </label>
      <select
        id="sfx-level"
        value={value}
        disabled={saving || live.running}
        onChange={(e) => void change(e.target.value as Choice)}
      >
        <option value="default">{t("sfx.default", { level: deflt ? level(deflt) : "…" })}</option>
        {SFX_LEVELS.map((l) => (
          <option key={l} value={l}>
            {level(l)}
          </option>
        ))}
      </select>
      {shown && (
        <p className="hint" data-testid="sfx-desc">
          {t(`sfx.${shown}.desc` as TKey)}
        </p>
      )}
      {stale && !live.running && (
        <div className="banner banner-info" data-testid="sfx-stale">
          <p>{t("sfx.stale")}</p>
          <button type="button" className="btn btn-primary" onClick={() => void recut()}>
            {t("style.recut")}
          </button>
        </div>
      )}
      <ErrorBanner error={err} />
    </section>
  );
}

/** Settings → Sound effects: the default level, per kind which sound is used (own upload,
 *  Openverse CC0, generated locally), upload / play / remove, and a one-off Openverse fetch. */
export function SfxSettingsPanel() {
  const { t } = useI18n();
  const [s, setS] = useState<SfxSettings | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [fetching, setFetching] = useState(false);
  useEffect(() => {
    api.sfxSettings().then(setS).catch(setErr);
  }, []);
  if (!s) return err ? <ErrorBanner error={err} /> : null;

  const act = (p: Promise<SfxSettings>) => {
    setErr(null);
    return p.then(setS).catch(setErr);
  };
  const fetchAll = () => {
    setFetching(true);
    void act(api.fetchSfx()).finally(() => setFetching(false));
  };

  return (
    <section className="panel stack" aria-labelledby="sfx-settings-title" data-testid="sfx-settings">
      <h2 id="sfx-settings-title">{t("sfx.settings.title")}</h2>
      <p className="hint">{t("sfx.settings.intro")}</p>
      <div className="field">
        <label htmlFor="sfx-default">{t("sfx.settings.default")}</label>
        <select
          id="sfx-default"
          value={s.sfx_default}
          onChange={(e) => void act(api.saveSfxDefault(e.target.value as SfxLevel))}
        >
          {SFX_LEVELS.map((l) => (
            <option key={l} value={l}>
              {t(`sfx.${l}` as TKey)}
            </option>
          ))}
        </select>
      </div>
      <ul className="stack-sm sfx-kinds">
        {(Object.keys(s.kinds) as SfxKind[]).map((k) => (
          <SfxKindRow key={k} kind={k} s={s} act={act} />
        ))}
      </ul>
      <p className="hint">{t("sfx.settings.upload.hint")}</p>
      <div className="stack-sm">
        <div className="actions">
          <button type="button" className="btn" disabled={fetching} onClick={fetchAll}>
            {fetching ? t("sfx.settings.fetching") : t("sfx.settings.fetch")}
          </button>
        </div>
        <p className="hint">{t("sfx.settings.fetch.hint")}</p>
      </div>
      <ErrorBanner error={err} />
    </section>
  );
}

function SfxKindRow({ kind, s, act }: { kind: SfxKind; s: SfxSettings; act: (p: Promise<SfxSettings>) => Promise<unknown> }) {
  const { t } = useI18n();
  const file = useRef<HTMLInputElement>(null);
  const k = s.kinds[kind];
  const fid = `sfx-upload-${kind}`;
  return (
    <li className="stack-sm" data-sfx-kind={kind}>
      <div className="meta-row">
        <strong>{t(`sfx.kind.${kind}` as TKey)}</strong>
        <span className="muted">{t("sfx.settings.uses", { origin: t(`sfx.origin.${k.uses}` as TKey) })}</span>
      </div>
      {k.sounds.length > 0 && (
        <ul className="stack-sm">
          {k.sounds.map((snd) => (
            <li key={snd.id} className="meta-row" data-sfx-sound={snd.id}>
              <span>
                {snd.title}
                {snd.attribution ? <span className="muted"> · {snd.attribution}</span> : null}
              </span>
              <audio controls preload="none" src={api.sfxAudioUrl(kind, snd.id)} aria-label={`${t("sfx.settings.play")}: ${snd.title}`} />
              <button
                type="button"
                className="btn btn-small btn-ghost"
                onClick={() => void act(api.deleteSfx(kind, snd.id))}
              >
                {t("sfx.settings.remove")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="field">
        <label htmlFor={fid} className="hint">
          {t("sfx.settings.upload")}
        </label>
        <input
          id={fid}
          ref={file}
          type="file"
          accept=".wav,.mp3,.m4a,.flac,.ogg,.aif,.aiff,audio/*"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void act(api.uploadSfx(kind, f)).finally(() => file.current && (file.current.value = ""));
          }}
        />
      </div>
    </li>
  );
}
