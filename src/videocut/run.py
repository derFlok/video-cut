"""`videocut run <video|job>`: the whole pipeline from the command line.

    prep     job new (when a file is given), then the deterministic tools: ingest --denoise,
             face (face track + blinks), transcribe (also writes candidates +
             takes_packed.md), sheet contact. A product demo (`--screen <recording>`, or
             job.json "screen") runs ingest (camera + screen) -> sync -> screen -> redact
             text (local OCR) -> face -> transcribe -> sheet contact + sheet screen, and
             stops with a hint for `tool sync --offset` when the recordings cannot be
             synced reliably
    plan     Claude run with the `plan` skill -> work/project.md, nothing is cut
    approve  the human reads work/project.md and approves it (prompt; `--approve` skips it)
    cut      Claude run with the `cut` skill (resumes the plan session when the plan is
             unchanged) -> work/edl.json, out/reel.mp4, out/verify.json, work/cut_report.md
    overlays Claude run with the `overlays` skill when the job has a brand (`--brand`):
             captions + overlay scenes in the brand's look on the approved cut -> the same
             outputs plus work/overlays.json, work/overlay.mov, work/overlay_report.md.
             No second approval, but it only ever runs on an approved, finished cut, and
             it must not change the cut (the EDL segments)

revise   (`--feedback TEXT [--at S]`) Claude run with the `revise` skill on a finished reel:
             changes only what the human's feedback asks (segments, zoom, layouts, overlays,
             caption text), re-renders and verifies. No new approval (the feedback is the
             human's), but guards run after it: the personal-data decision and redaction
             unchanged, segments only from the approved plan's source ranges (unless the
             feedback asks for new material: "extends_plan"), the plan unchanged. The
             previous reel goes to out/history/reel-<n>.mp4 (last HISTORY_KEEP); a failed
             run or guard restores it. One line per revision in work/revisions.jsonl
    (`--plan-feedback TEXT`) a plan run in revise-the-plan mode before approval (implies
             --replan); the approval is bound to the plan's sha, so a changed plan needs a
             new approval

Re-running is cheap: prep tools keep fresh outputs, an existing plan is offered for approval
again instead of being re-planned (`--replan` forces a new one), an approved plan goes
straight to the cut, and a finished cut of the approved plan is kept (`--recut` cuts again;
job.json "cut" records which plan, segments, redaction and output format it was).
`--format` sets the output format (job.json "format", tools/_format.py): for a new job like
`tool job new --format`, for an existing one like `tool job format` (a changed format makes
the cut stale, so the cut runs again). Plan and cut prompts name the job's format.
`--cut-style` likewise sets the cut style (job.json "cut_style", tools/_style.py; a changed
style makes the cut stale) and the prompts name it and its parameters; `recut()` is the entry
for "cut again with the approved plan" (the app's /recut: cut + overlays, no new approval).
`--target` sets the target length (job.json "target_s", tools/_length.py). It changes what
the plan contains: a plan made for another length is stale, its approval is revoked, and the
run plans again (with `--plan-feedback`, the revise-the-plan run is told the new length);
every plan run records the length it was made for (job.json "plan_target").
`--slides-mode none|upload|auto|both` and `--slides-wish TEXT` set the job's slides (job.json
"slides", tools/_job_slides.py; uploaded pages come from `tool job slides-import`). Like the
target length they change what the plan contains: a plan made for other slides is planned
again and needs a new approval (job.json "plan_slides"). The cut and overlay runs must keep
the approved slides' content (work/slides.json against the approved work/plan.json).
`--vocab "Term=variant,..."` gives a new job its transcript vocabulary (job.json
"vocabulary", tools/vocab.py); transcribe applies it together with the brand's.
The overlay stage runs when `--brand` is given or when the brand's overlays are not done for
the current cut. Every Claude run appends one line to work/runs.jsonl (wall time, turns,
cost, tokens), and its raw event stream goes to work/claude-<phase>.jsonl.
"""

import contextlib
import difflib
import functools
import hashlib
import io
import json
import re
import shutil
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import agent
from . import redaction as red
from .edl import segments_sha as edl_segments_sha
from .paths import ROOT
from .tools import _format, _job_slides, _length, _redact_style, _style
from .tools.common import ToolError, read_job, resolve_job, update_job
from .tools.job import (
    approve_job,
    brand_id,
    is_approved,
    job_lang,
    plan_made_for,
    plan_target_stale,
    project_sha,
    read_approval,
    redaction_enforced,
    resolved_format,
    resolved_range,
    resolved_style,
    revoke_approval,
    set_cut_style,
    set_format,
    set_redact_style,
    set_target,
    stamp_plan_target,
)

TYPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
PREP = [["ingest", "--denoise"], ["face"], ["transcribe"], ["sheet", "contact"]]
# Product demo (job.json "screen"): ingest also prepares the screen recording, sync aligns it
# with the camera, screen finds activity, clicks and zoom targets, redact text reads the
# text on the screen (local OCR, for the personal-data candidates; skipped without it), and
# one overview sheet of the screen joins the contact sheets.
OCR_STEP = ["redact", "text"]
DEMO_PREP = [
    ["ingest", "--denoise"],
    ["sync"],
    ["screen"],
    OCR_STEP,
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
FORMAT_NOTE = (
    ' Output format: {fmt} ({w}x{h}, job.json "format"); work/edl.json "size" is [{w}, {h}].'
)
STYLE_NOTE = (
    ' Cut style: {name} (job.json "cut_style"): natural pauses up to {keep_pause_s} s stay '
    "inside a segment, segments are at least {min_segment_s} s, {splice}; render crossfades "
    "the audio {xfade_ms} ms at every cut. Human-facing text (plan, reports): {lang}."
)
TARGET_NOTE = ' Target length (job.json "target_s", a human sets it): {note}.'
SLIDES_NOTE = (
    ' Slides (job.json "slides", a human sets them): mode {mode}, {pages} uploaded page(s)'
    "{wish}; `videocut tool job show` -> slides."
)
TARGET_CHANGED = (
    " The human changed the target length since this plan was made (it was made for "
    "{old}, now: {new}): fit the keep list to the new length (never pad; if the material "
    "is too short, say so), and name the new length in the change note."
)
LANG_NAMES = {"de": "German", "en": "English"}
CUT_AGAIN = (
    " The job was cut before; the human asked to cut it again ({why}): write a new "
    "work/edl.json (segments only; captions and overlays are redone after the cut), render "
    "and verify."
)
OVERLAYS_PROMPT = (
    "The human approved work/project.md and the cut is rendered. Use the video-cut:overlays "
    "skill to add captions and overlays to the Video Cut job in the current folder."
)
PLAN_REVISE = (
    " Revise-the-plan mode: the human gave feedback on the current work/project.md; it is "
    "in work/plan_feedback.json. Change only what the feedback asks, keep everything else as "
    "it is, and note the change at the top of work/project.md."
)
REVISE_PROMPT = (
    "The human gave feedback on the finished reel. Use the video-cut:revise skill to revise "
    "the Video Cut job in the current folder; the feedback is in work/revision_request.json."
)
OVERLAYS_RECUT = (
    " The cut changed since captions were last made: rebuild them with "
    "`videocut tool captions --force`."
)


class RunError(RuntimeError):
    pass


class _StdoutRouter(io.TextIOBase):
    """sys.stdout that sends each thread's output to that thread's capture buffer (if any).

    contextlib.redirect_stdout swaps the process-wide sys.stdout: in the local app a run
    thread and a request thread capturing tool output at the same time would mix their
    summary lines (and could leave sys.stdout pointing at a dead buffer)."""

    def __init__(self, out):
        self.out = out
        self.local = threading.local()

    def _target(self):
        return getattr(self.local, "buf", None) or self.out

    def write(self, s: str) -> int:
        return self._target().write(s)

    def flush(self) -> None:
        self._target().flush()

    def isatty(self) -> bool:
        return getattr(self.local, "buf", None) is None and self.out.isatty()


def install_stdout_router() -> None:
    """For processes that run pipeline steps in threads (the local app)."""
    if not isinstance(sys.stdout, _StdoutRouter):
        sys.stdout = _StdoutRouter(sys.stdout)


@contextlib.contextmanager
def capture_stdout():
    """Capture this thread's stdout (thread-safe once install_stdout_router() ran)."""
    buf = io.StringIO()
    router = sys.stdout if isinstance(sys.stdout, _StdoutRouter) else None
    if router is None:
        with contextlib.redirect_stdout(buf):
            yield buf
        return
    prev = getattr(router.local, "buf", None)
    router.local.buf = buf
    try:
        yield buf
    finally:
        router.local.buf = prev


def tool(job: Path, *argv: str, need_ok: bool = True) -> dict:
    """Run `videocut tool <argv> --job <job>` in-process; return its summary line.
    need_ok=False: a summary with "ok": false is returned (verify flags), only a failed
    run raises."""
    from .cli import main

    with capture_stdout() as buf:
        code = _main(main, ["tool", *argv, "--job", str(job)])
    lines = buf.getvalue().strip().splitlines()
    try:
        summary = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        summary = {}
    if code != 0 or (need_ok and not summary.get("ok", False)):
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
    format_: str | None = None,
    cut_style: str | None = None,
    lang: str | None = None,
    target: str | None = None,
    vocab: list[str] | None = None,
) -> Path:
    from .cli import main

    argv = ["tool", "job", "new", str(video)]
    argv += ["--type", type_] if type_ else []
    argv += ["--brief", brief] if brief else []
    argv += ["--screen", screen] if screen else []
    argv += ["--format", format_] if format_ else []
    argv += ["--cut-style", cut_style] if cut_style else []
    argv += ["--target", str(target)] if target is not None else []
    argv += ["--lang", lang] if lang else []
    for v in vocab or []:
        argv += ["--vocab", v]
    if assets:
        argv += ["--asset", *assets]
    with capture_stdout() as buf:
        code = _main(main, argv)
    lines = buf.getvalue().strip().splitlines()
    s = json.loads(lines[-1]) if code == 0 and lines else {}
    if code != 0 or "job" not in s:
        raise RunError(f"could not create a job from {video}: {s.get('error', code)}")
    return Path(s["job"])


