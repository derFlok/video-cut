"""Blurred redaction (_redact_style "blur"): the reference pixelation, the "nothing readable"
test, and render's calibration that makes a blur stronger until text recognition reads
nothing in it.

    pixelate(rgb, cell)   what render's blur_chain (_layouts) does to a box, in numpy:
                          area-average into ceil(side / cell) cells, nearest back up, then
                          a gaussian of half a cell (three box passes)
    readable(rgb)         the text lines local recognition (_ocr) still reads in an image:
                          lines with at least MIN_CHARS letters or digits
    calibrate(drawn)      per blurred box of render's plan (_layouts.plan_all): the screen
                          frame in the middle of its range, pixelated at the box's cell; while
                          anything is readable the cell doubles (never smaller), up to the
                          whole box; still readable then: a solid block instead (fail closed).
                          Changes the plan in place and returns what it changed
    edge_share(rgb)       share of an image's px at a sharp edge (verify: a blurred box has
                          none; text, UI lines and icons do)
"""

import re

import numpy as np

from . import _ocr

MIN_CHARS = 2  # a recognised line with this many letters/digits counts as readable
EDGE_LEVEL = 40  # grey step between neighbouring output px that counts as a sharp edge
EDGE_MAX = 0.005  # a blurred box shows at most this share of edge px (H.264 noise)


def _box_pass(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """Running mean of 2r+1 px along `axis`, edges repeated."""
    if r < 1:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return (hi - lo) / (2 * r + 1)


def _area(n: int, k: int) -> np.ndarray:
    """[k, n] weights that average n px into k cells of n / k px (FFmpeg scale flags=area:
    a px on a cell border counts in both, by its share)."""
    edges = np.arange(k + 1) * n / k
    lo, hi = edges[:-1, None], edges[1:, None]
    px = np.arange(n)[None, :]
    w = np.clip(np.minimum(hi, px + 1) - np.maximum(lo, px), 0, None)
    return w / w.sum(axis=1, keepdims=True)


def pixelate(rgb: np.ndarray, cell: int) -> np.ndarray:
    """`rgb` [h, w, 3] pixelated and blurred like _layouts.blur_chain draws it: averaged
    into ceil(w / cell) x ceil(h / cell) cells (so a cell may be smaller than `cell`),
    nearest back up, a gaussian of sigma cell / 2."""
    h, w = rgb.shape[:2]
    cw, ch = max(1, -(-w // cell)), max(1, -(-h // cell))
    a = rgb.astype(float)
    cells = np.einsum("yh,hwc,xw->yxc", _area(h, ch), a, _area(w, cw))
    rows = np.minimum((np.arange(h) + 0.5) * ch / h, ch - 1).astype(int)
    cols = np.minimum((np.arange(w) + 0.5) * cw / w, cw - 1).astype(int)
    up = cells[rows][:, cols]
    # three box passes of width ~ sigma * sqrt(12 / 3) approximate a gaussian
    r = max(1, round(cell / 2))
    for _ in range(3):
        up = _box_pass(_box_pass(up, r, 0), r, 1)
    return np.clip(up, 0, 255).astype(np.uint8)


def readable(rgb: np.ndarray) -> list[str]:
    """Lines recognition reads in `rgb` with at least MIN_CHARS letters or digits."""
    if rgb.shape[0] < 4 or rgb.shape[1] < 4:
        return []
    lines = _ocr.recognize(np.ascontiguousarray(rgb, dtype=np.uint8))
    return [x["text"] for x in lines if readable_text(x["text"])]


def readable_text(text: str) -> bool:
    """Whether a recognised line has at least MIN_CHARS letters or digits."""
    return len(re.findall(r"\w", text)) >= MIN_CHARS


def edge_share(rgb: np.ndarray) -> float:
    """Share of px whose grey step to the right or lower neighbour is EDGE_LEVEL or more."""
    g = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    if g.shape[0] < 2 or g.shape[1] < 2:
        return 0.0
    dx = np.abs(np.diff(g, axis=1))[:-1, :]
    dy = np.abs(np.diff(g, axis=0))[:, :-1]
    return float((np.maximum(dx, dy) >= EDGE_LEVEL).mean())


def calibrate(drawn: dict, fps) -> list[dict]:
    """Make every blurred box of `drawn` (_layouts.plan_all) unreadable on its middle
    frame: [{"segment", "t0", "t1", "cell", "was", "note"}] of the boxes changed. Without
    text recognition (not macOS) nothing changes (verify fails closed there)."""
    if not _ocr.available():
        return []
    from .redact import grab_screen

    info, src = drawn["info"], drawn["screen"]
    changed = []
    for seg in drawn["segs"].values():
        logs = [x for x in seg.redaction if x["style"] == "blur"]
        keep, blocks = [], []
        for (n0, n1, x, y, w, h, cell), log in zip(seg.blur, logs, strict=True):
            t = (seg.screen_frame + (n0 + n1) / 2) / float(fps)
            frame = grab_screen(src, info, t, float(fps))
            if frame is None:  # before or after the recording: background or held frame
                keep.append((n0, n1, x, y, w, h, cell))
                continue
            ox, oy = seg.origin
            region = frame[oy + y : oy + y + h, ox + x : ox + x + w]
            was = cell
            while readable(pixelate(region, cell)) and cell < max(w, h):
                cell = min(cell * 2, max(w, h) + max(w, h) % 2)
            if readable(pixelate(region, cell)):
                blocks.append((n0, n1, x, y, w, h))
                log.update(style="block", note="still readable when blurred: a solid block")
                log.pop("cell", None)
            else:
                keep.append((n0, n1, x, y, w, h, cell))
                if cell != was:
                    log.update(cell=cell, note=f"blur made stronger (cell {was} -> {cell} px)")
            if cell != was or log["style"] == "block":
                changed.append({"t0": log["t0"], "t1": log["t1"], "cell": log.get("cell"),
                                "was": was, "note": log["note"]})  # fmt: skip
        seg.blur, seg.redact = keep, [*seg.redact, *blocks]
    return changed
