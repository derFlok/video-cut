import { REDACT_STYLES, type RedactStyle } from "../api/types";
import { useI18n } from "../i18n";

/** "Hide as": how personal data on the screen is hidden, with a one-line explanation. */
export function RedactStyleSelect({
  id,
  value,
  disabled,
  onChange,
}: {
  id: string;
  value: RedactStyle;
  disabled?: boolean;
  onChange: (v: RedactStyle) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="field stack-sm">
      <label htmlFor={id}>{t("redact.label")}</label>
      <select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as RedactStyle)}>
        {REDACT_STYLES.map((s) => (
          <option key={s} value={s}>
            {t(`redact.${s}`)}
          </option>
        ))}
      </select>
      <p className="hint" data-testid="redact-desc">
        {t(`redact.${value}.desc`)}
      </p>
    </div>
  );
}
