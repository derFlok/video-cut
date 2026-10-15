"""`videocut tool render [--preview]`: work/edl.json -> out/ (pipeline steps 6, 7 and 9).

    snap     first `edl snap` (tools/edl.py, _snap.py): every cut point that is not in a
             quiet place moves to the nearest one within the cut style's snap_window_s;
             work/edl.json is rewritten when one moved (render.json "snap": moved,
             max_shift_ms, unresolved, style)
    cut      every segment [in, out] in source time, frame accurate (decode-side trim);
             the audio crossfades at every cut (equal power, the cut style's xfade_ms or
             the segment's "xfade_ms", centred, duration unchanged, shortened to the quiet
             room around the cut points; _render_plan), the video is a hard cut
    reframe  crop window of the output aspect around crop.cx/cy, `zoom`× tighter, scaled
             to the job's output size (job.json "format", _format.py; an EDL "size" of
             another format is overridden with a warning) at the EDL fps; zoom_keys are
             hard punch-ins from that source time on (no animated zoom yet). A segment
             without a crop is centred on the face median of a current work/face.json
             (following the face only on a large move, face.face_crops), else on the frame
             centre. Soft-crop warning below _render_plan.sharp_min_width (scaled with the
             output width)
    loudness loudnorm two-pass to -14 LUFS / -1 dBTP / LRA 11 (preview: single pass);
             the finished file is measured again
    colour   HDR sources (HLG/PQ) are tonemapped to SDR BT.709 and tagged so (_color.py)
    overlay  work/overlay.mov (transparent, `videocut tool overlays`) newer than edl.json is
             (and than overlays.json) is composited over the cut video (alpha; preview:
             scaled), recorded as "overlay": true in render.json; an older one is
             ignored with a warning. When the end card needs it (_plates: it starts
             after the last spoken word), the reel gets a held tail: the last frame
             repeats under the card, the audio is padded with silence after loudnorm
             (render.json "tail_s"; output_duration includes it)
    layouts  segments with "layout" screen/pip/split are drawn from the original screen
             recording (job.json "screen") at camera time + work/sync.json offset_s, with
             eased screen_zoom, redact blocks and brand background (_layouts.py); render.json
             gets "layouts" and "screen_zoom_clamped" (keys widened to stay >= 1 source px
             per output px). An EDL of face segments only renders exactly as before.
             Personal data: every candidate of work/redaction.json the human did not
             mark as demo data (no decision: all) is added as a redact block to the
             screen segments it overlaps (render.json "redaction")
    encode   H.264 high yuv420p + AAC 48 kHz 192k, +faststart, constant EDL fps (also
             from variable-frame-rate phone recordings)

Audio: work/audio.wav when ingest made it from this source (so `ingest --denoise` reaches
the reel; it is sample-aligned with the source), else the source's audio stream chosen
by ingest's probe (first AAC/PCM one, never a spatial-audio track).

Writes out/reel.mp4 + out/cover.jpg (preview: out/preview.mp4, half size, fast), and
out/render.json. The filter graph of the last run is kept in work/render_filter.txt; it is
passed to ffmpeg as a file, so long EDLs never hit the command line limit.

Both modes refuse unless a human approved the current work/project.md (job.is_approved).
"""

import hashlib
import json
import math
import re
from fractions import Fraction
from pathlib import Path

from ..edl import EdlError, load, read_sync, validate
from . import _audio_env, _layouts, _plates, _redact_blur, _redact_style, _snap
from . import _render_plan as plan_mod
from ._color import is_hdr, output_tags, to_sdr
from ._geometry import framed, sized
from .common import (
    ToolError,
    binary,
    ffmpeg_to,
    is_fresh,
    job_parent,
    read_job,
    resolve_job,
    run,
    run_tool,
    write_json,
)
from .edl import snap_job
from .ingest import audio_offset, probe
from .job import PROJECT, is_approved, redaction_enforced

CRF, PRESET = "18", "medium"
PREVIEW_PRESET = "veryfast"
COVER_OFFSET = 0.5  # default cover: this far into the first segment


