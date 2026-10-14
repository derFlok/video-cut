"""`videocut tool verify` (pipeline step 10): self-check of the last render.

Reads out/render.json and the render it describes (out/reel.mp4, or out/preview.mp4 after
`render --preview`), + work/edl.json when present, and writes
    out/verify/cut_NN.jpg   per inner cut point: frames -2..+2 around the cut (output time)
                            and the audio waveform +-100 ms, a red marker at the cut
    out/verify.json         numbers per cut, global checks, flags

The tool never opens the pictures itself; they are for Claude (a subagent) to look at.

Numbers per cut
    visual   mean absolute luma difference across the cut vs. the median difference of the
             neighbouring frame pairs; `jump_score` is their ratio. A jump cut in a talking
             head is expected, so a clear jump is only reported as info `jump_cut`; the flag
             `visual_jump` is reserved for a jump far above (>= 3x, and >= 8) the
             median jump_score of the reel's other cuts (e.g. a cut into a different shot
             or a glitch frame) where the EDL does not change the framing (crop or zoom:
             info `reframe`)
    audio    `click_peak`: largest sample-to-sample step within +-10 ms of the cut relative to
             the median step size around it; `hf_spike_db`: energy of the second difference
             (a high-pass) within +-10 ms vs. its local median (flag `click`)
    blink    flag `blink_at_cut` when one of the two output frames on either side of the cut
             shows a blink: the frames are mapped to source time via the EDL and looked up
             in the blink intervals of a current work/face.json
Overlays (when work/overlay_boxes.json from `videocut tool overlays` exists; boxes and face
track are in the output px of work/overlays.json)
    flag `overlay_in_safe_zone` when a box reaches into the organic safe zones (top 250 px,
    bottom 350 px, 6 % at the sides, docs/BRIEF.md §8); flag `overlay_on_face` when a box
    covers more than 2 % of the face box at any face sample (output time) while it is
    visible; flag `overlay_overlap` when an overlay and a caption on screen at the same time
    share more than 10 % of the smaller box (one hides the other: there was no free room).
    A full-frame end card (`cta_end`) is exempt from these and from the frozen check: it
    hides the video on purpose and keeps its own content inside the safe area. But flag
    `caption_on_plate` when a caption was drawn while the end card was on screen, and
    `plate_over_speech` when the end card starts before the last spoken word + 0.2 s
    (_plates; `videocut tool overlays` moves it there).
    Trust: the boxes are only believed when work/overlay.mov, overlay_boxes.json and
    overlays.json are exactly what `videocut tool overlays` recorded outside the job folder
    (overlays.check_record); otherwise one entry with flag `overlay_boxes_untrusted`.
    out/verify/captions_NN.jpg: one tile per caption group change (3x3 per sheet);
    out/verify/overlays_NN.jpg: one tile per overlay, at its middle.
Global checks: duration vs. render.json (+-1 frame), integrated loudness -14 +-1 LU, true peak
<= -1.0 dBTP, size of the job's format (job.json "format": 1080x1920 for 9:16, 1920x1080 for
16:9, ...), no black or frozen stretch longer than 0.5 s. A preview is
checked at its half size, and its loudness (single-pass) is reported but not judged.

The summary line has `ok` (all global checks pass and no cut or overlay is flagged),
`flagged` (cuts with their flags), `overlay_flagged` (overlay boxes with their flags),
`info` (cuts with info notes such as `jump_cut`) and `failed` (global checks).
The exit code is 0 whenever the checks ran.
"""

import json
import math
import re
import subprocess
from itertools import pairwise
from pathlib import Path

import numpy as np

from ..edl import Edl, EdlError, load
from ..paths import tool_env
from . import _image as im
from . import _plates, _verify_screen
from . import overlays as overlays_mod
from ._geometry import job_size, overlap, safe_zones, sized
from .common import (
    ToolError,
    binary,
    is_fresh,
    job_parent,
    read_job,
    resolve_job,
    run_tool,
    write_json,
)
from .face import face_crops, in_blink, load_face

