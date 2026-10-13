import { useId, useState, type DragEvent, type ReactNode } from "react";
import { useI18n } from "../i18n";

export const VIDEO_EXT = [".mov", ".mp4", ".m4v", ".mkv", ".webm", ".avi"];
export const IMAGE_EXT = [".png", ".jpg", ".jpeg", ".webp"];

export function hasExt(name: string, exts: string[]): boolean {
  const n = name.toLowerCase();
  return exts.some((e) => n.endsWith(e));
}

export function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

/** A file picker that also takes drag & drop. The input stays focusable (keyboard). */
export function DropZone({
  label,
  hint,
  accept,
  multiple = false,
  required = false,
  invalid = false,
  onFiles,
  children,
}: {
  label: string;
  hint: string;
  accept: string[];
  multiple?: boolean;
  required?: boolean;
  invalid?: boolean;
  onFiles: (files: File[]) => void;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  const id = useId();
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (files.length) onFiles(multiple ? files : files.slice(0, 1));
  };
  return (
    <div
      className={`dropzone${over ? " dropzone-over" : ""}${invalid ? " dropzone-invalid" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <label htmlFor={id} className="dropzone-label">
        <span className="dropzone-title">
          {label}
          {required && <span aria-hidden="true"> *</span>}
        </span>
        <span className="dropzone-hint">{hint}</span>
        <span className="dropzone-cta">{t("new.drop")}</span>
      </label>
      <input
        id={id}
        className="sr-only"
        type="file"
        accept={accept.join(",")}
        multiple={multiple}
        aria-required={required}
        aria-invalid={invalid}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) onFiles(files);
          e.target.value = "";
        }}
      />
      {children}
    </div>
  );
}
