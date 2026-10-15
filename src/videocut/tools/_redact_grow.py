"""Redact boxes grown so they never cut through text or a UI bar (every redaction style).

A candidate's box comes from a measurement (or the EDL) and can end in the middle of a
text line: half a line of browser tab titles stays readable below a top band, next to a
blurred name. Before a segment is planned (_layouts.plan_all, so render, overlays and
verify see the same boxes), every redact block is grown on the screen frames it covers:

    text   local text recognition (_ocr, the same Vision pass as everywhere) reads the
           frames around the box; a line the box cuts (part of it inside, part outside)
           pulls the box over the whole line plus MARGIN of its height
    band   a band along a screen edge (_layouts.band_edge, any style) then moves its inner
           edge on to the next strong horizontal (vertical) UI boundary within SNAP_MAX
           text heights: a step of the row (column) median, e.g. the bottom of the
           browser's chrome. Text and band repeat until nothing changes (PASSES)

    grow(box, frames, screen)   the grown box and why ("text", "band"), pure (tests)
    apply(job, segs, ...)       grown redact lists of EDL segments, cached in
                                work/redact_grown.json (per screen recording, time range
                                and box: OCR runs once per render)

Never smaller than the box planned (fail closed). Without text recognition (not macOS)
nothing grows; verify fails closed there.
"""

import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np

from . import _ocr

LINE_CUT = 0.1  # a line counts as cut when this share of its height is inside the box ...
MARGIN = 0.35  # ... and the box then grows over it plus this share of its height
SEARCH = 4  # text heights around a box searched for lines (SEARCH_X across)
SEARCH_X = 12
SNAP_MAX = 3  # text heights a band's inner edge moves on to a UI boundary at most
BOUNDARY = 8  # grey step between neighbouring rows' medians that counts as a UI boundary
PASSES = 4
SAMPLES = 5  # screen frames read per redact block (distinct frames only)
CACHE = "redact_grown.json"
VERSION = 1


def _text_px(screen: tuple[int, int]) -> float:
    from ._layouts import TEXT_PT, ui_scale

    return TEXT_PT * ui_scale(screen)


def cuts(line: list[float], box: list[float]) -> bool:
    """Whether `box` cuts text `line` (both normalised [x0, y0, x1, y1]): they overlap
    across, LINE_CUT of the line's height lies inside the box, and the line is not
    wholly inside."""
    lh, lw = line[3] - line[1], line[2] - line[0]
    oy = min(line[3], box[3]) - max(line[1], box[1])
    ox = min(line[2], box[2]) - max(line[0], box[0])
    inside = line[0] >= box[0] and line[2] <= box[2] and line[1] >= box[1] and line[3] <= box[3]
    return ox > 0 and lw > 0 and oy >= LINE_CUT * lh and not inside


def _lines(frames: list[np.ndarray], box: list[float], screen) -> list[list[float]]:
    """Text lines (normalised boxes of the recording) read near `box` in any frame."""
    sw, sh = screen
    t = _text_px(screen)
    x0 = max(0, math.floor(box[0] * sw - SEARCH_X * t))
    x1 = min(sw, math.ceil(box[2] * sw + SEARCH_X * t))
    y0 = max(0, math.floor(box[1] * sh - SEARCH * t))
    y1 = min(sh, math.ceil(box[3] * sh + SEARCH * t))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    out = []
    for lines in _ocr.pmap(lambda f: _ocr.recognize(np.ascontiguousarray(f[y0:y1, x0:x1])), frames):
        for ln in lines:
            if not re.search(r"\w", ln["text"]):
                continue
            b = ln["box"]
            out.append([(x0 + b[0] * (x1 - x0)) / sw, (y0 + b[1] * (y1 - y0)) / sh,
                        (x0 + b[2] * (x1 - x0)) / sw, (y0 + b[3] * (y1 - y0)) / sh])  # fmt: skip
    return out


def _over_text(box: list[float], lines: list[list[float]]) -> list[float]:
    """`box` grown over every line it cuts (and the lines the grown box then cuts)."""
    box = list(box)
    changed = True
    while changed:
        changed = False
        for ln in lines:
            if cuts(ln, box):
                m = MARGIN * (ln[3] - ln[1])
                grown = [
                    max(min(box[0], ln[0] - m), 0.0), max(min(box[1], ln[1] - m), 0.0),
                    min(max(box[2], ln[2] + m), 1.0), min(max(box[3], ln[3] + m), 1.0),
                ]  # fmt: skip
                if grown != box:
                    box, changed = grown, True
    return box


def _boundary(profile: np.ndarray) -> int | None:
    """Index of the first row (column) of `profile` (medians, scanned outward from the
    band) that steps by BOUNDARY or more from the one before it."""
    steps = np.nonzero(np.abs(np.diff(profile)) >= BOUNDARY)[0]
    return int(steps[0]) + 1 if len(steps) else None


