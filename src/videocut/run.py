"""`videocut run <video|job>`: the whole Phase 1 pipeline from the command line.

    prep     job new (when a file is given), then the deterministic tools: ingest --denoise,
             transcribe (also writes candidates + takes_packed.md), sheet contact
    plan     Claude run with the `plan` skill -> work/project.md, nothing is cut
    approve  the human reads work/project.md and approves it (prompt; `--approve` skips it)
    cut      Claude run with the `cut` skill (resumes the plan session when the plan is
             unchanged) -> work/edl.json, out/reel.mp4, out/verify.json, work/cut_report.md

Re-running is cheap: prep tools keep fresh outputs, an existing plan is offered for approval
again instead of being re-planned (`--replan` forces a new one), and an approved plan goes
straight to the cut. Every Claude run appends one line to work/runs.jsonl (wall time, turns,
cost, tokens), and its raw event stream goes to work/claude-<phase>.jsonl.
"""

import contextlib
import functools
import io
import json
import re
import shutil
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import agent
from .paths import ROOT
from .tools.common import ToolError, read_job, resolve_job, update_job
from .tools.job import approve_job, is_approved, project_sha

TYPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
PREP = [["ingest", "--denoise"], ["transcribe"], ["sheet", "contact"]]
TEMPLATES = ROOT / "templates"
PLAN_PROMPT = (
    "Use the video-cut:plan skill to plan the edit of the Video Cut job in the current folder. "
    "Write work/project.md and stop; do not cut."
)
CUT_PROMPT = (
    "The human approved work/project.md. Use the video-cut:cut skill to cut the Video Cut job "
    "in the current folder."
)


class RunError(RuntimeError):
    pass


def tool(job: Path, *argv: str) -> dict:
    """Run `videocut tool <argv> --job <job>` in-process; return its summary line."""
    from .cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = _main(main, ["tool", *argv, "--job", str(job)])
    lines = buf.getvalue().strip().splitlines()
    try:
        summary = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        summary = {}
    if code != 0 or not summary.get("ok", False):
        raise RunError(f"videocut tool {' '.join(argv)} failed: {summary.get('error', code)}")
    return summary


