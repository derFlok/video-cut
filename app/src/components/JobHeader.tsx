import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { JobDetail } from "../api/types";
import { useI18n } from "../i18n";
import { go } from "../router";
import { ErrorBanner } from "./ErrorBanner";

const TITLE_MAX = 120;

/** The job's title with inline rename, and the menu with "Duplicate". */
export function JobHeader({ job, busy, onChanged }: { job: JobDetail; busy: boolean; onChanged: () => void }) {
  const { t } = useI18n();
  const shown = job.title || t("home.untitled");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [menu, setMenu] = useState(false);
  const [dup, setDup] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [menu]);

  const edit = () => {
    setDraft(job.title || "");
    setErr(null);
    setEditing(true);
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const title = draft.trim();
    if (!title || title === job.title) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await api.setTitle(job.id, title);
      setEditing(false);
      onChanged();
    } catch (e2) {
      setErr(e2);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="titlebar-main">
      {editing ? (
        <form className="title-edit" onSubmit={save}>
          <input
            type="text"
            aria-label={t("job.title.label")}
            value={draft}
            maxLength={TITLE_MAX}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
          />
          <button type="submit" className="btn btn-primary btn-small" disabled={saving || !draft.trim()}>
            {t("common.save")}
          </button>
          <button type="button" className="btn btn-small" onClick={() => setEditing(false)}>
            {t("common.cancel")}
          </button>
        </form>
      ) : (
        <div className="title-row">
          <h1>{shown}</h1>
          {job.copy_of && <span className="badge">{t("job.copy.badge")}</span>}
          <button type="button" className="btn btn-icon" onClick={edit} disabled={busy} aria-label={t("job.title.rename")} title={t("job.title.rename")}>
            ✎
          </button>
          <div className="menu" ref={menuRef}>
            <button
              type="button"
              className="btn btn-icon"
              aria-haspopup="menu"
              aria-expanded={menu}
              aria-label={t("job.menu")}
              title={t("job.menu")}
              onClick={() => setMenu((m) => !m)}
            >
              ⋯
            </button>
            {menu && (
              <div className="menu-list" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  disabled={busy}
                  onClick={() => {
                    setMenu(false);
                    setDup(true);
                  }}
                >
                  {t("job.duplicate")}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
      <ErrorBanner error={err} />
      {dup && <DuplicateDialog job={job} title={shown} onCancel={() => setDup(false)} />}
    </div>
  );
}

function DuplicateDialog({ job, title, onCancel }: { job: JobDetail; title: string; onCancel: () => void }) {
  const { t } = useI18n();
  const [name, setName] = useState(() => t("job.duplicate.default", { title }).slice(0, TITLE_MAX));
  const [keep, setKeep] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const key = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      prev?.focus?.();
    };
  }, [onCancel]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const copy = await api.duplicate(job.id, { title: name.trim() || undefined, keep_approval: keep });
      go({ name: "job", id: copy.id });
    } catch (e2) {
      setErr(e2);
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop">
      <div className="modal modal-small" role="dialog" aria-modal="true" aria-labelledby="dup-title" tabIndex={-1} ref={ref}>
        <form className="stack" onSubmit={submit}>
          <h2 id="dup-title">{t("job.duplicate.title")}</h2>
          <p>{t("job.duplicate.intro")}</p>
          <div className="field">
            <label htmlFor="dup-name">{t("job.title.label")}</label>
            <input id="dup-name" type="text" value={name} maxLength={TITLE_MAX} onChange={(e) => setName(e.target.value)} />
          </div>
          {job.approved && (
            <div className="stack-sm">
              <label className="check">
                <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
                <span>{t("job.duplicate.keep")}</span>
              </label>
              <p className="muted">{t(keep ? "job.duplicate.keep.on" : "job.duplicate.keep.off")}</p>
            </div>
          )}
          <ErrorBanner error={err} />
          <div className="actions actions-end">
            <button type="button" className="btn" onClick={onCancel}>
              {t("common.cancel")}
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {t("job.duplicate.confirm")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
