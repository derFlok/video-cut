import type { Stage, Status } from "../api/types";
import { useI18n } from "../i18n";

export function StatusChip({ status, stage }: { status: Status; stage?: Stage }) {
  const { t } = useI18n();
  const label = (status === "running" || status === "waiting") && stage ? `${t(`status.${status}`)} · ${t(`stage.${stage}`)}` : t(`status.${status}`);
  return <span className={`chip chip-${status}`}>{label}</span>;
}
