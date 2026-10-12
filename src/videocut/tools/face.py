"""`videocut tool face` (pipeline step 4): face track, blinks and gaze of the source.

MediaPipe Face Landmarker (VIDEO mode, blendshapes) over the source in display orientation
(ffmpeg applies the rotation), decoded through a pipe at short side 480 px and SAMPLE_FPS,
HDR tonemapped like the proxy; no full-resolution frame is ever held in memory. Writes

    work/face.json  {"version", "source", "fps_sampled", "size", "frames": [{"t", "found",
                    "box": [x0, y0, x1, y1], "cx", "cy", "blink", "gaze_off"}],
                    "median": {"cx", "cy", "box"} | null, "blinks": [{"t0", "t1"}]}

All coordinates are normalised to the display frame (0..1), times are source seconds.
`blink` is max(eyeBlinkLeft, eyeBlinkRight). `gaze_off` is how far the eyes look away
from the lens (0 = straight into it, 1 = fully averted), the length of the mean
horizontal/vertical eyeLook* vector. Frames without a face have found=false and nulls.

Sampling: every frame up to SAMPLE_FPS. Blinks last 100-400 ms, so they need >= 15 fps;
the face centre alone would be fine at 5 fps, but one pass serves both. Measured on a
66 s 1080x1920 HLG phone clip (M3 Pro): 18-24 s at 30 fps (decode + tonemap ~10 s, Landmarker
~7.5 ms per frame), ~15 s at 15 fps with the same blink count; 30 fps is kept so blink
edges are frame accurate for the +-1 frame checks. Frames are scaled before tonemapping.

Thresholds: on that clip the blink score is <= 0.36 in 90 % of frames and >= 0.62 in the
top 5 %; the blink count barely moves between ON/OFF 0.4/0.3 and 0.6/0.4.

Blinks (blink_intervals): a blink starts at a frame with blink >= BLINK_ON and lasts while
blink >= BLINK_OFF (hysteresis, so a noisy score near one threshold makes one interval, not
several). The interval runs from the first closed frame to the end of the last one. Eyes
closed longer than BLINK_MAX_S (looking down, eyes shut) is no blink and is dropped.

Reframing (face_crops, used by render and verify): a segment without an explicit crop is
centred on the global face median; it follows the face only when the segment's own median is
more than FOLLOW_MIN off the global one (BRIEF §6: fixed crop, follow only large movement).

Idempotent: a face.json newer than the source and of this version is kept.
"""

import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..edl import Crop, Edl
from ..paths import tool_env
from ._color import to_sdr
from .common import (
    ToolError,
    binary,
    home,
    is_fresh,
    job_parent,
    read_job,
    resolve_job,
    run_tool,
    source_path,
    write_json,
)
from .ingest import probe

FACE_VERSION = 1
MODEL = "face_landmarker.task"
SHORT_SIDE = 480
SAMPLE_FPS = 30.0
BLINK_ON, BLINK_OFF = 0.5, 0.35
BLINK_MAX_S = 0.8
FOLLOW_MIN = 0.08  # fraction of the frame a segment's face median may drift before following
_GAZE = ("eyeLookInLeft", "eyeLookOutLeft", "eyeLookInRight", "eyeLookOutRight")
_GAZE_V = ("eyeLookUpLeft", "eyeLookUpRight", "eyeLookDownLeft", "eyeLookDownRight")


# ---------------------------------------------------------------- pure helpers


def blink_intervals(frames: list[dict], fps: float) -> list[dict]:
    """Blink intervals [{"t0", "t1"}] from per-frame blink scores (see module docstring)."""
    out, start, last = [], None, None

    def close(t1: float) -> None:
        if start is not None and t1 - start <= BLINK_MAX_S + 1e-9:
            out.append({"t0": round(start, 3), "t1": round(t1, 3)})

    for f in frames:
        b = f.get("blink") if f.get("found") else None
        if start is not None and b is not None and b >= BLINK_OFF:
            last = f["t"]
            continue
        close(f["t"])  # ends at the first open frame
        start = last = f["t"] if b is not None and b >= BLINK_ON else None
    if last is not None:
        close(last + 1 / fps)  # closed until the end: one frame past the last
    return out


def gaze_off(scores: dict[str, float]) -> float:
    """0 = eyes look into the lens, 1 = fully averted (from eyeLook* blendshapes)."""
    g = scores.get
    h = ((g(_GAZE[1], 0) - g(_GAZE[0], 0)) + (g(_GAZE[2], 0) - g(_GAZE[3], 0))) / 2
    v = ((g(_GAZE_V[0], 0) + g(_GAZE_V[1], 0)) - (g(_GAZE_V[2], 0) + g(_GAZE_V[3], 0))) / 2
    return min(1.0, float(np.hypot(h, v)))


def median_of(frames: list[dict]) -> dict | None:
    found = [f for f in frames if f.get("found")]
    if not found:
        return None
    box = np.median(np.array([f["box"] for f in found], float), axis=0)
    return {
        "cx": round(float(np.median([f["cx"] for f in found])), 4),
        "cy": round(float(np.median([f["cy"] for f in found])), 4),
        "box": [round(float(v), 4) for v in box],
    }


def segment_centre(face: dict, t0: float, t1: float) -> tuple[float, float] | None:
    """Crop centre for a segment [t0, t1] of source time: the global face median, or the
    segment's own median when that is more than FOLLOW_MIN away. None without a face."""
    glob = face.get("median")
    if not glob:
        return None
    own = median_of([f for f in face.get("frames", []) if t0 <= f["t"] <= t1])
    if own and max(abs(own["cx"] - glob["cx"]), abs(own["cy"] - glob["cy"])) > FOLLOW_MIN:
        return own["cx"], own["cy"]
    return glob["cx"], glob["cy"]


