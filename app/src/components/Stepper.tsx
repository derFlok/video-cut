import type { Stage, Status } from "../api/types";
import { useI18n } from "../i18n";

export const STEPS = ["prep", "plan", "approval", "cut", "overlays", "verify", "done"] as const;
/** With material (stock footage or AI images) a step between approval and cut. */
export const MATERIAL_STEPS = ["prep", "plan", "approval", "material", "cut", "overlays", "verify", "done"] as const;

/** Position of a stage on the stepper; the run kinds outside the main line map onto it. */
export function stepIndex(stage: Stage, material = false): number {
  const steps: readonly string[] = material ? MATERIAL_STEPS : STEPS;
  if (stage === "plan_revise") return 1;
  if (stage === "revise") return steps.indexOf("cut");
  if (stage === "error") return -1;
  if (stage === "material" && !material) return steps.indexOf("cut");
  return steps.indexOf(stage);
}

export function Stepper({
  stage,
  status,
  lastGood,
  material = false,
}: {
  stage: Stage;
  status: Status;
  lastGood?: Stage;
  /** The job has material: show the material step. */
  material?: boolean;
}) {
  const { t } = useI18n();
  const failed = status === "error" || stage === "error";
  const at = stepIndex(failed && lastGood ? lastGood : stage, material);
  const steps: readonly Stage[] = material ? MATERIAL_STEPS : STEPS;
  return (
    <ol className="stepper" aria-label={t("job.title")}>
      {steps.map((s, i) => {
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
