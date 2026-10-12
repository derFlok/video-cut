"""`videocut tool verify` (pipeline step 10): self-check of the last render.

Reads out/reel.mp4 + out/render.json (+ work/edl.json when present) and writes
    out/verify/cut_NN.jpg   per inner cut point: frames -2..+2 around the cut (output time)
                            and the audio waveform +-100 ms, a red marker at the cut
    out/verify.json         numbers per cut, global checks, flags

The tool never opens the pictures itself; they are for Claude (a subagent) to look at.

Numbers per cut
    visual   mean absolute luma difference across the cut vs. the median difference of the
             neighbouring frame pairs; `jump_score` is their ratio (flag `visual_jump`)
    audio    `click_peak`: largest sample-to-sample step within +-10 ms of the cut relative to
             the median step size around it; `hf_spike_db`: energy of the second difference
             (a high-pass) within +-10 ms vs. its local median (flag `click`)
Global checks: duration vs. render.json (+-1 frame), integrated loudness -14 +-1 LU, true peak
<= -1.0 dBTP, size 1080x1920, no black or frozen stretch longer than 0.5 s.

The summary line has `ok` (all global checks pass and no cut is flagged), `flagged` (cuts with
their flags) and `failed` (global checks). The exit code is 0 whenever the checks ran.
"""

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from ..edl import EdlError, load
from ..paths import tool_env
from . import _image as im
from .common import ToolError, binary, job_parent, resolve_job, run_tool, write_json

RATE = 48000
SMALL = (72, 128)  # luma frames for the difference score (w, h)
TARGET_SIZE = (1080, 1920)
LUFS_TARGET, LUFS_TOL = -14.0, 1.0
TRUE_PEAK_MAX = -1.0
MAX_STRETCH_S = 0.5
VISUAL_MIN_DIFF, VISUAL_MIN_SCORE = 8.0, 4.0  # luma levels (0..255), ratio to neighbours
CLICK_PEAK_MAX, HF_SPIKE_DB_MAX = 12.0, 15.0
CLICK_HALF_S, BASE_S, BLOCK = 0.010, 0.100, 240
FLOOR = 1e-4  # about -80 dBFS: keeps ratios finite next to digital silence
STRIP_TILES, TILE_W, WAVE_H = 5, 216, 140


# ---------------------------------------------------------------- decoding


def _decode(cmd: list[str]) -> bytes:
    r = subprocess.run(cmd, capture_output=True, env=tool_env())
    if r.returncode != 0:
        raise ToolError(f"ffmpeg failed: {r.stderr.decode(errors='replace').strip()[-500:]}")
    return r.stdout


