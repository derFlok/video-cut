import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { PdCandidate, RedactionDecision } from "../api/types";
import { clockFine, useI18n } from "../i18n";

type Mode = RedactionDecision["mode"];

/** The decision payload: ids are always the candidates that get redacted. */
export function decision(mode: Mode, picked: Set<number>, all: PdCandidate[]): RedactionDecision {
  if (mode === "demo") return { mode, ids: [] };
  if (mode === "redact_all") return { mode, ids: all.map((c) => c.id) };
  return { mode, ids: all.map((c) => c.id).filter((id) => picked.has(id)) };
}

export function PersonalDataDialog({
  jobId,
  candidates,
  busy,
  onCancel,
  onConfirm,
}: {
  jobId: string;
  candidates: PdCandidate[];
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (d: RedactionDecision) => void;
}) {
  const { t } = useI18n();
  // Safe default: everything is redacted until the human says otherwise.
  const [mode, setMode] = useState<Mode>("redact_all");
  const [picked, setPicked] = useState<Set<number>>(() => new Set(candidates.map((c) => c.id)));
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

  const choose = (m: Mode) => {
    setMode(m);
    if (m === "redact_all") setPicked(new Set(candidates.map((c) => c.id)));
    if (m === "demo") setPicked(new Set());
  };
  const toggle = (id: number) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
    setMode(next.size === candidates.length ? "redact_all" : next.size === 0 ? "demo" : "redact");
  };
  const d = decision(mode, picked, candidates);

  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="pd-title" tabIndex={-1} ref={ref}>
        <h2 id="pd-title">{t("pd.title")}</h2>
        <p>{t("pd.intro")}</p>
        <p className="banner banner-info">{t("pd.default")}</p>
        <div className="segmented" role="radiogroup" aria-label={t("pd.title")}>
          {(["redact_all", "demo", "redact"] as Mode[]).map((m) => (
            <label key={m} className={`seg${mode === m ? " seg-on" : ""}`}>
              <input type="radio" name="pd-mode" className="sr-only" checked={mode === m} onChange={() => choose(m)} />
              <span>{t(`pd.mode.${m}`)}</span>
            </label>
          ))}
        </div>
        <ul className="pdlist">
          {candidates.map((c) => (
            <li key={c.id} className={`pdrow${picked.has(c.id) ? " pdrow-on" : ""}`}>
              <div className="pd-still">
                {c.still ? (
                  <>
                    <img src={api.stillUrl(jobId, c.still)} alt="" />
                    <span
                      className={`pd-box${picked.has(c.id) ? " pd-box-on" : ""}`}
                      style={{
                        left: `${c.box[0] * 100}%`,
                        top: `${c.box[1] * 100}%`,
                        width: `${(c.box[2] - c.box[0]) * 100}%`,
                        height: `${(c.box[3] - c.box[1]) * 100}%`,
                      }}
                    />
                  </>
                ) : (
                  <span className="thumb-empty" />
                )}
              </div>
              <div className="pd-text">
                <strong>{t(`pd.kind.${c.kind}`)}</strong>
                <span className="muted">
                  {c.asset
                    ? t("pd.where.asset", { name: c.asset.split("/").pop() ?? c.asset })
                    : c.t0 !== undefined && c.t1 !== undefined
                      ? `${clockFine(c.t0)} – ${clockFine(c.t1)}`
                      : ""}
                </span>
                <span>{c.reason}</span>
              </div>
              <label className="check">
                <input type="checkbox" checked={picked.has(c.id)} onChange={() => toggle(c.id)} />
                {t("pd.row.redact")}
              </label>
            </li>
          ))}
        </ul>
        <p className="muted">{t("pd.count", { n: d.ids.length, total: candidates.length })}</p>
        <div className="actions actions-end">
          <button type="button" className="btn" onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onConfirm(d)}>
            {t("pd.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
