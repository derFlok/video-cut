import { useEffect, useState } from "react";
import { api } from "./api/client";
import type { Session } from "./api/types";
import { ErrorBanner } from "./components/ErrorBanner";
import { useI18n } from "./i18n";
import { href, useRoute } from "./router";
import { BrandEditor, BrandList } from "./screens/Brands";
import { Home } from "./screens/Home";
import { JobPage } from "./screens/JobPage";
import { NewJob } from "./screens/NewJob";
import { Settings, Stopped, Welcome, quitApp } from "./screens/Setup";

export function App() {
  const { t, lang, setLang } = useI18n();
  const route = useRoute();
  const [session, setSession] = useState<Session | null>(null);
  // null: not known yet; the first-start screen shows until the connection is chosen once
  const [setupDone, setSetupDone] = useState<boolean | null>(null);
  const [stopped, setStopped] = useState(false);
  const [quitErr, setQuitErr] = useState<unknown>(null);

  const loadSession = () => api.session().then(setSession).catch(() => setSession(null));
  useEffect(() => {
    void loadSession();
    api
      .setupStatus()
      .then((s) => setSetupDone(s.done))
      .catch(() => setSetupDone(true)); // an older server without the screen: go on
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const quit = async () => {
    setQuitErr(null);
    try {
      if (await quitApp(t)) setStopped(true);
    } catch (e) {
      setQuitErr(e);
    }
  };

  if (stopped)
    return (
      <main id="main" className="main" tabIndex={-1}>
        <Stopped />
      </main>
    );

  const firstStart = setupDone === false && route.name !== "settings";
  const showApp = setupDone !== null && !firstStart; // no flash of the job list before the check
  const section =
    route.name === "brands" || route.name === "brand" ? "brands" : route.name === "settings" ? "settings" : "jobs";
  return (
    <>
      <a
        className="skip"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        {t("nav.skip")}
      </a>
      <header className="topbar">
        <a className="product" href={href({ name: "home" })}>
          {t("app.name")}
        </a>
        <nav aria-label={t("app.name")}>
          <a href={href({ name: "home" })} aria-current={section === "jobs" ? "page" : undefined}>
            {t("nav.jobs")}
          </a>
          <a href={href({ name: "brands" })} aria-current={section === "brands" ? "page" : undefined}>
            {t("nav.brands")}
          </a>
          <a href={href({ name: "settings" })} aria-current={section === "settings" ? "page" : undefined}>
            {t("nav.settings")}
          </a>
        </nav>
        <button
          type="button"
          className="btn btn-ghost btn-small"
          aria-label={t("nav.lang.label")}
          lang={lang === "de" ? "en" : "de"}
          onClick={() => setLang(lang === "de" ? "en" : "de")}
        >
          {t("nav.lang")}
        </button>
        <button type="button" className="btn btn-ghost btn-small" onClick={quit}>
          {t("nav.quit")}
        </button>
      </header>
      {quitErr !== null && (
        <div className="topnote">
          <ErrorBanner error={quitErr} />
        </div>
      )}
      {session && !session.claude.ok && setupDone && route.name !== "settings" && (
        <p className="banner banner-warn topnote" role="alert">
          <a href={href({ name: "settings" })}>{t("claude.missing")}</a>
        </p>
      )}
      <main id="main" className="main" tabIndex={-1}>
        {firstStart && (
          <Welcome
            onDone={() => {
              setSetupDone(true);
              void loadSession();
            }}
          />
        )}
        {showApp && route.name === "settings" && <Settings />}
        {showApp && route.name === "home" && <Home />}
        {showApp && route.name === "new" && <NewJob />}
        {showApp && route.name === "job" && <JobPage key={route.id} id={route.id} />}
        {showApp && route.name === "brands" && <BrandList />}
        {showApp && route.name === "brand" && <BrandEditor key={route.id} id={route.id} />}
      </main>
      {session && <footer className="footer muted">{t("app.name")} {session.version}</footer>}
    </>
  );
}