def _source(job: Path, data) -> Path | None:
    rel = data.get("source") if isinstance(data, dict) else None
    if not isinstance(rel, str) or not rel:
        return None
    p = (job / rel).resolve()
    return p if p.is_relative_to(job) and p.is_file() else None


def _loudnorm_json(stderr: str) -> dict:
    """The JSON block loudnorm prints at the end of a run (print_format=json)."""
    m = re.search(r"\[Parsed_loudnorm[^\n]*\n(\{.*?\})", stderr, re.S)
    if not m:
        raise ToolError("loudnorm printed no measurement")
    return json.loads(m.group(1))


def _ffmpeg_info(*args: str) -> str:
    """ffmpeg at info log level (loudnorm prints its numbers there); returns stderr."""
    out = run([binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "info", *args])
    return out.stderr


def measure(path: Path) -> dict:
    """Integrated loudness, true peak and loudness range of a finished file."""
    stats = _loudnorm_json(
        _ffmpeg_info(
            "-i",
            str(path),
            "-vn",
            "-af",
            f"loudnorm=I={plan_mod.TARGET_I}:TP={plan_mod.TARGET_TP}"
            f":LRA={plan_mod.TARGET_LRA}:print_format=json",
            "-f",
            "null",
            "-",
        )
    )
    return {
        "integrated_lufs": _finite(stats["input_i"]),
        "true_peak_dbtp": _finite(stats["input_tp"]),
        "lra": _finite(stats["input_lra"]),
    }


def _finite(v) -> float | None:
    """loudnorm reports -inf for silence; JSON has no infinity, so that becomes None."""
    f = float(v)
    return f if math.isfinite(f) else None


def _audio_input(job: Path, src: Path, rel: str, info: dict) -> tuple[list[str], str, str]:
    """(extra ffmpeg input args, filter input label, description) for the audio to cut."""
    wav, meta = job / "work" / "audio.wav", job / "work" / "audio.json"
    # The wav is in video time when ingest says so ("time_base": "video": padded or trimmed
    # by the audio stream's start). A legacy wav starts at the audio's first sample, so it
    # is only usable when that is where the video starts too.
    try:
        video_time = json.loads(meta.read_text()).get("time_base") == "video"
    except (OSError, ValueError, AttributeError):
        video_time = False
    aligned = video_time or abs(info.get("audio_start", 0) - info.get("video_start", 0)) < 0.001
    fresh = meta.is_file() and is_fresh([wav, meta], [src])
    if read_job(job).get("source") == rel and aligned and fresh:
        return ["-i", str(wav)], "1:a", "work/audio.wav"
    return [], f"0:{info['audio_index']}", f"source stream {info['audio_index']}"


