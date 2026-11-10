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
             the median step size of the audible audio around it (+-100 ms, blocks quieter
             than -70 dBFS left out: the digital silence of a denoised track and room tone
             are no baseline, next to them any breath or mouth tick looked like a click);
             `hf_spike_db`: energy of the second difference (a high-pass) within +-10 ms vs.
             the same audible baseline; `step_db`: that largest step in dBFS;
             `step_sharpness`: that step vs. the median step within +-1 ms of it (a sample
             discontinuity stands alone, a breath, consonant or mouth tick does not). Flag
             `click` only for an audible (step_db >= -45), sharp (>= 10) step with
             click_peak >= 12 or hf_spike_db >= 15. Calibrated on generated hard cuts
             (sine and speech-like: click_peak 22-44, sharpness 29-51, step -13..-7 dBFS)
             against crossfaded ones and two real reels (a selfie and a demo, 28 cuts:
             highest click_peak 8.9, sharpness 2.6 at the one hf_spike_db >= 15; before,
             six false flags)
    blink    flag `blink_at_cut` when one of the two output frames on either side of the cut
             shows a blink: the frames are mapped to source time via the EDL and looked up
             in the blink intervals of a current work/face.json
Sound effects (render.json "sfx", _sfx_mix): check `sfx` with the flags `sfx_masks_speech`
(a hit leaves the voice's speech band less than _sfx_mix.MASK_DB above it while it speaks;
render already lowered such hits, so what is left is real) and `sfx_clipping` (the mix
before the limiter or the SFX bus too hot in a hit).

Speech (_verify_audio.py): check `speech_clipped` (a hard failure) lists every cut of the
OUTPUT audio where a word is cut off mid-phoneme (level and spectral flux within +-60 ms of
the cut, on the frame grid render cut at); the cut gets the flag `speech_clipped` too, with
`side` out/in/both. A segment with "speech_cut_ok": "<reason>" makes it info instead.
Overlays (when work/overlay_boxes.json from `videocut tool overlays` exists; boxes and face
track are in the output px of work/overlays.json)
    flag `overlay_in_safe_zone` when a box reaches into the organic safe zones (top 250 px,
    bottom 350 px, 6 % at the sides, docs/BRIEF.md §8); flag `overlay_on_face` when a box
    covers more than 2 % of the face box at any face sample (output time) while it is
    visible; flag `overlay_overlap` when an overlay and a caption drawn at the same time
    (longer than OVERLAP_MIN_S) share more than 10 % of the smaller box (one hides the
    other: there was no free room).
    A caption on the seam of a stacked slide_split may cover the slide part's empty margin
    and the forehead: flag `overlay_on_slide_content` when a box touches the slide's content
    box, and judge that caption on the face below the brow line only.
    A full-frame end card (`cta_end`) is exempt from these and from the frozen check: it
    hides the video on purpose and keeps its own content inside the safe area. But flag
    `caption_on_plate` when a caption was drawn while the end card was on screen, and
    `plate_over_speech` when the end card starts before the last spoken word + 0.2 s
    (_plates; `videocut tool overlays` moves it there).
    Trust: the boxes are only believed when work/overlay.mov, overlay_boxes.json and
    overlays.json are exactly what `videocut tool overlays` recorded outside the job folder
    (overlays.check_record); otherwise one entry with flag `overlay_boxes_untrusted`.
    AI label (_verify_ai.py): flag `ai_label_missing` when AI-generated material that must
    be labelled (work/material.json, _ai_label) is on screen and no drawn "ai_badge" box
    covers its whole span (boxes count only when render composited the overlay);
    `ai_label_unchecked` when the EDL cannot be read or is stale while work/material.json
    has a chosen AI-generated candidate; verify.json "ai_material" lists every AI item.
    out/verify/captions_NN.jpg: one tile per caption group change (3x3 per sheet);
    out/verify/overlays_NN.jpg: one tile per overlay and per AI label, at its middle.
