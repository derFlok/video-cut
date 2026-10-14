"""`videocut tool job new|show|approve|redaction|brand|screen|format`: create a job folder,
inspect it, approve its plan, decide which personal-data candidates are demo data (human
only, src/videocut/redaction.py), set its brand, its screen recording or its output format.

Layout of $VIDEOCUT_HOME/jobs/<id>/:
    job.json   id, created, source, type, brief, status (new -> ingested -> approved),
               screen (optional second source: a screen recording, product demos),
               format (optional output format, _format.FORMATS: "9:16" | "16:9" | "1:1" |
               "4:5"; absent: 16:9 with a screen recording or screenshot assets, else 9:16,
               resolved when read, never written back),
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
from .. import redaction as red
from . import _format
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
    "work/screen_probe.json",
    "work/screen_audio.wav",
    "work/screen_proxy.mp4",
    "work/sync.json",
    "work/screen.json",
    "work/screen_text.json",
    "work/redaction.json",
    "work/words.json",
    "work/takes_packed.md",
    "work/candidates.json",
    "work/project.md",
    "work/edl.json",
    "work/cut_report.md",
]
PROJECT = "work/project.md"
FORMAT_HELP = (
    f"output format: {', '.join(_format.FORMATS)} (default: 16:9 with a screen recording or "
    "screenshots, else 9:16)"
)
# Set in the environment of agent runs (agent.py): the human gate cannot be passed by Claude.
AGENT_ENV = "VIDEOCUT_AGENT"


def project_sha(job: Path) -> str | None:
    p = job / PROJECT
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def plan_json_sha(job: Path) -> str | None:
    """SHA-256 of work/plan.json (the structured twin the app shows), None without one."""
    p = job / "work" / "plan.json"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def approved_plan_json(job: Path) -> bool:
    """work/plan.json is the one the human saw when approving (bound like project.md)."""
    rec = read_approval(job)
    sha = plan_json_sha(job)
    return rec is not None and sha is not None and rec.get("plan_json_sha256") == sha


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
    try:
        cands = red.load(job) or []  # what the human saw; stays redacted (redaction.enforced)
    except red.RedactionError as e:
        raise ToolError(f"cannot approve: {e}") from None
    approval = {"at": datetime.now().astimezone().isoformat(timespec="seconds")}
    approval["project_sha256"] = sha
    approval["plan_json_sha256"] = plan_json_sha(job)
    approvals_root().mkdir(parents=True, exist_ok=True)
    write_json(
        approval_path(job),
        {"job": str(job.resolve()), **approval, "redaction_candidates": cands},
    )
    return update_job(job, status="approved", approval=approval)


def redaction_enforced(job: Path) -> tuple[list[dict], str]:
    """The candidates render, overlays and verify must redact (redaction.enforced, with the
    candidates the approval record saw)."""
    approved = (read_approval(job) or {}).get("redaction_candidates")
    try:
        return red.enforced(job, approved if isinstance(approved, list) else None)
    except red.RedactionError as e:
        raise ToolError(str(e)) from None


def revoke_approval(job: Path) -> None:
    approval_path(job).unlink(missing_ok=True)
    update_job(job, status="ingested", approval=None)


def _default_id(video: Path, now: datetime) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", video.stem).strip("-").lower()[:40] or "job"
    return f"{now:%Y%m%d-%H%M%S}-{stem}"


ASSET_EXT = {".png", ".jpg", ".jpeg", ".webp"}
SCREEN_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".webm"}
# Made from the screen recording; a replaced recording invalidates them (a hard link keeps
# the original mtime, so freshness alone could not tell).
SCREEN_OUTPUTS = [
    "work/screen_probe.json",
    "work/screen_audio.wav",
    "work/screen_proxy.mp4",
    "work/sync.json",
    "work/screen.json",
    "work/screen_text.json",
    "work/redaction_measure.json",
]


def _link_or_copy(src: Path, dst: Path) -> str:
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:  # other volume, or a filesystem without hard links
        shutil.copy2(src, dst)
        return "copy"


def _screen_file(ref: str) -> Path:
    screen = Path(ref).expanduser()
    if not screen.is_file():
        raise ToolError(f"screen recording not found: {ref}")
    if screen.suffix.lower() not in SCREEN_EXT:
        raise ToolError(
            f"screen recording {screen.name}: must be one of {', '.join(sorted(SCREEN_EXT))}"
        )
    return screen


def brand_id(ref: str) -> str:
    """Id of a brand that loads without errors; anything else fails the command. Only ids
    (`tool brand list`) are stored, since job.json keeps the id, never a folder path."""
    if ref not in brand.list_ids():
        raise ToolError(f"no brand {ref!r}; `videocut tool brand list` shows the brand ids")
    try:
        return brand.load(ref)["id"]
    except brand.BrandError as e:
        raise ToolError(str(e)) from None


def format_arg(value: str) -> str:
    try:
        return _format.parse_format(value)
    except _format.FormatError as e:
        raise ToolError(str(e)) from None


def resolved_format(data: dict) -> str:
    """The job's output format; ToolError for an invalid job.json "format"."""
    try:
        return _format.job_format(data)
    except _format.FormatError as e:
        raise ToolError(f"job.json: {e}") from None