def _write_graph(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _duration(path: Path) -> float:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    return float(out.stdout.strip())


def overlay_matrix(info: dict) -> str:
    """YUV matrix of the cut video the overlay is blended into: BT.709 after a tonemap and
    for untagged sources (what players assume for HD), else the source's own matrix."""
    space = info.get("color_space")
    if is_hdr(info) or space not in ("bt709", "smpte170m", "bt470bg"):
        return "bt709"
    return space


def composite_graph(index: int, fps, size: tuple[int, int], matrix: str = "bt709") -> str:
    """Filter lines that lay input `index` (the transparent overlay.mov) over [v] as [vo]:
    same fps, scaled to `size`, converted from the overlay's BT.709 to `matrix` (the cut
    video's), alpha blended in 4:4:4 so thin overlay edges keep their colour; the video
    decides the length (a shorter overlay just ends). Both inputs are put on the same
    frame grid first (pts = frame number), so overlay frame n lands on reel frame n even
    when the joined segments carry rounded timestamps."""
    w, h = size
    tb = f"{Fraction(fps).denominator}/{Fraction(fps).numerator}"
    return (
        f";\n[{index}:v]setpts=PTS-STARTPTS,fps={fps},settb={tb},setpts=N,"
        f"scale={w}:{h}:flags=lanczos:in_color_matrix=bt709:in_range=tv:"
        f"out_color_matrix={matrix}:out_range=tv,format=yuva444p[ov];\n"
        f"[v]settb={tb},setpts=N,format=yuv444p[base];\n"
        "[base][ov]overlay=0:0:format=yuv444:eof_action=pass:shortest=0,"
        "format=yuv420p[vo]\n"
    )


def _overlay_size(path: Path) -> tuple[int, int] | None:
    """The output size overlays.json was made for (None when unreadable)."""
    try:
        size = json.loads(path.read_text()).get("size")
        return (int(size[0]), int(size[1]))
    except (OSError, ValueError, TypeError, AttributeError, IndexError, OverflowError):
        return None


def _style_doc(b: dict) -> dict:
    """A render.json "redaction" "blocks" entry: segment, camera times, box, style used."""
    keys = ("segment", "t0", "t1", "style", "edge", "cell", "note", "grown")
    doc = {k: b[k] for k in keys if k in b}
    # "box" as drawn; "planned" the box before it grew over the text lines it cut
    # (_redact_grow)
    if "planned" in b:
        doc["planned"] = [round(v, 4) for v in b["planned"]]
    return {**doc, "box": [round(v, 4) for v in b["box"]]}


def _layout_video(job, plan, edl, info, face, video_in, inputs):
    """Video lines of a render with screen layouts (the screen is the next input after
    `inputs`) and the _layouts.plan_all doc (screen, clamped, speaker, warnings)."""
    drawn = _layouts.plan_all(job, edl, plan, tuple(info["size"]), face, edl.size)
    # a blur text recognition still reads is made stronger (or a solid block) first
    drawn["blur_raised"] = _redact_blur.calibrate(drawn, plan.fps)
    video = _layouts.video_chain(
        plan, drawn["segs"], lambda r: plan_mod.range_filters(plan, r), video_in,
        f"{len(inputs) // 2}:v:0", bg=_layouts.background(job), matrix=overlay_matrix(info),
        cam_sdr=to_sdr(info), screen_sdr=to_sdr(drawn["info"]),
    )  # fmt: skip
    return video, drawn


def render(args) -> dict:
    job = resolve_job(args.job)
    if not is_approved(job):  # the human gate: no cut, not even a preview, before approval
        raise ToolError(
            f"{PROJECT} is not approved (or changed since); a human runs "
            f"`videocut tool job approve {job.name}` first"
        )
    work, out = job / "work", job / "out"
    edl_path = work / "edl.json"
    if not edl_path.is_file():
        raise ToolError(f"EDL not found: {edl_path}")
    try:
        data = json.loads(edl_path.read_bytes())
    except json.JSONDecodeError:
        data = {}
    src = _source(job, data)
    info = probe(src) if src else None
    snapped = None
    if info and not validate(data, info["duration"]):  # an invalid EDL: load reports it
        snapped = snap_job(job)
    raw = edl_path.read_bytes()
    data = json.loads(raw) if snapped and snapped["written"] else data
    try:
        edl = load(
            edl_path,
            source_duration=info["duration"] if info else None,
            sync=read_sync(work / "sync.json"),
            check_sync=True,  # screen layouts only where both recordings have pictures
        )
    except EdlError as e:
        for err in e.errors:
            print(err)
        raise ToolError(
            f"{len(e.errors)} problem(s) in edl.json: " + "; ".join(e.errors[:5])
        ) from None
    if src is None or info is None:
        raise ToolError(f"EDL source {edl.source!r} is missing or outside the job folder")
    edl, size_note = sized(job, edl)
    edl, followed, face = framed(job, edl)
    # Personal data: every candidate the human did not mark as demo data is redacted, also
    # when the EDL forgot one (redaction.enforced; no decision = all of them).
    cands, red_source = redaction_enforced(job)
    edl = _layouts.with_redaction(edl, cands)
    redact_style = _redact_style.resolved(read_job(job))  # ToolError for an invalid one
    layouts = [s.layout for s in edl.segments]
    use_layouts = any(lay != "face" for lay in layouts)

    preview = bool(args.preview)
    ow, oh = edl.size
    if preview:
        ow, oh = max(2, ow // 4 * 2), max(2, oh // 4 * 2)
    style = _snap.cut_style(read_job(job))
    env = _audio_env.job_envelope(job) if snapped and not snapped.get("skipped") else None
    # room for the longest crossfade asked for (a segment's own xfade_ms may exceed the style)
    own = [s["xfade_ms"] for s in data["segments"] if isinstance(s.get("xfade_ms"), int | float)]
    caps = _snap.xfade_caps(data, env, max([style["xfade_ms"], *own]) / 2000) if env else None
    plan = plan_mod.build(
        edl, tuple(info["size"]), (ow, oh), layouts if use_layouts else None,
        style["xfade_ms"] / 1000, info["duration"], caps,
    )  # fmt: skip
    overlay = work / "overlay.mov"
    ov_json = work / "overlays.json"
    # words.json too: the end card's tail is computed from it (_plates), like overlays did.
    ov_inputs = [p for p in (ov_json, work / "words.json") if p.is_file()]
    use_overlay = is_fresh([overlay], [edl_path, *ov_inputs])
    made_for = _overlay_size(ov_json)
    if use_overlay and made_for and made_for != tuple(edl.size):  # made for another format
        use_overlay = False
    if use_overlay and plan.frames:  # the end card's held tail (same schedule as overlays)
        plan = plan_mod.with_tail(plan, _plates.for_job(job, edl, plan.duration).tail_frames)
    audio_args, audio_in, audio_from = _audio_input(job, src, edl.source, info)
    video_in = f"0:{info['video_index']}"
    inputs = ["-i", str(src), *audio_args]
    if plan.frames == 0 or not plan.ranges or not plan.audio:
        raise ToolError("the EDL is shorter than one output frame")
    work.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)

    # First loudness pass (audio only, fast). Also run for previews: loudnorm turns digital
    # silence into NaN samples, so silence must be known before the render.
    mgraph = work / "render_measure.txt"
    # a source stream is moved into video time by its start; work/audio.wav already is
    a_off = 0.0 if audio_args else audio_offset(info)
    _write_graph(mgraph, plan_mod.measure_graph(plan, audio_in, a_off))
    measured = _loudnorm_json(
        _ffmpeg_info(*inputs, "-/filter_complex", str(mgraph), "-map", "[a]", "-f", "null", "-")
    )
    mgraph.unlink(missing_ok=True)
    if not all(math.isfinite(float(measured[k])) for k in plan_mod.MEASURED_KEYS):
        measured = "silent"
    elif preview:
        measured = None  # single pass
    warnings, v_out, video, clamped, screen_rel, speaker = [], "[v]", None, [], None, []
    styles = []  # how each redact block of the screen segments was drawn
    if size_note:
        warnings.append(size_note)
    if snapped and snapped["unresolved"]:
        warnings.append(
            f"{len(snapped['unresolved'])} cut point(s) stay in speech after snapping "
            "(`videocut tool edl snap` lists them)"
        )
    if use_layouts:
        video, drawn = _layout_video(job, plan, edl, info, face, video_in, inputs)
        clamped, speaker = drawn["clamped"], drawn["speaker"]
        warnings += drawn["warnings"]
        styles = [_style_doc(b) for b in drawn["redaction"]]
        warnings += [
            f"segment {b['segment']}: redaction {b['note']}" for b in styles if "note" in b
        ]
        screen_rel = str(drawn["screen"].relative_to(job))
        inputs += ["-i", str(drawn["screen"])]
        for c in clamped:
            warnings.append(
                f"segment {c['segment']}: screen zoom {c['zoom']}x at {c['t']} s would be"
                f" blurry; clamped to {c['max_zoom']}x"
            )
    graph = plan_mod.graph(plan, measured, video_in, audio_in, to_sdr(info), video, a_off)
    if use_overlay:
        graph += composite_graph(len(inputs) // 2, plan.fps, (ow, oh), overlay_matrix(info))
        inputs += ["-i", str(overlay)]
        v_out = "[vo]"
    elif overlay.is_file():
        warnings.append(
            "work/overlay.mov is older than edl.json, overlays.json or words.json, or was made "
            "for another output size, and was left out; run `videocut tool overlays` again"
        )
    elif edl.captions or edl.overlays:
        warnings.append(
            "the EDL has captions/overlays but there is no work/overlay.mov; run "
            "`videocut tool overlays` first"
        )
    graph_file = work / "render_filter.txt"
    _write_graph(graph_file, graph)

    dst = out / ("preview.mp4" if preview else "reel.mp4")
    print(f"rendering {dst.name}: {len(edl.segments)} segments, {len(plan.ranges)} ranges")
    ffmpeg_to(
        dst,
        *inputs,
        "-/filter_complex",
        str(graph_file),
        "-map",
        v_out,
        "-map",
        "[a]",
        "-r",
        str(plan.fps),
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "23" if preview else CRF,
        "-preset",
        PREVIEW_PRESET if preview else PRESET,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        str(plan_mod.RATE),
        *output_tags(info),
        "-movflags",
        "+faststart",
    )

    cover = None
    if not preview:
        t = data.get("cover")
        if not isinstance(t, int | float) or isinstance(t, bool) or not 0 <= t < plan.duration:
            t = min(COVER_OFFSET, plan.duration / 2)
        cover = out / "cover.jpg"
        ffmpeg_to(
            cover,
            "-ss",
            f"{t:.3f}",
            "-i",
            str(dst),
            "-frames:v",
            "1",
            "-vf",
            f"scale={edl.size[0]}:{edl.size[1]}",
            "-q:v",
            "2",
            "-update",
            "1",
        )

    loud = measure(dst)
    min_w = plan.min_crop_width()
    if min_w < (sharp := plan_mod.sharp_min_width(edl.size)):
        warnings.append(
            f"crop is only {min_w} px wide (< {sharp}); the reel will look soft"
            " — use less zoom or record in 4K / portrait"
        )
    report = {
        "edl_sha256": hashlib.sha256(raw).hexdigest(),
        "output_duration": round(edl.output_duration() + float(plan.tail / plan.fps), 3),
        "cuts": [round(t, 3) for t in edl.cut_points()[1:-1]],
        "loudness": loud,
        "preview": preview,
    }
    if snapped is not None:
        report["snap"] = {
            k: snapped.get(k)
            for k in ("style", "moved", "max_shift_ms", "unresolved", "written", "skipped")
            if k in snapped
        }
    report["xfade_ms"] = style["xfade_ms"]
    if use_overlay:
        report["overlay"] = True
    if use_layouts:
        report["layouts"] = {k: layouts.count(k) for k in _layouts.LAYOUTS if k in layouts}
        report["screen_zoom_clamped"] = clamped
    if red_source != "none" or styles:
        report["redaction"] = {
            "source": red_source, "candidates": [c["id"] for c in cands],
            "style": redact_style, "blocks": styles,
        }  # fmt: skip
    if plan.tail:
        report["tail_s"] = round(float(plan.tail / plan.fps), 3)
    if loud["integrated_lufs"] is None:
        warnings.append("the reel is silent")
    elif abs(off := loud["integrated_lufs"] - plan_mod.TARGET_I) > 1:
        warnings.append(
            f"loudness {loud['integrated_lufs']} LUFS is {off:+.1f} LU off target"
            " (loudnorm needs a few seconds of material)"
        )
    write_json(out / "render.json", report)
    for w in warnings:
        print(f"warning: {w}")
    return {
        "job": str(job),
        "output": str(dst.relative_to(job)),
        "cover": str(cover.relative_to(job)) if cover else None,
        "size": [ow, oh],
        "fps": edl.fps,
        "duration": round(_duration(dst), 3),
        "output_duration": report["output_duration"],
        "segments": len(edl.segments),
        "cuts": report["cuts"],
        "loudness": loud,
        "min_crop_width": min_w,
        "preview": preview,
        "hdr_tonemapped": is_hdr(info),
        "audio_from": audio_from,
        "default_crop": "face" if face and face.get("median") else "centre",
        "face_follow_segments": followed,
        "overlay": use_overlay,
        "layouts": report.get("layouts"),
        "screen": screen_rel,
        "screen_zoom_clamped": clamped,
        "speaker": speaker,
        "redaction": report.get("redaction"),
        "snap": report.get("snap"),
        "warnings": warnings,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser("render", help="render work/edl.json", parents=[job_parent()])
    p.add_argument("--preview", action="store_true", help="fast low-resolution render")
    p.set_defaults(handler=run_tool("render", render))
