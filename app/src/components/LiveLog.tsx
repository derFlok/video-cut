import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";

export interface LogLine {
  kind: "log" | "tool" | "error" | "stage";
  text: string;
  /** Running number of the line on this page (the status line reads the current stage's lines). */
  n?: number;
}

/** Turns a raw tool line ("Bash: videocut tool edl locate 12.4") into something readable. */
export function readableTool(text: string): string {
  const s = text.trim();
  const m = s.match(/^(Bash|Read|Write|Edit|Glob|Grep|Task)\s*[:(]\s*(.*?)\)?$/s);
  if (!m) return s;
  const [, tool, arg] = m;
  if (tool === "Bash") return `▸ ${arg.replace(/^\S*videocut\s+tool\s+/, "").slice(0, 160)}`;
  const file = arg.split("/").slice(-2).join("/");
  return `▸ ${tool} ${file}`;
}

export function LiveLog({ lines, defaultOpen = false }: { lines: LogLine[]; defaultOpen?: boolean }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) end.current?.scrollIntoView?.({ block: "nearest" });
  }, [open, lines.length]);
  return (
    <section className="livelog">
      <button type="button" className="btn btn-ghost btn-small" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? t("job.log.hide") : t("job.log.show")} ({lines.length})
      </button>
      {open && (
        <div className="livelog-body" role="log" aria-live="polite" aria-label={t("job.log")}>
          {lines.length === 0 && <p className="muted">{t("job.log.empty")}</p>}
          {lines.map((l, i) => (
            <div key={i} className={`logline logline-${l.kind}`}>
              {l.kind === "tool" ? readableTool(l.text) : l.text}
            </div>
          ))}
          <div ref={end} />
        </div>
      )}
    </section>
  );
}