def open_job(
    ref: str,
    type_: str | None,
    brief: str | None,
    assets: list[str] | None = None,
    screen: str | None = None,
    format_: str | None = None,
    say=print,
    cut_style: str | None = None,
    target: str | None = None,
    vocab: list[str] | None = None,
) -> Path:
    p = Path(ref).expanduser()
    if p.is_file():
        return new_job(
            p, type_, brief, assets, screen, format_, cut_style, target=target, vocab=vocab
        )
    if assets or screen:
        raise RunError(
            "--asset/--screen only work when a new job is made from a recording "
            "(for an existing job: `videocut tool job screen <file>`)"
        )
    if vocab:
        raise RunError(
            "--vocab only works when a new job is made from a recording (for an existing "
            f"job: `videocut tool job vocab {ref} add <term> <variant>...`, then "
            f"`videocut tool vocab apply {ref}`)"
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
    if format_:
        set_format(job, format_, say)  # the cut of another format goes stale (cut_done)
    if cut_style:
        set_cut_style(job, cut_style, say)  # the cut of another style goes stale (cut_done)
    if target is not None:
        set_target(job, target, say)  # a plan for another length goes stale (plan again)
    return job


def sync_stop(job: Path, why: str) -> RunError:
    return RunError(
        f"the screen recording could not be synced reliably ({why}). Find a moment you can "
        "see or hear in both recordings (a click, a clap, a word), then set the offset by "
        f"hand: `videocut tool sync --job {job.name} --offset <screen_time - camera_time>` "
        f"and run `videocut run {job.name}` again"
    )


def ocr_available() -> bool:
    from .tools._ocr import available

    return available()


def prep(job: Path, steps: list[list[str]] | None = None, say=print) -> None:
    if steps is None:
        steps = DEMO_PREP if read_job(job).get("screen") else PREP
    for argv in steps:
        t0 = time.monotonic()
        if argv == OCR_STEP and not ocr_available():
            say(f"  {' '.join(argv):<16} skipped: no local text recognition (needs macOS)")
            continue
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
        if argv == OCR_STEP:
            extra = {"texts": len(s.get("texts", [])), "suggest": len(s.get("suggest", []))}
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


def _fs_now(job: Path) -> float:
    """Now, on the clock file mtimes use (Linux stamps files from a coarser clock than
    time.time(), so a file written right after time.time() can look older)."""
    mark = job / ".started"
    mark.touch()
    try:
        return mark.stat().st_mtime
    finally:
        mark.unlink(missing_ok=True)


def _mtime(p: Path) -> float | None:
    return p.stat().st_mtime if p.is_file() else None


def _snapshot(folder: Path) -> dict:
    return {p: p.stat().st_mtime for p in folder.rglob("*") if p.is_file()}


def segments_sha(job: Path) -> str | None:
    """Hash of what makes the cut (source, fps, size, segments) in work/edl.json; captions
    and overlays are not part of it."""
    try:
        return edl_segments_sha(json.loads((job / "work" / "edl.json").read_text()))
    except (OSError, ValueError, AttributeError):
        return None


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
    data = read_job(job)
    rec = data.get("cut")
    fmt = resolved_format(data)
    if isinstance(rec, dict):
        return (
            rec.get("project_sha256") == approval["project_sha256"]
            and rec.get("segments_sha256") == segments_sha(job)
            # (a record from before this field: the redaction of that render is kept)
            and rec.get("redaction_sha256", red_now := _redaction_sha(job)) == red_now
            # (before this field every cut was a 9:16 reel)
            and rec.get("format", _format.PORTRAIT) == fmt
            # (a record from before cut styles: it stays until the style is changed,
            # which stamps the record, tools/job.set_cut_style)
            and rec.get("cut_style", style_now := resolved_style(data)) == style_now
            # (a record from before target lengths: a changed target revokes the approval)
            and rec.get("target_s", data.get("target_s")) == data.get("target_s")
            # (a record from before redaction styles: it stays until the style is changed,
            # which stamps the record, tools/job.set_redact_style)
            and rec.get("redact_style", drawn_now := _redact_style_used(job)) == drawn_now
        )
    vfile = job / "out" / "verify.json"
    try:
        verify = json.loads(vfile.read_text())
    except (OSError, ValueError):
        return False
    size = ((verify.get("checks") or {}).get("size") or {}).get("size")
    if size is not None and tuple(size) != _format.FORMATS[fmt]:
        return False  # cut at another format
    edl_t, verify_t = _mtime(job / "work" / "edl.json"), _mtime(vfile) or 0
    approved_t = datetime.fromisoformat(approval["at"]).timestamp()
    return (
        # a cut from before cut styles was cut the way "tight" cuts now: a style the
        # human set since makes it stale (absent: it stays, like an old record)
        data.get("cut_style") in (None, "tight")
        # ... and drew personal data as solid blocks: likewise for a redaction style
        and (data.get("redact_style") in (None, "block") or _redact_style_used(job) is None)
        and verify.get("reel") == "out/reel.mp4"
        and not verify.get("preview")
        and edl_t is not None
        and verify_t >= max(edl_t, approved_t)
    )


def _check_redact_style(value: str) -> None:
    """--redact-style before a job is made from it: RunError for an unknown style."""
    try:
        _redact_style.parse(value)
    except ToolError as e:
        raise RunError(str(e)) from None


def _redact_style_used(job: Path) -> str | None:
    """How the reel hides personal data (_redact_style), None when there is none to hide:
    a changed style means a new render only then."""
    if _redaction_sha(job) is None:
        return None
    try:
        return _redact_style.resolved(read_job(job))
    except ToolError:
        return "invalid"


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
            "format": resolved_format(read_job(job)),
            "cut_style": resolved_style(read_job(job)),
            "target_s": read_job(job).get("target_s"),
            "redact_style": _redact_style_used(job),
            "verify_ok": bool(verify.get("ok")),
        },
        overlays=None,  # a new cut has no overlays yet
    )


def _file_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def overlays_done(job: Path, brand_id: str) -> bool:
    """The brand's overlays are in the current reel: recorded for this brand and exactly this
    EDL, verified ok, the last render composited them, and nothing marked them stale (a
    vocabulary applied to the captions, tools/vocab.py)."""
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
        and not rec.get("stale")
        and bool(composited)
    )


def _overlay_files(job: Path) -> dict:
    from .tools.overlays import file_hashes

    return file_hashes(job)


