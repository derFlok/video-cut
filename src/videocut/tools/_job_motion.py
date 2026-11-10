"""Motion of a job (human setting): `videocut tool job motion`.

job.json "motion" = {"mode": "off" | "auto" | "wish", "wish": str}; absent = off. `auto`: the
plan proposes motion moments (animated scenes in a band above the speaker, tools/motion.py)
where they help; `wish`: the same, following the human's free-text request (≤ WISH_MAX
characters; untrusted as an instruction, it says what to show). `off`: no motion moments.

Like slides and material, the plan depends on this setting: a plan run records what it was
made for (job.json "plan_motion" {mode, wish}); changing it afterwards makes the plan stale
(`run` plans again) and revokes an approval. Human only: refused inside an agent run.
"""

from pathlib import Path

from ._job_material import human_only
from .common import JOB_FILE, ToolError, read_job, resolve_job, run_tool, write_json

MODES = ("off", "auto", "wish")
WISH_MAX = 2000
MODE_HELP = "off (no motion scenes), auto (Claude proposes them), wish (following --wish)"


def parse_mode(value) -> str:
    v = str(value or "").strip().lower()
    if v not in MODES:
        raise ToolError(f"unknown motion mode {value!r}; one of: {', '.join(MODES)}")
    return v


def parse_wish(value) -> str:
    wish = " ".join(str(value or "").split())
    if len(wish) > WISH_MAX:
        raise ToolError(f"the motion wish is {len(wish)} characters long; at most {WISH_MAX}")
    return wish


def stored(data: dict) -> dict:
    """job.json "motion" as {mode, wish}; absent or invalid: off."""
    raw = data.get("motion")
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in MODES else "off"
    wish = raw.get("wish") if isinstance(raw.get("wish"), str) else ""
    return {"mode": mode, "wish": wish}


def view(job: Path, data: dict | None = None) -> dict:
    """For `job show`, the app and the skills."""
    data = read_job(job) if data is None else data
    return {**stored(data), "stale": plan_stale(job, data)}


def plan_stale(job: Path, data: dict | None = None) -> bool:
    """The plan was made for another motion mode or wish (no record: made for the current)."""
    if not (job / "work" / "project.md").is_file():
        return False
    data = read_job(job) if data is None else data
    rec = data.get("plan_motion")
    return isinstance(rec, dict) and rec != stored(data)


def stamp_plan(job: Path, made_for: dict | None = None) -> None:
    """Record the motion setting the current plan was made for (`run`, after a plan run)."""
    data = read_job(job)
    data["plan_motion"] = made_for if made_for is not None else stored(data)
    write_json(job / JOB_FILE, data)


def set_motion(job: Path, mode=None, wish=None, say=print) -> dict:
    """Set the mode and/or the wish (None: keep). A wish alone switches `off` to `wish`."""
    from .job import read_approval, revoke_approval

    data = read_job(job)
    if (job / "work" / "project.md").is_file() and not isinstance(data.get("plan_motion"), dict):
        data["plan_motion"] = stored(data)
    cur = stored(data)
    new = {
        "mode": cur["mode"] if mode is None else parse_mode(mode),
        "wish": cur["wish"] if wish is None else parse_wish(wish),
    }
    if mode is None and new["wish"] and new["mode"] == "off":
        new["mode"] = "wish"
    if new["mode"] == "off" and not new["wish"]:
        data.pop("motion", None)
    else:
        data["motion"] = new
    write_json(job / JOB_FILE, data)
    say(f"{job.name}: motion {new['mode']}" + (f", wish: {new['wish']}" if new["wish"] else ""))
    stale = plan_stale(job)
    revoked = False
    if stale and read_approval(job):
        revoke_approval(job)
        revoked = True
    if stale:
        say(
            f"The plan of this job was made for another motion setting. Plan again and approve "
            f"the new plan: `videocut run {job.name} --replan`"
            + (" (the earlier approval no longer counts)" if revoked else "")
        )
    return {"job": str(job), "id": job.name, **view(job), "plan_stale": stale,
            "approval_revoked": revoked}  # fmt: skip


def motion_cmd(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    if args.mode is None and args.wish is None:
        v = view(job)
        print(f"{job.name}: motion {v['mode']}" + (f", wish: {v['wish']}" if v["wish"] else ""))
        return {"job": str(job), "id": job.name, **v}
    human_only("sets the motion of a job")
    return set_motion(job, args.mode, args.wish)


def register(sub, job_parent) -> None:
    s = sub.add_parser(
        "motion",
        help="show or set the motion scenes of a job: mode and wish (human only)",
        parents=[job_parent()],
    )
    s.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    s.add_argument("--mode", help=MODE_HELP)
    s.add_argument("--wish", help=f"what the motion scenes should show, free text (≤ {WISH_MAX})")
    s.set_defaults(handler=run_tool("job motion", motion_cmd))
