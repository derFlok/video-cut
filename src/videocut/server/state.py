"""What the API reports about a job, read from its folder (and the run channel)."""

import json
import re
from pathlib import Path

from .. import redaction as red
from .. import run as pipeline
from ..tools.common import ID_RE, JOB_FILE, ToolError, jobs_root, read_job
from ..tools.job import (
    is_approved,
    job_lang,
    plan_made_for,
    plan_target_stale,
    project_sha,
    resolved_format,
    resolved_range,
    resolved_style,
)
from .runs import Channel

# GET /api/jobs/{id}/media/{name}: the only files the media route serves.
MEDIA = {
    "reel": "out/reel.mp4",
    "preview": "out/preview.mp4",
    "cover": "out/cover.jpg",
    "screen_proxy": "work/screen_proxy.mp4",
    "camera_proxy": "work/proxy.mp4",
}
HISTORY_RE = re.compile(r"history/reel-(\d{1,4})\.mp4")
STILL_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
# GET /api/jobs/{id}/stills/{path}: path relative to the job folder, under one of these.
STILL_PREFIXES = ("work/plan_stills/", "work/sheets/", "out/verify/")


class NotFound(Exception):
    pass


def job_dir(job_id: str) -> Path:
    """The job folder of an id; NotFound for anything that is not a plain job id."""
    if not ID_RE.fullmatch(job_id):
        raise NotFound(job_id)
    p = jobs_root() / job_id
    if not (p / JOB_FILE).is_file():
        raise NotFound(job_id)
    return p


def all_jobs() -> list[Path]:
    root = jobs_root()
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if ID_RE.fullmatch(p.name) and (p / JOB_FILE).is_file()]


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _jsonl(path: Path) -> list[dict]:
    out = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            v = json.loads(line)
        except ValueError:
            continue
        if isinstance(v, dict):
            out.append(v)
    return out


def plan_json(job: Path) -> dict | None:
    data = _json(job / "work" / "plan.json")
    return data if isinstance(data, dict) else None


def title(job: Path, data: dict) -> str:
    """The display title: the human's (job.json "title"), else the plan's, else the brief."""
    for t in (data.get("title"), (plan_json(job) or {}).get("title")):
        if isinstance(t, str) and t.strip():
            return t.strip()[:120]
    brief = (data.get("brief") or "").strip().splitlines()
    return brief[0][:120] if brief else job.name


