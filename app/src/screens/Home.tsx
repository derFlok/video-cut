import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { JobSummary, VideoType } from "../api/types";
import { AspectIcon } from "../components/AspectIcon";
import { ErrorBanner } from "../components/ErrorBanner";
import { StatusChip } from "../components/StatusChip";
import { date, useI18n } from "../i18n";
import { href } from "../router";

export function Home() {
  const { t, lang } = useI18n();
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [types, setTypes] = useState<VideoType[]>([]);
  const [err, setErr] = useState<unknown>(null);

  const load = () => {
    setErr(null);
    api.jobs().then(setJobs).catch(setErr);
    api.types().then(setTypes).catch(() => setTypes([]));
  };
  useEffect(() => {
    load();
    // Running jobs move on by themselves; refresh the list now and then.
    const timer = setInterval(() => api.jobs().then(setJobs).catch(() => undefined), 5000);
    return () => clearInterval(timer);
  }, []);

  const typeName = (id: string) => {
    const ty = types.find((x) => x.id === id);
    return ty ? (lang === "de" ? ty.name_de : ty.name_en) : id;
  };

  return (
    <div className="stack-lg">
      <div className="titlebar">
        <h1>{t("home.title")}</h1>
        <a className="btn btn-primary" href={href({ name: "new" })}>
          + {t("home.new")}
        </a>
      </div>
      <ErrorBanner error={err} onRetry={load} />
      {!jobs && !err && <p className="muted">{t("common.loading")}</p>}
      {jobs && jobs.length === 0 && <p className="empty">{t("home.empty")}</p>}
      {jobs && jobs.length > 0 && (
        <ul className="joblist">
          {jobs.map((j) => (
            <li key={j.id}>
              <a className="jobcard" href={href({ name: "job", id: j.id })}>
                <span className="jobcard-thumb">
                  {j.thumb_url ? <img src={api.url(j.thumb_url)} alt="" loading="lazy" /> : <span className="thumb-empty" />}
                </span>
                <span className="jobcard-body">
                  <span className="jobcard-title">
                    {j.title || t("home.untitled")}
                    {j.copy_of && <span className="badge badge-copy">{t("job.copy.badge")}</span>}
                  </span>
                  <span className="jobcard-meta">
                    <span>{typeName(j.type)}</span>
                    {j.format && j.format !== "auto" && (
                      <span className="meta-format">
                        <AspectIcon format={j.format} size={14} /> {j.format}
                      </span>
                    )}
                    <span className="muted">{t("home.created", { date: date(j.created, lang) })}</span>
                  </span>
                </span>
                <StatusChip status={j.status} stage={j.stage} />
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
