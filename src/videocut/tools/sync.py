"""`videocut tool sync [--offset S] [--redo]`: align the screen recording with the camera.

Both recordings carry the same voice (the screen recorder's microphone), so the offset is
found by audio cross-correlation:

1. coarse: correlate the loudness envelopes (1 kHz, compressed, slow level removed) of
   the camera and screen audio over every lag; confidence = 1 - second peak / peak
   (the second peak is the best lag at least 0.25 s away), so repeated phrases or noise
   that match almost as well give a low confidence;
2. fine: around the coarse peak (+-40 ms), GCC-PHAT on the raw 48 kHz samples of the
   loudest 12 s of the overlap, with sub-sample interpolation;
3. drift: the fine step on a window near the start and one near the end of the overlap;
   drift_ppm = change of the offset per second of camera time * 1e6. offset_s is then the
   offset at the middle of the overlap ("drift_ref"), which halves the worst error for
   readers that ignore drift.

Camera audio is work/audio_raw.wav (falls back to work/audio.wav): it has the same timeline
as audio.wav (DeepFilterNet runs delay-compensated) but keeps the room sound the screen
microphone also hears, so both inputs are alike; denoising would make them less similar.

When the correlation is weak (confidence < 0.3), a single sharp transient in both tracks
(a clap at the start of the take) is tried instead. Without screen audio, or when neither
works, the tool fails and asks for `--offset <s>`, which writes method "manual".

Writes work/sync.json:
    offset_s    screen_time = camera_time + offset_s (screen time 0 = first screen frame)
    method      "audio_xcorr" | "clap" | "manual"
    confidence  0..1
    drift_ppm   screen clock rate error against the camera (null when not measured)
    drift_ref   camera time at which offset_s is exact (optional, with drift_ppm)
    overlap     [camera_t0, camera_t1] where both recordings have pictures

Idempotent: a sync.json newer than both audio tracks is kept unless --redo or --offset; a
manual one is kept while it is newer than both probes (so `videocut run` reuses it).
"""

import json
import wave
from pathlib import Path

import numpy as np

from .. import edl
from .common import ToolError, is_fresh, job_parent, resolve_job, run_tool, write_json

RATE = 48000
ENV_RATE = 1000  # coarse envelope samples per second
MIN_CONFIDENCE = 0.3
PEAK_EXCLUSION_S = 0.25
FINE_SEARCH_S = 0.04
FINE_WINDOW_S = 12.0
DRIFT_MIN_SPAN_S = 15.0  # windows closer than this cannot resolve a useful drift
SILENT_RMS = 10 ** (-60 / 20)  # below -60 dBFS: a muted microphone counts as no audio
MANUAL_HINT = (
    "set it by hand: find a moment you can see/hear in both recordings, then run "
    "`videocut tool sync --offset <screen_time - camera_time>`"
)


class SyncError(ToolError):
    pass


def read_wav(path: Path) -> np.ndarray:
    """16-bit mono PCM as float32 in -1..1."""
    with wave.open(str(path), "rb") as f:
        if f.getsampwidth() != 2 or f.getnchannels() != 1 or f.getframerate() != RATE:
            raise ToolError(f"{path.name}: expected 48 kHz mono 16-bit PCM; run ingest again")
        data = f.readframes(f.getnframes())
    return np.frombuffer(data, "<i2").astype(np.float32) / 32768


def _envelope(x: np.ndarray, rate: int) -> np.ndarray:
    """Compressed loudness at ENV_RATE with the slow level (0.5 s) removed and unit RMS."""
    block = rate // ENV_RATE
    n = len(x) // block
    if n == 0:
        return np.zeros(0, np.float32)
    e = np.sqrt(np.sqrt(np.mean(x[: n * block].reshape(n, block) ** 2, axis=1)))
    k = ENV_RATE // 2
    if n < k:  # too short for the slow-level filter; callers reject it as too short
        return np.zeros(n, np.float32)
    slow = np.convolve(e, np.ones(k) / k, mode="same")
    f = e - slow
    rms = np.sqrt(np.mean(f**2))
    return f / rms if rms > 0 else f


