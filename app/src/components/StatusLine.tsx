import { useEffect, useState } from "react";
import { clock, useI18n } from "../i18n";
import { aboutMinutes, activity, expectedSeconds } from "../progress";
import type { Live } from "../screens/JobPage";

/** "What is happening": the current step in words, time in this step and a rough expectation. */
export function StatusLine({ live }: { live: Live }) {
  const { t } = useI18n();
  const job = live.job!;
  const { running } = live;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const text = activity(job, running, live.kind, live.stageLines, t);
  const since = Math.max(0, (now - live.stageSince) / 1000);
  const expected = running ? expectedSeconds(job.stage, job.source_duration_s) : null;
  const about = expected !== null ? aboutMinutes(expected, t) : null;

  return (
    <div className={`nowline${running ? " nowline-running" : ""}`} role="status" aria-label={t("now.title")}>
      <span className="nowline-label">{t("now.title")}</span>
      <p className={`nowline-text${running ? " working" : ""}`} data-testid="now-text">
        {text}
      </p>
      {running && (
        <p className="nowline-meta" data-testid="now-meta">
          <span>{t("now.since", { t: clock(since) })}</span>
          {about && (
            <span>{t("now.expected.video", { about, len: clock(job.source_duration_s ?? 0) })}</span>
          )}
          {expected !== null && since > expected * 1.5 && <span>{t("now.longer")}</span>}
        </p>
      )}
    </div>
  );
}
