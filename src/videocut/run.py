"""`videocut run <video|job>`: the whole pipeline from the command line.

    prep     job new (when a file is given), then the deterministic tools: ingest --denoise,
             face (face track + blinks), transcribe (also writes candidates +
             takes_packed.md), sheet contact. A product demo (`--screen <recording>`, or
             job.json "screen") runs ingest (camera + screen) -> sync -> screen -> face ->
             transcribe -> sheet contact + sheet screen, and stops with a hint for
             `tool sync --offset` when the recordings cannot be synced reliably
    plan     Claude run with the `plan` skill -> work/project.md, nothing is cut
    approve  the human reads work/project.md and approves it (prompt; `--approve` skips it)
    cut      Claude run with the `cut` skill (resumes the plan session when the plan is
             unchanged) -> work/edl.json, out/reel.mp4, out/verify.json, work/cut_report.md
    overlays Claude run with the `overlays` skill when the job has a brand (`--brand`):
             captions + overlay scenes in the brand's look on the approved cut -> the same
             outputs plus work/overlays.json, work/overlay.mov, work/overlay_report.md.
             No second approval, but it only ever runs on an approved, finished cut, and
             it must not change the cut (the EDL segments)

Re-running is cheap: prep tools keep fresh outputs, an existing plan is offered for approval
again instead of being re-planned (`--replan` forces a new one), an approved plan goes
straight to the cut, and a finished cut of the approved plan is kept (`--recut` cuts again;
job.json "cut" records which plan and segments it was). The overlay stage runs when
`--brand` is given or when the brand's overlays are not done for the current cut. Every
Claude run appends one line to work/runs.jsonl (wall time, turns, cost, tokens), and its raw
event stream goes to work/claude-<phase>.jsonl.
"""

import contextlib
import functools
import hashlib
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
from . import redaction as red
from .paths import ROOT
from .tools.common import ToolError, read_job, resolve_job, update_job
from .tools.job import (
    approve_job,
    brand_id,
    is_approved,
    project_sha,
    read_approval,
    redaction_enforced,
    revoke_approval,
)

TYPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
PREP = [["ingest", "--denoise"], ["face"], ["transcribe"], ["sheet", "contact"]]
# Product demo (job.json "screen"): ingest also prepares the screen recording, sync aligns it
# with the camera, screen finds activity, clicks and zoom targets, and one overview sheet of
# the screen joins the contact sheets.
DEMO_PREP = [
    ["ingest", "--denoise"],
    ["sync"],
    ["screen"],
    ["face"],
    ["transcribe"],
    ["sheet", "contact"],
    ["sheet", "screen"],
]
SYNC_MIN_CONFIDENCE = 0.3  # tool sync's own threshold; below it the offset is a guess
TEMPLATES = ROOT / "templates"
PLAN_PROMPT = (
    "Use the video-cut:plan skill to plan the edit of the Video Cut job in the current folder. "
    "Write work/project.md and stop; do not cut."
)
CUT_PROMPT = (
    "The human approved work/project.md. Use the video-cut:cut skill to cut the Video Cut job "
    "in the current folder."
)
OVERLAYS_PROMPT = (
    "The human approved work/project.md and the cut is rendered. Use the video-cut:overlays "
    "skill to add captions and overlays to the Video Cut job in the current folder."
)
OVERLAYS_RECUT = (
    " The cut changed since captions were last made: rebuild them with "
    "`videocut tool captions --force`."
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


def new_job(
    video: Path,
    type_: str | None,
    brief: str | None,
    assets: list[str] | None = None,
    screen: str | None = None,
) -> Path:
    from .cli import main

    buf = io.StringIO()
    argv = ["tool", "job", "new", str(video)]
    argv += ["--type", type_] if type_ else []
    argv += ["--brief", brief] if brief else []
    argv += ["--screen", screen] if screen else []
    if assets:
        argv += ["--asset", *assets]
    with contextlib.redirect_stdout(buf):
        code = _main(main, argv)
    lines = buf.getvalue().strip().splitlines()
    s = json.loads(lines[-1]) if code == 0 and lines else {}
    if code != 0 or "job" not in s:
        raise RunError(f"could not create a job from {video}")
    return Path(s["job"])


def open_job(
    ref: str,
    type_: str | None,
    brief: str | None,
    assets: list[str] | None = None,
    screen: str | None = None,
) -> Path:
    p = Path(ref).expanduser()
    if p.is_file():
        return new_job(p, type_, brief, assets, screen)
    if assets or screen:
        raise RunError(
            "--asset/--screen only work when a new job is made from a recording "
            "(for an existing job: `videocut tool job screen <file>`)"
        )
    try:
        job = resolve_job(ref)
    except ToolError as e:
        raise RunError(str(e)) from None
    data = read_job(job)
    changes = {k: v for k, v in (("type", type_), ("brief", brief)) if v and v != data.get(k)}
    if changes:
        if read_approval(job):  # any approval was for the old type/brief, even a stale one
            revoke_approval(job)
        update_job(job, **changes)
    return job


def sync_stop(job: Path, why: str) -> RunError:
    return RunError(
        f"the screen recording could not be synced reliably ({why}). Find a moment you can "
        "see or hear in both recordings (a click, a clap, a word), then set the offset by "
        f"hand: `videocut tool sync --job {job.name} --offset <screen_time - camera_time>` "
        f"and run `videocut run {job.name}` again"
    )


def prep(job: Path, steps: list[list[str]] | None = None, say=print) -> None:
    if steps is None:
        steps = DEMO_PREP if read_job(job).get("screen") else PREP
    for argv in steps:
        t0 = time.monotonic()
        try:
            s = tool(job, *argv)
        except RunError as e:
            if argv == ["sync"]:
                raise sync_stop(job, str(e).split(": ", 1)[-1]) from None
            raise
        if (
            argv == ["sync"]
            and s.get("method") != "manual"
            and (s.get("confidence") or 0) < SYNC_MIN_CONFIDENCE
        ):
            raise sync_stop(job, f"confidence {s.get('confidence')}")
        extra = {
            k: s[k]
            for k in (
                "sheets", "words", "transcribed", "denoised", "found_pct", "blinks",
                "offset_s", "method", "confidence",
            )
            if k in s
        }  # fmt: skip
        if argv == ["screen"]:
            extra = {"clicks": s.get("clicks"), "zoom_targets": len(s.get("zoom_targets", []))}
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


def segments_sha(job: Path) -> str | None:
    """Hash of what makes the cut (source, fps, size, segments) in work/edl.json; captions
    and overlays are not part of it."""
    try:
        edl = json.loads((job / "work" / "edl.json").read_text())
        cut = {k: edl.get(k) for k in ("source", "fps", "size", "segments")}
    except (OSError, ValueError, AttributeError):
        return None
    return hashlib.sha256(json.dumps(cut, sort_keys=True).encode()).hexdigest()


def _final_verify(job: Path, started: float, phase: str) -> dict:
    """out/verify.json of a full render made after `started`, or RunError."""
    reel, vfile = job / "out" / "reel.mp4", job / "out" / "verify.json"
    if not ((_mtime(reel) or 0) >= started and (_mtime(vfile) or 0) >= started):
        raise RunError(f"the {phase} run did not finish with a final render and verify")
    verify = json.loads(vfile.read_text())
    if verify.get("preview") or verify.get("reel") != "out/reel.mp4":
        raise RunError("the last verify checked a preview, not out/reel.mp4")
    return verify


def cut_done(job: Path) -> bool:
    """Whether out/reel.mp4 is the finished cut of the approved plan. job.json "cut" (set by
    `run` after a cut run) must match the approved plan and the EDL's segments; a job cut
    before that record existed counts when its last verify checked out/reel.mp4 after the
    last EDL change."""
    approval = read_approval(job)
    if not approval or not (job / "out" / "reel.mp4").is_file():
        return False
    rec = read_job(job).get("cut")
    if isinstance(rec, dict):
        return (
            rec.get("project_sha256") == approval["project_sha256"]
            and rec.get("segments_sha256") == segments_sha(job)
            and rec.get("redaction_sha256") == _redaction_sha(job)
        )
    vfile = job / "out" / "verify.json"
    try:
        verify = json.loads(vfile.read_text())
    except (OSError, ValueError):
        return False
    edl_t, verify_t = _mtime(job / "work" / "edl.json"), _mtime(vfile) or 0
    approved_t = datetime.fromisoformat(approval["at"]).timestamp()
    return (
        verify.get("reel") == "out/reel.mp4"
        and not verify.get("preview")
        and edl_t is not None
        and verify_t >= max(edl_t, approved_t)
    )


def _redaction_sha(job: Path) -> str | None:
    """What the reel redacts (a changed personal-data decision means a new render)."""
    try:
        return red.state_sha(redaction_enforced(job)[0])
    except ToolError:
        return "invalid"


def record_cut(job: Path, approved_sha: str) -> None:
    verify = json.loads((job / "out" / "verify.json").read_text())
    update_job(
        job,
        cut={
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "project_sha256": approved_sha,
            "segments_sha256": segments_sha(job),
            "redaction_sha256": _redaction_sha(job),
            "verify_ok": bool(verify.get("ok")),
        },
        overlays=None,  # a new cut has no overlays yet
    )


def _file_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def overlays_done(job: Path, brand_id: str) -> bool:
    """The brand's overlays are in the current reel: recorded for this brand and exactly this
    EDL, verified ok, and the last render composited them."""
    rec = read_job(job).get("overlays")
    try:
        composited = json.loads((job / "out" / "render.json").read_text()).get("overlay")
    except (OSError, ValueError, AttributeError):
        composited = False
    return (
        isinstance(rec, dict)
        and rec.get("brand") == brand_id
        and rec.get("edl_sha256") == _file_sha(job / "work" / "edl.json")
        and rec.get("verify_ok") is True
        and bool(composited)
    )


def _where(c: dict) -> str:
    return c["asset"] if "asset" in c else f"{c['t0']:.1f}-{c['t1']:.1f} s"


def decide_personal_data(
    job: Path, demo_data: bool, interactive: bool, ask: Callable[[str], str], say=print
) -> None:
    """The human decision about work/redaction.json at the plan gate. `--demo-data` = all
    demo (method "flag"); interactive: ask; otherwise no decision, so all are redacted."""
    try:
        cands = red.load(job)
    except red.RedactionError as e:
        raise RunError(str(e)) from None
    if not cands:
        return
    if demo_data:
        red.decide(job, [], "flag")
        say(f"personal data: {len(cands)} place(s) recorded as demo data (--demo-data)")
        return
    say(f"\n{len(cands)} place(s) with possible personal data found:")
    for c in cands:
        say(f"  {c['id']:>2}. {c['kind']:<8} {_where(c):<22} {c.get('reason', '')}")
    if not interactive:
        say(
            "personal data: no decision, so all of them are redacted "
            f"(`videocut tool job redaction {job.name} --all-demo | --redact 1,3`)"
        )
        return
    answer = ask("Demo data? [a]ll demo / [r]edact all / [s]elect: ").strip().lower()
    if answer in ("a", "all", "all demo"):
        ids = []
    elif answer in ("s", "select"):
        raw = ask("Numbers to redact (e.g. 1,3; the others are demo data): ")
        try:
            ids = [int(x) for x in raw.replace(" ", ",").split(",") if x.strip()]
        except ValueError:
            say("personal data: not a list of numbers; everything is redacted")
            ids = None
    else:
        ids = None  # "r" and anything else: the safe choice
    try:
        rec = red.decide(job, ids, "prompt")
    except red.RedactionError as e:
        say(f"personal data: {e}; everything is redacted")
        rec = red.decide(job, None, "prompt")
    say(f"personal data: redact {rec['redact'] or 'nothing'}, demo data {rec['demo'] or 'none'}")


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
    brand: str | None = None,
    recut: bool = False,
    assets: list[str] | None = None,
    screen: str | None = None,
    demo_data: bool = False,
    say=print,
    ask: Callable[[str], str] = input,
) -> int:
    if not agent.shutil.which("claude"):
        raise RunError("Claude Code is not installed (see docs/AUTH.md).")
    job = open_job(ref, type_, brief, assets, screen)
    say(f"job: {job}")
    if brand:
        update_job(job, brand=brand_id(brand))
    project, edl = job / "work" / "project.md", job / "work" / "edl.json"

    say("prep:")
    prep(job, say=say)

    plan_session, plan_sha = None, None
    if is_approved(job) and not replan:
        say("plan: already approved")
        if demo_data:
            decide_personal_data(job, demo_data, False, ask, say)
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
        interactive = not approve and (sys.stdin.isatty() or ask is not input)
        decide_personal_data(job, demo_data, interactive, ask, say)

    approved_sha = read_approval(job)["project_sha256"]
    if cut_done(job) and not recut and not plan_session:
        say("cut: already done for the approved plan (--recut cuts again)")
        if not isinstance(read_job(job).get("cut"), dict):
            record_cut(job, approved_sha)  # a job cut before the record existed
    else:
        resume = plan_session if plan_session and project_sha(job) == plan_sha else None
        started = time.time()
        claude(job, "cut", CUT_PROMPT, max_turns, resume, say)
        if project_sha(job) != approved_sha:
            raise RunError("work/project.md changed during the cut run; approve it again")
        _final_verify(job, started, "cut")
        record_cut(job, approved_sha)

    job_brand = read_job(job).get("brand")
    if job_brand and (brand or not overlays_done(job, job_brand)):
        cut_sha = segments_sha(job)
        started = time.time()
        prompt = OVERLAYS_PROMPT
        old = read_job(job).get("overlays")
        edl_caps = (json.loads(edl.read_text()).get("captions") if edl.is_file() else None) or []
        if edl_caps and not (isinstance(old, dict) and old.get("segments_sha256") == cut_sha):
            prompt += OVERLAYS_RECUT
        claude(job, "overlays", prompt, max_turns, None, say)
        if project_sha(job) != approved_sha:
            raise RunError("work/project.md changed during the overlay run; approve it again")
        if segments_sha(job) != cut_sha:
            raise RunError(
                "the overlay run changed the cut (EDL segments); it may only add overlays"
            )
        verify = _final_verify(job, started, "overlay")
        render = json.loads((job / "out" / "render.json").read_text())
        if not render.get("overlay"):
            raise RunError("the final render of the overlay run has no overlay composited")
        update_job(
            job,
            overlays={
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "brand": job_brand,
                "segments_sha256": cut_sha,
                "edl_sha256": _file_sha(job / "work" / "edl.json"),
                "verify_ok": bool(verify.get("ok")),
            },
        )
    elif job_brand:
        say(f"overlays: already done for brand {job_brand} (--brand {job_brand} redoes them)")
    else:
        say("overlays: skipped, the job has no brand (--brand <id>)")

    verify = json.loads((job / "out" / "verify.json").read_text())
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
            brand=args.brand,
            recut=args.recut,
            assets=args.asset,
            screen=args.screen,
            demo_data=args.demo_data,
            say=functools.partial(print, flush=True),  # progress also when piped to a log
        )
    except (RunError, agent.AgentError, ToolError) as e:
        print(f"\033[31m✗ {e}\033[0m", file=sys.stderr)
        return 1