RATE = 48000
SMALL = (72, 128)  # luma frames for the difference score (w, h)
LUFS_TARGET, LUFS_TOL = -14.0, 1.0
TRUE_PEAK_MAX = -1.0
MAX_STRETCH_S = 0.5
VISUAL_MIN_DIFF, VISUAL_MIN_SCORE = 8.0, 4.0  # luma levels (0..255), ratio to neighbours
LARGE_JUMP_X_MEDIAN = 3.0  # `visual_jump`: jump_score this many times the other cuts' median
LARGE_JUMP_MIN = 8.0  # ... and at least this (a still pose with a few normal jump cuts)
CLICK_PEAK_MAX, HF_SPIKE_DB_MAX = 12.0, 15.0
CLICK_HALF_S, BASE_S, BLOCK = 0.010, 0.100, 240
FLOOR = 1e-4  # about -80 dBFS: keeps ratios finite next to digital silence
STRIP_TILES, TILE_W, WAVE_H = 5, 216, 140
FACE_OVERLAP_MAX = 0.02  # share of the face box an overlay may cover
CAPTION_SETTLE_S = 0.15  # caption tiles show this long after the change (entry animation)
BLOCK_OVERLAP_MAX = 0.1  # share of the smaller box an overlay and a caption may share
FULL_FRAME_SCENES = _plates.PLATE_SCENES  # the end card: an opaque full-frame plate, by design
CAPTION_COLS = 3
STAGE_SCENES = ("screenshot_frame",)  # portrait: may cover the speaker on purpose


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


def is_jump(visual: dict) -> bool:
    return (
        visual["jump_score"] is not None
        and visual["diff_cut"] >= VISUAL_MIN_DIFF
        and visual["jump_score"] >= VISUAL_MIN_SCORE
    )


def cut_notes(
    visual: dict,
    audio: dict,
    median_jump: float | None,
    reframe: bool = False,
    blink: bool = False,
) -> tuple[list[str], list[str]]:
    """(flags, info) of one cut. `median_jump` is the median jump_score of the other cuts
    (None when there are none: a lone jump cut is never flagged); `reframe` means the EDL
    changes crop or zoom at this cut, so a large jump is intended; `blink` that a frame at
    the cut shows a blink."""
    flags, info = [], []
    if is_jump(visual):
        info.append("jump_cut")
        if reframe:
            info.append("reframe")
        elif median_jump is not None and visual["jump_score"] >= max(
            LARGE_JUMP_MIN, LARGE_JUMP_X_MEDIAN * median_jump
        ):
            flags.append("visual_jump")
    if audio["click_peak"] is not None and (
        audio["click_peak"] >= CLICK_PEAK_MAX or audio["hf_spike_db"] >= HF_SPIKE_DB_MAX
    ):
        flags.append("click")
    if blink:
        flags.append("blink_at_cut")
    return flags, info


def reframes(edl: Edl) -> list[bool]:
    """Per inner cut: does the framing (crop centre or zoom) change across it? A cut into,
    out of or between screen layouts (product demos) always changes the view."""

    def framing(seg, t: float) -> tuple:
        c = seg.crop
        cx, cy, z = (c.cx, c.cy, c.zoom) if c else (0.5, 0.5, 1.0)
        for kt, kz in sorted(seg.zoom_keys, key=lambda k: k[0]):
            if kt <= t:
                z = kz
        return (round(cx, 3), round(cy, 3), round(z, 3))

    return [
        a.layout != "face" or b.layout != "face" or framing(a, a.end) != framing(b, b.start)
        for a, b in pairwise(edl.segments)
    ]


def blinks_at_cuts(edl: Edl, blinks: list[dict]) -> list[bool]:
    """Per inner cut: does one of the two output frames before or after it show a blink?
    Output frames map to source time: the last ones of segment a end at a.end, the first
    ones of segment b start at b.start. A "screen" segment shows no camera."""
    dt = 1 / edl.fps
    out = []
    for a, b in pairwise(edl.segments):
        ts = (a.end - 2 * dt, a.end - dt) if a.layout != "screen" else ()
        ts += (b.start, b.start + dt) if b.layout != "screen" else ()
        out.append(any(in_blink(t, blinks) for t in ts))
    return out


