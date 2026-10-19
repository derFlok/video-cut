// Material: stock footage (B-roll) and AI-generated stills. The new-job field, the settings
// section (defaults, generator, keys), the plan's wishes and the screen between plan approval
// and cut where the human picks a candidate per wish (or none).

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import {
  IMAGE_PROVIDERS,
  MATERIAL_MODES,
  MATERIAL_WISH_MAX,
  SECRET_ACCOUNTS,
  STOCK_PROVIDERS,
  type ImageProviderName,
  type JobDetail,
  type MaterialCandidate,
  type MaterialMode,
  type MaterialSettings,
  type MaterialView,
  type MaterialWish,
  type SecretAccount,
  type Stage,
} from "../api/types";
import { clockFine, money, useI18n, type T, type TKey } from "../i18n";
import { ErrorBanner } from "./ErrorBanner";

export const usesStock = (m: MaterialMode) => m === "stock" || m === "both";
export const usesGenerator = (m: MaterialMode) => m === "generated" || m === "both";

/** Problems of the material part of the new-job form (empty: fine). */
export function materialErrors(wish: string, t: T): string[] {
  return wish.length > MATERIAL_WISH_MAX ? [t("material.err.wish", { n: MATERIAL_WISH_MAX })] : [];
}

/** The mode the new-job form starts with: the setting, else none (an older server). */
export function useMaterialDefault(): MaterialMode | null {
  const [mode, setMode] = useState<MaterialMode | null>(null);
  useEffect(() => {
    api
      .materialSettings()
      .then((s) => setMode(MATERIAL_MODES.includes(s.material_default_mode) ? s.material_default_mode : "none"))
      .catch(() => setMode("none"));
  }, []);
  return mode;
}

