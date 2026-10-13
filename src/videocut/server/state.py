"""What the API reports about a job, read from its folder (and the run channel)."""

import json
import re
from pathlib import Path

from .. import redaction as red
from .. import run as pipeline
from ..tools.common import ID_RE, JOB_FILE, ToolError, jobs_root, read_job
from ..tools.job import is_approved, project_sha, resolved_format
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
    t = (plan_json(job) or {}).get("title")
    if isinstance(t, str) and t.strip():
        return t.strip()[:120]
    brief = (data.get("brief") or "").strip().splitlines()
    return brief[0][:120] if brief else job.name


def _done(job: Path, data: dict) -> bool:
    try:
        if not pipeline.cut_done(job):
            return False
        b = data.get("brand")
        return not b or pipeline.overlays_done(job, b)
    except (ToolError, OSError, ValueError, KeyError, TypeError):
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
        "status": st,
        "stage": stage,
        "queued": ch is not None and ch.state == "queued",
        "gate": gate(job, data) if st != "running" else None,
        "title": title(job, data),
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
        "title": title(job, data),
        "status": st,
        "stage": stage,
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
        "revisions": revisions(job),
    }


def revisions(job: Path) -> list[dict]:
    """work/revisions.jsonl; an entry gets `video_url` while the reel it replaced is kept
    in out/history/ (the revise step keeps the last five)."""
    out = []
    for n, rev in enumerate(_jsonl(job / "work" / "revisions.jsonl"), 1):
        rev = dict(rev)
        num = rev.get("n") if isinstance(rev.get("n"), int) else n
        if (job / "out" / "history" / f"reel-{num}.mp4").is_file():
            rev["video_url"] = f"/api/jobs/{job.name}/media/history/reel-{num}.mp4"
        out.append(rev)
    return out


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
