"""`videocut tool edl check [file]`: validate an EDL (default work/edl.json).

Prints one line per problem with its JSON path. The source duration comes from
work/probe.json when ingest has run, so out-of-range segments are caught too.

Screen layouts (product demos): `layout`, `screen_zoom` and `redact` of a segment are
validated, and every non-face segment must lie inside the "overlap" of work/sync.json
(`tool sync`), the camera time in which both recordings have pictures.

Personal data: every candidate of work/redaction.json that is to be redacted (the human
decision of `job redaction`; without one: all) must be covered by a redact block of each
screen segment it overlaps, and by a props.redact box of each screenshot_frame of its image
(`redaction_missing`).
Blink warnings (never a failure): with a current work/face.json, every segment `in`/`out`
inside a blink (+-1 frame) is reported with the nearest blink-free time within +-150 ms that
also lies outside every word of work/words.json (when present) as a suggestion.
"""

import json
from pathlib import Path

from .. import redaction
from ..edl import EdlError, blink_warnings, load, read_sync
from .common import ToolError, job_parent, resolve_job, run_tool
from .face import load_face
from .job import redaction_enforced


def check(args) -> dict:
    if args.file and args.job is None:
        path = Path(args.file)
        try:
            job = resolve_job(None)
        except ToolError:
            job = None  # a loose file outside a job: check without source duration
    else:
        job = resolve_job(args.job)
        path = job / args.file if args.file else job / "work" / "edl.json"
    if not path.is_file():
        raise ToolError(f"EDL not found: {path}")
    duration = None
    if job is not None and (job / "work" / "probe.json").is_file():
        duration = json.loads((job / "work" / "probe.json").read_text())["duration"]
    sync = read_sync(job / "work" / "sync.json") if job is not None else None
    try:
        edl = load(path, source_duration=duration, sync=sync, check_sync=job is not None)
    except EdlError as e:
        for err in e.errors:
            print(err)
        raise ToolError(
            f"{len(e.errors)} problem(s) in {path.name}: " + "; ".join(e.errors[:5])
        ) from None
    if job is not None:  # personal data the human decided to redact (or all, by default)
        cands, _ = redaction_enforced(job)
        gaps = redaction.missing(cands, json.loads(path.read_text()))
        if gaps:
            errs = [
                f"{g['where']}: redaction_missing: candidate {g['id']} of work/redaction.json "
                "is not covered (a redact block over its box for its whole time)"
                for g in gaps
            ]
            for err in errs:
                print(err)
            raise ToolError(f"{len(errs)} problem(s) in {path.name}: " + "; ".join(errs[:5]))
    warnings = _blinks(job, edl, duration) if job is not None else []
    for w in warnings:
        print(f"warning: {w}")
    points = edl.cut_points()
    return {
        "file": str(path),
        "segments": len(edl.segments),
        "output_duration": round(edl.output_duration(), 3),
        "cuts": [round(t, 3) for t in points[1:-1]],
        "source_duration": duration,
        "layouts": sorted({s.layout for s in edl.segments}),
        "sync": None if sync is None else sync["method"],
        "warnings": warnings,
    }


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _blinks(job: Path, edl, duration: float | None) -> list[str]:
    face = load_face(job, edl.source)
    if not face or not face.get("blinks"):
        return []
    try:
        words = json.loads((job / "work" / "words.json").read_text())["words"]
        words = [w for w in words if all(_num(w.get(k)) for k in ("t0", "t1"))]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        words = None
    out = []
    for w in blink_warnings(edl, face["blinks"], words, duration):
        hint = f"try {w['suggest']}" if w["suggest"] is not None else "no blink-free time near"
        t0, t1 = w["blink"]
        out.append(f"{w['path']}: {w['t']} falls in a blink {t0}-{t1}; {hint}")
    return out


def register(subparsers) -> None:
    p = subparsers.add_parser("edl", help="work with the edit decision list")
    sub = p.add_subparsers(dest="edl_cmd", required=True)
    c = sub.add_parser("check", help="validate work/edl.json", parents=[job_parent()])
    c.add_argument("file", nargs="?", help="EDL file (default: work/edl.json of the job)")
    c.set_defaults(handler=run_tool("edl check", check))
