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
Readability warnings (never a failure): a screen window whose UI text would be lower than
_layouts.TEXT_MIN_PX output px is reported as `screen_text_small` with the zoom it needs.

`videocut tool edl locate <output_s>`: what the reel shows at an output time (feedback such
as "at 0:12 zoom out less"): the segment, the source (camera) time, its layout, the zoom key
in force (face `zoom_keys` or screen `screen_zoom`, by index), the EDL overlays on screen
(ids = their index in "overlays") and the caption text. Frame timing as render places it;
inside the held end-card tail (render.json "tail_s") it names the last segment.
`--source` maps the other way: source seconds -> every output time that shows them.
`render_current` says whether out/render.json was rendered from this EDL.
"""

import hashlib
import json
from pathlib import Path

from .. import redaction
from ..edl import (
    EdlError,
    blink_warnings,
    load,
    output_spans,
    output_to_source,
    read_sync,
    source_to_output,
)
from . import _layouts
from ._geometry import sized
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
        gaps = redaction.missing(cands, json.loads(path.read_text()), job)
        if gaps:
            errs = [
                f"{g['where']}: redaction_missing: candidate {g['id']} of work/redaction.json "
                "is not covered (a redact block over its box for its whole time)"
                for g in gaps
            ]
            for err in errs:
                print(err)
            raise ToolError(f"{len(errs)} problem(s) in {path.name}: " + "; ".join(errs[:5]))
    warnings = []
    if job is not None:  # readability at the job's output size, as render draws it
        sized_edl, size_note = sized(job, edl)
        warnings = [size_note] if size_note else []
        warnings += _blinks(job, edl, duration) + _text(job, sized_edl)
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


def _text(job: Path, edl) -> list[str]:
    if all(s.layout == "face" for s in edl.segments):
        return []
    try:
        screen = tuple(_layouts.probe_screen(_layouts.screen_source(job))["size"])
    except ToolError:
        return []  # no screen recording: the layout errors say so
    out = []
    for k, s in enumerate(edl.segments):
        if s.layout == "face":
            continue
        for d in _layouts.readability(s, screen, tuple(edl.size)):
            if d["flags"]:
                out.append(
                    f"segments[{k}]: screen_text_small: UI text about {d['text_px']} px high "
                    f"at {d['t']}s (zoom {d['zoom']}x); readable from zoom {d['readable_zoom']}x"
                )
    return out


def _active_key(keys: list, t: float, at) -> tuple[int, object] | None:
    """(index, key) of the last key at or before t (keys sorted by time as render does);
    before the first key: None."""
    best = None
    for i, k in sorted(enumerate(keys), key=lambda ik: at(ik[1])):
        if at(k) <= t + 1e-6:
            best = (i, k)
    return best


def _zoom_key(seg, t: float) -> dict | None:
    if seg.layout == "face":
        hit = _active_key(seg.zoom_keys, t, lambda k: k[0])
        if hit is None:
            return None
        i, (kt, z) = hit
        return {"kind": "zoom_keys", "index": i, "t": kt, "zoom": z}
    if not seg.screen_zoom:
        return None
    hit = _active_key(seg.screen_zoom, t, lambda k: k["t"])
    if hit is None:  # the whole screen, easing into the first key (upcoming)
        i, key = min(enumerate(seg.screen_zoom), key=lambda ik: ik[1]["t"])
        return {"kind": "screen_zoom", "index": i, "t": key["t"], "box": key["box"],
                "upcoming": True}  # fmt: skip
    i, key = hit
    return {"kind": "screen_zoom", "index": i, "t": key["t"], "box": key["box"]}


def _caption_text(edl, t: float) -> str | None:
    for c in edl.captions:
        if isinstance(c, dict) and c.get("t0", 0) <= t < c.get("t1", 0):
            words = [w.get("w", "") for w in c.get("words") or [] if isinstance(w, dict)]
            return " ".join(w for w in words if w) or c.get("text")
    return None


def locate(args) -> dict:
    job = resolve_job(args.job)
    path = job / "work" / "edl.json"
    if not path.is_file():
        raise ToolError(f"EDL not found: {path}")
    try:
        edl = load(path)
    except EdlError as e:
        raise ToolError(f"{path.name} is invalid: " + "; ".join(e.errors[:3])) from None
    try:
        render = json.loads((job / "out" / "render.json").read_text())
    except (OSError, ValueError):
        render = None
    current = isinstance(render, dict) and render.get("edl_sha256") == (
        hashlib.sha256(path.read_bytes()).hexdigest()
    )
    spans = output_spans(edl)
    end = spans[-1][1] if spans else 0.0
    tail = float(render.get("tail_s") or 0) if current else 0.0
    if args.source:
        hits = source_to_output(edl, args.t)
        print(f"source {args.t}s: " + (", ".join(f"{t}s" for _, t in hits) or "not in the cut"))
        return {
            "source_t": args.t,
            "outputs": [{"segment": k, "output_t": t} for k, t in hits],
            "render_current": current,
        }
    if not 0 <= args.t < end + tail:
        raise ToolError(f"output time {args.t}s is outside the reel (0..{round(end + tail, 3)}s)")
    hit = output_to_source(edl, args.t)
    in_tail = hit is None
    k, src_t = hit if hit is not None else (len(edl.segments) - 1, edl.segments[-1].end)
    seg = edl.segments[k]
    overlays = [
        {"id": i, "scene": o.get("scene"), "t0": o.get("t0"), "t1": o.get("t1")}
        for i, o in enumerate(edl.overlays)
        if isinstance(o, dict) and o.get("t0", 0) <= args.t < o.get("t1", 0)
    ]
    a, b = spans[k]
    out = {
        "output_t": args.t,
        "segment": k,
        "segment_in": seg.start,
        "segment_out": seg.end,
        "segment_output": [round(a, 3), round(b, 3)],
        "source_t": round(src_t, 3),
        "layout": seg.layout,
        "zoom_key": _zoom_key(seg, src_t),
        "crop": None if seg.crop is None else vars(seg.crop),
        "overlays": [o["id"] for o in overlays],
        "overlay_items": overlays,
        "caption_text": _caption_text(edl, args.t),
        "tail": in_tail,
        "render_current": current,
    }
    print(
        f"output {args.t}s -> segments[{k}] ({seg.layout}) source {out['source_t']}s"
        + (" (held end-card tail)" if in_tail else "")
        + ("" if current else "; out/render.json is not from this EDL")
    )
    return out


def register(subparsers) -> None:
    p = subparsers.add_parser("edl", help="work with the edit decision list")
    sub = p.add_subparsers(dest="edl_cmd", required=True)
    c = sub.add_parser("check", help="validate work/edl.json", parents=[job_parent()])
    c.add_argument("file", nargs="?", help="EDL file (default: work/edl.json of the job)")
    c.set_defaults(handler=run_tool("edl check", check))
    lo = sub.add_parser(
        "locate", help="what the reel shows at an output time", parents=[job_parent()]
    )
    lo.add_argument("t", type=float, help="output seconds (with --source: source seconds)")
    lo.add_argument(
        "--source", action="store_true", help="map source seconds to output times instead"
    )
    lo.set_defaults(handler=run_tool("edl locate", locate))
