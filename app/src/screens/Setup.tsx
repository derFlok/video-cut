// First start and settings: how Video Cut connects to Claude. The server asks the official
// claude program in the background (GET /api/setup/status); this screen only shows what
// it said. An API key is sent once to the server, which keeps it in the macOS keychain.

import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { AuthMode, BrandSummary, SetupStatus, SetupTest } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { useI18n } from "../i18n";
import { href } from "../router";
import { useTheme } from "../theme";
import { ThemeSwitch } from "./Brands";

const KEY_RE = /^sk-ant-[A-Za-z0-9_-]{20,300}$/;
const POLL_MS = 800;

/** The status, polled while the background check of the claude program runs. */
export function useSetupStatus(): [SetupStatus | null, unknown, (refresh?: boolean) => void] {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  const [refresh, setRefresh] = useState(false);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (fresh: boolean) =>
      api
        .setupStatus(fresh)
        .then((s) => {
          if (!live) return;
          setStatus(s);
          setErr(null);
          if (s.claude.state === "checking") timer = setTimeout(() => load(false), POLL_MS);
        })
        .catch((e) => live && setErr(e));
    void load(refresh);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [tick, refresh]);

  return [status, err, (fresh = false) => (setRefresh(fresh), setTick((n) => n + 1))];
}

function Detection({ status, onRecheck }: { status: SetupStatus; onRecheck: () => void }) {
  const { t } = useI18n();
  const c = status.claude;
  if (c.state === "checking" && c.installed === undefined)
    return (
      <p className="muted" role="status">
        {t("setup.checking")}
      </p>
    );
  if (c.signed_in)
    return (
      <div className="banner banner-ok" role="status">
        <strong>{t("setup.detected")}</strong>
        {c.account && <span>{t("setup.detected.account", { account: c.account })}</span>}
        {c.subscription && <span>{t("setup.detected.plan", { plan: c.subscription })}</span>}
      </div>
    );
  return (
    <div className="banner banner-info" role="status">
      <p>{c.error ? t("setup.detectError", { msg: c.error }) : c.installed ? t("setup.notSignedIn") : t("setup.notInstalled")}</p>
      <p>{t("setup.twoWays")}</p>
      <button type="button" className="btn btn-small" onClick={onRecheck} disabled={c.state === "checking"}>
        {c.state === "checking" ? t("setup.checking") : t("setup.recheck")}
      </button>
    </div>
  );
}

/** Choose local/API key, store the key, test the connection. Used by the first-start screen
 *  and by the settings page. */