# ---------------------------------------------------------------- pictures


def cut_image(
    reel: Path, v: dict, t: float, frame: int, dest: Path, name: str, gain: float = 1.0
) -> list[int]:
    """Frames frame-2..frame+2 (the cut lies in front of `frame`) + waveform +-100 ms
    (display gain `gain`, see _image.wave_gain)."""
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
        f"[1:a]{im.wave_filter(width, WAVE_H, (t - w0) / (w1 - w0), gain)},setpts=PTS-STARTPTS[w];"
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


# ---------------------------------------------------------------- overlays


def _drawn(b: dict) -> tuple[float, float]:
    """First and last output time the item drew (renderer "drawn"), else its life."""
    d = b.get("drawn")
    if isinstance(d, list) and len(d) == 2 and all(map(_num, d)):
        return d[0], d[1]
    return b["t0"], b["t1"]


def overlay_checks(
    boxes: list[dict],
    face: list[dict],
    size: tuple[int, int],
    last_word: float | None = None,
    views: list[dict] | None = None,
) -> list[dict]:
    """Per drawn box (renderer --boxes): flags `overlay_in_safe_zone` / `overlay_on_face`,
    `overlay_overlap`, `caption_on_plate`, `plate_over_speech` (end card before
    `last_word` + _plates.AFTER_SPEECH_S), `overlay_on_screen` (more than
    BLOCK_OVERLAP_MAX of the box lies on the screen view of a product-demo stretch,
    overlays.json `views`), the worst face overlap (share of the face box) and when it
    happens (output s). A portrait `screenshot_frame` (box taller than wide) is the
    picture of the moment and may cover the speaker (`stage`, never `overlay_on_face`)."""
    w, h = size
    safe = safe_zones(size)
    lo_x, hi_x, lo_y, hi_y = safe["side"], w - safe["side"], safe["top"], h - safe["bottom"]
    plates = [_drawn(b) for b in boxes if b.get("scene") in FULL_FRAME_SCENES]
    out = []
    for b in boxes:
        x0, y0, x1, y1 = b["box"]
        flags = []
        if b.get("scene") in FULL_FRAME_SCENES:  # a full-frame plate covers everything on purpose
            if last_word is not None and b["t0"] < last_word + _plates.AFTER_SPEECH_S - 1e-3:
                flags.append("plate_over_speech")
        elif x0 < lo_x or x1 > hi_x or y0 < lo_y or y1 > hi_y:
            flags.append("overlay_in_safe_zone")
        if b.get("scene") == "caption":
            c0, c1 = _drawn(b)
            if any(c0 <= p1 and c1 >= p0 for p0, p1 in plates):
                flags.append("caption_on_plate")
        worst, at = 0.0, None
        for f in face:
            if b["t0"] <= f["t"] <= b["t1"]:
                fb = f["box"]
                area = (fb[2] - fb[0]) * (fb[3] - fb[1])
                share = overlap(b["box"], fb) / area if area > 0 else 0.0
                if share > worst:
                    worst, at = share, f["t"]
        stage = b.get("scene") in STAGE_SCENES and (y1 - y0) > (x1 - x0)
        if worst > FACE_OVERLAP_MAX and b.get("scene") not in FULL_FRAME_SCENES and not stage:
            flags.append("overlay_on_face")
        if b.get("scene") not in FULL_FRAME_SCENES and any(
            v["t0"] < b["t1"] and v["t1"] > b["t0"]
            and overlap(b["box"], v["screen"]) > BLOCK_OVERLAP_MAX * _area(b["box"])
            for v in views or []
        ):  # fmt: skip
            flags.append("overlay_on_screen")
        if b.get("scene") not in ("caption", *FULL_FRAME_SCENES) and any(
            c.get("scene") == "caption"
            and c["t0"] < b["t1"]
            and c["t1"] > b["t0"]
            and overlap(b["box"], c["box"])
            > BLOCK_OVERLAP_MAX * min(_area(b["box"]), _area(c["box"]))
            for c in boxes
        ):
            flags.append("overlay_overlap")
        out.append(
            {
                "scene": b.get("scene"),
                "t0": b["t0"],
                "t1": b["t1"],
                "box": b["box"],
                "face_overlap": round(worst, 3),
                "face_t": at,
                "flags": flags,
                **({"stage": True} if stage else {}),
            }
        )
    return out