def _main(main, argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as e:  # argparse rejected the arguments
        return e.code if isinstance(e.code, int) else 2


def new_job(video: Path, type_: str | None, brief: str | None) -> Path:
    from .cli import main

    buf = io.StringIO()
    argv = ["tool", "job", "new", str(video)]
    argv += ["--type", type_] if type_ else []
    argv += ["--brief", brief] if brief else []
    with contextlib.redirect_stdout(buf):
        code = _main(main, argv)
    lines = buf.getvalue().strip().splitlines()
    s = json.loads(lines[-1]) if code == 0 and lines else {}
    if code != 0 or "job" not in s:
        raise RunError(f"could not create a job from {video}")
    return Path(s["job"])


def open_job(ref: str, type_: str | None, brief: str | None) -> Path:
    p = Path(ref).expanduser()
    if p.is_file():
        return new_job(p, type_, brief)
    try:
        job = resolve_job(ref)
    except ToolError as e:
        raise RunError(str(e)) from None
    data = read_job(job)
    changes = {k: v for k, v in (("type", type_), ("brief", brief)) if v and v != data.get(k)}
    if changes:
        if is_approved(job, data):  # the approved plan was made for the old type/brief
            changes.update(status="ingested", approval=None)
        update_job(job, **changes)
    return job


def prep(job: Path, steps: list[list[str]] | None = None, say=print) -> None:
    for argv in steps if steps is not None else PREP:
        t0 = time.monotonic()
        s = tool(job, *argv)
        extra = {k: s[k] for k in ("sheets", "words", "transcribed", "denoised") if k in s}
        say(f"  {' '.join(argv):<16} {time.monotonic() - t0:5.1f}s  {extra or ''}".rstrip())
    # Claude may only read inside the job folder, so the reel contract is copied in.
    type_, dst = read_job(job).get("type") or "", job / "work" / "contract.md"
    contract = TEMPLATES / f"{type_}.md"
    if TYPE_RE.fullmatch(type_) and contract.is_file():
        shutil.copyfile(contract, dst)
    else:
        dst.unlink(missing_ok=True)  # never a stale contract of another type


def log_run(job: Path, phase: str, res: agent.Result, wall: float, resumed: bool) -> dict:
    entry = {
        "phase": phase,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ok": res.ok,
        "wall_s": round(wall, 1),
        "turns": res.turns,
        "cost_usd": res.cost_usd,
        "usage": res.usage,
        "duration_ms": res.duration_ms,
        "session_id": res.session_id,
        "resumed": resumed,
        "auth": res.auth,
    }
    with (job / "work" / "runs.jsonl").open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def claude(job: Path, phase: str, prompt: str, max_turns: int, resume: str | None, say=print):
    def show(ev: agent.Event) -> None:
        if ev.kind in ("start", "tool"):
            say(f"  · {ev.text[:120]}")

    say(f"{phase}: Claude is working" + (" (resuming the plan session)" if resume else ""))
    t0 = time.monotonic()
    res = agent.run(
        prompt,
        job,
        show,
        resume=resume,
        log=job / "work" / f"claude-{phase}.jsonl",
        max_turns=max_turns,
    )
    entry = log_run(job, phase, res, time.monotonic() - t0, resume is not None)
    tokens = _tokens(res.usage)
    say(
        f"{phase}: {'done' if res.ok else 'FAILED'} in {entry['wall_s']}s, {res.turns} turns"
        + (f", ${res.cost_usd:.2f}" if res.cost_usd is not None else "")
        + (f", {tokens} tokens" if tokens else "")
    )
    if not res.ok:
        raise RunError(f"{phase} run failed: {res.text[-500:]}")
    return res


def _tokens(usage: dict | None) -> int | None:
    if not usage:
        return None
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    return sum(int(usage.get(k) or 0) for k in keys)


def _mtime(p: Path) -> float | None:
    return p.stat().st_mtime if p.is_file() else None


def _snapshot(folder: Path) -> dict:
    return {p: p.stat().st_mtime for p in folder.rglob("*") if p.is_file()}


def ask_approval(job: Path, ask: Callable[[str], str] = input) -> bool:
    if not sys.stdin.isatty() and ask is input:
        return False
    answer = ask("Approve this plan and start cutting? Type 'yes' (anything else stops): ")
    return answer.strip().lower() in ("y", "yes", "j", "ja")


def run(
    ref: str,
    type_: str | None = None,
    brief: str | None = None,
    approve: bool = False,
    max_turns: int = 60,
    replan: bool = False,
    say=print,
    ask: Callable[[str], str] = input,
) -> int:
    if not agent.shutil.which("claude"):
        raise RunError("Claude Code is not installed (see docs/AUTH.md).")
    job = open_job(ref, type_, brief)
    say(f"job: {job}")
    project, edl = job / "work" / "project.md", job / "work" / "edl.json"

    say("prep:")
    prep(job, say=say)

    plan_session, plan_sha = None, None
    if is_approved(job) and not replan:
        say("plan: already approved")
    else:
        if replan or not project.is_file():
            edl_before, out_before = _mtime(edl), _snapshot(job / "out")
            res = claude(job, "plan", PLAN_PROMPT, max_turns, None, say)
            if not project.is_file():
                raise RunError("the plan run did not write work/project.md")
            if _mtime(edl) != edl_before or _snapshot(job / "out") != out_before:
                raise RunError("the plan run wrote an EDL or rendered; it must only plan")
            plan_session, plan_sha = res.session_id, project_sha(job)
        else:
            say("plan: using the existing work/project.md (--replan makes a new one)")
        say(f"\nPlan: {project}\nRead it (edit it if you like) before approving.")
        if not approve and not ask_approval(job, ask):
            later = f"videocut tool job approve {job.name} && videocut run {job.name}"
            say(f"Not approved. Later: {later}")
            return 3
        approve_job(job)
        say("plan: approved")

    resume = plan_session if plan_session and project_sha(job) == plan_sha else None
    approved_sha = read_job(job)["approval"]["project_sha256"]
    started = time.time()
    claude(job, "cut", CUT_PROMPT, max_turns, resume, say)
    if project_sha(job) != approved_sha:
        raise RunError("work/project.md changed during the cut run; approve it again")
    reel, vfile = job / "out" / "reel.mp4", job / "out" / "verify.json"
    if not ((_mtime(reel) or 0) >= started and (_mtime(vfile) or 0) >= started):
        raise RunError("the cut run did not finish with a final render and verify")
    verify = json.loads(vfile.read_text())
    if verify.get("preview") or verify.get("reel") != "out/reel.mp4":
        raise RunError("the last verify checked a preview, not out/reel.mp4")
    say("\nout/:")
    for p in sorted((job / "out").iterdir()):
        if p.is_file():
            say(f"  {p}")
    state = "ok" if verify.get("ok") else "NOT ok"
    say(f"verify: {state}; report: {job / 'work' / 'cut_report.md'}")
    return 0 if verify.get("ok") else 4


def main(args) -> int:
    try:
        return run(
            args.target,
            type_=args.type,
            brief=args.brief,
            approve=args.approve,
            max_turns=args.max_turns,
            replan=args.replan,
            say=functools.partial(print, flush=True),  # progress also when piped to a log
        )
    except (RunError, agent.AgentError, ToolError) as e:
        print(f"\033[31m✗ {e}\033[0m", file=sys.stderr)
        return 1