def new(args) -> dict:
    video = Path(args.video).expanduser()
    if not video.is_file():
        raise ToolError(f"video not found: {args.video}")
    fmt = format_arg(args.format) if args.format else None
    brand_ref = brand_id(args.brand) if args.brand else None
    assets = [Path(a).expanduser() for a in (args.asset or [])]
    screen = _screen_file(args.screen) if args.screen else None
    names = [video.name]
    if screen is not None:
        if screen.name in names:
            raise ToolError(f"camera and screen recording are both named {screen.name}; rename one")
        names.append(screen.name)
    for a in assets:
        if not a.is_file():
            raise ToolError(f"asset not found: {a}")
        if a.suffix.lower() not in ASSET_EXT:
            raise ToolError(f"asset {a.name}: must be one of {', '.join(sorted(ASSET_EXT))}")
        if a.name in names:
            raise ToolError(f"two inputs are named {a.name}; rename one")
        names.append(a.name)
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
        for a in assets:
            _link_or_copy(a.resolve(), job / "input" / a.name)
        if screen is not None:
            _link_or_copy(screen.resolve(), job / "input" / screen.name)
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
    if brand_ref:
        data["brand"] = brand_ref
    if assets:
        data["assets"] = [f"input/{a.name}" for a in assets]
    if screen is not None:
        data["screen"] = f"input/{screen.name}"
    if fmt:
        data["format"] = fmt
    write_json(job / JOB_FILE, data)
    print(job)
    return {
        "job": str(job),
        "id": job_id,
        "source": data["source"],
        "imported": how,
        "brand": brand_ref,
        "assets": data.get("assets", []),
        "screen": data.get("screen"),
        "format": resolved_format(data),
        "format_default": fmt is None,
    }


def set_screen(args) -> dict:
    """Set or replace the screen recording; outputs made from the old one are removed."""
    if os.environ.get(AGENT_ENV):
        raise ToolError(
            "only a human picks the inputs of a job (not available inside an agent run)"
        )
    job = resolve_job(args.job)
    screen = _screen_file(args.file)
    data = read_job(job)
    rel = f"input/{screen.name}"
    taken = {data.get("source"), *data.get("assets", [])}
    if rel in taken:
        raise ToolError(
            f"{screen.name} is already an input of this job under another role; rename it"
        )
    old = data.get("screen")
    dst = job / rel
    try:
        if dst.exists() and dst.resolve() == screen.resolve():
            how = "in place"  # the file already sits in input/: never unlink the only copy
        else:
            dst.unlink(missing_ok=True)
            how = _link_or_copy(screen.resolve(), dst)
    except OSError as e:
        raise ToolError(f"could not import {screen.name}: {e}") from None
    if old and old != rel and old not in taken:
        (job / old).unlink(missing_ok=True)
    for out in SCREEN_OUTPUTS:
        (job / out).unlink(missing_ok=True)
    update_job(job, screen=rel)
    print(f"{job.name}: screen {rel}")
    return {"job": str(job), "id": job.name, "screen": rel, "imported": how, "replaced": old}


def set_brand(args) -> dict:
    if os.environ.get(AGENT_ENV):
        raise ToolError("only a human picks the brand of a job (not available inside an agent run)")
    job = resolve_job(args.job)
    brand_ref = brand_id(args.brand)
    update_job(job, brand=brand_ref)
    print(f"{job.name}: brand {brand_ref}")
    return {"job": str(job), "id": job.name, "brand": brand_ref}