Slides (_verify_slides.py; EDL segments with a slide layout): per slide the layout report
of `videocut tool slides render` (trusted only as recorded): `slide_text_overflow`,
`slide_reading_time`, `slide_bubble_over_text`, `slide_missing` / `slide_untrusted` /
`slide_stale` / `slide_unchecked`. A slide_full stretch is a still on purpose; in slide /
slide_split the frozen check looks inside the speaker area only.
out/verify/slides_NN.jpg: one tile per slide, near the end of its stretch.
Motion (_verify_motion.py; tools/motion.py spans): `motion_dropped`, `motion_missing`,
`motion_untrusted`, `motion_stale`, `motion_not_composited`, `motion_static` (no visible
change for > 0.6 s inside a span), `motion_repeats_caption` (band text = caption words),
`overlay_on_face` (the band's drawn box below the brow line) and `motion_caption_overlap`;
verify.json "motion" (per span) and "motion_flagged".
B-roll (_verify_broll.py; render.json "broll"): the middle frame of each stretch flags
`broll_black` (mostly black) or `broll_aspect` (letterbox/pillarbox bars: not cropped to
fill); check "broll". A broll segment shows no camera for the blink check. A b-roll image
(a slow Ken Burns push-in; kind from the trusted choice, span from the EDL) is a still on
purpose for the frozen check; a b-roll clip is not.
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

from ..edl import Edl, EdlError, is_broll, load, output_spans
from ..paths import tool_env
from . import _image as im
from . import (
    _plates,
    _sfx_mix,
    _verify_ai,
    _verify_audio,
    _verify_broll,
    _verify_motion,
    _verify_screen,
    _verify_slides,
)
from . import material as material_mod
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
CLICK_SHARP_MIN, SHARP_HALF, SHARP_GAP = 10.0, 48, 2  # the +-1 ms around a step, minus +-2
CLICK_AUDIBLE_DB = -45.0  # a smaller step (room tone, a soft mouth tick) is not a cut click
BASE_AUDIBLE_DB = -70.0  # baseline blocks quieter than this (digital silence) are left out
FLOOR = 1e-3  # -60 dBFS: the smallest baseline step, so ratios stay sane next to silence
STRIP_TILES, TILE_W, WAVE_H = 5, 216, 140
FACE_OVERLAP_MAX = 0.02  # share of the face box an overlay may cover
FOREHEAD = 0.25  # top share of the face box a seam caption may cover (renderer lib/layout.js)
CAPTION_SETTLE_S = 0.15  # caption tiles show this long after the change (entry animation)
BLOCK_OVERLAP_MAX = 0.1  # share of the smaller box an overlay and a caption may share
# an overlay and a caption that share a frame or so where one hands over to the other (a
# caption group running into the next cut) is not an overlap anyone sees
OVERLAP_MIN_S = 0.05
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
        return {"click_peak": None, "hf_spike_db": None, "step_db": None, "step_sharpness": None}
    x64 = x.astype(np.float64)
    d1 = np.diff(x64, prepend=x64[0])
    d2 = np.diff(d1, prepend=d1[0])
    near = slice(s - half, s + half)
    sides = [slice(max(0, s - base), s - half), slice(s + half, s + base)]
    audible = np.concatenate([_block_rms(x64[p]) for p in sides]) >= 10 ** (BASE_AUDIBLE_DB / 20)

    def around(d):
        blocks = np.concatenate([_block_rms(d[p]) for p in sides])[audible]
        return float(np.median(blocks)) if len(blocks) else 0.0

    k = s - half + int(np.abs(d1[near]).argmax())
    step = abs(float(d1[k]))
    local = np.abs(
        np.r_[d1[k - SHARP_HALF : k - SHARP_GAP], d1[k + SHARP_GAP + 1 : k + SHARP_HALF + 1]]
    )
    sharp = step / max(float(np.median(local)) if len(local) else 0.0, 1e-9)
    peak = step / max(around(d1), FLOOR)
    hf = float(np.sqrt((d2[near] ** 2).mean())) / max(around(d2), FLOOR * 0.1)
    return {
        "click_peak": round(peak, 2),
        "hf_spike_db": round(float(20 * np.log10(max(hf, 1e-9))), 1),
        "step_db": round(float(20 * np.log10(max(step, 1e-9))), 1),
        "step_sharpness": round(min(sharp, 1e6), 1),
    }


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
    if (
        audio["click_peak"] is not None
        and audio.get("step_db", 0.0) >= CLICK_AUDIBLE_DB
        and audio.get("step_sharpness", CLICK_SHARP_MIN) >= CLICK_SHARP_MIN
        and (audio["click_peak"] >= CLICK_PEAK_MAX or audio["hf_spike_db"] >= HF_SPIKE_DB_MAX)
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
    ones of segment b start at b.start. A "screen", "slide_full" or "broll" segment shows no
    camera."""
    dt = 1 / edl.fps
    blind = ("screen", "slide_full", "broll")
    out = []
    for a, b in pairwise(edl.segments):
        ts = (a.end - 2 * dt, a.end - dt) if a.layout not in blind else ()
        ts += (b.start, b.start + dt) if b.layout not in blind else ()
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


def _together(a: dict, b: dict) -> float:
    """Seconds two items were drawn at the same time (renderer "drawn" spans)."""
    (a0, a1), (b0, b1) = _drawn(a), _drawn(b)
    return min(a1, b1) - max(a0, b0)


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
    picture of the moment and may cover the speaker (`stage`, never `overlay_on_face`).
    A caption on the seam of a stacked slide_split view (its box crosses the view's `seam`)
    may lie on the slide part's empty margin and on the hair and forehead (the top FOREHEAD
    of the face box): it is judged on the slide's `content` box (`overlay_on_slide_content`
    when it touches it) and on the face from the brow line down. Any other box on such a
    view gets `overlay_on_slide_content` too when it touches the content box."""
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
        seams = [
            v for v in views or []
            if "seam" in v and v["t0"] < b["t1"] and v["t1"] > b["t0"]
            and b.get("scene") not in FULL_FRAME_SCENES
        ]  # fmt: skip
        on_seam = [v for v in seams if b.get("scene") == "caption" and y0 < v["seam"] < y1]
        worst, at = 0.0, None
        for f in face:
            if b["t0"] <= f["t"] <= b["t1"]:
                fb = f["box"]
                area = (fb[2] - fb[0]) * (fb[3] - fb[1])
                if any(v["t0"] <= f["t"] <= v["t1"] for v in on_seam):
                    fb = [fb[0], fb[1] + (fb[3] - fb[1]) * FOREHEAD, fb[2], fb[3]]
                share = overlap(b["box"], fb) / area if area > 0 else 0.0
                if share > worst:
                    worst, at = share, f["t"]
        stage = b.get("scene") in STAGE_SCENES and (y1 - y0) > (x1 - x0)
        if worst > FACE_OVERLAP_MAX and b.get("scene") not in FULL_FRAME_SCENES and not stage:
            flags.append("overlay_on_face")
        if b.get("scene") not in FULL_FRAME_SCENES and any(
            v["t0"] < b["t1"] and v["t1"] > b["t0"] and v not in on_seam
            and overlap(b["box"], v["screen"]) > BLOCK_OVERLAP_MAX * _area(b["box"])
            for v in views or []
        ):  # fmt: skip
            flags.append("overlay_on_screen")
        if any(overlap(b["box"], v["content"]) > 0 for v in seams):
            flags.append("overlay_on_slide_content")
        if b.get("scene") not in ("caption", *FULL_FRAME_SCENES) and any(
            c.get("scene") == "caption"
            and _together(b, c) > OVERLAP_MIN_S
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


def broll_image_spans(job: Path, edl: Edl) -> list[tuple[float, float]]:
    """Output spans of the b-roll segments that show an image (a slow Ken Burns drift: a
    still on purpose). The kind comes from the human's trusted choice (material.chosen),
    the span from the EDL render was checked against, never from render.json."""
    chosen = material_mod.chosen(job)
    return [
        span
        for s, span in zip(edl.segments, output_spans(edl), strict=True)
        if is_broll(s.layout) and (chosen.get(s.material) or {}).get("kind") == "image"
    ]


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


def _speech(audio: np.ndarray, render: dict, edl: Edl | None) -> dict:
    """speech_clipped numbers per cut: at the frame-grid cuts of `edl` (joins skipped,
    speech_cut_ok honoured), else at render.json's cuts."""
    if edl is None:
        return _verify_audio.check(audio, RATE, [float(c) for c in render["cuts"]])
    segs = edl.segments
    cuts = [a for a, _ in output_spans(edl)[1:]]
    joins = [abs(b.start - a.end) <= 0.001 for a, b in pairwise(segs)]
    oks = [a.speech_cut_ok or b.speech_cut_ok for a, b in pairwise(segs)]
    return _verify_audio.check(audio, RATE, cuts, joins, oks)


def _sfx_check(render: dict) -> dict | None:
    """Check `sfx` (render.json "sfx", _sfx_mix): flags `sfx_masks_speech` (a hit leaves the
    voice's speech band less than _sfx_mix.MASK_DB above it while it speaks) and
    `sfx_clipping` (the mix before the limiter or the SFX bus too hot in a hit). The reel's
    own peak is the `true_peak` check. None when render mixed no sound effects."""
    rep = render.get("sfx")
    if not isinstance(rep, dict) or not rep.get("mixed"):
        return None
    flags = _sfx_mix.flags(rep)
    a = rep.get("analysis") or {}
    return _check(
        not flags, flags=flags, level=rep.get("level"), hits=rep.get("hits"),
        min_margin_db=a.get("min_margin_db"), masked=a.get("masked") or [],
        clipped=a.get("clipped") or [], voice_lufs=a.get("voice_lufs"),
        sfx_lufs=a.get("sfx_lufs"), hit_vs_voice_db=a.get("hit_vs_voice_db"),
    )  # fmt: skip


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
    stale = False
    if edl is not None:
        cuts, edl_cuts = [float(c) for c in render["cuts"]], edl.cut_points()[1:-1]
        if len(cuts) != len(edl_cuts) or any(
            abs(a - b) > 1 / fps for a, b in zip(cuts, edl_cuts, strict=True)
        ):
            warnings.append("render.json cuts differ from work/edl.json; the render may be stale")
            stale = True
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
    speech = _speech(audio, render, edl if not stale else None)
    by_cut = {d["cut"]: d for d in speech["cuts"]}
    for c in cut_docs:
        if c["cut"] in by_cut:
            c["speech"] = by_cut[c["cut"]]
    scores = [c["visual"]["jump_score"] for c in cut_docs]
    for i, c in enumerate(cut_docs):
        others = [x for j, x in enumerate(scores) if j != i and x is not None]
        median = float(np.median(others)) if others else None
        i_cut = c["cut"] - 1
        c["flags"], c["info"] = cut_notes(
            c["visual"], c["audio"], median, reframe[i_cut], blink[i_cut]
        )
        sp = c.get("speech") or {}
        if sp.get("clipped"):
            (c["info"] if sp.get("speech_cut_ok") else c["flags"]).append(
                "speech_cut_ok" if sp.get("speech_cut_ok") else "speech_clipped"
            )

    demo = {}
    if edl is not None:
        edl_doc = {**json.loads(edl_path.read_text()), "size": list(target)}  # as render did
        demo = _verify_screen.check(job, edl_doc, render, reel, (v["width"], v["height"]))
    elif read_job(job).get("screen"):  # a demo reel whose EDL cannot be read: unchecked
        demo = {"error": "work/edl.json is missing or invalid; screen layouts not checked"}
    still_screen = demo.get("spans", [])
    bubbles = demo.get("bubbles", [])  # landscape/square pip: the camera is the bubble only
    slide_doc = (
        _verify_slides.check(job, edl, (v["width"], v["height"]))
        if edl is not None and not stale
        else {}
    )
    still_screen = still_screen + slide_doc.get("still", [])  # a full slide is a still
    still_screen += broll_image_spans(job, edl) if edl is not None and not stale else []
    bubbles = bubbles + slide_doc.get("bubbles", [])  # slide: the camera is the speaker area

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
    checks["speech_clipped"] = _check(
        not speech["flagged"],
        cuts=[
            {k: d[k] for k in ("cut", "t", "segments", "clipped", "level_db", "flux")}
            for d in speech["flagged"]
        ],
        speech_db=speech["speech_db"],
    )
    broll_docs = _verify_broll.check(render, reel, (v["width"], v["height"]))
    if broll_docs:
        checks["broll"] = _check(
            not any(d["flags"] for d in broll_docs),
            flagged=[d for d in broll_docs if d["flags"]],
        )
    sfx_doc = _sfx_check(render)
    if sfx_doc is not None:
        checks["sfx"] = sfx_doc
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
    sl_times = [t for t in slide_doc.get("times", []) if 0 <= t < video_dur]
    sl_sheets = caption_sheets(reel, v, sl_times, vdir, "slides") if sl_times else []
    for old in vdir.glob("slides_*.jpg"):
        if old not in sl_sheets:
            old.unlink()
    ai = (  # a reel without a composited overlay has no label, whatever old boxes say
        _verify_ai.check(job, edl, json.loads(edl_path.read_text()),
                         boxes if render.get("overlay") else None, fps)
        if edl is not None and not stale
        else _verify_ai.unchecked(job, video_dur)
    )  # fmt: skip
    ov_times = (
        overlay_times(sched.overlays + ai["badges"], fps, video_dur) if sched is not None else []
    )
    ov_sheets = caption_sheets(reel, v, ov_times, vdir, "overlays") if ov_times else []
    for old in vdir.glob("overlays_*.jpg"):
        if old not in ov_sheets:
            old.unlink()
    overlay_flagged = [
        {"scene": o["scene"], "t0": o["t0"], "t1": o["t1"], "flags": o["flags"]}
        for o in overlay_docs
        if o["flags"]
    ]
    overlay_flagged += ai["flagged"]
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
        for kind in ("coverage", "redaction", "text_cut", "screen_time")
        for d in demo.get(kind, [])
        if d["flags"]
    ] + demo.get("decision", [])  # fmt: skip
    slide_flagged = [
        {k: d[k] for k in ("id", "layout", "spans", "seconds", "need", "error") if k in d}
        | {"flags": d["flags"]}
        for d in slide_doc.get("slides", [])
        if d["flags"]
    ]
    motion = _verify_motion.check(job, render, edl.captions if edl is not None else [],
                                  boxes, face_track)  # fmt: skip
    warnings += motion["warnings"]
    motion_flagged = motion["flagged"]
    failed = [k for k, c in checks.items() if not c["ok"]]
    ok = (
        not failed and not flagged and not overlay_flagged and not screen_flagged
        and not slide_flagged and not motion_flagged
    )  # fmt: skip
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
        "ai_material": ai["items"],
        "screen": {
            k: demo[k]
            for k in ("coverage", "redaction", "text_cut", "screen_time", "clamped", "readability")
            if k in demo
        },
        "screen_flagged": screen_flagged,
        "screen_zoom_clamped": demo.get("clamped", []),
        "slides": slide_doc.get("slides", []),
        "slide_flagged": slide_flagged,
        "motion": motion["spans"],
        "motion_flagged": motion_flagged,
        "broll": broll_docs,
        "slide_sheets": [str(p.relative_to(job)) for p in sl_sheets],
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
    for d in slide_flagged:
        print(f"slide {d.get('id')}: {', '.join(d['flags'])}")
    for d in motion_flagged:
        print(f"motion {d.get('id')}: {', '.join(d['flags'])}")
    for d in broll_docs:
        if d["flags"]:
            print(f"b-roll segment {d['segment']} at {d['t']}s: {', '.join(d['flags'])}")
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
        "slide_flagged": slide_flagged,
        "motion_flagged": motion_flagged,
        "screen_zoom_clamped": demo.get("clamped", []),
        "failed": failed,
        "warnings": warnings,
        "images": [c["image"] for c in cut_docs] + [str(p.relative_to(job)) for p in sheets],
        "overlay_sheets": [str(p.relative_to(job)) for p in ov_sheets],
        "slide_sheets": [str(p.relative_to(job)) for p in sl_sheets],
    }


def register(subparsers) -> None:
    p = subparsers.add_parser("verify", help="check sheets at every cut", parents=[job_parent()])
    p.set_defaults(handler=run_tool("verify", verify))
