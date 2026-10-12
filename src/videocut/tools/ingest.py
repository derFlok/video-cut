"""`videocut tool ingest [--denoise]`: probe the source, extract audio, build a proxy.

Writes into work/:
    probe.json      duration, fps, display size (after rotation), rotation, colour (HDR?),
                    the video and audio stream used, audio rate
    audio_raw.wav   48 kHz mono PCM straight from the source
    audio.wav       48 kHz mono PCM the rest of the pipeline uses; with --denoise
                    cleaned by DeepFilterNet (delay-compensated), else equal to the raw track
    audio.json      how audio.wav was made (so a changed --denoise redoes it)
    proxy.mp4       short side 360 px, source fps, SDR BT.709 (HDR tonemapped), for
                    contact sheets and bursts

With a screen recording in job.json ("screen", product demos) also:
    screen_probe.json   like probe.json (audio fields null when it has no audio) plus
                        "file" (the job-relative path it describes) and "vfr"
    screen_audio.wav    48 kHz mono PCM of its microphone track (missing without audio);
                        `tool sync` aligns it with the camera audio
    screen_proxy.mp4    long side 960 px, constant 30 fps, no audio, SDR; time 0 is the
                        first video frame ("screen time"). Screen recordings are often VFR
                        (frames only when something changes); the proxy is CFR so frame n
                        is screen time n/30. The original stays the render input.

Audio comes from one deliberately chosen stream: the first AAC/PCM one, else the first
other decodable one (phones add extra tracks such as spatial audio that ffmpeg's default
"most channels" pick would prefer or fail on).

Idempotent: every output that is newer than its input is kept.
"""

import json
import shutil
import tempfile
from fractions import Fraction
from pathlib import Path

from ._color import is_hdr, output_tags, to_sdr
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
    source_path,
    update_job,
    write_json,
)

RATE = 48000
PROXY_SHORT_SIDE = 360
SCREEN_PROXY_LONG_SIDE = 960
SCREEN_PROXY_FPS = 30
PROBE_VERSION = 2  # bump when probe.json gains fields, so old jobs re-probe
UNDECODABLE_AUDIO = {"apple_apac", "unknown", None}


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return int(round(float(sd["rotation"]))) % 360
    return int(stream.get("tags", {}).get("rotate", 0)) % 360


def _pick_audio(streams: list[dict]) -> dict | None:
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    for s in audio:
        name = s.get("codec_name") or ""
        if name == "aac" or name.startswith("pcm_"):
            return s
    return next((s for s in audio if s.get("codec_name") not in UNDECODABLE_AUDIO), None)


def _vfr(video: dict) -> bool:
    try:
        r = Fraction(video.get("r_frame_rate") or "0/1")
        avg = Fraction(video.get("avg_frame_rate") or "0/1")
    except (ValueError, ZeroDivisionError):
        return False
    return bool(r and avg) and abs(float(r) - float(avg)) > 0.01 * float(r)


