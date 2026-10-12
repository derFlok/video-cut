"""`videocut tool render [--preview]`: work/edl.json -> out/ (pipeline steps 6, 7 and 9).

    cut      every segment [in, out] in source time, frame accurate (decode-side trim),
             audio faded by fade_ms at every boundary
    reframe  crop window of the output aspect around crop.cx/cy, `zoom`× tighter, scaled
             to the EDL size at the EDL fps; zoom_keys are hard punch-ins from that
             source time on (no animated zoom yet). A segment without a crop is centred
             on the face median of a current work/face.json (following the face only on
             a large move, face.face_crops), else on the frame centre
    loudness loudnorm two-pass to -14 LUFS / -1 dBTP / LRA 11 (preview: single pass);
             the finished file is measured again
    colour   HDR sources (HLG/PQ) are tonemapped to SDR BT.709 and tagged so (_color.py)
    overlay  work/overlay.mov (transparent, `videocut tool overlays`) newer than edl.json is
             (and than overlays.json) is composited over the cut video (alpha; preview:
             scaled), recorded as "overlay": true in render.json; an older one is
             ignored with a warning
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
from pathlib import Path

from ..edl import EdlError, load
from . import _render_plan as plan_mod
from ._color import is_hdr, output_tags, to_sdr
from ._geometry import framed
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
from .ingest import probe
from .job import PROJECT, is_approved

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
    # The wav starts at the audio's first sample; only use it when that is where the video
    # starts too, else cutting it by source time would shift the audio.
    aligned = abs(info.get("audio_start", 0) - info.get("video_start", 0)) < 0.001
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


def composite_graph(index: int, fps, size: tuple[int, int]) -> str:
    """Filter lines that lay input `index` (the transparent overlay.mov) over [v] as [vo]:
    same fps, scaled to `size`, alpha blended in 4:4:4 so thin overlay edges keep their
    colour; the video decides the length (a shorter overlay just ends)."""
    w, h = size
    return (
        f";\n[{index}:v]setpts=PTS-STARTPTS,fps={fps},scale={w}:{h}:flags=lanczos,"
        "format=yuva444p[ov];\n"
        "[v]format=yuv444p[base];\n"
        "[base][ov]overlay=0:0:format=yuv444:eof_action=pass:shortest=0,"
        "format=yuv420p[vo]\n"
    )


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
    raw = edl_path.read_bytes()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    src = _source(job, data)
    info = probe(src) if src else None
    try:
        edl = load(edl_path, source_duration=info["duration"] if info else None)
    except EdlError as e:
        for err in e.errors:
            print(err)
        raise ToolError(
            f"{len(e.errors)} problem(s) in edl.json: " + "; ".join(e.errors[:5])
        ) from None
    if src is None or info is None:
        raise ToolError(f"EDL source {edl.source!r} is missing or outside the job folder")
    edl, followed, face = framed(job, edl)

    preview = bool(args.preview)
    ow, oh = edl.size
    if preview:
        ow, oh = max(2, ow // 4 * 2), max(2, oh // 4 * 2)
    plan = plan_mod.build(edl, tuple(info["size"]), (ow, oh))
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
    _write_graph(mgraph, plan_mod.measure_graph(plan, audio_in))
    measured = _loudnorm_json(
        _ffmpeg_info(*inputs, "-/filter_complex", str(mgraph), "-map", "[a]", "-f", "null", "-")
    )
    mgraph.unlink(missing_ok=True)
    if not all(math.isfinite(float(measured[k])) for k in plan_mod.MEASURED_KEYS):
        measured = "silent"
    elif preview:
        measured = None  # single pass
    graph = plan_mod.graph(plan, measured, video_in, audio_in, to_sdr(info))
    warnings, v_out = [], "[v]"
    overlay = work / "overlay.mov"
    ov_json = work / "overlays.json"
    use_overlay = is_fresh([overlay], [edl_path] + ([ov_json] if ov_json.is_file() else []))
    if use_overlay:
        graph += composite_graph(1 + len(audio_args) // 2, plan.fps, (ow, oh))
        inputs += ["-i", str(overlay)]
        v_out = "[vo]"
    elif overlay.is_file():
        warnings.append(
            "work/overlay.mov is older than edl.json or overlays.json and was left out; run "
            "`videocut tool overlays` again"
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
    if min_w < plan_mod.SHARP_MIN_WIDTH:
        warnings.append(
            f"crop is only {min_w} px wide (< {plan_mod.SHARP_MIN_WIDTH}); the reel will look soft"
            " — use less zoom or record in 4K / portrait"
        )
    report = {
        "edl_sha256": hashlib.sha256(raw).hexdigest(),
        "output_duration": round(edl.output_duration(), 3),
        "cuts": [round(t, 3) for t in edl.cut_points()[1:-1]],
        "loudness": loud,
        "preview": preview,
    }
    if use_overlay:
        report["overlay"] = True
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
        "warnings": warnings,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser("render", help="render work/edl.json", parents=[job_parent()])
    p.add_argument("--preview", action="store_true", help="fast low-resolution render")
    p.set_defaults(handler=run_tool("render", render))
