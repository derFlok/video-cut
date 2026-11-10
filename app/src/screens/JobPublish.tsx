import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Disclosure } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { clock, useI18n, type TKey } from "../i18n";

/** "Veröffentlichen": what to tick on the platforms, the description text with its credits
 * and the AI material in the video (work/disclosure.json from the last full render). */
export function JobPublish({ id, reel }: { id: string; reel: string | null | undefined }) {
  const { t } = useI18n();
  const [doc, setDoc] = useState<Disclosure | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    setDoc(null);
    setCopied(false);
    setErr(null);
    api
      .disclosure(id)
      .then((d) => live && setDoc(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [id, reel]);

  if (!doc?.ready && !err) return null;
  const ai = doc?.ai ?? [];
  const stock = doc?.stock ?? [];
  const sounds = doc?.sounds ?? [];
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(doc?.snippet ?? "");
      setCopied(true);
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <section className="panel stack" aria-labelledby="publish-title">
      <h2 id="publish-title">{t("disclosure.title")}</h2>
      <ErrorBanner error={err} />
      {doc?.ready && !ai.length && !stock.length && <p>{t("disclosure.nothing")}</p>}
      {doc?.ready && sounds.length > 0 && (
        <>
          <h3>{t("disclosure.sounds")}</h3>
          <ul className="stack-sm" data-testid="publish-sounds">
            {sounds.map((s) => (
              <li key={`${s.kind}-${s.id}`}>
                {t(`sfx.kind.${s.kind}` as TKey)}: {s.attribution ?? s.title ?? t(`sfx.origin.${s.origin}` as TKey)}
                {s.licence && <span className="muted"> · {s.licence}</span>}
                {s.origin !== "openverse" && <span className="muted"> · {t(`sfx.origin.${s.origin}` as TKey)}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      {doc?.ready && (ai.length > 0 || stock.length > 0) && (
        <>
          <h3>{t("disclosure.platforms")}</h3>
          <ul className="stack-sm" aria-label={t("disclosure.platforms")}>
            {(doc.platforms ?? []).map((p) => (
              <li key={p.id}>
                <label className="check-row">
                  <input type="checkbox" readOnly checked={p.status === "required"} />
                  <span>
                    {p.label}: <strong>{p.status === "required" ? t("disclosure.required") : t("disclosure.not_needed")}</strong>
                  </span>
                </label>
                <p className="hint">{p.why}</p>
              </li>
            ))}
          </ul>
          {doc.snippet && (
            <div className="field">
              <label htmlFor="publish-snippet">{t("disclosure.snippet")}</label>
              <textarea id="publish-snippet" readOnly rows={Math.min(6, doc.snippet.split("\n").length + 1)} value={doc.snippet} />
              <div className="actions">
                <button type="button" className="btn" onClick={() => void copy()}>
                  {copied ? t("disclosure.copied") : t("disclosure.copy")}
                </button>
              </div>
            </div>
          )}
          {ai.length > 0 && (
            <>
              <h3>{t("disclosure.ai")}</h3>
              <ul className="stack-sm">
                {ai.map((a, i) => (
                  <li key={`${a.n}-${i}`}>
                    {t("disclosure.ai.item", { from: clock(a.t0), to: clock(a.t1), model: a.model ?? "?" })} ·{" "}
                    <span className="muted">{a.labelled ? t("disclosure.ai.labelled") : t("disclosure.ai.unlabelled")}</span>
                    {a.prompt && <p className="hint">{t("disclosure.ai.prompt", { p: a.prompt })}</p>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {stock.length > 0 && (
            <>
              <h3>{t("disclosure.stock")}</h3>
              <ul className="stack-sm">
                {stock.map((s, i) => (
                  <li key={`${s.n}-${i}`}>
                    {s.attribution ?? s.author ?? s.source}
                    {s.licence && <span className="muted"> · {s.licence}</span>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
      {doc?.ready && doc.c2pa && <p className="hint">{t(`disclosure.c2pa.${doc.c2pa}`)}</p>}
    </section>
  );
}
