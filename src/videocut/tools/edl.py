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
Slides: a segment with a slide layout must name a slide of work/slides.json, its
reveal times may not outnumber the slide's build steps, and an image slide's file must
exist (tools/slides.py).
Readability warnings (never a failure): a screen window whose UI text would be lower than
_layouts.TEXT_MIN_PX output px is reported as `screen_text_small` with the zoom it needs.

Cut points in speech (error `cut_in_speech`): every segment `in`/`out` whose +-20 ms of
work/audio.wav lies within 12 dB of the job's speech median (_audio_env) clips a word, unless
the segment has "speech_cut_ok": "<reason>"; `edl snap` moves them to quiet points. Two
segments that continue each other in the source (out == next in) are not a cut there.
"fade_ms" is legacy (render crossfades every cut, segment "xfade_ms" overrides the length).

`videocut tool edl snap`: moves every `in`/`out` to the nearest quiet point within the cut
style's snap_window_s, never across a word the segment must not contain (_snap.py), keeps
the originals as "in_src"/"out_src" and writes work/edl.json (captions, overlays, broll and
cover follow in output time). Idempotent. Prints {moved, max_shift_ms, unresolved}.
Render runs it before every render.

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
    is_screen,
    is_slide,
    load,
    output_spans,
    output_to_source,
    read_sync,
    remap_output_times,
    source_to_output,
    validate,
)
from . import _audio_env, _layouts, _snap
from . import slides as slides_mod
from ._geometry import sized
from .common import ToolError, job_parent, read_job, resolve_job, run_tool, write_json
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
        raw = json.loads(path.read_text())
        errs = [
            f"{g['where']}: redaction_missing: candidate {g['id']} of work/redaction.json "
            f"is not covered; add {json.dumps(g['need'])}"
            for g in redaction.missing(cands, raw, job)
        ]
        env = _audio_env.job_envelope(job) if _wav_matches(job, edl.source) else None
        errs += [
            f"$.segments[{c['segment']}].{c['edge']}: cut_in_speech: {c['t']} cuts into "
            f"speech ({c['level_db']} dB, speech median {round(env.speech_db, 1)} dB); "
            "run `videocut tool edl snap`, move the cut to a pause, or set "
            '"speech_cut_ok": "<reason>"'
            for c in (_snap.in_speech(raw, env, duration) if env else [])
        ]
        if any(is_slide(seg.layout) for seg in edl.segments):  # the slides it names
            try:
                doc = slides_mod.load_doc(job)
                errs += slides_mod.edl_problems(doc, raw) + slides_mod.asset_errors(job, doc)
            except ToolError as e:
                errs.append(f"$.segments: the EDL shows slides but {e}")
        if errs:
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


def _wav_matches(job: Path, source: str) -> bool:
    """work/audio.wav is the audio of the EDL source (ingest made it from job.json "source")."""
    try:
        return read_job(job).get("source") == source and (job / "work" / "audio.wav").is_file()
    except (OSError, ValueError):
        return False


def _words(job: Path) -> tuple[list[dict], bool]:
    """(words of work/words.json with numeric times in video time, are they aligned?)."""
    try:
        words = json.loads((job / "work" / "words.json").read_text())["words"]
        words = [w for w in words if all(_num(w.get(k)) for k in ("t0", "t1"))]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        words = []
    off = _audio_env.wav_offset(job)  # a legacy job: words are in audio-stream time
    if off:
        words = [{**w, "t0": w["t0"] + off, "t1": w["t1"] + off} for w in words]
    try:
        meta = json.loads((job / "work" / "transcribe.json").read_text())
        aligned = isinstance(meta.get("align"), dict)
    except (OSError, ValueError, AttributeError):
        aligned = False
    return words, aligned or any("t0_asr" in w for w in words)


def snap_job(job: Path, write: bool = True) -> dict:
    """Snap work/edl.json of `job` (see `snap`); writes it only when a cut point moved.
    Returns the summary plus "style", "written" and "skipped" (why nothing was snapped)."""
    path = job / "work" / "edl.json"
    data = json.loads(path.read_text())
    probe = job / "work" / "probe.json"
    duration = json.loads(probe.read_text())["duration"] if probe.is_file() else None
    if validate(data, duration):
        raise ToolError("work/edl.json is invalid; run `videocut tool edl check`")
    style = _snap.cut_style(read_job(job))
    base = {
        "moved": 0,
        "max_shift_ms": 0,
        "unresolved": [],
        "style": style["name"],
        "written": False,
    }
    if not _wav_matches(job, data["source"]):
        return {**base, "skipped": "no work/audio.wav of the EDL source"}
    env = _audio_env.job_envelope(job)
    if env is None:
        return {**base, "skipped": "work/audio.wav is unreadable"}
    if env.offset:
        # A job ingested before the video time base: snapping it would leave joins and
        # fixed edges in the old time base, and the upgrade would then move it again.
        return {
            **base,
            "skipped": "work/audio.wav is not in video time yet; run "
            "`videocut tool ingest` (it moves the job into video time) and snap again",
        }
    words, aligned = _words(job)
    sync = read_sync(job / "work" / "sync.json")
    new, summary = _snap.snap(
        data, env, words, style["snap_window_s"], style["xfade_ms"] / 1000, duration, aligned,
        0.0, tuple(sync["overlap"]) if sync else None,
    )  # fmt: skip
    out = {**base, **summary, "aligned_words": aligned}
    if summary["moved"]:
        new = remap_output_times(new, data["segments"], new["segments"])
        if write:
            write_json(path, new)
            out["written"] = True
    return out


def snap(args) -> dict:
    job = resolve_job(args.job)
    if not (job / "work" / "edl.json").is_file():
        raise ToolError(f"EDL not found: {job / 'work' / 'edl.json'}")
    out = snap_job(job, write=not args.dry_run)
    if out.get("skipped"):
        print(f"not snapped: {out['skipped']}")
    dry = out["moved"] and not out["written"]
    print(
        f"snapped: {out['moved']} cut point(s) moved, max {out['max_shift_ms']} ms "
        f"(style {out['style']})" + ("; dry run, edl.json unchanged" if dry else "")
    )
    for u in out["unresolved"]:
        print(
            f"unresolved: $.segments[{u['segment']}].{u['edge']} at {u['t']} stays in speech "
            f'({u["level_db"]} dB): move the cut to a pause or set "speech_cut_ok"'
        )
    return out


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
    if not any(is_screen(s.layout) for s in edl.segments):
        return []
    try:
        screen = tuple(_layouts.probe_screen(_layouts.screen_source(job))["size"])
    except ToolError:
        return []  # no screen recording: the layout errors say so
    out = []
    for k, s in enumerate(edl.segments):
        if not is_screen(s.layout):
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
    sn = sub.add_parser(
        "snap", help="move cut points to quiet points (work/edl.json)", parents=[job_parent()]
    )
    sn.add_argument("--dry-run", action="store_true", help="report only, do not write")
    sn.set_defaults(handler=run_tool("edl snap", snap))
    lo = sub.add_parser(
        "locate", help="what the reel shows at an output time", parents=[job_parent()]
    )
    lo.add_argument("t", type=float, help="output seconds (with --source: source seconds)")
    lo.add_argument(
        "--source", action="store_true", help="map source seconds to output times instead"
    )
    lo.set_defaults(handler=run_tool("edl locate", locate))