export function ConnectClaude({ status, onSaved, onRecheck }: { status: SetupStatus; onSaved: (s: SetupStatus) => void; onRecheck: () => void }) {
  const { t } = useI18n();
  const signedIn = !!status.claude.signed_in;
  const [mode, setMode] = useState<AuthMode>(status.done ? status.mode : signedIn ? "local" : "apikey");
  const [key, setKey] = useState("");
  const [keyErr, setKeyErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<SetupTest | null>(null);
  const [howOpen, setHowOpen] = useState(false);
  const [touched, setTouched] = useState(false);
  const choose = (m: AuthMode) => (setTouched(true), setMode(m));

  // The detection finished after the first render: suggest the subscription it found,
  // unless the person already chose.
  useEffect(() => {
    if (!status.done && signedIn && !touched) setMode("local");
  }, [status.done, signedIn, touched]);

  const save = async () => {
    setKeyErr(null);
    setErr(null);
    setTest(null);
    const k = key.trim();
    if (mode === "apikey") {
      if (!k && !status.api_key_stored) return setKeyErr(t("setup.err.nokey"));
      if (k && !KEY_RE.test(k)) return setKeyErr(t("setup.err.key"));
    }
    setSaving(true);
    try {
      const s = await api.setupAuth(mode, mode === "apikey" && k ? k : undefined);
      setKey("");
      setSaved(true);
      onSaved(s);
    } catch (e) {
      if (e instanceof ApiError && e.code === "bad_key") setKeyErr(t("setup.err.key"));
      else setErr(e);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api.setupTest());
    } catch (e) {
      setErr(e);
    } finally {
      setTesting(false);
    }
  };

  const localDisabled = !signedIn && !(status.done && status.mode === "local");
  return (
    <div className="stack">
      <Detection status={status} onRecheck={onRecheck} />
      <fieldset className="stack">
        <legend className="label">{t("setup.choice")}</legend>
        <label className={`card ${mode === "local" ? "card-selected" : ""}`}>
          <span className="check">
            <input
              type="radio"
              name="auth-mode"
              value="local"
              checked={mode === "local"}
              onChange={() => choose("local")}
              onClick={() => setTouched(true)}
              aria-labelledby="mode-local"
              aria-describedby="mode-local-desc"
            />
            <span className="card-title" id="mode-local">{t("setup.local")}</span>
          </span>
          <span className="card-desc" id="mode-local-desc">{t("setup.local.desc")}</span>
          <span className="card-desc">{t("setup.terms")}</span>
          {localDisabled && <span className="hint">{t("setup.local.unavailable")}</span>}
        </label>
        {!signedIn && (
          <div className="stack-sm">
            <button type="button" className="linkbtn" aria-expanded={howOpen} onClick={() => setHowOpen(!howOpen)}>
              {t("setup.signin.how")}
            </button>
            {howOpen && <p className="hint">{t("setup.signin.steps")}</p>}
          </div>
        )}
        <label className={`card ${mode === "apikey" ? "card-selected" : ""}`}>
          <span className="check">
            <input
              type="radio"
              name="auth-mode"
              value="apikey"
              checked={mode === "apikey"}
              onChange={() => choose("apikey")}
              onClick={() => setTouched(true)}
              aria-labelledby="mode-apikey"
              aria-describedby="mode-apikey-desc"
            />
            <span className="card-title" id="mode-apikey">{t("setup.apikey")}</span>
          </span>
          <span className="card-desc" id="mode-apikey-desc">{t("setup.apikey.desc")}</span>
        </label>
        {mode === "apikey" && (
          <div className="field">
            <label htmlFor="api-key">{t("setup.key.label")}</label>
            <input
              id="api-key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="sk-ant-…"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              aria-invalid={!!keyErr}
              aria-describedby="api-key-hint"
            />
            <span id="api-key-hint" className="hint">
              {status.api_key_stored && t("setup.key.stored")}{" "}
              <a href={status.console_url} target="_blank" rel="noreferrer noopener">
                {t("setup.key.get")}
              </a>
            </span>
            {keyErr && (
              <span className="field-error" role="alert">
                {keyErr}
              </span>
            )}
          </div>
        )}
      </fieldset>
      <ErrorBanner error={err} />
      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={save} disabled={saving || (mode === "local" && localDisabled)}>
          {saving ? t("setup.saving") : t("setup.save")}
        </button>
        <button type="button" className="btn" onClick={runTest} disabled={testing || !(status.done || saved)}>
          {testing ? t("setup.testing") : t("setup.test")}
        </button>
        {saved && !test && (
          <span className="muted" role="status">
            {t("setup.saved")}
          </span>
        )}
      </div>
      {test && (
        <p className={`banner ${test.ok ? "banner-ok" : "banner-error"}`} role="status">
          {test.ok ? t("setup.test.ok", { s: test.seconds ?? "?" }) : t("setup.test.fail", { msg: test.message || "?" })}
        </p>
      )}
    </div>
  );
}

