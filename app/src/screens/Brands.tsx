import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import { FORMATS, type Brand, type BrandSummary, type Format, type ThemeNote } from "../api/types";
import { AspectIcon } from "../components/AspectIcon";
import { ErrorBanner } from "../components/ErrorBanner";
import { VocabEditor, cleanVocabulary } from "../components/Vocabulary";
import { useI18n } from "../i18n";
import { go, href } from "../router";
import { useTheme } from "../theme";

export const EXAMPLE_ID = "_example";
const COLOR_KEYS = ["bg", "surface", "text", "muted", "accent"] as const;
const FONT_EXT = [".woff2", ".ttf", ".otf"];
const LOGO_EXT = [".svg", ".png"];
const HEX = /^#[0-9a-fA-F]{6}$/;

export function BrandList() {
  const { t } = useI18n();
  const [brands, setBrands] = useState<BrandSummary[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const { theme, settings, saveSettings } = useTheme();

  const load = () => {
    setErr(null);
    api.brands().then(setBrands).catch(setErr);
  };
  useEffect(load, []);
  const defaultId = settings ? settings.default_brand : (brands?.find((b) => b.default)?.id ?? null);
  const setDefault = (id: string | null) => {
    setErr(null);
    saveSettings({ default_brand: id }).then(load, setErr);
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const { id } = await api.newBrand(name.trim());
      go({ name: "brand", id });
    } catch (e2) {
      setErr(e2);
    }
  };

  return (
    <div className="stack-lg">
      <div className="titlebar">
        <h1>{t("brands.title")}</h1>
        {!creating && (
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            + {t("brands.new")}
          </button>
        )}
      </div>
      {creating && (
        <form className="panel stack-sm" onSubmit={create}>
          <label htmlFor="brand-name">{t("brands.new.name")}</label>
          <input id="brand-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          <p className="hint">{t("brands.new.hint")}</p>
          <div className="actions">
            <button type="submit" className="btn btn-primary" disabled={!name.trim()}>
              {t("brands.new")}
            </button>
            <button type="button" className="btn" onClick={() => setCreating(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
      <ErrorBanner error={err} onRetry={load} />
      <section className="panel stack-sm" aria-labelledby="brand-default-title">
        <h2 id="brand-default-title" className="h3">
          {t("brands.default.title")}
        </h2>
        <p className="hint">{defaultId ? t("brands.default.is", { name: brands?.find((b) => b.id === defaultId)?.name ?? defaultId }) : t("brands.default.none")}</p>
        <ThemeSwitch />
        {defaultId && theme.invalid && <p className="banner banner-warn">{t("theme.invalid")}</p>}
        {defaultId && <ContrastNotes notes={theme.notes} />}
        {defaultId && (
          <div className="actions">
            <button type="button" className="btn btn-small" onClick={() => setDefault(null)}>
              {t("brands.default.unset")}
            </button>
          </div>
        )}
      </section>
      {brands && brands.length === 0 && <p className="empty">{t("brands.empty")}</p>}
      {brands && (
        <ul className="brandgrid">
          {brands.map((b) => (
            <li key={b.id} className="brandcard-item">
              <a className="brandcard" href={href({ name: "brand", id: b.id })}>
                <img src={api.url(b.preview_url)} alt="" loading="lazy" />
                <span className="brandcard-name">{b.name}</span>
              </a>
              <div className="brandcard-actions">
                {b.id === defaultId ? (
                  <span className="default-badge">{t("brands.default.badge")}</span>
                ) : (
                  <button type="button" className="btn btn-small" onClick={() => setDefault(b.id)} aria-label={`${t("brands.default.set")}: ${b.name}`}>
                    {t("brands.default.set")}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** "App-Design an Marke anpassen": keep a default brand for new videos but a neutral UI. */
export function ThemeSwitch() {
  const { t } = useI18n();
  const { settings, saveSettings } = useTheme();
  const [err, setErr] = useState<unknown>(null);
  if (!settings) return null;
  return (
    <div className="stack-sm">
      <label className="check-row">
        <input
          type="checkbox"
          checked={settings.theme_follows_brand}
          onChange={(e) => {
            setErr(null);
            saveSettings({ theme_follows_brand: e.target.checked }).catch(setErr);
          }}
        />
        <span>{t("theme.follow")}</span>
      </label>
      <p className="hint">{t("theme.follow.hint")}</p>
      <ErrorBanner error={err} />
    </div>
  );
}

/** Plain-language hints for the tokens the contrast guard replaced with neutral ones. */
export function ContrastNotes({ notes }: { notes: ThemeNote[] }) {
  const { t, lang } = useI18n();
  if (!notes.length) return null;
  return (
    <ul className="banner banner-warn" aria-label={t("theme.notes")}>
      {notes.map((n) => (
        <li key={`${n.code}-${n.scheme}`}>
          {t(`theme.note.${n.code}`, { ratio: n.ratio.toLocaleString(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) })}
          {n.scheme === "dark" ? ` ${t("theme.note.inDark")}` : ""}
        </li>
      ))}
    </ul>
  );
}

/** Validation messages from the brand check, keyed by the field they name ("colors.bg"). */
export function fieldErrors(errors: string[]): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const e of errors) {
    const m = e.match(/^\$\.([\w.[\]]+?):\s*(.*)$/);
    const key = m ? m[1] : "_";
    (out[key] ??= []).push(m ? m[2] : e);
  }
  return out;
}

export function BrandEditor({ id }: { id: string }) {
  const { t } = useI18n();
  const [brand, setBrand] = useState<Brand | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [saved, setSaved] = useState(false);
  const [note, setNote] = useState("");
  const [fmt, setFmt] = useState<Format>("9:16");
  const [version, setVersion] = useState(0);
  const [previewOn, setPreviewOn] = useState(false);
  const [notes, setNotes] = useState<ThemeNote[]>([]);
  const theme = useTheme();
  const { setPreview } = theme;
  const readonly = id === EXAMPLE_ID;

  const load = () => {
    setErr(null);
    api.brand(id).then(setBrand).catch(setErr);
  };
  useEffect(load, [id]);
  // The edited (unsaved) brand's theme: contrast hints always, the live look while the
  // preview is on. Leaving the editor restores the real theme.
  const edited = brand ? JSON.stringify(brand) : null;
  useEffect(() => {
    if (!edited) return;
    let gone = false;
    const timer = setTimeout(() => {
      api
        .previewTheme(id, JSON.parse(edited) as Brand)
        .then((v) => {
          if (gone) return;
          setNotes(v.notes);
          setPreview(previewOn ? v : null);
        })
        .catch(() => undefined); // invalid while typing: the field errors show on save
    }, 250);
    return () => {
      gone = true;
      clearTimeout(timer);
    };
  }, [id, edited, previewOn, setPreview]);
  useEffect(() => () => setPreview(null), [setPreview]);

  if (err && !brand) return <ErrorBanner error={err} onRetry={load} />;
  if (!brand) return <p className="muted">{t("common.loading")}</p>;

  const set = (path: string, value: unknown) => {
    const [sec, key] = path.split(".");
    setSaved(false);
    setBrand((b) => {
      if (!b) return b;
      if (!key) return { ...b, [sec]: value };
      return { ...b, [sec]: { ...((b[sec] as Record<string, unknown>) ?? {}), [key]: value } };
    });
  };
  const files = brand.files ?? [];
  const fonts = files.filter((f) => FONT_EXT.some((e) => f.toLowerCase().endsWith(e)));
  const logos = files.filter((f) => LOGO_EXT.some((e) => f.toLowerCase().endsWith(e)));
  const fe = (k: string) => errors[k]?.map((m) => <span key={m} className="field-error">{m}</span>);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setErrors({});
    const { files: _files, ...tokens } = brand;
    void _files;
    if (Array.isArray(tokens.vocabulary)) {
      const words = cleanVocabulary(tokens.vocabulary);
      if (words.length) tokens.vocabulary = words;
      else delete tokens.vocabulary;
    }
    try {
      const b = await api.saveBrand(id, tokens as Brand);
      setBrand({ ...b, files: b.files ?? files });
      setSaved(true);
      setVersion((v) => v + 1);
      if (theme.settings?.default_brand === id) void theme.reload();
    } catch (e2) {
      if (e2 instanceof ApiError && e2.errors.length) setErrors(fieldErrors(e2.errors));
      setErr(e2);
    }
  };

  const upload = async (kind: "logo" | "font", file: File | undefined, role?: string) => {
    if (!file) return;
    setErr(null);
    try {
      const b = await api.brandFile(id, kind, file, role);
      setBrand((cur) => ({ ...(cur as Brand), ...b }));
      setNote(t("brands.uploaded", { name: file.name }));
      setVersion((v) => v + 1);
      if (theme.settings?.default_brand === id) void theme.reload();
    } catch (e2) {
      setErr(e2);
    }
  };

  const remove = async () => {
    if (!window.confirm(t("brands.delete.confirm", { name: brand.name }))) return;
    setErr(null);
    try {
      await api.deleteBrand(id);
      await theme.reload();
      go({ name: "brands" });
    } catch (e2) {
      setErr(e2);
    }
  };

  return (
    <div className="stack-lg">
      <div className="titlebar">
        <h1>{brand.name}</h1>
        {theme.settings?.default_brand === id && <span className="default-badge">{t("brands.default.badge")}</span>}
        <a className="btn btn-ghost" href={href({ name: "brands" })}>
          {t("common.back")}
        </a>
      </div>
      {readonly && <p className="banner banner-info">{t("brands.readonly")}</p>}
      <div className="panel stack-sm">
        <label className="check-row">
          <input type="checkbox" checked={previewOn} onChange={(e) => {
              setPreviewOn(e.target.checked);
              if (!e.target.checked) setPreview(null);
            }}
          />
          <span>{t("theme.preview")}</span>
        </label>
        <p className="hint">{t("theme.preview.hint")}</p>
        <ContrastNotes notes={notes} />
      </div>
      <div className="editor-layout">
        <form className="panel stack" onSubmit={save} aria-label={t("brands.editor")}>
          <fieldset disabled={readonly} className="stack">
            <div className="field">
              <label htmlFor="b-name">{t("brands.name")}</label>
              <input id="b-name" value={brand.name ?? ""} onChange={(e) => set("name", e.target.value)} />
              {fe("name")}
            </div>

            <fieldset className="stack-sm">
              <legend className="h3">{t("brands.colors")}</legend>
              <div className="colorgrid">
                {COLOR_KEYS.map((k) => {
                  const v = brand.colors?.[k] ?? "";
                  return (
                    <div key={k} className="field color-field">
                      <label htmlFor={`c-${k}`}>{t(`brands.color.${k}`)}</label>
                      <div className="color-row">
                        <input
                          type="color"
                          aria-label={`${t(`brands.color.${k}`)} (Picker)`}
                          value={HEX.test(v) ? v.toLowerCase() : "#000000"}
                          onChange={(e) => set(`colors.${k}`, e.target.value.toUpperCase())}
                        />
                        <input
                          id={`c-${k}`}
                          className="mono"
                          value={v}
                          aria-invalid={!!errors[`colors.${k}`] || (v !== "" && !HEX.test(v))}
                          onChange={(e) => set(`colors.${k}`, e.target.value)}
                        />
                      </div>
                      {fe(`colors.${k}`)}
                    </div>
                  );
                })}
              </div>
            </fieldset>

            <fieldset className="stack-sm">
              <legend className="h3">{t("brands.fonts")}</legend>
              <div className="grid-2">
                {(["display", "body"] as const).map((role) => (
                  <div className="field" key={role}>
                    <label htmlFor={`f-${role}`}>{t(`brands.font.${role}`)}</label>
                    <select id={`f-${role}`} value={String(brand.fonts?.[role] ?? "system-ui")} onChange={(e) => set(`fonts.${role}`, e.target.value)}>
                      <option value="system-ui">{t("brands.font.system")}</option>
                      {fonts.map((f) => (
                        <option key={f} value={f}>
                          {f.split("/").pop()}
                        </option>
                      ))}
                    </select>
                    {fe(`fonts.${role}`)}
                  </div>
                ))}
              </div>
              <div className="field">
                <FileButton id="f-upload" label={t("brands.font.upload")} accept={FONT_EXT} onFile={(f) => void upload("font", f)} />
              </div>
            </fieldset>

            <div className="grid-2">
              <div className="field">
                <label htmlFor="b-pace">{t("brands.pace")}</label>
                <select id="b-pace" value={String(brand.captions?.pace ?? "normal")} onChange={(e) => set("captions.pace", e.target.value)}>
                  {(["calm", "normal", "fast"] as const).map((p) => (
                    <option key={p} value={p}>
                      {t(`brands.pace.${p}`)}
                    </option>
                  ))}
                </select>
                {fe("captions.pace")}
              </div>
              <div className="field">
                <span className="label" id="b-address">
                  {t("brands.address")}
                </span>
                <div className="segmented" role="radiogroup" aria-labelledby="b-address">
                  {(["du", "sie"] as const).map((a) => (
                    <label key={a} className={`seg${brand.voice?.address === a ? " seg-on" : ""}`}>
                      <input type="radio" className="sr-only" name="address" checked={brand.voice?.address === a} onChange={() => set("voice.address", a)} />
                      <span>{t(`brands.address.${a}`)}</span>
                    </label>
                  ))}
                </div>
                {fe("voice.address")}
              </div>
            </div>

            <fieldset className="stack-sm">
              <legend className="h3">{t("vocab.brand.title")}</legend>
              <VocabEditor
                id="bv"
                value={brand.vocabulary ?? []}
                onChange={(v) => set("vocabulary", v)}
                errors={Object.entries(errors)
                  .filter(([k]) => k === "vocabulary" || k.startsWith("vocabulary["))
                  .flatMap(([k, ms]) => ms.map((m) => `${k}: ${m}`))}
              />
            </fieldset>

            <fieldset className="stack-sm">
              <legend className="h3">{t("brands.logo")}</legend>
              <div className="grid-2">
                {(["light", "dark"] as const).map((role) => (
                  <div className="field" key={role}>
                    <FileButton
                      id={`l-${role}`}
                      label={`${t("brands.logo.upload")} – ${t(`brands.logo.${role}`)}`}
                      accept={LOGO_EXT}
                      onFile={(f) => void upload("logo", f, role)}
                    />
                    <span className="muted">{(brand.logo?.[role] as string | null) ?? t("common.none")}</span>
                    {fe(`logo.${role}`)}
                  </div>
                ))}
              </div>
              <div className="field">
                <label htmlFor="l-place">{t("brands.logo.placement")}</label>
                <select id="l-place" value={String(brand.logo?.placement ?? "none")} onChange={(e) => set("logo.placement", e.target.value)}>
                  {(["intro", "outro", "corner", "none"] as const).map((p) => (
                    <option key={p} value={p}>
                      {t(`brands.logo.${p}`)}
                    </option>
                  ))}
                </select>
                {fe("logo.placement")}
              </div>
              {logos.length > 0 && (
                <p className="muted">
                  {t("brands.files")}: {logos.join(", ")}
                </p>
              )}
            </fieldset>
          </fieldset>

          {errors._ && (
            <ul className="field-error">
              {errors._.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          )}
          {Object.keys(errors).length > 0 && <p className="field-error">{t("brands.errors")}</p>}
          <ErrorBanner error={err} />
          {saved && <p className="banner banner-ok" role="status">{t("brands.saved")}</p>}
          {note && !saved && <p className="muted" role="status">{note}</p>}
          {!readonly && (
            <div className="actions">
              <button type="submit" className="btn btn-primary">
                {t("common.save")}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => void remove()}>
                {t("brands.delete")}
              </button>
            </div>
          )}
        </form>

        <section className="panel stack-sm" aria-label={t("brands.preview")}>
          <h2 className="h3">{t("brands.preview")}</h2>
          <div className="segmented" role="tablist">
            {FORMATS.map((f) => (
              <button key={f} type="button" role="tab" aria-selected={fmt === f} className={`seg${fmt === f ? " seg-on" : ""}`} onClick={() => setFmt(f)}>
                <AspectIcon format={f} /> {f}
              </button>
            ))}
          </div>
          <img className="sheet" src={api.sheetUrl(id, fmt, String(version))} alt={`${t("brands.preview")} ${fmt}`} />
        </section>
      </div>
    </div>
  );
}

/** A native file input styled as a button (the browser's own label is not translated). */
function FileButton({ id, label, accept, onFile }: { id: string; label: string; accept: string[]; onFile: (f: File) => void }) {
  return (
    <span className="filebutton">
      <input
        id={id}
        type="file"
        className="sr-only"
        accept={accept.join(",")}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
      <label htmlFor={id} className="btn btn-small">
        {label}
      </label>
    </span>
  );
}