/** "Material" in the new-job form: none, stock, AI images, both, plus a free-text wish. */
export function MaterialPicker({
  mode,
  onMode,
  wish,
  onWish,
  disabled,
}: {
  mode: MaterialMode;
  onMode: (m: MaterialMode) => void;
  wish: string;
  onWish: (w: string) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  return (
    <fieldset className="stack" data-testid="new-material">
      <legend className="h2">{t("material.title")}</legend>
      <p className="hint">{t("material.hint")}</p>
      <div className="segmented segmented-wrap" role="radiogroup" aria-label={t("material.title")}>
        {MATERIAL_MODES.map((m) => (
          <label key={m} className={`seg${mode === m ? " seg-on" : ""}`}>
            <input
              type="radio"
              name="material-mode"
              className="sr-only"
              value={m}
              checked={mode === m}
              disabled={disabled}
              onChange={() => onMode(m)}
            />
            <span>{t(`material.mode.${m}` as TKey)}</span>
          </label>
        ))}
      </div>
      {mode !== "none" && <p className="hint">{t(`material.mode.${mode}.hint` as TKey)}</p>}
      {mode !== "none" && (
        <div className="field">
          <label htmlFor="material-wish">{t("material.wish")}</label>
          <textarea
            id="material-wish"
            rows={2}
            value={wish}
            maxLength={MATERIAL_WISH_MAX}
            disabled={disabled}
            placeholder={t("material.wish.placeholder")}
            onChange={(e) => onWish(e.target.value)}
          />
        </div>
      )}
    </fieldset>
  );
}

// ---------------------------------------------------------------- settings

const ACCOUNT_LABEL: Record<SecretAccount, string> = {
  "pexels-api-key": "Pexels",
  "coverr-api-key": "Coverr",
  "gemini-api-key": "Gemini",
  "openai-api-key": "OpenAI",
};

/** Settings → Material: the default mode, the image generator, the stock libraries, the cost
 *  limit and the providers' keys (write-only: a stored key is never shown). */
export function MaterialSettingsPanel() {
  const { t } = useI18n();
  const [s, setS] = useState<MaterialSettings | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [budget, setBudget] = useState("");
  useEffect(() => {
    api
      .materialSettings()
      .then((v) => {
        setS(v);
        setBudget(String(v.material_budget_usd));
      })
      .catch(setErr);
  }, []);
  if (!s) return err ? <ErrorBanner error={err} /> : null;

  const save = (changes: Parameters<typeof api.saveMaterialSettings>[0]) => {
    setErr(null);
    api
      .saveMaterialSettings(changes)
      .then((v) => {
        setS(v);
        setBudget(String(v.material_budget_usd));
      })
      .catch(setErr);
  };
  const providerInfo = (name: ImageProviderName) => s.image_providers.find((p) => p.name === name);
  const shown = IMAGE_PROVIDERS.filter((p) => p === "none" || p !== "local" || providerInfo(p)?.registered);
  const current = providerInfo(s.image_provider);

  return (
    <section className="panel stack" aria-labelledby="material-settings-title" data-testid="material-settings">
      <h2 id="material-settings-title">{t("material.settings.title")}</h2>
      <p className="hint">{t("material.settings.hint")}</p>
      <div className="grid-2">
        <div className="field">
          <label htmlFor="material-default">{t("material.settings.default")}</label>
          <select
            id="material-default"
            value={s.material_default_mode}
            onChange={(e) => save({ material_default_mode: e.target.value as MaterialMode })}
          >
            {MATERIAL_MODES.map((m) => (
              <option key={m} value={m}>
                {t(`material.mode.${m}` as TKey)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="material-provider">{t("material.settings.provider")}</label>
          <select
            id="material-provider"
            value={s.image_provider}
            onChange={(e) => save({ image_provider: e.target.value as ImageProviderName })}
          >
            {shown.map((p) => {
              const info = providerInfo(p);
              const off = p !== "none" && info !== undefined && !info.available && p !== s.image_provider;
              return (
                <option key={p} value={p} disabled={off}>
                  {t(`material.provider.${p}` as TKey)}
                  {info && !info.available ? ` – ${info.reason || t("material.provider.unavailable")}` : ""}
                </option>
              );
            })}
          </select>
          {current && !current.available && (
            <p className="hint" data-testid="provider-reason">
              {t("material.provider.notready", { reason: current.reason || t("material.provider.unavailable") })}
            </p>
          )}
        </div>
      </div>
      <fieldset className="stack-sm">
        <legend className="label">{t("material.settings.stock")}</legend>
        <div className="row-wrap">
          {STOCK_PROVIDERS.map((p) => (
            <label key={p} className="check">
              <input
                type="checkbox"
                checked={s.stock_providers.includes(p)}
                onChange={(e) =>
                  save({
                    stock_providers: e.target.checked
                      ? STOCK_PROVIDERS.filter((x) => x === p || s.stock_providers.includes(x))
                      : s.stock_providers.filter((x) => x !== p),
                  })
                }
              />
              <span>{ACCOUNT_LABEL[`${p}-api-key` as SecretAccount]}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="field">
        <label htmlFor="material-budget">{t("material.settings.budget")}</label>
        <input
          id="material-budget"
          type="number"
          min={0}
          step={0.1}
          inputMode="decimal"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          onBlur={() => {
            const v = Number(budget.replace(",", "."));
            if (Number.isFinite(v) && v >= 0 && v !== s.material_budget_usd) save({ material_budget_usd: v });
            else setBudget(String(s.material_budget_usd));
          }}
        />
        <p className="hint">{t("material.settings.budget.hint")}</p>
      </div>
      <div className="stack-sm">
        <h3>{t("material.keys.title")}</h3>
        <p className="hint">{t("material.keys.hint")}</p>
        {SECRET_ACCOUNTS.map((a) => (
          <SecretField
            key={a}
            account={a}
            label={ACCOUNT_LABEL[a]}
            stored={!!s.secrets[a]}
            onChanged={(secrets) => setS({ ...s, secrets })}
            onError={setErr}
          />
        ))}
      </div>
      <ErrorBanner error={err} />
    </section>
  );
}

function SecretField({
  account,
  label,
  stored,
  onChanged,
  onError,
}: {
  account: SecretAccount;
  label: string;
  stored: boolean;
  onChanged: (secrets: Record<SecretAccount, boolean>) => void;
  onError: (e: unknown) => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const id = `secret-${account}`;
  const send = async (v: string) => {
    setBusy(true);
    onError(null);
    try {
      const r = await api.setSecret(account, v);
      onChanged(r.secrets);
      setValue("");
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (value.trim()) void send(value.trim());
  };
  return (
    <form className="secret-row" onSubmit={submit} data-testid={id}>
      <label htmlFor={id}>{t("material.key.label", { name: label })}</label>
      <input
        id={id}
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={value}
        placeholder={stored ? t("material.key.stored.placeholder") : t("material.key.placeholder")}
        onChange={(e) => setValue(e.target.value)}
        disabled={busy}
      />
      <button type="submit" className="btn" disabled={busy || !value.trim()} aria-label={t("material.key.save", { name: label })}>
        {t("common.save")}
      </button>
      {stored && (
        <>
          <span className="chip chip-done">{t("material.key.stored")}</span>
          <button type="button" className="btn btn-ghost" disabled={busy} aria-label={t("material.key.remove.label", { name: label })} onClick={() => void send("")}>
            {t("material.key.remove")}
          </button>
        </>
      )}
    </form>
  );
}

// ---------------------------------------------------------------- plan

export function kindName(kind: string, t: T): string {
  const key = `material.kind.${kind}` as TKey;
  const s = t(key);
  return s === key ? kind : s;
}

/** The plan's material wishes (what the human approves with the plan). */
export function PlanMaterialWishes({ wishes }: { wishes: MaterialWish[] }) {
  const { t } = useI18n();
  return (
    <div className="stack-sm" data-testid="plan-material">
      <h3>{t("material.plan.title")}</h3>
      <ol className="timeline">
        {wishes.map((w) => (
          <li key={w.n}>
            <span className="tl-time">
              {clockFine(w.src_from)} – {clockFine(w.src_to)}
            </span>
            <div className="tl-body">
              <p>
                <span className="badge">{kindName(w.kind, t)}</span>{" "}
                {w.query && <span>{t("material.plan.query", { q: w.query })}</span>}
                {w.query && w.prompt && " · "}
                {w.prompt && <span>{t("material.plan.prompt", { p: w.prompt })}</span>}
              </p>
              {w.why && (
                <p className="muted">
                  {t("plan.why")}: {w.why}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------- the material screen

/** Whether the job page shows the material screen. */
export function showMaterial(job: JobDetail): boolean {
  if (job.stage === "material" || job.gate === "material") return true;
  const m = job.material;
  return !!m && m.mode !== "none" && job.approved && !job.outputs?.reel && m.wishes > 0;
}

type Live = { job: JobDetail | null; running: boolean; tick: number; reload: () => Promise<void>; begin: (s: Stage) => void };

/** Between plan approval and cut: per wish the candidates, the human picks one or none. */
export function MaterialScreen({ live }: { live: Live }) {
  const { t, lang } = useI18n();
  const job = live.job!;
  const [view, setView] = useState<MaterialView | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busyWish, setBusyWish] = useState<number | null>(null);
  const running = live.running && job.stage === "material";

  const load = useCallback(() => {
    api.material(job.id).then(setView).catch(setErr);
  }, [job.id]);
  useEffect(load, [load, live.tick]);
  useEffect(() => {
    if (!running) setBusyWish(null);
  }, [running]);

  const fetchAll = async (body: Parameters<typeof api.fetchMaterial>[1] = {}) => {
    setErr(null);
    try {
      live.begin("material");
      await api.fetchMaterial(job.id, body);
    } catch (e) {
      setErr(e);
      setBusyWish(null);
      void live.reload();
    }
  };
  const choose = async (n: number, candidate: string | null) => {
    setErr(null);
    try {
      setView(await api.chooseMaterial(job.id, n, candidate));
      void live.reload();
    } catch (e) {
      setErr(e);
    }
  };
  const goOn = async () => {
    setErr(null);
    try {
      live.begin("cut");
      await api.start(job.id);
    } catch (e) {
      setErr(e);
      void live.reload();
    }
  };

  if (!view) {
    return (
      <section className="panel stack" aria-labelledby="material-title">
        <h2 id="material-title">{t("material.screen.title")}</h2>
        {err ? <ErrorBanner error={err} onRetry={load} /> : <p className="muted">{t("common.loading")}</p>}
      </section>
    );
  }
  const open = view.pending.length;
  return (
    <section className="panel stack" aria-labelledby="material-title" data-testid="material-screen">
      <div className="titlebar">
        <h2 id="material-title">{t("material.screen.title")}</h2>
        <span className="muted" data-testid="material-cost">
          {t("material.cost", { c: money(view.cost_usd, lang), b: money(view.budget_usd, lang) })}
        </span>
      </div>
      <p className="hint">{t("material.screen.hint")}</p>
      {running && (
        <div className="stack-sm" role="status">
          <progress aria-label={t("now.material")} />
          <p className="muted">{busyWish ? t("material.fetching.one", { n: busyWish }) : t("now.material")}</p>
        </div>
      )}
      {!view.fetched && !running && (
        <div className="actions">
          <button type="button" className="btn btn-primary" onClick={() => void fetchAll()}>
            {t("material.fetch")}
          </button>
        </div>
      )}
      {view.wishes.map((w) => (
        <WishCard
          key={w.n}
          wish={w}
          disabled={running}
          onChoose={(c) => void choose(w.n, c)}
          onSearch={(query) => {
            setBusyWish(w.n);
            void fetchAll({ wish: w.n, query });
          }}
          onGenerate={(prompt) => {
            setBusyWish(w.n);
            void fetchAll({ wish: w.n, prompt, generate: true });
          }}
          canSearch={usesStock(view.mode) && w.kind !== "icon"}
          canGenerate={usesGenerator(view.mode) && w.kind !== "broll" && view.image_provider !== "none"}
        />
      ))}
      <ErrorBanner error={err} />
      <div className="actions">
        {open > 0 ? (
          <p className="muted" data-testid="material-open">
            {t("material.open", { n: open })}
          </p>
        ) : (
          <button type="button" className="btn btn-primary" disabled={running || live.running} onClick={() => void goOn()}>
            {t("material.continue")}
          </button>
        )}
      </div>
    </section>
  );
}

function WishCard({
  wish,
  disabled,
  onChoose,
  onSearch,
  onGenerate,
  canSearch,
  canGenerate,
}: {
  wish: MaterialView["wishes"][number];
  disabled: boolean;
  onChoose: (candidate: string | null) => void;
  onSearch: (query: string) => void;
  onGenerate: (prompt: string) => void;
  canSearch: boolean;
  canGenerate: boolean;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState(wish.query ?? "");
  const [prompt, setPrompt] = useState(wish.prompt ?? "");
  useEffect(() => setQuery(wish.query ?? ""), [wish.query]);
  useEffect(() => setPrompt(wish.prompt ?? ""), [wish.prompt]);
  const none = wish.decided && wish.chosen === null;
  return (
    <article className="wishcard stack-sm" data-testid={`wish-${wish.n}`}>
      <header className="wishcard-head">
        <span className="badge">{kindName(wish.kind, t)}</span>
        <span className="muted">
          {clockFine(wish.src_from)} – {clockFine(wish.src_to)}
        </span>
        {wish.decided ? (
          <span className="chip chip-done">{none ? t("material.none.chosen") : t("material.chosen", { id: wish.chosen ?? "" })}</span>
        ) : (
          <span className="chip chip-waiting">{t("material.undecided")}</span>
        )}
      </header>
      {wish.why && <p>{wish.why}</p>}
      {wish.candidates.length === 0 ? (
        <p className="muted">{t("material.nocands")}</p>
      ) : (
        <ul className="candcards">
          {wish.candidates.map((c) => (
            <Candidate key={c.id} cand={c} chosen={wish.chosen === c.id} disabled={disabled} onChoose={() => onChoose(c.id)} />
          ))}
        </ul>
      )}
      <div className="actions">
        <button type="button" className={`btn${none ? " btn-primary" : ""}`} disabled={disabled} aria-pressed={none} onClick={() => onChoose(null)}>
          {t("material.none")}
        </button>
      </div>
      {(canSearch || canGenerate) && (
        <details className="details">
          <summary>{t("material.redo")}</summary>
          <div className="stack-sm">
            {canSearch && (
              <form
                className="secret-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (query.trim()) onSearch(query.trim());
                }}
              >
                <label htmlFor={`q-${wish.n}`}>{t("material.query")}</label>
                <input id={`q-${wish.n}`} type="text" value={query} maxLength={200} onChange={(e) => setQuery(e.target.value)} disabled={disabled} />
                <button type="submit" className="btn" disabled={disabled || !query.trim()}>
                  {t("material.search")}
                </button>
              </form>
            )}
            {canGenerate && (
              <form
                className="secret-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (prompt.trim()) onGenerate(prompt.trim());
                }}
              >
                <label htmlFor={`p-${wish.n}`}>{t("material.prompt")}</label>
                <input id={`p-${wish.n}`} type="text" value={prompt} maxLength={600} onChange={(e) => setPrompt(e.target.value)} disabled={disabled} />
                <button type="submit" className="btn" disabled={disabled || !prompt.trim()}>
                  {t("material.generate")}
                </button>
              </form>
            )}
          </div>
        </details>
      )}
    </article>
  );
}

function Candidate({
  cand,
  chosen,
  disabled,
  onChoose,
}: {
  cand: MaterialCandidate;
  chosen: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  const { t } = useI18n();
  const [playing, setPlaying] = useState(false);
  const video = useRef<HTMLVideoElement | null>(null);
  const play = (on: boolean) => {
    if (cand.kind !== "video") return;
    setPlaying(on);
  };
  useEffect(() => {
    if (playing) video.current?.play?.()?.catch?.(() => undefined);
  }, [playing]);
  return (
    <li className={`candcard${chosen ? " candcard-chosen" : ""}`} data-testid={`cand-${cand.id}`}>
      <div
        className="candcard-media"
        onMouseEnter={() => play(true)}
        onMouseLeave={() => play(false)}
        onClick={() => play(!playing)}
      >
        {playing && cand.kind === "video" ? (
          <video ref={video} src={api.url(cand.file_url)} muted loop playsInline poster={api.url(cand.thumb_url)} />
        ) : (
          <img src={api.url(cand.thumb_url)} alt="" loading="lazy" />
        )}
        {cand.ai_generated && (
          <span className="badge badge-accent candcard-ai" title={cand.model ?? undefined}>
            {t("material.ai")}
          </span>
        )}
        {cand.kind === "video" && cand.duration_s ? <span className="badge candcard-dur">{Math.round(cand.duration_s)} s</span> : null}
      </div>
      <div className="candcard-meta">
        <span className="muted small">
          {cand.licence}
          {cand.attribution ? ` · ${cand.attribution}` : ""}
        </span>
        {cand.source_url && (
          <a className="small" href={cand.source_url} target="_blank" rel="noreferrer noopener">
            {t("material.source")}
          </a>
        )}
        <button type="button" className={`btn${chosen ? " btn-primary" : ""}`} disabled={disabled} aria-pressed={chosen} onClick={onChoose}>
          {chosen ? t("material.chosen.short") : t("material.choose")}
        </button>
      </div>
    </li>
  );
}