def _xcorr(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, int]:
    """r[lag] = sum_t a[t] * b[t + lag] for every lag; returns (r, lag of r[0])."""
    n = len(a) + len(b) - 1
    size = 1 << (n - 1).bit_length()
    r = np.fft.irfft(np.conj(np.fft.rfft(a, size)) * np.fft.rfft(b, size), size)
    # lags -(len(a)-1) .. len(b)-1
    return np.concatenate([r[size - (len(a) - 1) :], r[: len(b)]]), -(len(a) - 1)


def coarse(cam: np.ndarray, scr: np.ndarray, rate: int = RATE) -> tuple[float, float]:
    """(lag_s, confidence): scr(t + lag) matches cam(t)."""
    a, b = _envelope(cam, rate), _envelope(scr, rate)
    if len(a) < ENV_RATE or len(b) < ENV_RATE:
        raise SyncError("a recording is shorter than 1 s; cannot sync")
    r, first = _xcorr(a, b)
    # Lags with less than 2 s (or half the shorter track) of overlap are not trusted.
    lags = np.arange(len(r)) + first
    overlap = np.minimum(len(a), len(b) - lags) - np.maximum(0, -lags)
    r = np.where(overlap >= min(2 * ENV_RATE, min(len(a), len(b)) // 2), r, -np.inf)
    i = int(np.argmax(r))
    peak = float(r[i])
    if not np.isfinite(peak) or peak <= 0:
        return 0.0, 0.0
    ex = int(PEAK_EXCLUSION_S * ENV_RATE)
    rest = np.concatenate([r[: max(i - ex, 0)], r[i + ex + 1 :]])
    second = float(np.max(rest)) if len(rest) and np.isfinite(rest).any() else 0.0
    return (i + first) / ENV_RATE, float(np.clip(1 - max(second, 0) / peak, 0, 1))


def fine(
    cam: np.ndarray, scr: np.ndarray, t0: float, t1: float, lag: float, search: float,
    rate: int = RATE,
) -> float | None:  # fmt: skip
    """Lag (s) of camera window [t0, t1) in the screen track near `lag` (+-search), by
    GCC-PHAT with parabolic sub-sample interpolation; None when it lies at the edge."""
    i0, i1 = int(t0 * rate), int(t1 * rate)
    w = int(search * rate)
    j0 = i0 + int(round(lag * rate)) - w
    if i0 < 0 or i1 > len(cam) or j0 < 0 or j0 + (i1 - i0) + 2 * w > len(scr) or i1 <= i0:
        return None
    a = cam[i0:i1].astype(np.float64)
    b = scr[j0 : j0 + (i1 - i0) + 2 * w].astype(np.float64)
    a = (a - a.mean()) * np.hanning(len(a))
    size = 1 << (len(a) + len(b) - 1).bit_length()
    cross = np.conj(np.fft.rfft(a, size)) * np.fft.rfft(b, size)
    cross /= np.abs(cross) + 1e-12 * np.abs(cross).max()
    r = np.fft.irfft(cross, size)[: 2 * w + 1]  # lags 0..2w of b against a
    k = int(np.argmax(r))
    if k == 0 or k == 2 * w:
        return None
    y0, y1, y2 = r[k - 1], r[k], r[k + 1]
    den = y0 - 2 * y1 + y2
    frac = 0.5 * (y0 - y2) / den if den else 0.0
    return (j0 - i0 + k + frac) / rate


def _loudest(cam: np.ndarray, t0: float, t1: float, length: float, rate: int) -> float:
    """Start of the loudest `length`-second window inside [t0, t1]."""
    if t1 - t0 <= length:
        return t0
    e = _envelope(cam, rate) ** 2
    k = int(length * ENV_RATE)
    s = np.convolve(e[int(t0 * ENV_RATE) : int(t1 * ENV_RATE)], np.ones(k), mode="valid")
    return t0 + int(np.argmax(s)) / ENV_RATE


def clap(cam: np.ndarray, scr: np.ndarray, rate: int = RATE) -> tuple[float, float] | None:
    """(lag_s, confidence) from the single sharpest transient in each track, or None."""

    def onset(x: np.ndarray) -> tuple[float, float] | None:
        frame = rate // 200  # 5 ms
        n = len(x) // frame
        if n < 40:
            return None
        e = np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-10
        before = np.convolve(e, np.ones(20) / 20, mode="full")[:n]  # mean of the 100 ms up to i
        jump = np.zeros(n)
        jump[1:] = e[1:] / before[:-1]
        i = int(np.argmax(jump))
        if jump[i] < 30:  # ~15 dB in 5 ms: not sharp enough for a clap
            return None
        rest = np.concatenate([jump[: max(i - 100, 0)], jump[i + 100 :]])
        second = float(rest.max()) if len(rest) else 0.0
        seg = np.abs(x[i * frame : (i + 1) * frame])
        first = int(np.argmax(seg >= 0.5 * seg.max()))
        return (i * frame + first) / rate, float(np.clip(1 - second / jump[i], 0, 1))

    a, b = onset(cam), onset(scr)
    if a is None or b is None:
        return None
    return b[0] - a[0], min(a[1], b[1])


def estimate(cam: np.ndarray, scr: np.ndarray, rate: int = RATE) -> dict:
    """Offset of `scr` against `cam` in audio time: {"lag", "method", "confidence",
    "drift_ppm", "drift_ref"}. Raises SyncError when the tracks cannot be aligned."""
    if len(scr) == 0 or float(np.sqrt(np.mean(scr.astype(np.float64) ** 2))) < SILENT_RMS:
        raise SyncError(f"the screen recording has no usable audio; {MANUAL_HINT}")
    lag, conf = coarse(cam, scr, rate)
    if conf < MIN_CONFIDENCE:
        c = clap(cam, scr, rate)
        if c is None or c[1] < 0.5:
            raise SyncError(
                f"the audio of both recordings does not match clearly (confidence {conf:.2f}) "
                f"and there is no clap in both; {MANUAL_HINT}"
            )
        return {"lag": c[0], "method": "clap", "confidence": c[1], "drift_ppm": None}
    cam_dur, scr_dur = len(cam) / rate, len(scr) / rate
    t0, t1 = max(0.0, -lag), min(cam_dur, scr_dur - lag)
    pad = FINE_SEARCH_S + 0.01
    t0, t1 = t0 + pad, t1 - pad
    out = {"lag": lag, "method": "audio_xcorr", "confidence": conf, "drift_ppm": None}
    if t1 - t0 >= DRIFT_MIN_SPAN_S + 4:
        # Two windows, each a quarter of the overlap (2..FINE_WINDOW_S s), at both ends;
        # wide enough search for a drift up to 2000 ppm.
        win = float(np.clip((t1 - t0) / 4, 2.0, FINE_WINDOW_S))
        search = max(FINE_SEARCH_S, 2000e-6 * (t1 - t0))
        a0, a1 = t0 + search, t1 - search - win
        la = fine(cam, scr, a0, a0 + win, lag, search, rate)
        lb = fine(cam, scr, a1, a1 + win, lag, search, rate)
        if la is not None and lb is not None and a1 - a0 >= DRIFT_MIN_SPAN_S:
            ca, cb = a0 + win / 2, a1 + win / 2
            slope = (lb - la) / (cb - ca)
            ref = (t0 + t1) / 2
            out.update(lag=la + slope * (ref - ca), drift_ppm=slope * 1e6, drift_ref=ref)
            return out
    w0 = _loudest(cam, t0, t1, min(FINE_WINDOW_S, t1 - t0), rate)
    f = fine(cam, scr, w0, min(t1, w0 + FINE_WINDOW_S), lag, FINE_SEARCH_S, rate)
    if f is not None:
        out["lag"] = f
    return out


def _probe(job: Path, name: str) -> dict:
    try:
        return json.loads((job / "work" / name).read_text())
    except (OSError, ValueError):
        raise ToolError(f"work/{name} is missing; run `videocut tool ingest` first") from None


def overlap(offset: float, camera_duration: float, screen_duration: float) -> list[float]:
    """Camera-time range in which both recordings have pictures."""
    return [
        round(max(0.0, -offset), 3),
        round(min(camera_duration, screen_duration - offset), 3),
    ]


def sync(args) -> dict:
    job = resolve_job(args.job)
    from .ingest import screen_path

    if screen_path(job) is None:
        raise ToolError("this job has no screen recording; add one with `tool job screen <file>`")
    work = job / "work"
    out = work / "sync.json"
    cam_probe, scr_probe = _probe(job, "probe.json"), _probe(job, "screen_probe.json")
    if scr_probe.get("file") != json.loads((job / "job.json").read_text()).get("screen"):
        raise ToolError("work/screen_probe.json describes another file; run ingest again")
    # Screen time counts from the first screen frame; its wav starts at the audio stream.
    scr_shift = (scr_probe.get("audio_start") or 0.0) - (scr_probe.get("video_start") or 0.0)
    # Camera time counts from the camera's first video frame; audio_raw.wav starts at its audio.
    cam_shift = (cam_probe.get("audio_start") or 0.0) - (cam_probe.get("video_start") or 0.0)

    if args.offset is not None:
        result = {"offset_s": float(args.offset), "method": "manual", "confidence": 1.0}
        result["drift_ppm"] = None
    else:
        prev = edl.read_sync(out)
        probes = [work / "probe.json", work / "screen_probe.json"]
        if not args.redo and prev and prev["method"] == "manual" and is_fresh([out], probes):
            # A human set it (also the only way for a screen recording without audio).
            print(f"kept {out.name} (manual); --redo detects again")
            return _report(job, prev, kept=True)
        cam_wav = work / "audio_raw.wav"
        if not cam_wav.is_file():
            cam_wav = work / "audio.wav"
        scr_wav = work / "screen_audio.wav"
        if not cam_wav.is_file():
            raise ToolError("work/audio_raw.wav is missing; run `videocut tool ingest` first")
        if scr_probe.get("audio_index") is None:
            raise SyncError(f"the screen recording has no audio track; {MANUAL_HINT}")
        if not scr_wav.is_file():
            raise ToolError("work/screen_audio.wav is missing; run `videocut tool ingest` first")
        if not args.redo and is_fresh([out], [cam_wav, scr_wav]):
            prev = edl.read_sync(out)
            if prev is not None:
                print(f"kept {out.name} ({prev['method']}); --redo detects again")
                return _report(job, prev, kept=True)
        est = estimate(read_wav(cam_wav), read_wav(scr_wav))
        result = {
            "offset_s": round(est["lag"] + scr_shift - cam_shift, 4),
            "method": est["method"],
            "confidence": round(est["confidence"], 3),
            "drift_ppm": None if est["drift_ppm"] is None else round(est["drift_ppm"], 1),
        }
        if est.get("drift_ref") is not None:
            result["drift_ref"] = round(est["drift_ref"] + cam_shift, 3)
    result["overlap"] = overlap(result["offset_s"], cam_probe["duration"], scr_probe["duration"])
    if result["overlap"][1] - result["overlap"][0] <= 0:
        raise SyncError(
            f"with offset {result['offset_s']} s the recordings do not overlap; {MANUAL_HINT}"
        )
    errors = edl.validate_sync(result)
    if errors:  # a bug, not a user error; never write a file the EDL check would refuse
        raise ToolError("; ".join(errors))
    write_json(out, result)
    return _report(job, result, kept=False)


def _report(job: Path, s: dict, kept: bool) -> dict:
    drift = "n/a" if s.get("drift_ppm") is None else f"{s['drift_ppm']:+.0f} ppm"
    print(
        f"offset {s['offset_s']:+.3f} s ({s['method']}), confidence {s['confidence']:.2f}, "
        f"drift {drift}, overlap {s['overlap'][0]}-{s['overlap'][1]} s camera time"
    )
    return {"job": str(job), "kept": kept, **s}


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "sync",
        help="align the screen recording with the camera (writes work/sync.json)",
        parents=[job_parent()],
    )
    p.add_argument(
        "--offset",
        type=float,
        metavar="S",
        help="set the offset by hand: screen_time = camera_time + S (method manual)",
    )
    p.add_argument("--redo", action="store_true", help="detect again even if sync.json is current")
    p.set_defaults(handler=run_tool("sync", sync))
