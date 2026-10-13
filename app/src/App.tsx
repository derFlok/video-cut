import { useEffect, useState } from "react";
import { api } from "./api/client";
import type { Session } from "./api/types";
import { useI18n } from "./i18n";
import { href, useRoute } from "./router";
import { BrandEditor, BrandList } from "./screens/Brands";
import { Home } from "./screens/Home";
import { JobPage } from "./screens/JobPage";
import { NewJob } from "./screens/NewJob";

export function App() {
  const { t, lang, setLang } = useI18n();
  const route = useRoute();
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    api.session().then(setSession).catch(() => setSession(null));
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const section = route.name === "brands" || route.name === "brand" ? "brands" : "jobs";
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
      </header>
      {session && !session.claude.ok && (
        <p className="banner banner-warn topnote" role="alert">
          {t("claude.missing")}
        </p>
      )}
      <main id="main" className="main" tabIndex={-1}>
        {route.name === "home" && <Home />}
        {route.name === "new" && <NewJob />}
        {route.name === "job" && <JobPage key={route.id} id={route.id} />}
        {route.name === "brands" && <BrandList />}
        {route.name === "brand" && <BrandEditor key={route.id} id={route.id} />}
      </main>
      {session && <footer className="footer muted">{t("app.name")} {session.version}</footer>}
    </>
  );
}
