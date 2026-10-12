"""`videocut tool job new|show|approve`: create a job folder, inspect it, approve its plan.

Layout of $VIDEOCUT_HOME/jobs/<id>/:
    job.json   id, created, source, type, brief, status (new -> ingested -> approved),
               approval {at, project_sha256}: a display copy only, never trusted
    input/     the recording (hard link or copy, never moved) and other uploads
    work/      intermediate artefacts (probe.json, audio.wav, proxy.mp4, edl.json, ...)
    out/       finished renders

The approval itself lives outside the job folder, which agent runs may write to:
$VIDEOCUT_HOME/approvals/<id>.json = {job, at, project_sha256}. Only that record, matched
against the current work/project.md, counts as approved.
"""

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .. import brand
from .common import (
    ID_RE,
    JOB_FILE,
    ToolError,
    home,
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


def approvals_root() -> Path:
    """Approval records; outside every job folder, so an agent run cannot reach them."""
    return home() / "approvals"


def approval_path(job: Path) -> Path:
    return approvals_root() / f"{job.resolve().name}.json"


def read_approval(job: Path) -> dict | None:
    """The human approval of this job folder, or None (missing, unreadable, other folder)."""
    try:
        record = json.loads(approval_path(job).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("job") != str(job.resolve()):
        return None
    return record


def is_approved(job: Path) -> bool:
    """True when a human approved work/project.md and it is unchanged since.

    Reads only the record under approvals/; status/approval in job.json are not trusted."""
    sha = project_sha(job)
    record = read_approval(job)
    return sha is not None and record is not None and record.get("project_sha256") == sha


def approve_job(job: Path) -> dict:
    sha = project_sha(job)
    if sha is None:
        raise ToolError(f"{PROJECT} is missing; run the plan step first")
    approval = {"at": datetime.now().astimezone().isoformat(timespec="seconds")}
    approval["project_sha256"] = sha
    approvals_root().mkdir(parents=True, exist_ok=True)
    write_json(approval_path(job), {"job": str(job.resolve()), **approval})
    return update_job(job, status="approved", approval=approval)


def revoke_approval(job: Path) -> None:
    approval_path(job).unlink(missing_ok=True)
    update_job(job, status="ingested", approval=None)


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


def _brand_id(ref: str) -> str:
    """Id of a brand that loads without errors; anything else fails the command. Only ids
    (`tool brand list`) are stored, since job.json keeps the id, never a folder path."""
    if ref not in brand.list_ids():
        raise ToolError(f"no brand {ref!r}; `videocut tool brand list` shows the brand ids")
    try:
        return brand.load(ref)["id"]
    except brand.BrandError as e:
        raise ToolError(str(e)) from None


def new(args) -> dict:
    video = Path(args.video).expanduser()
    if not video.is_file():
        raise ToolError(f"video not found: {args.video}")
    brand_id = _brand_id(args.brand) if args.brand else None
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
    if brand_id:
        data["brand"] = brand_id
    write_json(job / JOB_FILE, data)
    print(job)
    return {
        "job": str(job),
        "id": job_id,
        "source": data["source"],
        "imported": how,
        "brand": brand_id,
    }


def set_brand(args) -> dict:
    if os.environ.get(AGENT_ENV):
        raise ToolError("only a human picks the brand of a job (not available inside an agent run)")
    job = resolve_job(args.job)
    brand_id = _brand_id(args.brand)
    update_job(job, brand=brand_id)
    print(f"{job.name}: brand {brand_id}")
    return {"job": str(job), "id": job.name, "brand": brand_id}


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
        "approved": is_approved(job),
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
    n.add_argument("--brand", help="brand id (see `tool brand list`)")
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
    b = sub.add_parser("brand", help="set or replace the brand of a job", parents=[job_parent()])
    b.add_argument("brand", help="brand id (see `tool brand list`)")
    b.set_defaults(handler=run_tool("job brand", set_brand))
