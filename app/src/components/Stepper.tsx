import type { Stage, Status } from "../api/types";
import { useI18n } from "../i18n";

export const STEPS = ["prep", "plan", "approval", "cut", "overlays", "verify", "done"] as const;

/** Position of a stage on the stepper; the run kinds outside the main line map onto it. */
export function stepIndex(stage: Stage): number {
  if (stage === "plan_revise") return 1;
  if (stage === "revise") return 3;
  if (stage === "error") return -1;
  return STEPS.indexOf(stage as (typeof STEPS)[number]);
}

export function Stepper({ stage, status, lastGood }: { stage: Stage; status: Status; lastGood?: Stage }) {
  const { t } = useI18n();
  const failed = status === "error" || stage === "error";
  const at = stepIndex(failed && lastGood ? lastGood : stage);
  return (
    <ol className="stepper" aria-label={t("job.title")}>
      {STEPS.map((s, i) => {
        const state =
          stage === "done" || i < at ? "done" : i === at ? (failed ? "failed" : status === "running" ? "active" : "current") : "todo";
        const label = i === at && stage === "revise" ? t("stage.revise") : t(`stage.${s}`);
        return (
          <li key={s} className={`step step-${state}`} aria-current={i === at ? "step" : undefined}>
            <span className="step-dot" aria-hidden="true">
              {state === "done" ? "✓" : state === "failed" ? "!" : i + 1}
            </span>
            <span className="step-label">{label}</span>
          </li>
        );
      })}
    </ol>
  );
}