def set_format(job: Path, value: str, say=print) -> dict:
    """Set or replace the output format. A plan or cut made before was made for the old one:
    the cut goes stale (run.cut_done compares the format of the cut record), and the hint
    says how to redo it. Approval stays (the plan text did not change)."""
    fmt = format_arg(value)
    data = read_job(job)
    try:
        old = resolved_format(data)
    except ToolError:  # an invalid value is what this command repairs
        old = None
    update_job(job, format=fmt)
    w, h = _format.FORMATS[fmt]
    say(f"{job.name}: format {fmt} ({w}x{h})")
    redo = fmt != old and (job / PROJECT).is_file()
    if redo:
        say(
            f"The plan (and any cut) of this job was made for {old or 'another format'}. "
            f"Cut again for {fmt}: "
            f"`videocut run {job.name}` (the cut is redone); plan again if the layouts "
            f"should change: `videocut run {job.name} --replan`"
        )
    return {"job": str(job), "id": job.name, "format": fmt, "size": [w, h], "previous": old,
            "redo": redo}  # fmt: skip


def format_cmd(args) -> dict:
    if os.environ.get(AGENT_ENV):
        raise ToolError("only a human picks the output format (not available inside an agent run)")
    return set_format(resolve_job(args.job_ref or args.job), args.format)


def show(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    data = read_job(job)
    present = [a for a in ARTEFACTS if (job / a).is_file()]
    outputs = sorted(p.name for p in (job / "out").glob("*") if p.is_file())
    fmt = resolved_format(data)
    w, h = _format.FORMATS[fmt]
    print(job)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    how = "set" if "format" in data else "default"
    print(f"format: {fmt} ({w}x{h}, {how})")
    out = {
        "job": str(job),
        **data,
        "format": fmt,
        "format_default": "format" not in data,
        "size": [w, h],
        "approved": is_approved(job),
        "artefacts": present,
        "out": outputs,
    }
    personal = red.summary(job)
    if personal is not None:
        out["redaction"] = personal
    return out


def decide_redaction(args) -> dict:
    """The human decision about the personal-data candidates of work/redaction.json."""
    if os.environ.get(AGENT_ENV):
        raise ToolError(
            "only a human decides what is demo data (not available inside an agent run)"
        )
    job = resolve_job(args.job_ref or args.job)
    if args.all_demo:
        ids, what = [], "all demo data, nothing redacted"
    elif args.redact_all:
        ids, what = None, "everything redacted"
    else:
        try:
            ids = [int(x) for x in args.redact.split(",") if x.strip()]
        except ValueError:
            raise ToolError("--redact takes numbers like 1,3") from None
        what = f"redact {ids}, the rest is demo data"
    try:
        rec = red.decide(job, ids, "command")
    except red.RedactionError as e:
        raise ToolError(str(e)) from None
    print(f"{job.name}: {what}")
    return {"job": str(job), "redact": rec["redact"], "demo": rec["demo"], "method": "command"}


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
    n.add_argument(
        "--asset",
        action="extend",
        nargs="+",
        metavar="FILE",
        help="extra inputs such as screenshots (png/jpg/webp): several files, repeatable",
    )
    n.add_argument(
        "--screen",
        metavar="FILE",
        help="screen recording of the same take (product demo), synced by `tool sync`",
    )
    n.add_argument("--format", help=FORMAT_HELP)
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
    r = sub.add_parser(
        "redaction",
        help="decide which personal-data candidates are demo data (human only)",
        parents=[job_parent()],
    )
    r.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--all-demo", action="store_true", help="all candidates are demo data")
    g.add_argument("--redact-all", action="store_true", help="redact every candidate")
    g.add_argument("--redact", metavar="IDS", help="redact these numbers (e.g. 1,3), rest demo")
    r.set_defaults(handler=run_tool("job redaction", decide_redaction))
    b = sub.add_parser("brand", help="set or replace the brand of a job", parents=[job_parent()])
    b.add_argument("brand", help="brand id (see `tool brand list`)")
    b.set_defaults(handler=run_tool("job brand", set_brand))
    sc = sub.add_parser(
        "screen",
        help="set or replace the screen recording of a job (human only)",
        parents=[job_parent()],
    )
    sc.add_argument("file", help="screen recording (linked or copied, never moved)")
    sc.set_defaults(handler=run_tool("job screen", set_screen))
    f = sub.add_parser(
        "format",
        help="set or replace the output format of a job (human only)",
        parents=[job_parent()],
    )
    f.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    f.add_argument("format", help=FORMAT_HELP)
    f.set_defaults(handler=run_tool("job format", format_cmd))
