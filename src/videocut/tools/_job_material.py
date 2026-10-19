"""Material of a job (human settings): `videocut tool job material`.

job.json "material" = {"mode": "none" | "stock" | "generated" | "both", "wish": str}; absent
= none. `stock`: the plan proposes material wishes that `videocut tool material fetch`
searches in the stock libraries (Pixabay, Pexels, Coverr); `generated`: an image generator
makes stills for them (settings "image_provider"); `both`: either. The wish (≤ WISH_MAX
characters) is the human's free-text request ("calm office pictures, no people").

Like the slides, the plan depends on these settings: a plan run records what it was made
for (job.json "plan_material" {mode, wish}); changing them afterwards makes the plan stale
(`run` plans again) and revokes an approval. Human only: refused inside an agent run.
"""

import os
from pathlib import Path

from .common import JOB_FILE, ToolError, read_job, resolve_job, run_tool, write_json

MODES = ("none", "stock", "generated", "both")
WISH_MAX = 2000
AGENT_ENV = "VIDEOCUT_AGENT"
MODE_HELP = (
    "none (no material), stock (stock video and photos), generated (AI-generated stills), both"
)


def human_only(what: str) -> None:
    if os.environ.get(AGENT_ENV):
        raise ToolError(f"only a human {what} (not available inside an agent run)")


def parse_mode(value) -> str:
    v = str(value or "").strip().lower()
    if v not in MODES:
        raise ToolError(f"unknown material mode {value!r}; one of: {', '.join(MODES)}")
    return v


def parse_wish(value) -> str:
    wish = " ".join(str(value or "").split())
    if len(wish) > WISH_MAX:
        raise ToolError(f"the material wish is {len(wish)} characters long; at most {WISH_MAX}")
    return wish


def stored(data: dict) -> dict:
    """job.json "material" as {mode, wish}; absent or invalid: none."""
    raw = data.get("material")
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in MODES else "none"
    wish = raw.get("wish") if isinstance(raw.get("wish"), str) else ""
    return {"mode": mode, "wish": wish}


def view(job: Path, data: dict | None = None) -> dict:
    """For `job show`, the app and the skills."""
    data = read_job(job) if data is None else data
    return {**stored(data), "stale": plan_stale(job, data)}


def plan_stale(job: Path, data: dict | None = None) -> bool:
    """The plan was made for another material mode or wish. A plan without a record counts
    as made for the current settings (set_material records the old ones first)."""
    if not (job / "work" / "project.md").is_file():
        return False
    data = read_job(job) if data is None else data
    rec = data.get("plan_material")
    return isinstance(rec, dict) and rec != stored(data)


def stamp_plan(job: Path, made_for: dict | None = None) -> None:
    """Record the material settings the current plan was made for (`run`, after a plan run,
    with the settings taken when the run started)."""
    data = read_job(job)
    data["plan_material"] = made_for if made_for is not None else stored(data)
    write_json(job / JOB_FILE, data)


def set_material(job: Path, mode=None, wish=None, say=print) -> dict:
    """Set the mode and/or the wish (None: keep)."""
    from .job import read_approval, revoke_approval

    data = read_job(job)
    if (job / "work" / "project.md").is_file() and not isinstance(data.get("plan_material"), dict):
        data["plan_material"] = stored(data)
    cur = stored(data)
    new = {
        "mode": cur["mode"] if mode is None else parse_mode(mode),
        "wish": cur["wish"] if wish is None else parse_wish(wish),
    }
    if new["mode"] == "none" and not new["wish"]:
        data.pop("material", None)
    else:
        data["material"] = new
    write_json(job / JOB_FILE, data)
    say(f"{job.name}: material {new['mode']}" + (f", wish: {new['wish']}" if new["wish"] else ""))
    stale = plan_stale(job)
    revoked = False
    if stale and read_approval(job):
        revoke_approval(job)
        revoked = True
    if stale:
        say(
            f"The plan of this job was made for other material. Plan again and approve the "
            f"new plan: `videocut run {job.name} --replan`"
            + (" (the earlier approval no longer counts)" if revoked else "")
        )
    return {"job": str(job), "id": job.name, **view(job), "plan_stale": stale,
            "approval_revoked": revoked}  # fmt: skip


def material_cmd(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    if args.mode is None and args.wish is None:
        v = view(job)
        print(f"{job.name}: material {v['mode']}" + (f", wish: {v['wish']}" if v["wish"] else ""))
        return {"job": str(job), "id": job.name, **v}
    human_only("sets the material of a job")
    return set_material(job, args.mode, args.wish)


def register(sub, job_parent) -> None:
    s = sub.add_parser(
        "material",
        help="show or set the material of a job: mode and wish (human only)",
        parents=[job_parent()],
    )
    s.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    s.add_argument("--mode", help=MODE_HELP)
    s.add_argument("--wish", help=f"what the material should show, free text (≤ {WISH_MAX})")
    s.set_defaults(handler=run_tool("job material", material_cmd))