export function FirstVideo({ onLater }: { onLater: () => void }) {
  const { t } = useI18n();
  return (
    <section className="panel stack" aria-labelledby="first-title">
      <h2 id="first-title">{t("first.title")}</h2>
      <ul className="plainlist">
        <li>{t("first.tip.format")}</li>
        <li>{t("first.tip.sound")}</li>
        <li>{t("first.tip.gate")}</li>
      </ul>
      <div className="actions">
        <a className="btn btn-primary" href={href({ name: "new" })} onClick={onLater}>
          {t("first.start")}
        </a>
        <button type="button" className="btn btn-ghost" onClick={onLater}>
          {t("first.later")}
        </button>
      </div>
    </section>
  );
}

/** Shown instead of the app until the connection is chosen once. */
export function Welcome({ onSaved, onDone }: { onSaved: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const [status, err, reload] = useSetupStatus();
  const [savedOnce, setSavedOnce] = useState(false);
  return (
    <div className="stack-lg setup">
      <h1>{t("setup.title")}</h1>
      <p>{t("setup.intro")}</p>
      <ErrorBanner error={err} onRetry={() => reload()} />
      {!status && !err && <p className="muted">{t("common.loading")}</p>}
      {status && (
        <section className="panel">
          <ConnectClaude
            status={status}
            onSaved={() => {
              setSavedOnce(true);
              onSaved();
            }}
            onRecheck={() => reload(true)}
          />
        </section>
      )}
      {savedOnce && <FirstVideo onLater={onDone} />}
    </div>
  );
}

export function Settings({ onSaved }: { onSaved?: () => void }) {
  const { t } = useI18n();
  const [status, err, reload] = useSetupStatus();
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    api.session().then((s) => setVersion(s.version)).catch(() => undefined);
  }, []);
  return (
    <div className="stack-lg setup">
      <h1>{t("settings.title")}</h1>
      <ErrorBanner error={err} onRetry={() => reload()} />
      {!status && !err && <p className="muted">{t("common.loading")}</p>}
      {status && (
        <section className="panel stack" aria-labelledby="conn-title">
          <h2 id="conn-title">{t("settings.connection")}</h2>
          {status.done && <p className="muted">{status.mode === "apikey" ? t("settings.mode.apikey") : t("settings.mode.local")}</p>}
          <ConnectClaude
            status={status}
            onSaved={() => {
              reload();
              onSaved?.();
            }}
            onRecheck={() => reload(true)}
          />
        </section>
      )}
      <BrandLook />
      {version && <p className="muted">{t("settings.version", { v: version })}</p>}
    </div>
  );
}

/** Settings: the default brand (new videos preselect it) and whether the app follows it. */
function BrandLook() {
  const { t } = useI18n();
  const { settings, saveSettings } = useTheme();
  const [brands, setBrands] = useState<BrandSummary[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    api.brands().then(setBrands).catch(setErr);
  }, []);
  if (!settings) return null;
  return (
    <section className="panel stack" aria-labelledby="look-title">
      <h2 id="look-title">{t("settings.look")}</h2>
      <div className="field">
        <label htmlFor="default-brand">{t("brands.default.title")}</label>
        <select
          id="default-brand"
          value={settings.default_brand ?? ""}
          onChange={(e) => {
            setErr(null);
            saveSettings({ default_brand: e.target.value || null }).catch(setErr);
          }}
        >
          <option value="">{t("new.brand.none")}</option>
          {(brands ?? []).map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </div>
      <ThemeSwitch />
      <ErrorBanner error={err} />
    </section>
  );
}

/** The "Beenden" button: stop the server (asks first while a run is active). */
export async function quitApp(t: (k: "quit.busy") => string, confirm: (msg: string) => boolean = window.confirm): Promise<boolean> {
  try {
    await api.shutdown(false);
    return true;
  } catch (e) {
    if (e instanceof ApiError && e.code === "busy") {
      if (!confirm(t("quit.busy"))) return false;
      await api.shutdown(true);
      return true;
    }
    throw e;
  }
}

export function Stopped() {
  const { t } = useI18n();
  return (
    <div className="stack setup" role="status">
      <h1>{t("quit.done.title")}</h1>
      <p>{t("quit.done.text")}</p>
    </div>
  );
}