def probe(src: Path, need_audio: bool = True) -> dict:
    """Stream facts of a recording. Without `need_audio` (screen recordings may have no
    microphone track) a missing audio stream gives null audio fields instead of an error."""
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(src),
        ]
    )
    info = json.loads(out.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = _pick_audio(streams)
    if video is None:
        raise ToolError("source has no video stream")
    if audio is None and need_audio:
        if any(s.get("codec_type") == "audio" for s in streams):
            raise ToolError("source has no decodable audio stream")
        raise ToolError("source has no audio stream")
    rate = video.get("avg_frame_rate") or "0/1"
    if rate in ("0/0", "0/1"):
        rate = video.get("r_frame_rate", "0/1")
    fps = float(Fraction(rate)) if rate not in ("0/0", "") else 0.0
    rotation = _rotation(video)
    w, h = int(video["width"]), int(video["height"])
    if rotation in (90, 270):
        w, h = h, w
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    audio = audio or {}
    info = {
        "version": PROBE_VERSION,
        "duration": round(duration, 3),
        "fps": round(fps, 3),
        "fps_exact": rate,
        "size": [w, h],
        "coded_size": [int(video["width"]), int(video["height"])],
        "rotation": rotation,
        "video_codec": video.get("codec_name"),
        # Stream start times: a wav extracted from the audio starts at its first sample.
        "video_start": float(video.get("start_time") or 0),
        "audio_start": float(audio.get("start_time") or 0) if audio else None,
        "video_index": int(video["index"]),
        "pix_fmt": video.get("pix_fmt"),
        "color_transfer": video.get("color_transfer"),
        "color_primaries": video.get("color_primaries"),
        "color_space": video.get("color_space"),
        "audio_index": int(audio["index"]) if audio else None,
        "audio_rate": int(audio.get("sample_rate", 0)) if audio else None,
        "audio_channels": int(audio.get("channels", 0)) if audio else None,
        "audio_codec": audio.get("codec_name"),
        # Variable frame rate: the nominal rate differs from the average one.
        "vfr": _vfr(video),
    }
    info["hdr"] = is_hdr(info)
    return info


def _extract_audio(src: Path, dst: Path, index: int | None = None) -> None:
    pick = ["-map", f"0:{index}"] if index is not None else ["-vn"]
    ffmpeg_to(dst, "-i", str(src), *pick, "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le")


def _denoise(raw: Path, dst: Path) -> None:
    with tempfile.TemporaryDirectory(dir=dst.parent) as tmp:
        # -D compensates STFT/lookahead delay, so the cleaned track stays in sync with video.
        run([binary("deep-filter"), "-D", "-o", tmp, str(raw)])
        cleaned = Path(tmp) / raw.name
        if not cleaned.is_file():
            raise ToolError("deep-filter wrote no output")
        # Normalise format (deep-filter keeps the rate but may write float samples).
        _extract_audio(cleaned, dst)


def _proxy(src: Path, dst: Path, info: dict) -> None:
    s = PROXY_SHORT_SIDE
    scale = f"scale='if(gt(iw,ih),-2,{s})':'if(gt(iw,ih),{s},-2)'"
    vf = ",".join(f for f in (scale, to_sdr(info)) if f)
    ffmpeg_to(
        dst,
        "-i",
        str(src),
        "-map",
        f"0:{info['video_index']}",
        "-map",
        f"0:{info['audio_index']}",
        "-vf",
        vf,
        *output_tags(info),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ac",
        "1",
        "-movflags",
        "+faststart",
    )


def screen_path(job: Path) -> Path | None:
    """The job's screen recording, None when it has none; a broken entry fails."""
    rel = read_job(job).get("screen")
    if not rel:
        return None
    p = (job / rel).resolve()
    if not p.is_relative_to(job.resolve()) or not p.is_file():
        raise ToolError(f"screen recording {rel!r} is missing or outside the job folder")
    return p


def _screen_proxy(src: Path, dst: Path, info: dict) -> None:
    s = SCREEN_PROXY_LONG_SIDE
    scale = f"scale='if(gt(iw,ih),{s},-2)':'if(gt(iw,ih),-2,{s})'"
    # setpts: time 0 = first video frame; fps: duplicate/drop to a constant rate.
    chain = ["setpts=PTS-STARTPTS", f"fps={SCREEN_PROXY_FPS}", scale, to_sdr(info)]
    ffmpeg_to(
        dst,
        "-i",
        str(src),
        "-map",
        f"0:{info['video_index']}",
        "-an",
        "-vf",
        ",".join(f for f in chain if f),
        *output_tags(info),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    )


def _ingest_screen(job: Path, step) -> dict | None:
    """The screen recording's outputs (see the module docstring); None without one."""
    src = screen_path(job)
    if src is None:
        return None
    work = job / "work"
    rel = read_job(job)["screen"]
    probe_file = work / "screen_probe.json"
    audio, proxy = work / "screen_audio.wav", work / "screen_proxy.mp4"
    try:
        prev = json.loads(probe_file.read_text())
        if prev.get("version") != PROBE_VERSION or prev.get("file") != rel:
            probe_file.unlink()
    except (OSError, ValueError):
        pass

    def make_probe() -> None:
        write_json(probe_file, {**probe(src, need_audio=False), "file": rel})
        audio.unlink(missing_ok=True)  # stream choice may differ: redo what uses it
        proxy.unlink(missing_ok=True)

    step("screen_probe", [probe_file], [src], make_probe)
    info = json.loads(probe_file.read_text())
    if info["audio_index"] is not None:
        step(
            "screen_audio", [audio], [src], lambda: _extract_audio(src, audio, info["audio_index"])
        )
    else:
        audio.unlink(missing_ok=True)
    step("screen_proxy", [proxy], [src], lambda: _screen_proxy(src, proxy, info))
    return {
        "file": rel,
        "duration": info["duration"],
        "fps": info["fps"],
        "vfr": info["vfr"],
        "size": info["size"],
        "audio": info["audio_index"] is not None,
    }


def ingest(args) -> dict:
    job = resolve_job(args.job)
    src = source_path(job)
    work = job / "work"
    work.mkdir(exist_ok=True)
    done, skipped = [], []

    def step(name: str, outputs: list[Path], inputs: list[Path], fn) -> None:
        if is_fresh(outputs, inputs):
            skipped.append(name)
        else:
            fn()
            done.append(name)

    probe_file = work / "probe.json"
    raw, proxy = work / "audio_raw.wav", work / "proxy.mp4"
    try:
        if json.loads(probe_file.read_text()).get("version") != PROBE_VERSION:
            probe_file.unlink()
    except (OSError, ValueError):
        pass
    step("probe", [probe_file], [src], lambda: write_json(probe_file, probe(src)))
    info = json.loads(probe_file.read_text())
    if "probe" in done:  # stream choice or colour handling may differ: redo what uses them
        raw.unlink(missing_ok=True)
        proxy.unlink(missing_ok=True)
    step("audio_raw", [raw], [src], lambda: _extract_audio(src, raw, info["audio_index"]))

    audio, meta = work / "audio.wav", work / "audio.json"
    try:
        prev = json.loads(meta.read_text())
    except (OSError, ValueError):
        prev = {}
    if prev.get("denoised") != args.denoise:
        audio.unlink(missing_ok=True)

    def make_audio() -> None:
        meta.unlink(missing_ok=True)  # written last: marks audio.wav as complete
        if args.denoise:
            _denoise(raw, audio)
        else:
            shutil.copyfile(raw, audio)
        write_json(meta, {"denoised": args.denoise, "rate": RATE, "channels": 1})

    step("audio", [audio, meta], [raw], make_audio)
    step("proxy", [proxy], [src], lambda: _proxy(src, proxy, info))
    screen = _ingest_screen(job, step)

    if read_job(job).get("status") == "new":
        update_job(job, status="ingested")
    for name in done:
        print(f"made {name}")
    return {
        "job": str(job),
        "done": done,
        "skipped": skipped,
        "denoised": args.denoise,
        "duration": info["duration"],
        "fps": info["fps"],
        "size": info["size"],
        "rotation": info["rotation"],
        "hdr": info["hdr"],
        "screen": screen,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "ingest",
        help="probe, extract 48 kHz audio, build proxies (camera and screen recording)",
        parents=[job_parent()],
    )
    p.add_argument("--denoise", action="store_true", help="clean the audio with DeepFilterNet")
    p.set_defaults(handler=run_tool("ingest", ingest))
