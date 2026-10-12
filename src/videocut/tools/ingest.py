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


def probe(src: Path) -> dict:
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
    if audio is None:
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
        "audio_start": float(audio.get("start_time") or 0),
        "video_index": int(video["index"]),
        "pix_fmt": video.get("pix_fmt"),
        "color_transfer": video.get("color_transfer"),
        "color_primaries": video.get("color_primaries"),
        "color_space": video.get("color_space"),
        "audio_index": int(audio["index"]),
        "audio_rate": int(audio.get("sample_rate", 0)),
        "audio_channels": int(audio.get("channels", 0)),
        "audio_codec": audio.get("codec_name"),
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
    }


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "ingest", help="probe, extract 48 kHz audio, build a 360p proxy", parents=[job_parent()]
    )
    p.add_argument("--denoise", action="store_true", help="clean the audio with DeepFilterNet")
    p.set_defaults(handler=run_tool("ingest", ingest))