def _area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def full_frame_spans(overlays: list[dict]) -> list[tuple[float, float]]:
    return [
        (o["t0"], o["t1"])
        for o in overlays
        if isinstance(o, dict) and o.get("scene") in FULL_FRAME_SCENES
        and _num(o.get("t0")) and _num(o.get("t1"))
    ]  # fmt: skip


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _load_boxes(
    job: Path, target: tuple[int, int]
) -> tuple[list[dict] | None, list[dict], tuple[int, int], list[str]]:
    """(boxes or None without work/overlay_boxes.json, face track, overlay size (`target`
    unless overlays.json says otherwise), warnings)."""
    path = job / "work" / "overlay_boxes.json"
    if not path.is_file():
        return None, [], target, []
    warnings = []
    edl_path = job / "work" / "edl.json"
    if edl_path.is_file() and not is_fresh([path], [edl_path]):
        warnings.append("work/overlay_boxes.json is older than edl.json; the boxes may be stale")
    try:
        raw = json.loads(path.read_text())
    except ValueError:
        return None, [], target, ["work/overlay_boxes.json is not valid JSON; not checked"]
    boxes = [
        b
        for b in (raw if isinstance(raw, list) else [])
        if isinstance(b, dict)
        and _num(b.get("t0"))
        and _num(b.get("t1"))
        and isinstance(b.get("box"), list)
        and len(b["box"]) == 4
        and all(map(_num, b["box"]))
    ]
    if not isinstance(raw, list) or len(boxes) != len(raw):
        warnings.append("work/overlay_boxes.json has malformed entries; they were skipped")
    face, size = [], target
    try:
        doc = json.loads((job / "work" / "overlays.json").read_text())
        size = (int(doc["size"][0]), int(doc["size"][1]))
        face = [
            f
            for f in doc.get("face", [])
            if _num(f.get("t"))
            and isinstance(f.get("box"), list)
            and len(f["box"]) == 4
            and all(map(_num, f["box"]))
        ]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        warnings.append("work/overlays.json is missing or malformed; face overlap not checked")
    return boxes, face, size, warnings


def caption_times(captions: list[dict], fps: float, duration: float) -> list[float]:
    """One output time per caption group change: CAPTION_SETTLE_S after its start (at most
    its middle), snapped to a frame, unique, inside the reel."""
    times = []
    for c in captions:
        t = im.snap(min(c["t0"] + CAPTION_SETTLE_S, (c["t0"] + c["t1"]) / 2), fps)
        if 0 <= t < duration and t not in times:
            times.append(t)
    return sorted(times)


def overlay_times(overlays: list[dict], fps: float, duration: float) -> list[float]:
    """One output time per overlay (not caption): its middle, when entry animations and a
    screenshot zoom have played; snapped, unique, inside the reel."""
    times = []
    for o in overlays:
        t0, t1 = o.get("t0"), o.get("t1")
        if not (_num(t0) and _num(t1)) or t1 <= t0:
            continue
        t = im.snap((t0 + t1) / 2, fps)
        if 0 <= t < duration and t not in times:
            times.append(t)
    return sorted(times)


def caption_sheets(
    reel: Path, v: dict, times: list[float], dest: Path, prefix: str = "captions"
) -> list[Path]:
    """3x3 contact sheets of the frames at `times`: out/verify/<prefix>_NN.jpg."""
    tw, th = im.tile_size(v["width"], v["height"])
    font = im.find_font()
    label = "," + im.drawtext(font, im.label_expr(v["fps"]), "4", "h-th-4", 22) if font else ""
    n = CAPTION_COLS
    graph = (
        f"[0:v]select='{im.select_times(times, v['fps'])}',scale={tw}:{th}{label},"
        f"tile={n}x{n}:padding=0:margin=0[out]"
    )
    return im.render_jpegs([["-i", str(reel)]], graph, dest, lambda k: f"{prefix}_{k:02d}.jpg")


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