def face_crops(edl: Edl, face: dict | None) -> tuple[Edl, int]:
    """EDL whose segments without a crop get the face-based centre (zoom 1.0), and the number
    of segments that follow the face away from the global median. Unchanged without a face."""
    if not face or not face.get("median"):
        return edl, 0
    glob = (face["median"]["cx"], face["median"]["cy"])
    segs, followed = [], 0
    for s in edl.segments:
        if s.crop is None and (c := segment_centre(face, s.start, s.end)):
            followed += c != glob
            s = replace(s, crop=Crop(cx=c[0], cy=c[1], zoom=1.0))
        segs.append(s)
    return replace(edl, segments=segs), followed


def load_face(job: Path, source_rel: str | None = None) -> dict | None:
    """work/face.json when it is current (this version, newer than the source, made from
    `source_rel` if given), else None."""
    path = job / "work" / "face.json"
    try:
        face = json.loads(path.read_text())
        src = (job / face["source"]).resolve()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if face.get("version") != FACE_VERSION or not src.is_relative_to(job) or not src.is_file():
        return None
    if source_rel is not None and face["source"] != source_rel:
        return None
    return face if is_fresh([path], [src]) else None


def in_blink(t: float, blinks: list[dict], pad: float = 0.0) -> dict | None:
    return next((b for b in blinks if b["t0"] - pad <= t <= b["t1"] + pad), None)


# ---------------------------------------------------------------- detection


def _scaled(size: list[int]) -> tuple[int, int]:
    w, h = size
    s = min(1.0, SHORT_SIDE / min(w, h))
    return max(2, round(w * s / 2) * 2), max(2, round(h * s / 2) * 2)


def detect(src: Path, info: dict, model: Path) -> tuple[list[dict], float, tuple[int, int]]:
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import (
        FaceLandmarker,
        FaceLandmarkerOptions,
        RunningMode,
    )

    fps = min(SAMPLE_FPS, info.get("fps") or SAMPLE_FPS)
    w, h = _scaled(info["size"])
    vf = ",".join(
        f for f in (f"fps={fps}", f"scale={w}:{h}:flags=area", to_sdr(info), "format=rgb24") if f
    )
    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(src)]
    cmd += ["-map", f"0:{info['video_index']}", "-vf", vf, "-f", "rawvideo", "-"]
    opts = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model)),
        running_mode=RunningMode.VIDEO,
        num_faces=1,
        output_face_blendshapes=True,
    )
    frames: list[dict] = []
    nbytes = w * h * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=tool_env())
    try:
        with FaceLandmarker.create_from_options(opts) as lm:
            i = 0
            while len(buf := proc.stdout.read(nbytes)) == nbytes:
                arr = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
                img = mp.Image(image_format=mp.ImageFormat.SRGB, data=arr)
                res = lm.detect_for_video(img, int(round(i * 1000 / fps)))
                frames.append(_frame(round(i / fps, 3), res))
                i += 1
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace")
        proc.wait()
    if proc.returncode != 0:
        raise ToolError(f"ffmpeg failed: {err.strip()[-500:]}")
    return frames, fps, (w, h)


def _frame(t: float, res) -> dict:
    if not res.face_landmarks:
        return {"t": t, "found": False, "box": None, "cx": None, "cy": None,
                "blink": None, "gaze_off": None}  # fmt: skip
    pts = np.array([(p.x, p.y) for p in res.face_landmarks[0]], float).clip(0, 1)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    scores = {c.category_name: c.score for c in (res.face_blendshapes or [[]])[0]}
    return {
        "t": t,
        "found": True,
        "box": [round(float(v), 4) for v in (x0, y0, x1, y1)],
        "cx": round(float(x0 + x1) / 2, 4),
        "cy": round(float(y0 + y1) / 2, 4),
        "blink": round(max(scores.get("eyeBlinkLeft", 0), scores.get("eyeBlinkRight", 0)), 3),
        "gaze_off": round(gaze_off(scores), 3),
    }


# ---------------------------------------------------------------- the tool


def face(args) -> dict:
    job = resolve_job(args.job)
    src = source_path(job)
    rel = read_job(job)["source"]
    work = job / "work"
    work.mkdir(exist_ok=True)
    dst = work / "face.json"
    model = home() / "models" / MODEL
    t0 = time.monotonic()
    doc = load_face(job, rel)
    skipped = doc is not None
    if doc is None:
        if not model.is_file():
            raise ToolError(f"face model missing: {model}; run `videocut models pull`")
        pfile = work / "probe.json"
        try:
            info = json.loads(pfile.read_text()) if is_fresh([pfile], [src]) else probe(src)
        except (OSError, ValueError):
            info = probe(src)
        frames, fps, size = detect(src, info, model)
        doc = {
            "version": FACE_VERSION,
            "source": rel,
            "fps_sampled": round(fps, 3),
            "size": list(size),
            "frames": frames,
            "median": median_of(frames),
            "blinks": blink_intervals(frames, fps),
        }
        write_json(dst, doc)
    runtime = round(time.monotonic() - t0, 1)
    n = len(doc["frames"])
    found = sum(1 for f in doc["frames"] if f["found"])
    pct = round(100 * found / n, 1) if n else 0.0
    print(f"face: {n} frames, face in {pct} %, {len(doc['blinks'])} blinks, {runtime} s")
    return {
        "job": str(job),
        "file": "work/face.json",
        "skipped": skipped,
        "frames": n,
        "fps_sampled": doc["fps_sampled"],
        "found_pct": pct,
        "blinks": len(doc["blinks"]),
        "median": doc["median"],
        "runtime_s": runtime,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "face", help="face track, blinks and gaze -> work/face.json", parents=[job_parent()]
    )
    p.set_defaults(handler=run_tool("face", face))