def _snap(box: list[float], frames: list[np.ndarray], screen) -> list[float]:
    """A band's inner edge moved on to the next UI boundary within SNAP_MAX text heights
    (the farthest any frame shows; unchanged when none shows one)."""
    from ._layouts import band_edge

    edge = band_edge(box, "crop")
    if edge is None or not frames:
        return box
    sw, sh = screen
    lim = math.ceil(SNAP_MAX * _text_px(screen))
    box = list(box)
    best = None
    for f in frames:
        g = f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114
        if edge in ("top", "bottom"):
            xs = slice(math.floor(box[0] * sw), math.ceil(box[2] * sw))
            if edge == "top":
                a = min(math.ceil(box[3] * sh), sh)
                prof = np.median(g[max(a - 1, 0) : min(a + lim, sh), xs], axis=1)
            else:
                a = max(math.floor(box[1] * sh), 0)
                prof = np.median(g[max(a - lim, 0) : min(a + 1, sh), xs], axis=1)[::-1]
        else:
            ys = slice(math.floor(box[1] * sh), math.ceil(box[3] * sh))
            if edge == "left":
                a = min(math.ceil(box[2] * sw), sw)
                prof = np.median(g[ys, max(a - 1, 0) : min(a + lim, sw)], axis=0)
            else:
                a = max(math.floor(box[0] * sw), 0)
                prof = np.median(g[ys, max(a - lim, 0) : min(a + 1, sw)], axis=0)[::-1]
        k = _boundary(prof)
        if k is not None:
            best = max(best or 0, k - 1)  # px past the edge: the boundary row stays shown
    if best:
        if edge == "top":
            box[3] = min(box[3] + best / sh, 1.0)
        elif edge == "bottom":
            box[1] = max(box[1] - best / sh, 0.0)
        elif edge == "left":
            box[2] = min(box[2] + best / sw, 1.0)
        else:
            box[0] = max(box[0] - best / sw, 0.0)
    return box


def grow(box: list[float], frames: list[np.ndarray], screen) -> tuple[list[float], list[str]]:
    """(`box` grown over the text lines it cuts and, a band, on to the next UI boundary;
    ["text", "band"] what moved it). `frames`: full screen frames (rgb) it covers."""
    box, why = [float(v) for v in box], []
    for _ in range(PASSES):
        before = box
        lines = _lines(frames, box, screen)
        box = _over_text(box, lines)
        if box != before and "text" not in why:
            why.append("text")
        snapped = _snap(box, frames, screen)
        if snapped != box and "band" not in why:
            why.append("band")
        box = snapped
        if box == before:
            break
    return [round(v, 5) for v in box], why


def _key(t0: float, t1: float, box: list[float]) -> str:
    return json.dumps([round(t0, 3), round(t1, 3), [round(v, 5) for v in box]])


def _screen_id(src: Path) -> str:
    st = src.stat()
    return hashlib.sha256(f"{src.name}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:16]


def _times(src: Path, info: dict, t0: float, t1: float, fps: float) -> list[float]:
    """Up to SAMPLES screen times in [t0, t1] that show distinct recorded frames."""
    from .redact import shown_pts

    k = SAMPLES
    ts = [t0 + (t1 - t0) * j / (k - 1) for j in range(k)] if t1 > t0 else [t0]
    ts = [min(max(t, 0.0), info["duration"]) for t in ts]
    seen, out = set(), []
    for t in ts:
        p = shown_pts(src, info, t, fps)
        if p not in seen:
            seen.add(p)
            out.append(t)
    return out


def apply(job: Path, blocks: list[dict], src: Path, info: dict, fps: float) -> list[dict]:
    """`blocks` [{"t0", "t1", "box", "offset"}] (camera time; offset: + = screen time)
    -> [{"box", "planned", "grown"}] in the same order (planned and grown only for a box
    that grew). Cached in the job's work/CACHE."""
    from .common import write_json
    from .redact import grab_screen

    if not blocks or not _ocr.available():
        return [{"box": list(b["box"])} for b in blocks]
    path = job / "work" / CACHE
    sid = _screen_id(src)
    try:
        cache = json.loads(path.read_text())
        if cache.get("version") != VERSION or cache.get("screen") != sid:
            cache = None
    except (OSError, ValueError, AttributeError):
        cache = None
    cache = cache or {"version": VERSION, "screen": sid, "boxes": {}}
    out, dirty = [], False
    for b in blocks:
        t0, t1 = b["t0"] + b["offset"], b["t1"] + b["offset"]
        key = _key(t0, t1, b["box"])
        hit = cache["boxes"].get(key)
        if not (isinstance(hit, dict) and isinstance(hit.get("box"), list)):
            frames = [f for t in _times(src, info, t0, t1, fps)
                      if (f := grab_screen(src, info, t, fps)) is not None]  # fmt: skip
            box, why = grow(b["box"], frames, tuple(info["size"])) if frames else (b["box"], [])
            hit = {"box": list(box), "grown": why}
            cache["boxes"][key] = hit
            dirty = True
        planned = [round(float(v), 5) for v in b["box"]]
        if hit["grown"] and hit["box"] != planned:
            out.append({"box": hit["box"], "planned": planned, "grown": hit["grown"]})
        else:
            out.append({"box": list(b["box"])})
    if dirty:
        write_json(path, cache)
    return out