def bad_stretches(reel: Path, duration: float, box: list[int] | None = None) -> list[dict]:
    """Black or frozen stretches longer than MAX_STRETCH_S (ffmpeg blackdetect/freezedetect),
    of the whole frame or only inside `box` ([x0, y0, x1, y1] px)."""
    crop = f"crop={box[2] - box[0]}:{box[3] - box[1]}:{box[0]}:{box[1]}," if box else ""
    log = _info(
        reel,
        [
            "-an",
            "-vf",
            f"{crop}blackdetect=d={MAX_STRETCH_S}:pix_th=0.10,"
            f"freezedetect=n=0.001:d={MAX_STRETCH_S}",
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


def merge_spans(spans: list[tuple[float, float]], slack: float) -> list[tuple[float, float]]:
    """Time spans with overlapping or touching ones (within `slack`) joined."""
    out: list[tuple[float, float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + slack:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def inscribed(box: list[int]) -> list[int]:
    """The even-sized square inside the round bubble `box` (its corners show the screen)."""
    x0, y0, x1, y1 = box
    side = max(2, int(min(x1 - x0, y1 - y0) / math.sqrt(2)) // 2 * 2)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    return [cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side]


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
    render = _read_render(job)
    preview = bool(render.get("preview"))
    reel_name = "preview.mp4" if preview else "reel.mp4"
    reel = job / "out" / reel_name
    if not reel.is_file():
        raise ToolError(f"out/{reel_name} is missing; render first (`videocut tool render`)")
    target = job_size(job)
    v = im.probe_video(reel)
    fps = v["fps"]
    frames, audio = luma_frames(reel), mono_audio(reel)
    n = len(frames)
    video_dur = n / fps

    edl, warnings = None, []
    edl_path = job / "work" / "edl.json"
    if edl_path.is_file():
        try:
            edl = load(edl_path)
        except EdlError as e:
            warnings.append(f"work/edl.json is invalid: {e}")
    reframe = [False] * len(render["cuts"])
    blink = [False] * len(render["cuts"])
    if edl is not None:
        cuts, edl_cuts = [float(c) for c in render["cuts"]], edl.cut_points()[1:-1]
        if len(cuts) != len(edl_cuts) or any(
            abs(a - b) > 1 / fps for a, b in zip(cuts, edl_cuts, strict=True)
        ):
            warnings.append("render.json cuts differ from work/edl.json; the render may be stale")
        else:
            face = load_face(job, edl.source)
            framed_edl = face_crops(sized(job, edl)[0], face)[0]  # the framing render used
            reframe = reframes(framed_edl)
            if face:
                blink = blinks_at_cuts(edl, face.get("blinks", []))

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
        win = audio[max(0, int((t - 0.1) * RATE)) : int((t + 0.1) * RATE)]
        gain = im.wave_gain(float(np.abs(win).max()) if len(win) else 0.0)
        idx = cut_image(reel, v, t, k, vdir, name, gain)
        cut_docs.append(
            {
                "cut": i,
                "t": round(t, 3),
                "frame": k,
                "frames": idx,
                "image": f"out/verify/{name}",
                "visual": vis,
                "audio": aud,
            }
        )
    scores = [c["visual"]["jump_score"] for c in cut_docs]
    for i, c in enumerate(cut_docs):
        others = [x for j, x in enumerate(scores) if j != i and x is not None]
        median = float(np.median(others)) if others else None
        i_cut = c["cut"] - 1
        c["flags"], c["info"] = cut_notes(
            c["visual"], c["audio"], median, reframe[i_cut], blink[i_cut]
        )

    demo = {}
    if edl is not None:
        edl_doc = {**json.loads(edl_path.read_text()), "size": list(target)}  # as render did
        demo = _verify_screen.check(job, edl_doc, render, reel, (v["width"], v["height"]))
    elif read_job(job).get("screen"):  # a demo reel whose EDL cannot be read: unchecked
        demo = {"error": "work/edl.json is missing or invalid; screen layouts not checked"}
    still_screen = demo.get("spans", [])
    bubbles = demo.get("bubbles", [])  # landscape/square pip: the camera is the bubble only

    loud = loudness(reel)
    # The end card as `videocut tool overlays` scheduled it (after the last word, over the
    # held tail). A static end card is not a frozen picture (one frame of slack each end).
    sched = _plates.for_job(job, edl, edl.output_duration()) if edl is not None else None
    plates = full_frame_spans(sched.overlays) if sched is not None else []
    # touching spans count as one (a still screen over several segments, or running into
    # a pip stretch)
    still = merge_spans(plates + still_screen + [(a, b) for a, b, _ in bubbles], 1 / fps)
    stretches = [
        s
        for s in bad_stretches(reel, video_dur)
        if not (
            s["kind"] == "frozen"
            and any(a - 1 / fps <= s["t0"] and s["t1"] <= b + 1 / fps for a, b in still)
        )
    ]
    for box in {tuple(b) for _, _, b in bubbles}:  # a frozen speaker inside the bubble
        spans = merge_spans([(a, b) for a, b, bb in bubbles if tuple(bb) == box], 1 / fps)
        for s in bad_stretches(reel, video_dur, inscribed(list(box))):
            for a, b in spans:
                t0, t1 = max(s["t0"], a), min(s["t1"], b)
                if s["kind"] == "frozen" and t1 - t0 > MAX_STRETCH_S:
                    stretches.append({"kind": "frozen", "t0": t0, "t1": t1, "where": "speaker"})
    stretches.sort(key=lambda s: s["t0"])
    want = float(render["output_duration"])
    want_size = tuple(x // 4 * 2 for x in target) if preview else target
    lufs, peak = loud["integrated_lufs"], loud["true_peak_dbtp"]
    checks = {
        "duration": _check(
            abs(video_dur - want) <= 1 / fps + 1e-6,
            video_s=round(video_dur, 3),
            render_json_s=round(want, 3),
        ),
        "loudness": _check(
            preview or (lufs is not None and abs(lufs - LUFS_TARGET) <= LUFS_TOL),
            integrated_lufs=lufs,
        ),
        "true_peak": _check(
            preview or (peak is not None and peak <= TRUE_PEAK_MAX), true_peak_dbtp=peak
        ),
        "size": _check(
            (v["width"], v["height"]) == want_size,
            size=[v["width"], v["height"]],
            want=list(want_size),
        ),
        "black_or_frozen": _check(not stretches, stretches=stretches),
    }
    for old in vdir.glob("cut_*.jpg"):
        if f"out/verify/{old.name}" not in {c["image"] for c in cut_docs}:
            old.unlink()
    checks["cuts_in_range"] = _check(not bad_cuts, outside=bad_cuts)
    if "error" in demo:
        checks["screen_layouts"] = _check(False, error=demo["error"])
    elif "overlap" in demo:
        checks["screen_overlap"] = _check(not demo["overlap"], problems=demo["overlap"])

    boxes, face_track, ov_size, ov_warn = _load_boxes(job, target)
    warnings += ov_warn
    last_word = sched.last_word if sched is not None else None
    overlay_docs = (
        overlay_checks(boxes, face_track, ov_size, last_word, _verify_screen.load_views(job))
        if boxes is not None
        else []
    )
    if boxes is not None and not render.get("overlay"):
        warnings.append("this render has no overlay composited; boxes checked anyway")
    # Checked whenever there is an overlay at all: a missing or broken boxes file next to an
    # overlay.mov (or a render with one composited) is no excuse to skip the record.
    has_overlay = (job / "work" / "overlay.mov").is_file() or bool(render.get("overlay"))
    untrusted = overlays_mod.check_record(job) if boxes is not None or has_overlay else None
    if untrusted:
        warnings.append(f"overlay boxes not trusted: {untrusted}")
    times = caption_times(edl.captions, fps, video_dur) if edl is not None else []
    sheets = caption_sheets(reel, v, times, vdir) if times else []
    for old in vdir.glob("captions_*.jpg"):
        if old not in sheets:
            old.unlink()
    ov_times = overlay_times(sched.overlays, fps, video_dur) if sched is not None else []
    ov_sheets = caption_sheets(reel, v, ov_times, vdir, "overlays") if ov_times else []
    for old in vdir.glob("overlays_*.jpg"):
        if old not in ov_sheets:
            old.unlink()
    overlay_flagged = [
        {"scene": o["scene"], "t0": o["t0"], "t1": o["t1"], "flags": o["flags"]}
        for o in overlay_docs
        if o["flags"]
    ]
    if untrusted:  # run `videocut tool overlays` again; never edit its outputs
        overlay_flagged.insert(
            0,
            {
                "scene": None,
                "t0": 0.0,
                "t1": round(video_dur, 3),
                "flags": ["overlay_boxes_untrusted"],
            },
        )
    flagged = [{"cut": c["cut"], "t": c["t"], "flags": c["flags"]} for c in cut_docs if c["flags"]]
    info = [{"cut": c["cut"], "t": c["t"], "info": c["info"]} for c in cut_docs if c["info"]]
    screen_flagged = [
        {"check": kind, **{k: d[k] for k in ("id", "where", "segment", "t0", "t1", "t") if k in d},
         "flags": d["flags"]}
        for kind in ("coverage", "redaction", "screen_time")
        for d in demo.get(kind, [])
        if d["flags"]
    ] + demo.get("decision", [])  # fmt: skip
    failed = [k for k, c in checks.items() if not c["ok"]]
    ok = not failed and not flagged and not overlay_flagged and not screen_flagged
    doc = {
        "ok": ok,
        "reel": f"out/{reel_name}",
        "preview": preview,
        "fps": fps,
        "checks": checks,
        "cuts": cut_docs,
        "flagged": flagged,
        "info": info,
        "overlays": overlay_docs,
        "overlay_flagged": overlay_flagged,
        "screen": {
            k: demo[k]
            for k in ("coverage", "redaction", "screen_time", "clamped", "readability")
            if k in demo
        },
        "screen_flagged": screen_flagged,
        "screen_zoom_clamped": demo.get("clamped", []),
        "caption_sheets": [str(p.relative_to(job)) for p in sheets],
        "caption_times": times,
        "overlay_sheets": [str(p.relative_to(job)) for p in ov_sheets],
        "overlay_times": ov_times,
        "warnings": warnings,
    }
    write_json(job / "out" / "verify.json", doc)
    for c in flagged:
        print(f"cut {c['cut']} at {c['t']}s: {', '.join(c['flags'])}")
    for o in overlay_flagged:
        print(f"overlay {o['scene'] or '(all)'} {o['t0']}-{o['t1']}s: {', '.join(o['flags'])}")
    for d in screen_flagged:
        where = f"candidate {d['id']} {d['where']}" if "id" in d else f"segment {d.get('segment')}"
        print(f"screen {d['check']} {where}: {', '.join(d['flags'])}")
    for c in demo.get("clamped", []):
        print(f"info: segment {c['segment']} screen zoom {c['zoom']}x at {c['t']}s clamped "
              f"to {c['max_zoom']}x (sharpness)")  # fmt: skip
    for k in failed:
        print(f"check failed: {k} {checks[k]}")
    return {
        "ok": ok,
        "file": "out/verify.json",
        "checked": f"out/{reel_name}",
        "cuts": len(cut_docs),
        "flagged": flagged,
        "info": info,
        "overlay_flagged": overlay_flagged,
        "screen_flagged": screen_flagged,
        "screen_zoom_clamped": demo.get("clamped", []),
        "failed": failed,
        "warnings": warnings,
        "images": [c["image"] for c in cut_docs] + [str(p.relative_to(job)) for p in sheets],
        "overlay_sheets": [str(p.relative_to(job)) for p in ov_sheets],
    }


def register(subparsers) -> None:
    p = subparsers.add_parser("verify", help="check sheets at every cut", parents=[job_parent()])
    p.set_defaults(handler=run_tool("verify", verify))