def overlays_resumable(job: Path, brand_id: str, cut_sha: str | None) -> bool:
    """A finished overlay run whose final render or verify failed (job.json
    "overlays_pending", written when the Claude run returned) left exactly the files still
    there: same brand, cut and EDL, the overlay files `videocut tool overlays` recorded,
    overlay.mov newer than its inputs (render composites it). Then render + verify are
    repeated as tool steps instead of a new Claude run."""
    from .tools.common import is_fresh
    from .tools.overlays import check_record

    rec = read_job(job).get("overlays_pending")
    work = job / "work"
    files = _overlay_files(job)
    inputs = (work / "edl.json", work / "overlays.json", work / "words.json")
    return (
        isinstance(rec, dict)
        and rec.get("brand") == brand_id
        and cut_sha is not None
        and rec.get("segments_sha256") == cut_sha
        and rec.get("edl_sha256") == _file_sha(work / "edl.json")
        and None not in files.values()
        and rec.get("files") == files
        and check_record(job) is None
        and is_fresh([work / "overlay.mov"], [p for p in inputs if p.is_file()])
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
        if red.valid_decision(job):
            say(f"personal data: decided before (`videocut tool job show {job.name}`)")
            return
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


def can_ask(ask: Callable[[str], str]) -> bool:
    return sys.stdin.isatty() or ask is not input


def not_approved(job: Path, asked: bool) -> list[str]:
    """What to run later when the plan was not approved. Without a terminal (nobody could
    be asked) and with personal-data candidates, the full commands for both decisions."""
    name = job.name
    lines = [f"Not approved. Later: videocut tool job approve {name} && videocut run {name}"]
    if asked:
        return lines
    lines[0] = "Not approved: no terminal to ask (stdin is not a TTY)."
    try:
        cands = red.load(job) or []
    except red.RedactionError:
        cands = []
    if not cands:
        return [*lines, f"Approve and cut: videocut run {name} --approve"]
    ids = ",".join(str(c["id"]) for c in cands[:2])
    lines.append(f"The plan lists {len(cands)} place(s) with possible personal data:")
    lines += [
        f"  {c['id']:>2}. {c['kind']:<8} {_where(c):<22} {c.get('reason', '')}" for c in cands
    ]
    return [
        *lines,
        "Approve and cut, choosing one:",
        f"  all of it is demo data:  videocut run {name} --approve --demo-data",
        f"  redact all of it:        videocut tool job redaction {name} --redact-all"
        f" && videocut run {name} --approve",
        f"  redact some (e.g. {ids}): videocut tool job redaction {name} --redact {ids}"
        f" && videocut run {name} --approve",
    ]


def format_note(job: Path) -> str:
    """The job's output format, cut style and target length, for every plan, cut and revise
    prompt."""
    data = read_job(job)
    fmt = resolved_format(data)
    w, h = _format.FORMATS[fmt]
    st = _style.STYLES[resolved_style(data)]
    splice = (
        "a sentence may be spliced only to drop: " + ", ".join(st["splice_only"])
        if st["splice_in_sentence"]
        else "never assemble a sentence from pieces"
    )
    lang = job_lang(data)
    resolved_range(data)  # ToolError for an invalid job.json "target_s"
    return (
        FORMAT_NOTE.format(fmt=fmt, w=w, h=h)
        + STYLE_NOTE.format(name=resolved_style(data), splice=splice, lang=LANG_NAMES[lang], **st)
        + TARGET_NOTE.format(note=_length.describe(data))
        + slides_note(job, data)
    )


def slides_note(job: Path, data: dict) -> str:
    sl = _job_slides.view(job, data)
    if sl["mode"] == "none":
        return ""
    wish = " and a wish from the human" if sl["wish"] else ""
    return SLIDES_NOTE.format(mode=sl["mode"], pages=len(sl["pages"]), wish=wish)


def slides_kept(job: Path) -> str | None:
    """Why work/slides.json does not show what the human approved (None: it does). The
    approved work/plan.json lists each slide's template and content; every slide the EDL
    shows must be one of them, unchanged. A plan approved as markdown only (no plan.json
    the human saw) is not checked: there is nothing structured to compare."""
    from .tools.job import approved_plan_json

    if not approved_plan_json(job):
        return None
    try:
        plan = json.loads((job / "work" / "plan.json").read_text())
        edl = json.loads((job / "work" / "edl.json").read_text())
    except (OSError, ValueError):
        return None
    used = {s.get("slide") for s in edl.get("segments") or [] if isinstance(s, dict)} - {None}
    if not used:
        return None
    approved = {
        s.get("id"): (s.get("template"), s.get("content"))
        for s in plan.get("slides") or []
        if isinstance(s, dict)
    }
    try:
        have = json.loads((job / "work" / "slides.json").read_text()).get("slides") or []
    except (OSError, ValueError, AttributeError):
        return "work/slides.json is missing or unreadable"
    have = {s.get("id"): (s.get("template"), s.get("content")) for s in have if isinstance(s, dict)}
    for sid in sorted(map(str, used)):
        if sid not in approved:
            return f"the EDL shows slide {sid}, which is not in the approved plan"
        if have.get(sid) != approved[sid]:
            return f"slide {sid} in work/slides.json differs from the approved plan"
    return None


def stale_why(job: Path) -> str:
    """Why a cut that exists is not the current one (for the cut-again prompt)."""
    data = read_job(job)
    rec = data.get("cut")
    approval = read_approval(job) or {}
    if not isinstance(rec, dict):
        return "the plan, the personal-data decision, the output format or the cut style changed"
    why = []
    if rec.get("project_sha256") != approval.get("project_sha256"):
        why.append("the plan changed")
    if rec.get("redaction_sha256", now := _redaction_sha(job)) != now:
        why.append("the personal-data decision changed")
    if rec.get("format", _format.PORTRAIT) != resolved_format(data):
        why.append("the output format changed")
    if rec.get("cut_style", style := resolved_style(data)) != style:
        why.append(f"the cut style changed from {rec.get('cut_style')} to {style}")
    if rec.get("redact_style", drawn := _redact_style_used(job)) != drawn:
        why.append(f"personal data is now shown as {drawn}, not {rec.get('redact_style')}")
    if rec.get("target_s", target := data.get("target_s")) != target:
        why.append(
            f"the target length changed from {_length.label(rec.get('target_s'))} to "
            f"{_length.label(target)}"
        )
    if not why and rec.get("segments_sha256") != segments_sha(job):
        why.append("work/edl.json changed after the cut")
    return "; ".join(why) or "the human asked for it"


def ask_approval(job: Path, ask: Callable[[str], str] = input) -> bool:
    if not can_ask(ask):
        return False
    answer = ask("Approve this plan and start cutting? Type 'yes' (anything else stops): ")
    return answer.strip().lower() in ("y", "yes", "j", "ja")


# ---------------------------------------------------------------- revision runs

HISTORY_KEEP = 5  # previous reels kept in out/history
PLAN_TOL_S = 0.25  # a revised segment may reach this far past an approved source range
QUOTE_MIN = 8  # characters of the feedback the revise run must quote to extend the plan
# What a revise run rewrites (or must not touch); restored when it fails, so the previous
# reel and its plan stay consistent.
REVISION_FILES = [
    "work/edl.json",
    "work/project.md",
    "work/plan.json",
    "work/redaction.json",
    "work/slides.json",
    "work/overlays.json",
    "work/overlay.mov",
    "work/overlay_boxes.json",
    "out/render.json",
    "out/verify.json",
    "out/cover.jpg",
    "out/preview.mp4",
]


def _sha_stream(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def history(job: Path) -> list[tuple[int, Path]]:
    """(n, path) of the previous reels in out/history, oldest first."""
    folder = job / "out" / "history"
    found = []
    for p in folder.glob("reel-*.mp4") if folder.is_dir() else []:
        m = re.fullmatch(r"reel-(\d+)\.mp4", p.name)
        if m:
            found.append((int(m.group(1)), p))
    return sorted(found)


def save_history(job: Path) -> Path:
    """Copy out/reel.mp4 to out/history/reel-<n>.mp4 (n counts on). Pruning to the last
    HISTORY_KEEP happens after a successful revision (prune_history), so a failed one
    never costs a kept version."""
    old = history(job)
    dst = job / "out" / "history" / f"reel-{old[-1][0] + 1 if old else 1}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(job / "out" / "reel.mp4", dst)
    return dst


def prune_history(job: Path) -> None:
    for _, p in history(job)[:-HISTORY_KEEP]:
        p.unlink()


def archive_revisions(job: Path) -> None:
    """A new cut (another approved plan, --recut) starts the version list again: the old
    revisions.jsonl and out/history are kept aside with a time stamp."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log, hist = job / "work" / "revisions.jsonl", job / "out" / "history"
    if log.is_file():
        log.rename(log.with_name(f"revisions-before-{stamp}.jsonl"))
    if hist.is_dir():
        hist.rename(hist.with_name(f"history-before-{stamp}"))


def _backup_dir(job: Path) -> Path:
    # outside the job folder: the revise run cannot touch what restores the previous state
    from .paths import state_dir

    return state_dir() / job.resolve().name / "revision_backup"


def _backup(job: Path) -> dict:
    from .tools.overlays import record_path

    folder = _backup_dir(job)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    had = {}
    for rel in [*REVISION_FILES, "overlay_record"]:
        src = record_path(job) if rel == "overlay_record" else job / rel
        had[rel] = src.is_file()
        if had[rel]:
            shutil.copy2(src, folder / rel.replace("/", "__"))
    return had


def _restore(job: Path, had: dict, saved: Path, reel_sha: str | None) -> str | None:
    """Put the state from before the revise run back; a problem as text (None = done)."""
    from .tools.overlays import record_path

    folder = _backup_dir(job)
    for rel, existed in had.items():
        dst = record_path(job) if rel == "overlay_record" else job / rel
        if existed:
            shutil.copy2(folder / rel.replace("/", "__"), dst)
        else:
            dst.unlink(missing_ok=True)
    shutil.rmtree(folder, ignore_errors=True)
    if _sha_stream(saved) != reel_sha:
        return f"{saved.relative_to(job)} changed during the run; out/reel.mp4 was not restored"
    shutil.copy2(saved, job / "out" / "reel.mp4")
    saved.unlink()
    return None


def _ranges(data: dict) -> list[tuple[float, float]]:
    out = []
    for s in data.get("segments") or [] if isinstance(data, dict) else []:
        if isinstance(s, dict) and red._num(s.get("in")) and red._num(s.get("out")):
            out.append((float(s["in"]), float(s["out"])))
    return out


def plan_ranges(job: Path, edl_data: dict) -> list[tuple[float, float]]:
    """The source ranges the human approved: the keep list of work/plan.json plus the
    segments of the current cut (made from the approved plan, plus what earlier revisions
    added on the human's request), merged. plan.json counts only when it is the one the
    approval saw (an agent run may write work/, so a later plan.json is not trusted)."""
    from .tools.job import approved_plan_json

    try:
        keep = json.loads((job / "work" / "plan.json").read_text()).get("keep_list") or []
    except (OSError, ValueError, AttributeError):
        keep = []
    if not approved_plan_json(job):
        keep = []
    ranges = _ranges(edl_data)
    for it in keep if isinstance(keep, list) else []:
        src = it.get("src") if isinstance(it, dict) else None
        if isinstance(src, list) and len(src) == 2 and all(red._num(v) for v in src):
            ranges.append((float(src[0]), float(src[1])))
    merged: list[list[float]] = []
    for a, b in sorted(r for r in ranges if r[0] < r[1]):
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def outside_plan(data: dict, ranges: list[tuple[float, float]]) -> list[int]:
    """Indices of segments that are not inside one approved range (+-PLAN_TOL_S)."""
    return [
        k
        for k, (a, b) in enumerate(_ranges(data))
        if not any(r0 - PLAN_TOL_S <= a and b <= r1 + PLAN_TOL_S for r0, r1 in ranges)
    ]


def redaction_state(job: Path) -> str:
    """Fingerprint of everything the human decided about personal data: work/redaction.json,
    the decision record, the candidates the approval saw and what is enforced."""
    approval = read_approval(job) or {}
    try:
        decision = red.decision_path(job).read_bytes()
    except OSError:
        decision = b""
    state = {
        "file": red.file_sha(job),
        "decision": hashlib.sha256(decision).hexdigest(),
        "approved": approval.get("redaction_candidates"),
        "enforced": _redaction_sha(job),
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _blocks(data: dict) -> set[tuple]:
    out = set()
    for s in data.get("segments") or []:
        for r in (s.get("redact") or []) if isinstance(s, dict) else []:
            if isinstance(r, dict) and red._box(r.get("box")):
                out.add((round(r.get("t0", 0), 3), round(r.get("t1", 0), 3), tuple(r["box"])))
    return out


def _shot_boxes(data: dict) -> set[tuple]:
    out = set()
    for o in data.get("overlays") or []:
        props = o.get("props") if isinstance(o, dict) else None
        if o.get("scene") == "screenshot_frame" and isinstance(props, dict):
            for b in props.get("redact") or []:
                if red._box(b):
                    out.add((str(props.get("file")), tuple(b)))
    return out


def redaction_removed(before: dict, after: dict) -> list[str]:
    """Redact blocks/boxes of the EDL `before` that `after` drops or changes while their
    picture is still in the reel (dropping one with the material it covered is fine;
    adding redaction is fine)."""
    problems = []
    shown = [
        (s["in"], s["out"])
        for s in after.get("segments") or []
        if isinstance(s, dict) and s.get("layout", "face") != "face"
    ]
    for t0, t1, box in sorted(_blocks(before) - _blocks(after)):
        if any(min(t1, b) - max(t0, a) > red.EPS for a, b in shown):
            problems.append(f"redact block {t0}-{t1} s {list(box)}")
    files = {f for f, _ in _shot_boxes(after)} | {
        str((o.get("props") or {}).get("file"))
        for o in after.get("overlays") or []
        if isinstance(o, dict) and o.get("scene") == "screenshot_frame"
    }
    for f, box in sorted(_shot_boxes(before) - _shot_boxes(after)):
        if f in files:
            problems.append(f"redact box {list(box)} of {f}")
    return problems


def _seq_diff(before: list, after: list) -> dict:
    a = [json.dumps(x, sort_keys=True) for x in before]
    b = [json.dumps(x, sort_keys=True) for x in after]
    added, removed, modified = [], [], []
    for op, i0, i1, j0, j1 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        n = min(i1 - i0, j1 - j0) if op == "replace" else 0
        for i, j in zip(range(i0, i0 + n), range(j0, j0 + n), strict=True):
            x, y = before[i], after[j]
            keys = sorted(set(x) | set(y)) if isinstance(x, dict) and isinstance(y, dict) else []
            modified.append(
                {"before": i, "after": j, "fields": [k for k in keys if x.get(k) != y.get(k)]}
            )
        removed += range(i0 + n, i1)
        added += range(j0 + n, j1)
    return {k: v for k, v in (("added", added), ("removed", removed), ("modified", modified)) if v}


def _caption_texts(data: dict) -> list[str]:
    return [
        " ".join(w.get("w", "") for w in c.get("words") or [] if isinstance(w, dict))
        for c in data.get("captions") or []
        if isinstance(c, dict)
    ]


def edl_changes(before: dict, after: dict) -> dict:
    """What a revision changed in work/edl.json, computed (not as Claude reports it)."""
    out = {}
    for key in ("segments", "overlays"):
        d = _seq_diff(before.get(key) or [], after.get(key) or [])
        if d:
            out[key] = d
    a, b = _caption_texts(before), _caption_texts(after)
    if a != b:
        out["captions"] = (
            {"changed": [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]}
            if len(a) == len(b)
            else {"before": len(a), "after": len(b)}
        )
    dur = [round(sum(o - i for i, o in _ranges(d)), 3) for d in (before, after)]
    if dur[0] != dur[1]:
        out["output_duration"] = dur
    return out


def _asked(claim, feedback: str) -> str | None:
    """The words of the feedback that ask for new material, when the revise run quotes them
    (work/revision.json {"extends_plan": true, "asked": "<verbatim>"}); else None."""
    if not isinstance(claim, dict) or claim.get("extends_plan") is not True:
        return None
    quote = " ".join(str(claim.get("asked") or "").split()).casefold()
    text = " ".join(feedback.split()).casefold()
    return quote if len(quote) >= QUOTE_MIN and quote in text else None


def _last_run(job: Path, phase: str, since: str) -> dict:
    try:
        lines = (job / "work" / "runs.jsonl").read_text().splitlines()
        last = json.loads(lines[-1]) if lines else {}
    except (OSError, ValueError):
        return {}
    return last if last.get("phase") == phase and last.get("at", "") >= since else {}


def revisions(job: Path) -> list[dict]:
    try:
        lines = (job / "work" / "revisions.jsonl").read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out


def _record_overlays(job: Path, brand: str, verify: dict) -> None:
    update_job(
        job,
        overlays={
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "brand": brand,
            "segments_sha256": segments_sha(job),
            "edl_sha256": _file_sha(job / "work" / "edl.json"),
            "verify_ok": bool(verify.get("ok")),
        },
    )


def upgrade_time_base(job: Path) -> None:
    """Move a job ingested before the video time base into it (ingest shifts its wavs,
    words and EDL; the cut and overlay records follow). Nothing for an up-to-date job. A
    revision runs no prep, so it does this first: otherwise its snap would place the cut
    in video time and a later upgrade would shift it again."""
    try:
        meta = json.loads((job / "work" / "audio.json").read_text())
    except (OSError, ValueError):
        return
    if isinstance(meta, dict) and meta.get("time_base") != "video":
        tool(job, "ingest", *(["--denoise"] if meta.get("denoised") else []))


def revise(
    job: Path, feedback: str, at_s: float | None = None, max_turns: int = 60, say=print
) -> int:
    """A revision run on the finished reel (see the module docstring); 0 = verify ok,
    4 = rendered but verify not ok. RunError when it fails (the previous reel stays)."""
    feedback = (feedback or "").strip()
    if not feedback:
        raise RunError("the feedback is empty")
    work, out = job / "work", job / "out"
    if not is_approved(job):
        raise RunError(f"the plan of {job.name} is not approved; feedback needs a finished reel")
    if not ((out / "reel.mp4").is_file() and (work / "edl.json").is_file()):
        raise RunError(f"{job.name} has no finished reel yet; run `videocut run {job.name}`")
    upgrade_time_base(job)
    if not cut_done(job):
        raise RunError(
            f"out/reel.mp4 of {job.name} is not the cut of the approved plan; "
            f"run `videocut run {job.name}` first"
        )
    located = None
    if at_s is not None:
        located = tool(job, "edl", "locate", str(at_s))
        located = {k: v for k, v in located.items() if k not in ("tool", "ok")}
    n = len(revisions(job)) + 1
    approved_sha = read_approval(job)["project_sha256"]
    before = json.loads((work / "edl.json").read_text())
    ranges = plan_ranges(job, before)
    red_before = redaction_state(job)
    brand = read_job(job).get("brand")
    try:  # the reel had the overlay track composited (even when its verify flagged it)
        had_overlays = bool(json.loads((out / "render.json").read_text()).get("overlay"))
    except (OSError, ValueError, AttributeError):
        had_overlays = False
    reel_sha = _sha_stream(out / "reel.mp4")
    had = _backup(job)
    saved = save_history(job)
    from .tools.common import write_json

    write_json(
        work / "revision_request.json",
        {
            "n": n,
            "text": feedback,
            "at_s": at_s,
            "located": located,
            "previous_reel": str(saved.relative_to(job)),
        },
    )
    (work / "revision.json").unlink(missing_ok=True)
    since = datetime.now().astimezone().isoformat(timespec="seconds")
    say(f"revise #{n}: {feedback!r}" + (f" at {at_s}s" if at_s is not None else ""))
    started = _fs_now(job)
    t0 = time.monotonic()
    entry = {"n": n, "time": since, "text": feedback, "at_s": at_s, "located": located}
    try:
        claude(job, "revise", REVISE_PROMPT + format_note(job), max_turns, None, say)
        if project_sha(job) != approved_sha:
            raise RunError("the revise run changed work/project.md")
        if redaction_state(job) != red_before:
            raise RunError("the revise run changed the personal-data candidates or decision")
        verify = _final_verify(job, started, "revise")
        after = json.loads((work / "edl.json").read_text())
        tool(job, "edl", "check")  # also: every decided redaction is still covered
        dropped = redaction_removed(before, after)
        if dropped:
            raise RunError(
                "the revise run removed redaction still in the reel: " + "; ".join(dropped)
            )
        render = json.loads((out / "render.json").read_text())
        if render.get("edl_sha256") != _file_sha(work / "edl.json"):
            raise RunError("the final render is not of the current work/edl.json")
        if had_overlays and not render.get("overlay"):
            raise RunError("the revised reel lost its captions/overlays (run overlays again)")
        try:
            claim = json.loads((work / "revision.json").read_text())
        except (OSError, ValueError):
            claim = None
        extra = outside_plan(after, ranges)
        asked = _asked(claim, feedback) if extra else None
        if extra and not asked:
            raise RunError(
                f"segments {extra} use source time outside the approved plan, and the feedback "
                "does not ask for new material (work/revision.json extends_plan + asked)"
            )
    except BaseException as e:  # also Ctrl-C: the previous reel comes back
        problem = _restore(job, had, saved, reel_sha)
        run_ = _last_run(job, "revise", since)
        entry.update(ok=False, error=str(e), cost=run_.get("cost_usd"), turns=run_.get("turns"))
        entry["wall_s"] = round(time.monotonic() - t0, 1)
        _append(work / "revisions.jsonl", entry)
        if not isinstance(e, RunError | agent.AgentError | ToolError | ValueError | OSError):
            raise
        raise RunError(
            f"revision #{n} failed: {e}"
            + (f" ({problem})" if problem else "; the previous reel is back")
        ) from None
    record_cut(job, approved_sha)
    if brand and render.get("overlay"):
        _record_overlays(job, brand, verify)
    shutil.rmtree(_backup_dir(job), ignore_errors=True)
    prune_history(job)
    run_ = _last_run(job, "revise", since)
    entry.update(
        ok=True,
        changed=edl_changes(before, after),
        extends_plan=bool(extra),
        asked=asked,
        summary=claim.get("summary") if isinstance(claim, dict) else None,
        verify_ok=bool(verify.get("ok")),
        previous_reel=str(saved.relative_to(job)),
        cost=run_.get("cost_usd"),
        turns=run_.get("turns"),
        wall_s=round(time.monotonic() - t0, 1),
    )
    _append(work / "revisions.jsonl", entry)
    say(f"revise #{n}: changed {json.dumps(entry['changed'], ensure_ascii=False)}")
    if extra:
        say(f"revise #{n}: extends the plan (segments {extra}), as the feedback asked")
    say(f"verify: {'ok' if verify.get('ok') else 'NOT ok'}; previous reel: {saved}")
    return 0 if verify.get("ok") else 4


def _append(path: Path, entry: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def plan_check_note(job: Path) -> str:
    """One line about work/plan.json after a plan run (the markdown stays the authority)."""
    from .tools.plan import FILE, check_job

    if not (job / FILE).is_file():
        return "plan: no work/plan.json (the app shows the markdown only)"
    _, errs = check_job(job)
    return f"plan: work/plan.json has {len(errs)} problem(s): {errs[0]}" if errs else ""


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
    format_: str | None = None,
    say=print,
    ask: Callable[[str], str] = input,
    feedback: str | None = None,
    at_s: float | None = None,
    plan_feedback: str | None = None,
    cut_style: str | None = None,
    target: str | None = None,
    vocab: list[str] | None = None,
    redact_style: str | None = None,
    slides_mode: str | None = None,
    slides_wish: str | None = None,
) -> int:
    if not agent.shutil.which("claude"):
        raise RunError("Claude Code is not installed (see docs/AUTH.md).")
    if at_s is not None and feedback is None:
        raise RunError("--at needs --feedback")
    others = (plan_feedback, replan or None, recut or None, brand, type_, brief, assets, screen,
              format_, cut_style, target, vocab or None, redact_style, slides_mode,
              slides_wish)  # fmt: skip
    if feedback is not None and any(v is not None for v in others):
        raise RunError(
            "--feedback revises the finished reel; it cannot be combined with --plan-feedback, "
            "--replan, --recut, --brand, --type, --brief, --asset, --screen, --format, "
            "--cut-style, --target, --vocab, --redact-style, --slides-mode or --slides-wish"
        )
    if redact_style is not None:
        _check_redact_style(redact_style)
    if slides_mode is not None:
        _job_slides.parse_mode(slides_mode)  # before a job is made
    if slides_wish is not None:
        _job_slides.parse_wish(slides_wish)
    job = open_job(ref, type_, brief, assets, screen, format_, say, cut_style, target, vocab)
    if redact_style is not None:
        set_redact_style(job, redact_style, say)  # a reel in another style goes stale
    if slides_mode is not None or slides_wish is not None:
        _job_slides.set_slides(job, slides_mode, slides_wish, say)  # a plan for others: stale
    say(f"job: {job}")
    if feedback is not None:
        return revise(job, feedback, at_s, max_turns, say)
    if plan_feedback is not None:
        if not plan_feedback.strip():
            raise RunError("the plan feedback is empty")
        if not (job / "work" / "project.md").is_file():
            raise RunError("there is no plan to revise yet (run without --plan-feedback first)")
        replan = True
    target_changed = plan_target_stale(job)
    if target_changed and not replan:
        old = _length.label(plan_made_for(read_job(job)))
        say(f"plan: work/project.md was made for the target length {old}; planning again")
        replan = True
    slides_changed = _job_slides.plan_stale(job)
    if slides_changed and not replan:
        say("plan: work/project.md was made for other slides; planning again")
        replan = True
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
            prompt, plan_before = PLAN_PROMPT + format_note(job), project_sha(job)
            request = job / "work" / "plan_feedback.json"
            if plan_feedback is not None:
                from .tools.common import write_json

                write_json(request, {"text": plan_feedback.strip(), "plan_sha256": plan_before})
                prompt += PLAN_REVISE
                if target_changed:
                    data = read_job(job)
                    prompt += TARGET_CHANGED.format(
                        old=_length.label(plan_made_for(data)),
                        new=_length.describe(data),
                    )
            else:
                request.unlink(missing_ok=True)  # never a stale request in a fresh plan run
            try:
                planned_for = read_job(job).get("target_s")  # what the prompt asked for
                slides_for = _job_slides.signature(job)
                res = claude(job, "plan", prompt, max_turns, None, say)
            finally:
                request.unlink(missing_ok=True)
            if plan_feedback is not None and project_sha(job) == plan_before:
                say("plan: the feedback did not change work/project.md")
            if note := plan_check_note(job):
                say(note)
            if not project.is_file():
                raise RunError("the plan run did not write work/project.md")
            if _mtime(edl) != edl_before or _snapshot(job / "out") != out_before:
                raise RunError("the plan run wrote an EDL or rendered; it must only plan")
            stamp_plan_target(job, planned_for)  # the target this plan run was asked for
            _job_slides.stamp_plan(job, slides_for)  # and the slides
            if plan_target_stale(job):
                say("plan: the target length changed during the plan run; plan again")
            plan_session, plan_sha = res.session_id, project_sha(job)
        else:
            say("plan: using the existing work/project.md (--replan makes a new one)")
        say(f"\nPlan: {project}\nRead it (edit it if you like) before approving.")
        if not approve and not ask_approval(job, ask):
            for line in not_approved(job, can_ask(ask)):
                say(line)
            return 3
        approve_job(job)
        say("plan: approved")
        interactive = not approve and can_ask(ask)
        decide_personal_data(job, demo_data, interactive, ask, say)

    approved_sha = read_approval(job)["project_sha256"]
    if cut_done(job) and not recut and not plan_session:
        say("cut: already done for the approved plan (--recut cuts again)")
        if not isinstance(read_job(job).get("cut"), dict):
            record_cut(job, approved_sha)  # a job cut before the record existed
    else:
        resume = plan_session if plan_session and project_sha(job) == plan_sha else None
        started = _fs_now(job)
        prompt = CUT_PROMPT + format_note(job)
        if not plan_session and (job / "out" / "reel.mp4").is_file():
            why = "--recut" if recut else stale_why(job)
            prompt += CUT_AGAIN.format(why=why)
        archive_revisions(job)
        claude(job, "cut", prompt, max_turns, resume, say)
        if project_sha(job) != approved_sha:
            raise RunError("work/project.md changed during the cut run; approve it again")
        if why := slides_kept(job):
            raise RunError(f"the cut run changed the approved slides: {why}")
        _final_verify(job, started, "cut")
        record_cut(job, approved_sha)

    job_brand = read_job(job).get("brand")
    if job_brand and (brand or not overlays_done(job, job_brand)):
        cut_sha = segments_sha(job)
        slides_sha = _file_sha(job / "work" / "slides.json")
        started = _fs_now(job)
        pending = read_job(job).get("overlays_pending")
        same_plan = isinstance(pending, dict) and pending.get("project_sha256") == approved_sha
        if not brand and same_plan and overlays_resumable(job, job_brand, cut_sha):
            say("overlays: the last overlay run finished; rendering and verifying again "
                "without a new Claude run")  # fmt: skip
            tool(job, "render")
            tool(job, "verify", need_ok=False)  # flags are judged below, like after a run
        else:
            prompt = OVERLAYS_PROMPT
            old = read_job(job).get("overlays")
            edl_caps = (
                json.loads(edl.read_text()).get("captions") if edl.is_file() else None
            ) or []
            if edl_caps and not (isinstance(old, dict) and old.get("segments_sha256") == cut_sha):
                prompt += OVERLAYS_RECUT
            update_job(job, overlays_pending=None)
            claude(job, "overlays", prompt, max_turns, None, say)
            # The Claude part is done: a failing render or verify below is resumed without it.
            update_job(
                job,
                overlays_pending={
                    "brand": job_brand,
                    "project_sha256": approved_sha,
                    "segments_sha256": cut_sha,  # the run must not change the cut
                    "edl_sha256": _file_sha(edl),
                    "files": _overlay_files(job),
                },
            )
        if project_sha(job) != approved_sha:
            raise RunError("work/project.md changed during the overlay run; approve it again")
        if segments_sha(job) != cut_sha:
            raise RunError(
                "the overlay run changed the cut (EDL segments); it may only add overlays"
            )
        if _file_sha(job / "work" / "slides.json") != slides_sha:
            raise RunError("the overlay run changed work/slides.json; it may only add overlays")
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
            overlays_pending=None,
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


def recut(ref: str, say=print, ask: Callable[[str], str] = input, max_turns: int = 60) -> int:
    """Cut again with the approved plan (e.g. after a changed cut style), then redo the
    overlays: no new plan run and no new approval. RunError when the plan is not approved."""
    try:
        job = resolve_job(ref)
    except ToolError as e:
        raise RunError(str(e)) from None
    if not is_approved(job):
        raise RunError("cutting again needs an approved plan; approve the plan first")
    return run(str(job), recut=True, max_turns=max_turns, say=say, ask=ask)


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
            format_=args.format,
            feedback=args.feedback,
            at_s=args.at,
            plan_feedback=args.plan_feedback,
            cut_style=args.cut_style,
            target=args.target_length,
            vocab=args.vocab,
            redact_style=args.redact_style,
            slides_mode=args.slides_mode,
            slides_wish=args.slides_wish,
            say=functools.partial(print, flush=True),  # progress also when piped to a log
        )
    except (RunError, agent.AgentError, ToolError) as e:
        print(f"\033[31m✗ {e}\033[0m", file=sys.stderr)
        return 1