def _ff(*args: str) -> list[str]:
    return [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", *args]


def luma_frames(reel: Path) -> np.ndarray:
    w, h = SMALL
    raw = _decode(
        _ff("-i", str(reel), "-an", "-vf", f"scale={w}:{h}:flags=area,format=gray")
        + ["-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    )
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w)


def mono_audio(reel: Path) -> np.ndarray:
    raw = _decode(_ff("-i", str(reel), "-vn", "-ac", "1", "-ar", str(RATE)) + ["-f", "f32le", "-"])
    return np.frombuffer(raw, np.float32)


def _info(reel: Path, args: list[str]) -> str:
    return subprocess.run(
        [binary("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-i", str(reel), *args]
        + ["-f", "null", "-"],
        capture_output=True,
        text=True,
        env=tool_env(),
    ).stderr


# ---------------------------------------------------------------- numbers


def visual_numbers(frames: np.ndarray, k: int) -> dict:
    """Difference between frames k-1 and k vs. the neighbouring frame pairs."""
    if not 1 <= k < len(frames):
        return {"diff_cut": None, "diff_around": None, "jump_score": None}
    lo = max(0, k - 4)
    f = frames[lo : k + 4].astype(np.float32)
    k, n = k - lo, len(f)
    cut = float(np.abs(f[k] - f[k - 1]).mean())
    around = [
        float(np.abs(f[j] - f[j - 1]).mean()) for j in range(max(1, k - 3), min(n, k + 4)) if j != k
    ]
    base = float(np.median(around)) if around else 0.0
    return {
        "diff_cut": round(cut, 2),
        "diff_around": round(base, 2),
        "jump_score": round(cut / max(base, 0.5), 2),
    }


def _block_rms(x: np.ndarray) -> np.ndarray:
    n = len(x) // BLOCK
    if n == 0:
        return np.zeros(0)
    return np.sqrt((x[: n * BLOCK].reshape(n, BLOCK).astype(np.float64) ** 2).mean(axis=1))


def audio_numbers(x: np.ndarray, t: float) -> dict:
    """Click indicators within +-10 ms of output time t."""
    s = int(round(t * RATE))
    half, base = int(CLICK_HALF_S * RATE), int(BASE_S * RATE)
    if s - half < 2 or s + half >= len(x):
        return {"click_peak": None, "hf_spike_db": None}
    d1 = np.diff(x.astype(np.float64), prepend=x[0])
    d2 = np.diff(d1, prepend=d1[0])
    near = slice(s - half, s + half)

    def around(d):
        parts = [d[max(0, s - base) : s - half], d[s + half : s + base]]
        blocks = np.concatenate([_block_rms(p) for p in parts if len(p) >= BLOCK] or [np.zeros(0)])
        return float(np.median(blocks)) if len(blocks) else 0.0

    peak = float(np.abs(d1[near]).max()) / max(around(d1), FLOOR)
    hf = float(np.sqrt((d2[near] ** 2).mean())) / max(around(d2), FLOOR * 0.1)
    return {"click_peak": round(peak, 2), "hf_spike_db": round(20 * np.log10(max(hf, 1e-9)), 1)}


def cut_flags(visual: dict, audio: dict) -> list[str]:
    flags = []
    if (
        visual["jump_score"] is not None
        and visual["diff_cut"] >= VISUAL_MIN_DIFF
        and visual["jump_score"] >= VISUAL_MIN_SCORE
    ):
        flags.append("visual_jump")
    if audio["click_peak"] is not None and (
        audio["click_peak"] >= CLICK_PEAK_MAX or audio["hf_spike_db"] >= HF_SPIKE_DB_MAX
    ):
        flags.append("click")
    return flags


# ---------------------------------------------------------------- pictures


def cut_image(reel: Path, v: dict, t: float, frame: int, dest: Path, name: str) -> list[int]:
    """Frames frame-2..frame+2 (the cut lies in front of `frame`) + waveform +-100 ms."""
    fps = v["fps"]
    idx = [i for i in range(frame - 2, frame + 3) if 0 <= i < v["frames"]]
    times = [i / fps for i in idx]
    th = round(TILE_W * v["height"] / v["width"] / 2) * 2
    width = TILE_W * len(idx)
    w0, w1 = max(0.0, t - 0.1), t + 0.1
    pos = idx.index(frame) if frame in idx else len(idx)
    font = im.find_font()
    label = "," + im.drawtext(font, im.label_expr(fps), "3", "h-th-3", 13) if font else ""
    cutline = f",drawbox=x={max(0, pos * TILE_W - 1)}:y=0:w=2:h={th}:color=red:t=fill"
    graph = (
        f"[0:v]select='{im.select_times(times, fps)}',scale={TILE_W}:{th}{label},"
        f"tile={len(idx)}x1:padding=0:margin=0,format=rgb24{cutline},setpts=PTS-STARTPTS[g];"
        f"[1:a]{im.wave_filter(width, WAVE_H, (t - w0) / (w1 - w0))},setpts=PTS-STARTPTS[w];"
        "[g][w]vstack=inputs=2[out]"
    )
    im.render_jpegs(
        [
            ["-ss", f"{max(0.0, times[0] - 0.5):.4f}", "-copyts", "-i", str(reel)],
            ["-ss", f"{w0:.4f}", "-t", f"{w1 - w0:.4f}", "-i", str(reel)],
        ],
        graph,
        dest,
        [name],
    )
    return idx


# ---------------------------------------------------------------- global checks


def _last(pattern: str, text: str) -> float | None:
    m = re.findall(pattern, text)
    if not m:
        return None
    return float("-inf") if m[-1] == "-inf" else float(m[-1])


def loudness(reel: Path) -> dict:
    log = _info(reel, ["-vn", "-af", "ebur128=peak=true"])
    tail = log.split("Summary:")[-1]
    return {
        "integrated_lufs": _last(r"I:\s+(-?[\d.]+|-inf) LUFS", tail),
        "true_peak_dbtp": _last(r"Peak:\s+(-?[\d.]+|-inf) dBFS", tail),
    }


def bad_stretches(reel: Path, duration: float) -> list[dict]:
    """Black or frozen stretches longer than MAX_STRETCH_S (ffmpeg blackdetect/freezedetect)."""
    log = _info(
        reel,
        [
            "-an",
            "-vf",
            f"blackdetect=d={MAX_STRETCH_S}:pix_th=0.10,freezedetect=n=0.001:d={MAX_STRETCH_S}",
        ],
    )
    found = [
        {"kind": "black", "t0": float(a), "t1": float(b)}
        for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)
    ]
    open_start = None
    for kind, val in re.findall(r"freezedetect\.freeze_(start|end): ([\d.]+)", log):
        if kind == "start":
            open_start = float(val)
        elif open_start is not None:
            found.append({"kind": "frozen", "t0": open_start, "t1": float(val)})
            open_start = None
    if open_start is not None and duration - open_start > MAX_STRETCH_S:
        found.append({"kind": "frozen", "t0": open_start, "t1": round(duration, 3)})
    return sorted(found, key=lambda s: s["t0"])


def _check(ok: bool, **fields) -> dict:
    return {"ok": bool(ok), **fields}


# ---------------------------------------------------------------- the tool


def _read_render(job: Path) -> dict:
    path = job / "out" / "render.json"
    if not path.is_file():
        raise ToolError("out/render.json is missing; render first (`videocut tool render`)")
    try:
        data = json.loads(path.read_text())
        float(data["output_duration"])
        [float(c) for c in data["cuts"]]
    except (ValueError, KeyError, TypeError) as e:
        raise ToolError(f"out/render.json is malformed: {e!r}") from None
    return data


def verify(args) -> dict:
    job = resolve_job(args.job)
    reel = job / "out" / "reel.mp4"
    if not reel.is_file():
        raise ToolError("out/reel.mp4 is missing; render first (`videocut tool render`)")
    render = _read_render(job)
    v = im.probe_video(reel)
    fps = v["fps"]
    frames, audio = luma_frames(reel), mono_audio(reel)
    n = len(frames)
    video_dur = n / fps

    edl_cuts, warnings = None, []
    edl_path = job / "work" / "edl.json"
    if edl_path.is_file():
        try:
            edl_cuts = load(edl_path).cut_points()[1:-1]
        except EdlError as e:
            warnings.append(f"work/edl.json is invalid: {e}")
    if edl_cuts is not None:
        cuts = [float(c) for c in render["cuts"]]
        if len(cuts) != len(edl_cuts) or any(
            abs(a - b) > 1 / fps for a, b in zip(cuts, edl_cuts, strict=True)
        ):
            warnings.append("render.json cuts differ from work/edl.json; the render may be stale")

    vdir = job / "out" / "verify"
    vdir.mkdir(parents=True, exist_ok=True)
    cut_docs, bad_cuts = [], []
    for i, t in enumerate(render["cuts"], 1):
        t = float(t)
        k = int(round(t * fps))
        if not 1 <= k < n:
            bad_cuts.append({"cut": i, "t": t})
            continue
        name = f"cut_{i:02d}.jpg"
        vis, aud = visual_numbers(frames, k), audio_numbers(audio, t)
        idx = cut_image(reel, v, t, k, vdir, name)
        cut_docs.append(
            {
                "cut": i,
                "t": round(t, 3),
                "frame": k,
                "frames": idx,
                "image": f"out/verify/{name}",
                "visual": vis,
                "audio": aud,
                "flags": cut_flags(vis, aud),
            }
        )

    loud = loudness(reel)
    stretches = bad_stretches(reel, video_dur)
    want = float(render["output_duration"])
    lufs, peak = loud["integrated_lufs"], loud["true_peak_dbtp"]
    checks = {
        "duration": _check(
            abs(video_dur - want) <= 1 / fps + 1e-6,
            video_s=round(video_dur, 3),
            render_json_s=round(want, 3),
        ),
        "loudness": _check(
            lufs is not None and abs(lufs - LUFS_TARGET) <= LUFS_TOL, integrated_lufs=lufs
        ),
        "true_peak": _check(peak is not None and peak <= TRUE_PEAK_MAX, true_peak_dbtp=peak),
        "size": _check((v["width"], v["height"]) == TARGET_SIZE, size=[v["width"], v["height"]]),
        "black_or_frozen": _check(not stretches, stretches=stretches),
    }
    for old in vdir.glob("cut_*.jpg"):
        if f"out/verify/{old.name}" not in {c["image"] for c in cut_docs}:
            old.unlink()
    checks["cuts_in_range"] = _check(not bad_cuts, outside=bad_cuts)
    flagged = [{"cut": c["cut"], "t": c["t"], "flags": c["flags"]} for c in cut_docs if c["flags"]]
    failed = [k for k, c in checks.items() if not c["ok"]]
    ok = not failed and not flagged
    doc = {
        "ok": ok,
        "reel": "out/reel.mp4",
        "fps": fps,
        "checks": checks,
        "cuts": cut_docs,
        "flagged": flagged,
        "warnings": warnings,
    }
    write_json(job / "out" / "verify.json", doc)
    for c in flagged:
        print(f"cut {c['cut']} at {c['t']}s: {', '.join(c['flags'])}")
    for k in failed:
        print(f"check failed: {k} {checks[k]}")
    return {
        "ok": ok,
        "file": "out/verify.json",
        "cuts": len(cut_docs),
        "flagged": flagged,
        "failed": failed,
        "warnings": warnings,
        "images": [c["image"] for c in cut_docs],
    }


def register(subparsers) -> None:
    p = subparsers.add_parser("verify", help="check sheets at every cut", parents=[job_parent()])
    p.set_defaults(handler=run_tool("verify", verify))
