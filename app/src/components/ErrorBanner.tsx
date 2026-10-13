import { ApiError } from "../api/client";
import { useI18n, type T } from "../i18n";

/** Plain-language text for a failed request (409 busy/stale, 403 session, network). */
export function errorText(t: T, e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return t("error.network");
    if (e.status === 401 || e.status === 403) return t("error.forbidden");
    if (e.status === 409) {
      // The server says which conflict it is: busy (a run is active), stale (the plan
      // changed since it was read), or another state problem (shown with its message).
      if (e.code === "busy") return t("error.busy");
      if (e.code === "stale") return t("error.stale");
      return t("error.generic", { msg: e.message });
    }
    return t("error.generic", { msg: e.message });
  }
  return t("error.generic", { msg: e instanceof Error ? e.message : String(e) });
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useI18n();
  if (!error) return null;
  const list = error instanceof ApiError ? error.errors : [];
  return (
    <div className="banner banner-error" role="alert">
      <p>{errorText(t, error)}</p>
      {list.length > 0 && (
        <ul>
          {list.map((x) => (
            <li key={x}>
              <code>{x}</code>
            </li>
          ))}
        </ul>
      )}
      {onRetry && (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          {t("common.retry")}
        </button>
      )}
    </div>
  );
}
