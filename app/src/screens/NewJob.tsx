import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";
import { FORMATS, type BrandSummary, type CutStyleChoice, type FormatChoice, type TargetChoice, type VideoType } from "../api/types";
import { AspectIcon } from "../components/AspectIcon";
import { CutStyleSelect } from "../components/CutStyle";
import { TargetSelect } from "../components/TargetLength";
import { DropZone, formatBytes, hasExt, IMAGE_EXT, VIDEO_EXT } from "../components/DropZone";
import { ErrorBanner } from "../components/ErrorBanner";
import { useI18n, type T } from "../i18n";
import { go } from "../router";

export interface NewJobDraft {
  type: string | null;
  camera: File | null;
  screen: File | null;
  assets: File[];
}

/** Problems that block the upload, in plain language; empty when the form can be sent. */
export function validateNewJob(d: NewJobDraft, types: VideoType[], t: T): string[] {
  const errs: string[] = [];
  const type = types.find((x) => x.id === d.type);
  if (!type) errs.push(t("new.err.type"));
  if (!d.camera) errs.push(t("new.err.camera"));
  const vid = VIDEO_EXT.join(", ");
  const img = IMAGE_EXT.join(", ");
  if (d.camera && !hasExt(d.camera.name, VIDEO_EXT)) errs.push(t("new.err.video", { name: d.camera.name, ext: vid }));
  if (d.screen && !hasExt(d.screen.name, VIDEO_EXT)) errs.push(t("new.err.video", { name: d.screen.name, ext: vid }));
  for (const a of d.assets) if (!hasExt(a.name, IMAGE_EXT)) errs.push(t("new.err.image", { name: a.name, ext: img }));
  if (type) {
    const missing = type.needs.filter(
      (n) => (n === "screen" && !d.screen) || (n === "assets" && d.assets.length === 0),
    );
    if (missing.length) errs.push(t("new.err.needs", { what: missing.map((m) => t(m === "screen" ? "new.screen" : "new.assets")).join(", ") }));
  }
  return errs;
}

