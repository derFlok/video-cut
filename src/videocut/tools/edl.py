"""`videocut tool edl check [file]`: validate an EDL (default work/edl.json).

Prints one line per problem with its JSON path. The source duration comes from
work/probe.json when ingest has run, so out-of-range segments are caught too.

Blink warnings (never a failure): with a current work/face.json, every segment `in`/`out`
inside a blink (+-1 frame) is reported with the nearest blink-free time within +-150 ms that
also lies outside every word of work/words.json (when present) as a suggestion.
"""

import json
from pathlib import Path

from ..edl import EdlError, blink_warnings, load
from .common import ToolError, job_parent, resolve_job, run_tool
from .face import load_face


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
    try:
        edl = load(path, source_duration=duration)
    except EdlError as e:
        for err in e.errors:
            print(err)
        raise ToolError(
            f"{len(e.errors)} problem(s) in {path.name}: " + "; ".join(e.errors[:5])
        ) from None
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
        "warnings": warnings,
    }


def _blinks(job: Path, edl, duration: float | None) -> list[str]:
    face = load_face(job, edl.source)
    if not face or not face.get("blinks"):
        return []
    try:
        words = json.loads((job / "work" / "words.json").read_text())["words"]
    except (OSError, ValueError, KeyError, TypeError):
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
