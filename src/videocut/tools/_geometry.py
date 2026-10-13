"""Shared frame geometry of render, overlays and verify: crop windows, the framing render
uses, source -> output mapping of face boxes, safe zones.

render.py crops with exactly these windows and overlays.py maps face boxes through the
same ones, so a caption placed beside the face in the overlay is beside it in the reel.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from ..edl import Edl
from . import _format
from .common import ToolError, read_job
from .face import face_crops, load_face


@dataclass
class Window:
    w: int
    h: int
    x: int
    y: int


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def crop_window(
    src: tuple[int, int], out: tuple[int, int], cx: float, cy: float, zoom: float
) -> Window:
    """Window of the output aspect in a `src`-sized (display) frame, `zoom`× tighter than
    the largest one that fits, centred on (cx, cy) in normalised coords, kept inside."""
    sw, sh = src
    aspect = out[0] / out[1]
    if sw / sh > aspect:  # wider than the output (landscape): full height
        bw, bh = sh * aspect, sh
    else:  # narrower or equal (portrait): full width
        bw, bh = sw, sw / aspect
    w = min(_even(bw / zoom), sw - sw % 2)
    h = min(_even(bh / zoom), sh - sh % 2)
    x = min(max(int(round(cx * sw - w / 2)), 0), sw - w)
    y = min(max(int(round(cy * sh - h / 2)), 0), sh - h)
    return Window(w, h, x, y)


def job_size(job: Path) -> tuple[int, int]:
    """Output size of the job folder's format (job.json "format" or the default rule)."""
    try:
        return _format.output_size(read_job(job))
    except _format.FormatError as e:
        raise ToolError(f"job.json: {e}") from None


def sized(job: Path, edl: Edl) -> tuple[Edl, str | None]:
    """The EDL at the job's output size, and a note when its own "size" differed (the job's
    format decides; an EDL written for another format is rendered at the job's)."""
    size = job_size(job)
    if tuple(edl.size) == size:
        return edl, None
    note = (
        f"edl.json size {edl.size[0]}x{edl.size[1]} is not the job's format "
        f"{_format.job_format(read_job(job))} ({size[0]}x{size[1]}); using {size[0]}x{size[1]}"
    )
    return replace(edl, size=size), note


def framed(job: Path, edl: Edl) -> tuple[Edl, int, dict | None]:
    """The EDL as render frames it (segments without a crop centred on the face of a
    current work/face.json), the number of face-following segments, and that face.json."""
    face = load_face(job, edl.source)
    edl, followed = face_crops(edl, face)
    return edl, followed, face


def map_box(
    box: list[float], src: tuple[int, int], win: Window, out: tuple[int, int]
) -> list[int] | None:
    """A box normalised to the source display frame -> output px through crop window `win`
    (scaled to `out`), clipped to the output frame; None when nothing of it is visible."""
    sx, sy = out[0] / win.w, out[1] / win.h
    x0 = (box[0] * src[0] - win.x) * sx
    y0 = (box[1] * src[1] - win.y) * sy
    x1 = (box[2] * src[0] - win.x) * sx
    y1 = (box[3] * src[1] - win.y) * sy
    x0, x1 = max(0.0, x0), min(float(out[0]), x1)
    y0, y1 = max(0.0, y0), min(float(out[1]), y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(x0), round(y0), round(x1), round(y1)]


def face_track(plan, face: dict | None, src: tuple[int, int]) -> list[dict]:
    """Face boxes of work/face.json (source time, normalised) in OUTPUT time and px:
    [{"t", "box": [x0, y0, x1, y1]}] for every face sample that lies in a rendered range,
    mapped through that range's crop window (face-layout ranges only). `plan` is a
    _render_plan.Plan."""
    if not face:
        return []
    frames = sorted(
        (f for f in face.get("frames", []) if f.get("found") and f.get("box")),
        key=lambda f: f["t"],
    )
    out, f0 = [], 0
    for r in plan.ranges:
        if getattr(r, "layout", "face") != "face":  # screen layouts: no camera face here
            f0 += r.frames
            continue
        dur = float(r.frames / plan.fps)
        t_out0 = float(f0 / plan.fps)
        for f in frames:
            if r.src_in <= f["t"] < r.src_in + dur:
                box = map_box(f["box"], src, r.window, plan.out_size)
                if box is not None:
                    out.append({"t": round(t_out0 + f["t"] - r.src_in, 3), "box": box})
        f0 += r.frames
    return out


def face_at(track: list[dict], t: float, max_gap: float = 0.25) -> list[int] | None:
    """Face box of the sample nearest to output time t (None when none is within max_gap)."""
    best = min(track, key=lambda s: abs(s["t"] - t), default=None)
    return best["box"] if best and abs(best["t"] - t) <= max_gap else None


def safe_zones(size: tuple[int, int]) -> dict:
    """Organic safe zones in px for an output of `size` (_format.safe_zones)."""
    return _format.safe_zones(size)


def overlap(a: list[float], b: list[float]) -> float:
    """Intersection area of two [x0, y0, x1, y1] boxes."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)