export function NewJob() {
  const { t, lang } = useI18n();
  const [types, setTypes] = useState<VideoType[] | null>(null);
  const [brands, setBrands] = useState<BrandSummary[]>([]);
  const [loadErr, setLoadErr] = useState<unknown>(null);
  const [draft, setDraft] = useState<NewJobDraft>({ type: null, camera: null, screen: null, assets: [] });
  const [brand, setBrand] = useState("");
  const [format, setFormat] = useState<FormatChoice>("auto");
  const [brief, setBrief] = useState("");
  const [cutStyle, setCutStyle] = useState<CutStyleChoice>("auto");
  // undefined: a custom length that is not valid yet (blocks the upload)
  const [target, setTarget] = useState<TargetChoice | undefined>(null);
  const [tried, setTried] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [phase, setPhase] = useState<"idle" | "upload" | "start">("idle");
  const [sendErr, setSendErr] = useState<unknown>(null);

  const load = () => {
    setLoadErr(null);
    Promise.all([api.types(), api.brands()])
      .then(([ty, br]) => {
        setTypes(ty);
        setBrands(br);
      })
      .catch(setLoadErr);
  };
  useEffect(load, []);

  const errors = types ? validateNewJob(draft, types, t) : [];
  if (target === undefined) errors.push(t("target.custom.invalid"));
  const busy = phase !== "idle";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (!types || errors.length || !draft.camera || !draft.type || target === undefined) return;
    setSendErr(null);
    setPhase("upload");
    setProgress(0);
    try {
      const { id } = await api.createJob(
        {
          camera: draft.camera,
          screen: draft.screen,
          assets: draft.assets,
          type: draft.type,
          brand: brand || undefined,
          format,
          brief: brief.trim() || undefined,
          cut_style: cutStyle,
          target_s: target,
        },
        (p) => setProgress(p.total ? Math.round((p.loaded / p.total) * 100) : null),
      );
      setPhase("start");
      try {
        await api.start(id);
      } finally {
        go({ name: "job", id });
      }
    } catch (err) {
      setSendErr(err);
      setPhase("idle");
      setProgress(null);
    }
  };

  const moveAsset = (i: number, d: -1 | 1) => {
    const a = [...draft.assets];
    const j = i + d;
    if (j < 0 || j >= a.length) return;
    [a[i], a[j]] = [a[j], a[i]];
    setDraft({ ...draft, assets: a });
  };

  const withScreen = !!draft.screen || draft.assets.length > 0;
  const selectedBrand = brands.find((b) => b.id === brand);

  if (loadErr) return <ErrorBanner error={loadErr} onRetry={load} />;
  if (!types) return <p className="muted">{t("common.loading")}</p>;

  return (
    <form className="stack-lg" onSubmit={submit} noValidate>
      <h1>{t("new.title")}</h1>

      <fieldset className="stack">
        <legend className="h2">{t("new.type")}</legend>
        <div className="cards" role="radiogroup" aria-label={t("new.type")}>
          {types.map((ty) => (
            <label key={ty.id} className={`card card-choice${draft.type === ty.id ? " card-selected" : ""}`}>
              <input
                type="radio"
                name="type"
                className="sr-only"
                value={ty.id}
                checked={draft.type === ty.id}
                onChange={() => setDraft({ ...draft, type: ty.id })}
              />
              <span className="card-title">{lang === "de" ? ty.name_de : ty.name_en}</span>
              <span className="card-desc">{lang === "de" ? ty.desc_de : ty.desc_en}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="stack">
        <legend className="h2">{t("new.files")}</legend>
        <DropZone
          label={t("new.camera")}
          hint={t("new.camera.hint")}
          accept={VIDEO_EXT}
          required
          invalid={tried && !draft.camera}
          onFiles={(f) => setDraft({ ...draft, camera: f[0] })}
        >
          {draft.camera && <FileRow file={draft.camera} onRemove={() => setDraft({ ...draft, camera: null })} />}
        </DropZone>
        <div className="grid-2">
          <DropZone
            label={t("new.screen")}
            hint={t("new.screen.hint")}
            accept={VIDEO_EXT}
            onFiles={(f) => setDraft({ ...draft, screen: f[0] })}
          >
            {draft.screen && <FileRow file={draft.screen} onRemove={() => setDraft({ ...draft, screen: null })} />}
          </DropZone>
          <DropZone
            label={t("new.assets")}
            hint={t("new.assets.hint")}
            accept={IMAGE_EXT}
            multiple
            onFiles={(f) => setDraft({ ...draft, assets: [...draft.assets, ...f] })}
          >
            {draft.assets.length > 0 && (
              <ol className="filelist">
                {draft.assets.map((a, i) => (
                  <li key={`${a.name}-${i}`}>
                    <FileRow
                      file={a}
                      onRemove={() => setDraft({ ...draft, assets: draft.assets.filter((_, j) => j !== i) })}
                    >
                      <button type="button" className="btn btn-icon" aria-label={`${t("new.up")}: ${a.name}`} disabled={i === 0} onClick={() => moveAsset(i, -1)}>
                        ↑
                      </button>
                      <button
                        type="button"
                        className="btn btn-icon"
                        aria-label={`${t("new.down")}: ${a.name}`}
                        disabled={i === draft.assets.length - 1}
                        onClick={() => moveAsset(i, 1)}
                      >
                        ↓
                      </button>
                    </FileRow>
                  </li>
                ))}
              </ol>
            )}
          </DropZone>
        </div>
      </fieldset>

      <fieldset className="stack">
        <legend className="h2">{t("new.look")}</legend>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="brand">{t("new.brand")}</label>
            <div className="brand-pick">
              <select id="brand" value={brand} onChange={(e) => setBrand(e.target.value)}>
                <option value="">{t("new.brand.none")}</option>
                {brands.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
              {selectedBrand && <img className="brand-thumb" src={api.url(selectedBrand.preview_url)} alt="" />}
            </div>
          </div>
          <div className="field">
            <span id="format-label" className="label">
              {t("new.format")}
            </span>
            <div className="segmented" role="radiogroup" aria-labelledby="format-label">
              {(["auto", ...FORMATS] as FormatChoice[]).map((f) => (
                <label key={f} className={`seg${format === f ? " seg-on" : ""}`}>
                  <input type="radio" name="format" className="sr-only" value={f} checked={format === f} onChange={() => setFormat(f)} />
                  <AspectIcon format={f} />
                  <span>{f === "auto" ? t("new.format.auto") : f}</span>
                </label>
              ))}
            </div>
            {format === "auto" && (
              <p className="hint">{withScreen ? t("new.format.auto.hint.screen") : t("new.format.auto.hint.camera")}</p>
            )}
          </div>
        </div>
      </fieldset>

      <div className="field">
        <label htmlFor="cut-style" className="h2">
          {t("style.new")}
        </label>
        <CutStyleSelect id="cut-style" value={cutStyle} resolved={withScreen ? "calm" : "standard"} onChange={setCutStyle} />
      </div>

      <div className="field target-pick">
        <label htmlFor="target" className="h2">
          {t("target.new")}
        </label>
        <TargetSelect id="target" value={target} onChange={setTarget} />
      </div>

      <div className="field">
        <label htmlFor="brief" className="h2">
          {t("new.brief")}
        </label>
        <input
          id="brief"
          type="text"
          value={brief}
          maxLength={300}
          placeholder={t("new.brief.placeholder")}
          onChange={(e) => setBrief(e.target.value)}
        />
      </div>

      {tried && errors.length > 0 && (
        <div className="banner banner-error" role="alert">
          <ul>
            {errors.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      <ErrorBanner error={sendErr} />

      {busy && (
        <div className="stack-sm">
          <progress max={100} value={progress ?? undefined} aria-label={t("new.uploading", { pct: progress ?? 0 })} />
          <p className="muted">{phase === "upload" ? t("new.uploading", { pct: progress ?? 0 }) : t("new.starting")}</p>
        </div>
      )}

      <div className="actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {t("new.submit")}
        </button>
      </div>
    </form>
  );
}

function FileRow({ file, onRemove, children }: { file: File; onRemove: () => void; children?: ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="filerow">
      <span className="filerow-name" title={file.name}>
        {file.name}
      </span>
      <span className="muted">{formatBytes(file.size)}</span>
      {children}
      <button type="button" className="btn btn-icon" aria-label={`${t("new.remove")}: ${file.name}`} onClick={onRemove}>
        ×
      </button>
    </div>
  );
}
