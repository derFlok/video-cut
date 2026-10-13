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

Small faces (a speaker tile of a video-call recording): when the full-frame pass finds a
face in fewer than SMALL_BELOW of the frames, a second pass decodes the source again (short
side SMALL_SHORT) and looks for the face in upscaled regions of the frames still without
one (`small_pass`): around the last face found (the region of interest), else in a scan of
overlapping tiles (TILE of the frame every TILE_STEP) at most every SCAN_EVERY_S. Faces
found there are marked "small": true; face.json "small_pass" says how many frames it
checked and found.

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

Idempotent: a face.json newer than the source and of this version is kept (`tool face`
analyses a version-1 file again; readers accept both).
"""

import json
import math
import subprocess
import tempfile
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

FACE_VERSION = 2  # 2: the small-face pass
READ_VERSIONS = (1, 2)
MODEL = "face_landmarker.task"
SHORT_SIDE = 480
SAMPLE_FPS = 30.0
BLINK_ON, BLINK_OFF = 0.5, 0.35
BLINK_MAX_S = 0.8
FOLLOW_MIN = 0.08  # fraction of the frame a segment's face median may drift before following
FOLLOW_MIN_FRAMES = 5  # a segment needs this many face frames before it may follow
SMALL_BELOW = 0.5  # share of frames with a face below which the small-face pass runs
SMALL_SHORT = 1080  # its decode size (short side, never above the source's)
ROI_SIZE = 320  # a region is scaled to this short side for the detector
ROI_PAD = 1.0  # region of interest = the face box grown by this x its size on each side
TILE, TILE_STEP = 0.4, 0.2  # scan tiles: share of the frame side, step (4 x 4 tiles)
SCAN_EVERY_S = 0.5  # without a face in the region of interest, scan this often
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
    mine = [f for f in face.get("frames", []) if t0 <= f["t"] <= t1]
    own = median_of(mine) if sum(1 for f in mine if f.get("found")) >= FOLLOW_MIN_FRAMES else None
    if own and max(abs(own["cx"] - glob["cx"]), abs(own["cy"] - glob["cy"])) > FOLLOW_MIN:
        return own["cx"], own["cy"]
    return glob["cx"], glob["cy"]


def segment_box(face: dict, t0: float, t1: float) -> list[float] | None:
    """Face box for a segment [t0, t1] of source time: the median of its own face frames when
    it has FOLLOW_MIN_FRAMES of them, else the global median box. None without a face."""
    mine = [f for f in face.get("frames", []) if t0 <= f["t"] <= t1 and f.get("found")]
    if len(mine) >= FOLLOW_MIN_FRAMES:
        return median_of(mine)["box"]
    return (face.get("median") or {}).get("box")


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
    if face.get("version") not in READ_VERSIONS or not src.is_relative_to(job) or not src.is_file():
        return None
    if source_rel is not None and face["source"] != source_rel:
        return None
    return face if is_fresh([path], [src]) and _valid(face) else None


def _unit(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and 0 <= v <= 1


def _time(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _valid(face: dict) -> bool:
    """work/face.json is agent-writable: a malformed one counts as missing, never crashes."""
    frames, blinks, med = face.get("frames"), face.get("blinks"), face.get("median")
    if not (isinstance(frames, list) and isinstance(blinks, list)):
        return False
    if not all(isinstance(b, dict) and _time(b.get("t0")) and _time(b.get("t1")) for b in blinks):
        return False
    for f in frames:
        if not (isinstance(f, dict) and _time(f.get("t"))):
            return False
        box = f.get("box")
        if f.get("found") and not (
            _unit(f.get("cx")) and _unit(f.get("cy"))
            and isinstance(box, list) and len(box) == 4 and all(map(_unit, box))
        ):  # fmt: skip
            return False
    if med is not None:
        if not isinstance(med, dict) or not (
            _unit(med.get("cx")) and _unit(med.get("cy")) and isinstance(med.get("box"), list)
        ):
            return False
    return isinstance(face.get("fps_sampled", 0), int | float)


def in_blink(t: float, blinks: list[dict], pad: float = 0.0) -> dict | None:
    return next((b for b in blinks if b["t0"] - pad <= t <= b["t1"] + pad), None)


# ---------------------------------------------------------------- detection


def _scaled(size: list[int]) -> tuple[int, int]:
    w, h = size
    s = min(1.0, SHORT_SIDE / min(w, h))
    return max(2, round(w * s / 2) * 2), max(2, round(h * s / 2) * 2)


def _rgb_frames(src: Path, info: dict, fps: float, size: tuple[int, int]):
    """Yield the source's frames as rgb arrays [h, w, 3] at `fps` and `size` (display
    orientation, HDR tonemapped) from an ffmpeg pipe."""
    w, h = size
    vf = ",".join(
        f for f in (f"fps={fps}", f"scale={w}:{h}:flags=area", to_sdr(info), "format=rgb24") if f
    )
    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(src)]
    cmd += ["-map", f"0:{info['video_index']}", "-vf", vf, "-f", "rawvideo", "-"]
    nbytes = w * h * 3
    errf = tempfile.TemporaryFile()  # a file, not a pipe: a chatty ffmpeg never blocks
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, env=tool_env())
    done = False
    try:
        while len(buf := proc.stdout.read(nbytes)) == nbytes:
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        done = True
    finally:
        proc.stdout.close()
        if not done:  # the consumer stopped reading: ffmpeg would wait forever
            proc.kill()
        proc.wait()
        errf.seek(0)
        err = errf.read().decode(errors="replace")
        errf.close()
    if proc.returncode != 0:
        raise ToolError(f"ffmpeg failed: {err.strip()[-500:]}")


def _landmarker(model: Path, video: bool):
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import (
        FaceLandmarker,
        FaceLandmarkerOptions,
        RunningMode,
    )

    opts = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model)),
        running_mode=RunningMode.VIDEO if video else RunningMode.IMAGE,
        num_faces=1,
        output_face_blendshapes=True,
    )
    return FaceLandmarker.create_from_options(opts)


def detect(src: Path, info: dict, model: Path) -> tuple[list[dict], float, tuple[int, int]]:
    import mediapipe as mp

    fps = min(SAMPLE_FPS, info.get("fps") or SAMPLE_FPS)
    w, h = _scaled(info["size"])
    frames: list[dict] = []
    with _landmarker(model, video=True) as lm:
        for i, arr in enumerate(_rgb_frames(src, info, fps, (w, h))):
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=arr)
            res = lm.detect_for_video(img, int(round(i * 1000 / fps)))
            frames.append(_frame(round(i / fps, 3), _landmarks(res)))
    return frames, fps, (w, h)


def detect_small(src: Path, info: dict, model: Path, frames: list[dict], fps: float) -> dict:
    """The small-face pass over `frames` (changed in place): {"checked", "found"}."""
    import mediapipe as mp

    s = min(1.0, SMALL_SHORT / min(info["size"]))
    size = (max(2, round(info["size"][0] * s / 2) * 2), max(2, round(info["size"][1] * s / 2) * 2))
    with _landmarker(model, video=False) as lm:

        def find(arr: np.ndarray) -> dict | None:
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(arr))
            return _landmarks(lm.detect(img))

        return small_pass(frames, _rgb_frames(src, info, fps, size), find, fps)


def _landmarks(res) -> dict | None:
    """{"box" (normalised to the detector's image), "blink", "gaze_off"} or None."""
    if not res.face_landmarks:
        return None
    pts = np.array([(p.x, p.y) for p in res.face_landmarks[0]], float).clip(0, 1)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    scores = {c.category_name: c.score for c in (res.face_blendshapes or [[]])[0]}
    return {
        "box": [float(v) for v in (x0, y0, x1, y1)],
        "blink": max(scores.get("eyeBlinkLeft", 0), scores.get("eyeBlinkRight", 0)),
        "gaze_off": gaze_off(scores),
    }


def _frame(t: float, hit: dict | None) -> dict:
    if hit is None:
        return {"t": t, "found": False, "box": None, "cx": None, "cy": None,
                "blink": None, "gaze_off": None}  # fmt: skip
    x0, y0, x1, y1 = hit["box"]
    return {
        "t": t,
        "found": True,
        "box": [round(float(v), 4) for v in (x0, y0, x1, y1)],
        "cx": round(float(x0 + x1) / 2, 4),
        "cy": round(float(y0 + y1) / 2, 4),
        "blink": round(float(hit["blink"]), 3),
        "gaze_off": round(float(hit["gaze_off"]), 3),
    }


# ---------------------------------------------------------------- small faces


def _resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Bilinear resize of an rgb uint8 image to w x h."""
    ih, iw = img.shape[:2]
    ys = np.clip((np.arange(h) + 0.5) * ih / h - 0.5, 0, ih - 1)
    xs = np.clip((np.arange(w) + 0.5) * iw / w - 0.5, 0, iw - 1)
    y0, x0 = ys.astype(int), xs.astype(int)
    y1, x1 = np.minimum(y0 + 1, ih - 1), np.minimum(x0 + 1, iw - 1)
    fy, fx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    a = img.astype(np.float32)
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bottom = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return (top * (1 - fy) + bottom * fy + 0.5).astype(np.uint8)


def tiles() -> list[tuple[float, float, float, float]]:
    """Scan regions [x0, y0, x1, y1] (normalised), overlapping, covering the frame."""
    n = round((1 - TILE) / TILE_STEP) + 1
    steps = [round(i * TILE_STEP, 4) for i in range(n)]
    return [(x, y, x + TILE, y + TILE) for y in steps for x in steps]


def roi(box: list[float], aspect: float) -> tuple[float, float, float, float]:
    """Region of interest (normalised) around a face box: grown by ROI_PAD of its size on
    each side, square in px (`aspect` = frame w / h), kept inside the frame."""
    side = max(box[2] - box[0], (box[3] - box[1]) / aspect) * (1 + 2 * ROI_PAD)
    w, h = min(side, 1.0), min(side * aspect, 1.0)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    x0, y0 = min(max(cx - w / 2, 0.0), 1 - w), min(max(cy - h / 2, 0.0), 1 - h)
    return (x0, y0, x0 + w, y0 + h)


def _look(img: np.ndarray, region, find) -> dict | None:
    """Run `find` on `region` of `img` upscaled to ROI_SIZE (short side); the hit's box
    mapped back to the whole frame (normalised)."""
    ih, iw = img.shape[:2]
    x0, y0 = int(region[0] * iw), int(region[1] * ih)
    x1, y1 = (
        max(int(math.ceil(region[2] * iw)), x0 + 2),
        max(int(math.ceil(region[3] * ih)), y0 + 2),
    )
    crop = img[y0:y1, x0:x1]
    k = ROI_SIZE / min(crop.shape[:2])
    hit = find(_resize(crop, max(2, round(crop.shape[1] * k)), max(2, round(crop.shape[0] * k))))
    if hit is None:
        return None
    b = hit["box"]
    fw, fh = (x1 - x0) / iw, (y1 - y0) / ih
    ox, oy = x0 / iw, y0 / ih  # the crop's origin (whole px)
    box = [ox + b[0] * fw, oy + b[1] * fh, ox + b[2] * fw, oy + b[3] * fh]
    return {**hit, "box": [min(max(v, 0.0), 1.0) for v in box]}


def small_pass(frames: list[dict], images, find, fps: float) -> dict:
    """Fill the frames without a face (found false) from `images` (rgb arrays, one per
    frame, in order) with `find(rgb) -> {"box" normalised to that image, "blink",
    "gaze_off"} | None`, run on an upscaled region: around the last face found (by either
    pass), else every SCAN_EVERY_S on each scan tile. Changes `frames` in place; returns
    {"checked", "found"}."""
    every = max(1, round(SCAN_EVERY_S * fps))
    last_box, last_scan = None, -every
    checked = found = 0
    for i, img in enumerate(images):
        if i >= len(frames):
            break
        f = frames[i]
        if f.get("found"):
            last_box = f["box"]
            continue
        hit, tried = None, last_box is not None
        if last_box is not None:
            hit = _look(img, roi(last_box, img.shape[1] / img.shape[0]), find)
        if hit is None and i - last_scan >= every:
            last_scan, tried = i, True
            for region in tiles():
                if (hit := _look(img, region, find)) is not None:
                    break
        checked += tried
        if hit is None:
            last_box = None
            continue
        frames[i] = {**_frame(f["t"], hit), "small": True}
        last_box = frames[i]["box"]
        found += 1
    return {"checked": checked, "found": found}


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
    if doc is not None and doc.get("version") != FACE_VERSION:
        doc = None  # an older analysis (without the small-face pass): analyse again
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
        small = None
        if frames and sum(f["found"] for f in frames) < SMALL_BELOW * len(frames):
            small = detect_small(src, info, model, frames, fps)
        doc = {
            "version": FACE_VERSION,
            "source": rel,
            "fps_sampled": round(fps, 3),
            "size": list(size),
            "frames": frames,
            "median": median_of(frames),
            "blinks": blink_intervals(frames, fps),
            "small_pass": small,
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
        "small_pass": doc.get("small_pass"),
        "runtime_s": runtime,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "face", help="face track, blinks and gaze -> work/face.json", parents=[job_parent()]
    )
    p.set_defaults(handler=run_tool("face", face))
