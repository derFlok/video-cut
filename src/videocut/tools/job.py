"""`videocut tool job new|show|approve`: create a job folder, inspect it, approve its plan.

Layout of $VIDEOCUT_HOME/jobs/<id>/:
    job.json   id, created, source, type, brief, status (new -> ingested -> approved),
               approval {at, project_sha256} once a human approved work/project.md
    input/     the recording (hard link or copy, never moved) and other uploads
    work/      intermediate artefacts (probe.json, audio.wav, proxy.mp4, edl.json, ...)
    out/       finished renders
"""

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .common import (
    ID_RE,
    JOB_FILE,
    ToolError,
    job_parent,
    jobs_root,
    read_job,
    resolve_job,
    run_tool,
    update_job,
    write_json,
)

# Known artefacts, in pipeline order; `job show` reports which exist.
ARTEFACTS = [
    "work/probe.json",
    "work/audio_raw.wav",
    "work/audio.wav",
    "work/proxy.mp4",
    "work/words.json",
    "work/takes_packed.md",
    "work/candidates.json",
    "work/project.md",
    "work/edl.json",
    "work/cut_report.md",
]
PROJECT = "work/project.md"
# Set in the environment of agent runs (agent.py): the human gate cannot be passed by Claude.
AGENT_ENV = "VIDEOCUT_AGENT"


def project_sha(job: Path) -> str | None:
    p = job / PROJECT
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def is_approved(job: Path, data: dict | None = None) -> bool:
    """True when a human approved work/project.md and it is unchanged since."""
    data = data if data is not None else read_job(job)
    sha = project_sha(job)
    return (
        data.get("status") == "approved"
        and sha is not None
        and (data.get("approval") or {}).get("project_sha256") == sha
    )


def approve_job(job: Path) -> dict:
    sha = project_sha(job)
    if sha is None:
        raise ToolError(f"{PROJECT} is missing; run the plan step first")
    approval = {"at": datetime.now().astimezone().isoformat(timespec="seconds")}
    approval["project_sha256"] = sha
    return update_job(job, status="approved", approval=approval)


def _default_id(video: Path, now: datetime) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", video.stem).strip("-").lower()[:40] or "job"
    return f"{now:%Y%m%d-%H%M%S}-{stem}"


def _link_or_copy(src: Path, dst: Path) -> str:
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:  # other volume, or a filesystem without hard links
        shutil.copy2(src, dst)
        return "copy"


def new(args) -> dict:
    video = Path(args.video).expanduser()
    if not video.is_file():
        raise ToolError(f"video not found: {args.video}")
    now = datetime.now().astimezone()
    job_id = args.id or _default_id(video, now)
    if not ID_RE.fullmatch(job_id):
        raise ToolError(f"invalid job id {job_id!r} (letters, digits, '.', '_', '-'; max 80)")
    job = jobs_root() / job_id
    if job.exists():
        raise ToolError(f"job {job_id} already exists")
    try:
        for d in ("input", "work", "out"):
            (job / d).mkdir(parents=True)
        how = _link_or_copy(video.resolve(), job / "input" / video.name)
    except OSError as e:  # e.g. disk full: leave no half-made job that blocks a retry
        shutil.rmtree(job, ignore_errors=True)
        raise ToolError(f"could not import {video.name}: {e}") from None
    data = {
        "id": job_id,
        "created": now.isoformat(timespec="seconds"),
        "source": f"input/{video.name}",
        "type": args.type,
        "brief": args.brief,
        "status": "new",
    }
    write_json(job / JOB_FILE, data)
    print(job)
    return {"job": str(job), "id": job_id, "source": data["source"], "imported": how}


def show(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    data = read_job(job)
    present = [a for a in ARTEFACTS if (job / a).is_file()]
    outputs = sorted(p.name for p in (job / "out").glob("*") if p.is_file())
    print(job)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return {
        "job": str(job),
        **data,
        "approved": is_approved(job, data),
        "artefacts": present,
        "out": outputs,
    }


def approve(args) -> dict:
    if os.environ.get(AGENT_ENV):
        raise ToolError("only a human can approve the plan (not available inside an agent run)")
    job = resolve_job(args.job_ref or args.job)
    data = approve_job(job)
    print(f"approved {PROJECT} of {data['id']}")
    return {"job": str(job), "status": data["status"], **data["approval"]}


def register(subparsers) -> None:
    p = subparsers.add_parser("job", help="create or inspect a job folder")
    sub = p.add_subparsers(dest="job_cmd", required=True)
    n = sub.add_parser("new", help="create a job from a recording")
    n.add_argument("video", help="source recording (linked or copied, never moved)")
    n.add_argument("--type", help="video type, e.g. a reel contract name")
    n.add_argument("--brief", help="free-text instruction from the user")
    n.add_argument("--id", help="job id (default: timestamp + file name)")
    n.set_defaults(handler=run_tool("job new", new))
    s = sub.add_parser(
        "show", help="print job.json and which artefacts exist", parents=[job_parent()]
    )
    s.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    s.set_defaults(handler=run_tool("job show", show))
    a = sub.add_parser(
        "approve",
        help="approve work/project.md so the cut step may start (human only)",
        parents=[job_parent()],
    )
    a.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    a.set_defaults(handler=run_tool("job approve", approve))