def _done(job: Path, data: dict) -> bool:
    """The reel of the approved plan is there, with the brand's overlays composited onto the
    current EDL. Unlike `run.overlays_done`, a flagged verify still counts: the result page
    shows the findings and takes feedback, while /start would redo the overlay run."""
    try:
        if not pipeline.cut_done(job):
            return False
        b = data.get("brand")
        if not b or pipeline.overlays_done(job, b):
            return True
        rec = data.get("overlays")
        edl = pipeline._file_sha(job / "work" / "edl.json")
        render = _json(job / "out" / "render.json") or {}
        return (
            isinstance(rec, dict)
            and rec.get("brand") == b
            and rec.get("edl_sha256") == edl
            and bool(render.get("overlay"))
            and render.get("edl_sha256") == edl
        )
    except (ToolError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


# Status ids: new | running | waiting (at the plan gate) | done (the reel of the approved
# plan is there: the result gate, feedback possible) | error (the last run failed).
# Stage ids: prep, plan, approval, cut, overlays, verify, done, revise, plan_revise, error.


def disk_stage(job: Path, data: dict) -> tuple[str, str]:
    """(status, stage) from the files alone: where /start would continue."""
    if _done(job, data):
        return "done", "done"
    if is_approved(job):
        return "new", "cut"  # approved, cut not finished: /start continues with the cut
    if (job / "work" / "project.md").is_file():
        return "waiting", "approval"
    if (job / "work" / "takes_packed.md").is_file():
        return "new", "plan"
    return "new", "prep"


def status(job: Path, data: dict, ch: Channel | None) -> tuple[str, str]:
    st, stage = disk_stage(job, data)
    if ch is not None and ch.state != "idle":
        return "running", ch.stage or stage
    if ch is not None and ch.ok is False:
        return "error", "error"
    return st, stage


def gate(job: Path, data: dict) -> str | None:
    st, _ = disk_stage(job, data)
    return {"waiting": "plan", "done": "result"}.get(st)


def _format(data: dict) -> tuple[str, str | None]:
    try:
        resolved = resolved_format(data)
    except ToolError:
        resolved = None
    return data.get("format") or "auto", resolved


def _cut_style(data: dict) -> tuple[str, str | None]:
    """(job.json "cut_style" or "auto", the resolved style or None when invalid)."""
    try:
        resolved = resolved_style(data)
    except ToolError:
        resolved = None
    return data.get("cut_style") or "auto", resolved


def _target(job: Path, data: dict) -> dict:
    """The target length as the API shows it: target_s (null: auto), target_resolved
    ({min_s, max_s} or null: no fixed range, also for an invalid value), and whether the
    plan was made for another target (plan_target_stale, with the length it was made for)."""
    try:
        rng = resolved_range(data)
    except ToolError:
        rng = None
    target = data.get("target_s")
    stale = plan_target_stale(job, data)
    return {
        "target_s": target if isinstance(target, (int, float)) else None,
        "target_resolved": None if rng is None else {"min_s": rng[0], "max_s": rng[1]},
        "plan_target_stale": stale,
        "plan_target_s": plan_made_for(data) if stale else None,
    }


def cut_stale(job: Path) -> bool:
    """A reel of the approved plan exists but no longer matches the job (another cut style,
    format, plan or personal-data decision): the app offers to cut again (/recut)."""
    try:
        return (
            is_approved(job)
            and (job / MEDIA["reel"]).is_file()
            and isinstance(read_job(job).get("cut"), dict)
            and not pipeline.cut_done(job)
        )
    except (ToolError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def overlays_stale(job: Path) -> bool:
    """The reel's captions/overlays were made before a vocabulary was applied to the
    captions (job.json overlays.stale): the app offers to set them again (/overlays)."""
    try:
        rec = read_job(job).get("overlays")
        return (
            isinstance(rec, dict)
            and bool(rec.get("stale"))
            and is_approved(job)
            and (job / MEDIA["reel"]).is_file()
            and pipeline.cut_done(job)
        )
    except (ToolError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def transcribe_meta(job: Path) -> dict:
    meta = _json(job / "work" / "transcribe.json")
    return meta if isinstance(meta, dict) else {}


def words(job: Path) -> list[dict] | None:
    """work/words.json words, None before transcription."""
    doc = _json(job / "work" / "words.json")
    ws = doc.get("words") if isinstance(doc, dict) else None
    return ws if isinstance(ws, list) else None


def source_duration(job: Path) -> float | None:
    """Duration of the camera recording in seconds (work/probe.json), None before ingest."""
    d = (_json(job / "work" / "probe.json") or {}).get("duration")
    return float(d) if isinstance(d, (int, float)) and d > 0 else None


def summary(job: Path, ch: Channel | None) -> dict:
    data = read_job(job)
    st, stage = status(job, data, ch)
    fmt, _ = _format(data)
    cover = (job / MEDIA["cover"]).is_file()
    return {
        "id": job.name,
        "created": data.get("created"),
        "type": data.get("type"),
        "brand": data.get("brand"),
        "format": fmt,
        "cut_style": _cut_style(data)[0],
        "target_s": data.get("target_s"),
        "status": st,
        "stage": stage,
        "queued": ch is not None and ch.state == "queued",
        "gate": gate(job, data) if st != "running" else None,
        "title": title(job, data),
        "copy_of": data.get("copy_of"),
        "thumb_url": f"/api/jobs/{job.name}/media/cover" if cover else None,
    }


def redaction_view(job: Path) -> dict:
    try:
        cands = red.load(job) or []
    except red.RedactionError as e:
        return {"candidates": [], "decision": None, "error": str(e)}
    rec = red.valid_decision(job)
    decision = None
    if rec is not None:
        demo, redact = rec.get("demo") or [], rec.get("redact") or []
        mode = "demo" if not redact else "redact_all" if not demo else "redact"
        decision = {
            "mode": mode,
            "ids": redact,
            "demo": demo,
            "method": rec.get("method"),
            "at": rec.get("at"),
        }
    return {"candidates": cands, "decision": decision}


def outputs(job: Path) -> dict | None:
    have = {k: (job / v).is_file() for k, v in MEDIA.items() if k in ("reel", "preview", "cover")}
    if not any(have.values()):
        return None
    return {k: f"/api/jobs/{job.name}/media/{k}" if ok else None for k, ok in have.items()}


def detail(job: Path, ch: Channel | None) -> dict:
    data = read_job(job)
    st, stage = status(job, data, ch)
    fmt, resolved = _format(data)
    cut_style, style_resolved = _cut_style(data)
    revs, versions = revisions(job)
    verify = _json(job / "out" / "verify.json")
    runs = [
        {k: r.get(k) for k in ("phase", "wall_s", "turns", "cost_usd", "ok", "at")}
        for r in _jsonl(job / "work" / "runs.jsonl")
    ]
    return {
        "id": job.name,
        "created": data.get("created"),
        "type": data.get("type"),
        "brief": data.get("brief"),
        "brand": data.get("brand"),
        "format": fmt,
        "format_resolved": resolved,
        "cut_style": cut_style,
        "cut_style_resolved": style_resolved,
        "cut_stale": cut_stale(job),
        "overlays_stale": overlays_stale(job),
        **_target(job, data),
        "source_duration_s": source_duration(job),
        "lang": job_lang(data),
        "title": title(job, data),
        "copy_of": data.get("copy_of"),
        "status": st,
        "stage": stage,
        # when the running stage started (ISO), for "since ... in this step" after a reload
        "stage_since": ch.stage_at if st == "running" and ch.stage == stage else None,
        "queued": ch is not None and ch.state == "queued",
        "gate": gate(job, data) if st != "running" else None,
        "error": ch.error if ch is not None and ch.state == "idle" else None,
        "failed_stage": ch.failed_stage if st == "error" else None,
        "approved": is_approved(job),
        "plan_sha": project_sha(job),
        "has_screen": bool(data.get("screen")),
        "assets": data.get("assets", []),
        "redaction": redaction_view(job),
        "outputs": outputs(job),
        "verify": verify if isinstance(verify, dict) else None,
        "runs": runs,
        "revisions": revs,
        "versions": versions,
    }


def _history_url(job: Path, rel) -> str | None:
    """The media URL of a kept reel ("out/history/reel-<k>.mp4") while the file exists."""
    if not isinstance(rel, str) or not rel.startswith("out/"):
        return None
    name = rel[len("out/") :]
    if not HISTORY_RE.fullmatch(name) or not (job / rel).is_file():
        return None
    return f"/api/jobs/{job.name}/media/{name}"


def revisions(job: Path) -> tuple[list[dict], list[dict]]:
    """(revisions, versions) from work/revisions.jsonl.

    Version 1 is the reel of the approved plan; every successful revision makes the next
    version. A revision's `previous_reel` is the reel BEFORE it (kept in out/history/, the
    last five), so the reel a revision made is the next successful revision's
    `previous_reel`, or out/reel.mp4 for the latest one. A revision entry gets `version`
    (the one it made) and `video_url` while that reel is still there; `versions` lists
    every version newest first with its `video_url` (None once out/history dropped it)."""
    revs = [dict(r) for r in _jsonl(job / "work" / "revisions.jsonl")]
    good = [r for r in revs if r.get("ok") is not False and not r.get("error")]
    current = f"/api/jobs/{job.name}/media/reel" if (job / MEDIA["reel"]).is_file() else None
    versions = [
        {
            "version": 1,
            "revision": None,
            "text": None,
            "video_url": _history_url(job, good[0].get("previous_reel")) if good else current,
            "current": not good,
        }
    ]
    for i, rev in enumerate(good):
        last = i == len(good) - 1
        url = current if last else _history_url(job, good[i + 1].get("previous_reel"))
        rev["version"] = i + 2
        rev["previous_version"] = i + 1
        if url:
            rev["video_url"] = url
        versions.append(
            {
                "version": i + 2,
                "revision": rev.get("n"),
                "text": rev.get("text"),
                "video_url": url,
                "current": last,
            }
        )
    return revs, versions[::-1]


SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


def still_path(job: Path, name: str) -> Path:
    """A still image of the job: `name` is relative to the job folder and starts with one
    of STILL_PREFIXES ("work/plan_stills/view-3.jpg"); every part is a plain file name,
    the extension an image one, and the resolved file (symlinks followed) stays inside
    the prefix folder. NotFound otherwise."""
    parts = name.split("/")
    if not all(SAFE_PART.fullmatch(p) for p in parts):
        raise NotFound(name)
    if Path(parts[-1]).suffix.lower() not in STILL_EXT:
        raise NotFound(name)
    prefix = next((p for p in STILL_PREFIXES if name.startswith(p)), None)
    if prefix is None:
        raise NotFound(name)
    base = (job / prefix).resolve()
    p = (job / name).resolve()
    if not (p.is_relative_to(base) and p.is_file()):
        raise NotFound(name)
    return p


def media_path(job: Path, name: str) -> Path:
    """A fixed output (MEDIA) or a kept earlier reel (history/reel-<n>.mp4)."""
    rel = MEDIA.get(name) or (f"out/{name}" if HISTORY_RE.fullmatch(name) else None)
    if rel is None:
        raise NotFound(name)
    p = (job / rel).resolve()
    if not p.is_relative_to(job.resolve()) or not p.is_file():
        raise NotFound(name)
    return p
