"""Product-demo checks of `videocut tool verify` (EDL segments with layout screen/pip/split).

    redaction       every redact block, mapped through the drawn zoom window into output
                    px, is checked on sampled output frames of its range (first, middle,
                    last, up to REDACT_SAMPLES): at least REDACT_MIN_SHARE of a solid
                    block's pixels must be the brand background; a blurred box must show
                    no sharp edges (_redact_blur.EDGE_MAX) and no text recognition can read
                    there; a segment that cuts a band off (crop style) must show the
                    recording without it (its middle frame, _crop_docs). Flag
                    `redaction_missing` otherwise
                    (a block zoomed fully out of the view, or under a landscape/square
                    speaker bubble, has nothing to hide there; the bubble is left out)
    text_cut        text a redaction cuts through: text recognition on output frames
                    around each blocked or blurred box's planned position (before
                    _redact_grow grew it) must read no line that overlaps it, and on the
                    recording no line may cross an edge a crop cuts at (first, middle,
                    last frame of the segment): flag `redaction_partial` (a failure:
                    half a line stays readable next to or below the box)
    screen time     per screen segment one output frame (its middle) is compared with the
                    screen recording at the mapped screen time (camera time + offset_s)
                    and at +-0.5 s / +-1 s, through the same zoom window: flag
                    `screen_time_mismatch` when another time matches clearly better
                    (static screens match everywhere and pass)
    clamps          render.json `screen_zoom_clamped` (keys widened so the screen stays
                    sharp) are reported as info, never a flag
    readability     per screen window (the whole screen until the first zoom key, then
                    each key) the estimated UI text height in output px at the EDL size
                    (_layouts.readability); `screen_text_small` below TEXT_MIN_PX is
                    information for the cut agent, never a failure
    overlap         every non-face segment must lie inside work/sync.json "overlap" (the
                    rule of `edl check`); a check that fails otherwise
    coverage        the personal data itself, independent of the boxes render drew: for
                    every enforced candidate with "text", on up to LEAK_SAMPLES output
                    frames of every screen segment that shows its time (and every
                    REEL_OCR_STEP s of every screen segment for all of them), the text is
                    found again on the screen recording frame that segment shows (local
                    OCR, _ocr.py); each place it is shown, mapped through the drawn zoom
                    window into output px, must be hidden: solid brand background or a
                    blurred box without sharp edges (>= LEAK_MIN_SHARE of its px), or cut
                    off by a crop (outside the view), and so must the candidate's measured
                    text box of that time (or its whole area) in any case. The reel
                    frame itself is read too: the text readable in the screen view is a
                    leak. Flag `redaction_leak` (a failure) with
                    the time and the candidate id. `redaction_unchecked` (a failure as
                    well): no text recognition (not macOS), a frame that cannot be read,
                    an e-mail/phone/name candidate without "text", or a text candidate
                    whose measurement does not match its text and area (measure_sig).
                    Screenshots (asset_coverage): the text found on the image must lie in
                    a redact box of every screenshot_frame item showing it
    decision        every personal-data candidate to redact (the human decision of `job
                    redaction`; without one: all) must be covered by the EDL (redact blocks
                    of the screen segments, props.redact of its screenshot_frame items):
                    flag `redaction_missing` (check "decision"). Render adds them anyway,
                    and the redaction samples above include those blocks

The geometry comes from the same _layouts plan render draws, at the size of the checked
reel (a preview is checked at its half size).
"""

import json
import math
import subprocess
from pathlib import Path

import numpy as np

from .. import edl as edl_mod
from .. import redaction as red_mod
from ..paths import tool_env
from . import _image, _layouts, _ocr, _redact_blur, _redact_grow
from . import _render_plan as plan_mod
from ._geometry import framed, sized
from .common import ToolError, binary
from .ingest import probe
from .job import redaction_enforced
from .redact import grab_screen, image_rgb

REDACT_SAMPLES = 5
REDACT_MIN_SHARE = 0.9
REDACT_TOL = 28  # max per-channel difference to the background colour (yuv420 + H.264)
REDACT_INSET = 3  # px left out at each edge (chroma bleeding)
CROP_MARGIN = 3.0  # grey levels the cropped reference must beat the uncropped one by
PARTIAL_SAMPLES = 3  # output frames read around a box's planned position
PARTIAL_CUT = 0.25  # share of a read line's height over that position that is a cut line
PARTIAL_PAD = 24  # output px read around it (at least; and the box's height)
LEAK_SAMPLES = 5
LEAK_CHUNK = 32  # reel frames decoded at once by the coverage check
LEAK_MIN_SHARE = 0.98  # of the text region's px that must be the block colour
REEL_OCR_STEP = 1.0  # seconds between reel frames read for every candidate text
MAP_OFFSETS = (-1.0, -0.5, 0.5, 1.0)  # alternative screen times, seconds
MAP_W = 96  # compared at this width (grey)
MAP_MARGIN = 3.0  # grey levels another time must beat the mapped one by
MAP_MIN = 4.0  # ... and the mapped one must be at least this far off


def context(job: Path, data: dict, size: tuple[int, int]) -> dict | None:
    """Drawn segments of the EDL dict `data` at reel `size`, or None for an EDL without
    screen layouts (face and slide segments only).
    Raises ToolError when the screen inputs are missing (render would have failed too)."""
    segs = data.get("segments") if isinstance(data, dict) else None
    if not any(isinstance(s, dict) and edl_mod.is_screen(s.get("layout")) for s in segs or []):
        return None
    src = (job / data["source"]).resolve()
    info = probe(src)
    edl = edl_mod.from_dict(data, info["duration"])
    edl, _ = sized(job, edl)  # the job's format, as render drew it
    edl, _, face = framed(job, edl)
    edl = _layouts.with_redaction(edl, redaction_enforced(job)[0])  # what render drew
    layouts = [s.layout for s in edl.segments]
    plan = plan_mod.build(edl, tuple(info["size"]), size, layouts)
    drawn = _layouts.plan_all(job, edl, plan, tuple(info["size"]), face, edl.size)
    starts, f0 = [], 0
    for r in plan.ranges:
        starts.append(f0)
        f0 += r.frames
    return {**drawn, "plan": plan, "starts": starts, "edl": edl, "bg": _layouts.background(job)}


def _raw(args: list[str]) -> bytes:
    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", *args]
    r = subprocess.run(cmd, capture_output=True, env=tool_env())
    if r.returncode != 0:
        raise ToolError(f"ffmpeg failed: {r.stderr.decode(errors='replace').strip()[-400:]}")
    return r.stdout


def _rgb(color: str) -> np.ndarray:
    v = int(color[2:], 16)
    return np.array([(v >> 16) & 255, (v >> 8) & 255, v & 255])


def _out_rect(seg: _layouts.Segment, n: int, x: int, y: int, w: int, h: int):
    """Source-px rect (x, y, w, h) of the screen -> output px [x0, y0, x1, y1] at local
    frame n, clipped to the view; None when nothing of it is visible."""
    wx, wy, ww = _layouts.window_at(seg, n)
    k = seg.view.w / ww
    v = seg.view
    x0, y0 = max(v.x + (x - wx) * k, v.x), max(v.y + (y - wy) * k, v.y)
    x1, y1 = min(v.x + (x + w - wx) * k, v.x + v.w), min(v.y + (y + h - wy) * k, v.y + v.h)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return [math.ceil(x0), math.ceil(y0), math.floor(x1), math.floor(y1)]


def _frames(reel: Path, numbers: list[int], size: tuple[int, int]) -> dict[int, np.ndarray]:
    """Output frames `numbers` of the reel as rgb arrays {n: [h, w, 3]}."""
    if not numbers:
        return {}
    want = sorted(set(numbers))
    sel = _image.select_frames(want)
    out = _raw(
        ["-i", str(reel), "-an", "-vf", f"select='{sel}'", "-fps_mode", "passthrough"]
        + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    )
    w, h = size
    arr = np.frombuffer(out, np.uint8).reshape(-1, h, w, 3)
    return dict(zip(want, arr, strict=False))


def _frame_stream(reel: Path, numbers: list[int], size: tuple[int, int]):
    """Output frames `numbers` of the reel, in order, one at a time: (n, rgb array) — one
    decode of the reel, never all frames in memory. A frame ffmpeg does not deliver is
    yielded as (n, None)."""
    want = sorted(set(numbers))
    if not want:
        return
    w, h = size
    sel = _image.select_frames(want)
    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(reel),
           "-an", "-vf", f"select='{sel}'", "-fps_mode", "passthrough", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]  # fmt: skip
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=tool_env())
    try:
        for n in want:
            buf = proc.stdout.read(w * h * 3) if proc.stdout else b""
            if len(buf) != w * h * 3:
                yield n, None
                continue
            yield n, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.kill()
        proc.wait()


def redaction(reel: Path, ctx: dict, size: tuple[int, int]) -> list[dict]:
    """Per redact block: the sampled output frames, the background share (solid blocks) or
    the edge share and the text read there (blurred boxes), and flags. A band the segment
    crops off is not on the screen view at all (the coverage check maps it outside)."""
    plan = ctx["plan"]
    todo = []  # (doc, local n, output frame, rect, pip inset, other blurred boxes)
    docs = []
    for i, seg in ctx["segs"].items():
        r = plan.ranges[i]
        boxes = [(*b, "block") for b in seg.redact] + [(*b[:6], "blur") for b in seg.blur]
        for n0, n1, x, y, w, h, style in boxes:
            k = min(REDACT_SAMPLES, n1 - n0 + 1)
            ns = sorted({round(n0 + (n1 - n0) * j / max(k - 1, 1)) for j in range(k)})
            doc = {
                "segment": r.seg,
                "t0": round(float((ctx["starts"][i] + n0) / plan.fps), 3),
                "t1": round(float((ctx["starts"][i] + n1 + 1) / plan.fps), 3),
                "style": style,
                "samples": [],
                "flags": [],
            }
            docs.append(doc)
            for n in ns:
                rect = _out_rect(seg, n, x, y, w, h)
                if rect is None:
                    continue
                x0, y0, x1, y1 = rect
                rect = [x0 + REDACT_INSET, y0 + REDACT_INSET, x1 - REDACT_INSET, y1 - REDACT_INSET]
                if rect[2] <= rect[0] or rect[3] <= rect[1]:
                    continue
                blurs = _blur_rects(seg, n) if style == "blur" else []
                todo.append((doc, n, ctx["starts"][i] + n, rect, seg.inset, blurs))
    frames = _frames(reel, [f for _, _, f, _, _, _ in todo], size)
    bg = _rgb(ctx["bg"])
    for doc, _, f, rect, inset, blurs in todo:
        img = frames.get(f)
        if img is None:
            continue
        x0, y0, x1, y1 = rect
        patch = img[y0:y1, x0:x1].astype(int)
        seen = _uncovered(rect, inset)
        if not seen.any():  # wholly under the speaker bubble: nothing of it shows
            continue
        sample = {"t": round(float(f / ctx["plan"].fps), 3), "box": rect}
        if doc["style"] == "blur":
            edges = _edge_share(patch, seen & ~_seams(blurs, img.shape)[y0:y1, x0:x1])
            read = _redact_blur.readable(np.where(seen[..., None], patch, 0).astype(np.uint8))
            sample |= {"edge_share": round(edges, 4), **({"read": read} if read else {})}
            ok = edges <= _redact_blur.EDGE_MAX and not read
        else:
            share = float((np.abs(patch - bg).max(axis=2) <= REDACT_TOL)[seen].mean())
            sample["bg_share"] = round(share, 3)
            ok = share >= REDACT_MIN_SHARE
        doc["samples"].append(sample)
        if not ok and "redaction_missing" not in doc["flags"]:
            doc["flags"].append("redaction_missing")
    return docs + _crop_docs(reel, ctx, size)


def _over(line: list[float], rect: list[int], cut: float) -> bool:
    """Whether a read `line` [x0, y0, x1, y1] (px) overlaps `rect` by `cut` of its height
    (and half its height across: a character)."""
    lh = line[3] - line[1]
    oy = min(line[3], rect[3]) - max(line[1], rect[1])
    ox = min(line[2], rect[2]) - max(line[0], rect[0])
    return lh > 0 and oy >= cut * lh and ox >= 0.5 * lh


def _read_lines(img: np.ndarray, region: list[int]) -> list[tuple[list[float], str]]:
    """Readable lines (_redact_blur.MIN_CHARS) in `region` of `img`, boxes in img px."""
    x0, y0, x1, y1 = region
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    out = []
    for ln in _ocr.recognize(np.ascontiguousarray(img[y0:y1, x0:x1], dtype=np.uint8)):
        if _redact_blur.readable_text(ln["text"]):
            b = ln["box"]
            out.append(([x0 + b[0] * (x1 - x0), y0 + b[1] * (y1 - y0),
                         x0 + b[2] * (x1 - x0), y0 + b[3] * (y1 - y0)], ln["text"]))  # fmt: skip
    return out


def text_cut(reel: Path, ctx: dict, size: tuple[int, int]) -> list[dict]:
    """Text a redaction cut through (flag `redaction_partial`): per blocked or blurred box,
    lines read on output frames around its planned position that overlap it; per crop,
    lines on the recording that cross the edge it cuts at."""
    plan = ctx["plan"]
    todo, docs = [], []
    for i, seg in ctx["segs"].items():
        r = plan.ranges[i]
        for n0, n1, x, y, w, h in seg.planned:
            k = min(PARTIAL_SAMPLES, n1 - n0 + 1)
            ns = sorted({round(n0 + (n1 - n0) * j / max(k - 1, 1)) for j in range(k)})
            doc = {"segment": r.seg, "kind": "box",
                   "t0": round(float((ctx["starts"][i] + n0) / plan.fps), 3),
                   "t1": round(float((ctx["starts"][i] + n1 + 1) / plan.fps), 3),
                   "samples": [], "flags": []}  # fmt: skip
            docs.append(doc)
            for n in ns:
                rect = _out_rect(seg, n, x, y, w, h)
                if rect is not None:
                    todo.append((doc, ctx["starts"][i] + n, rect, seg.view))
    frames = _frames(reel, [f for _, f, _, _ in todo], size)
    for doc, f, rect, v in todo:
        img = frames.get(f)
        if img is None:
            doc["flags"].append("redaction_partial")  # fail closed: nothing read
            continue
        pad = max(PARTIAL_PAD, rect[3] - rect[1])
        region = [max(rect[0] - 3 * pad, v.x), max(rect[1] - pad, v.y),
                  min(rect[2] + 3 * pad, v.x + v.w), min(rect[3] + pad, v.y + v.h)]  # fmt: skip
        cut = [t for b, t in _read_lines(img, region) if _over(b, rect, PARTIAL_CUT)]
        doc["samples"].append({"t": round(float(f / plan.fps), 3), "box": rect,
                               **({"read": cut} if cut else {})})  # fmt: skip
        if cut and "redaction_partial" not in doc["flags"]:
            doc["flags"].append("redaction_partial")
    return docs + _crop_cut_docs(ctx)


def _crop_cut_docs(ctx: dict) -> list[dict]:
    """Per segment that cuts a band off: text lines on the recording (first, middle, last
    frame) that cross an edge the crop cuts at; flag `redaction_partial`."""
    plan, sinfo = ctx["plan"], ctx["info"]
    docs = []
    for i, seg in ctx["segs"].items():
        if seg.full_screen in (None, seg.screen):
            continue
        (ox, oy), (sw, sh), (fw, fh) = seg.origin, seg.screen, seg.full_screen
        pad = math.ceil(_redact_grow.SEARCH * _redact_grow._text_px(seg.full_screen))
        strips = []  # (rect read on the recording, the cut: axis, position)
        if oy > 0:
            strips.append(([0, max(oy - pad, 0), fw, min(oy + pad, fh)], 1, oy))
        if oy + sh < fh:
            strips.append(([0, max(oy + sh - pad, 0), fw, min(oy + sh + pad, fh)], 1, oy + sh))
        if ox > 0:
            strips.append(([max(ox - pad, 0), 0, min(ox + pad, fw), fh], 0, ox))
        if ox + sw < fw:
            strips.append(([max(ox + sw - pad, 0), 0, min(ox + sw + pad, fw), fh], 0, ox + sw))
        doc = {"segment": plan.ranges[i].seg, "kind": "crop",
               "t0": round(float(ctx["starts"][i] / plan.fps), 3),
               "t1": round(float((ctx["starts"][i] + seg.frames) / plan.fps), 3),
               "cuts": [c for _, _, c in strips], "samples": [], "flags": []}  # fmt: skip
        docs.append(doc)
        for n in sorted({0, seg.frames // 2, seg.frames - 1}):
            t = float((seg.screen_frame + n) / plan.fps)
            img = grab_screen(ctx["screen"], sinfo, t, float(plan.fps))
            if img is None:
                if 0 <= t <= sinfo["duration"]:
                    doc["flags"].append("redaction_partial")  # fail closed
                continue
            cut = []
            for region, axis, at in strips:
                for b, text in _read_lines(img, region):
                    lo, hi = b[axis + 0], b[axis + 2]
                    m = _redact_grow.LINE_CUT * (hi - lo)
                    if lo < at - m and hi > at + m:
                        cut.append(text)
            doc["samples"].append({"t": round(t, 3), **({"read": cut} if cut else {})})
            if cut and "redaction_partial" not in doc["flags"]:
                doc["flags"].append("redaction_partial")
    return docs


def _crop_docs(reel: Path, ctx: dict, size: tuple[int, int]) -> list[dict]:
    """Per segment that cuts a band off (crop style): its middle frame's screen view must
    match the recording without the band (the window from the kept area's origin) clearly
    better than the same window on the whole recording (what an uncropped view shows):
    by CROP_MARGIN grey levels, or within MAP_MIN when both look alike (a plain screen).
    Redacted boxes are left out. Flag `redaction_missing` otherwise (fail closed: also
    when a frame cannot be read)."""
    plan, sinfo = ctx["plan"], ctx["info"]
    crops = {i: s for i, s in ctx["segs"].items() if s.full_screen not in (None, s.screen)}
    frames = _frames(reel, [ctx["starts"][i] + s.frames // 2 for i, s in crops.items()], size)
    docs = []
    for i, seg in crops.items():
        n = seg.frames // 2
        img = frames.get(ctx["starts"][i] + n)
        v = seg.view
        doc = {"segment": plan.ranges[i].seg, "style": "crop",
               "t0": round(float(ctx["starts"][i] / plan.fps), 3),
               "t1": round(float((ctx["starts"][i] + seg.frames) / plan.fps), 3),
               "kept": [*seg.origin, *seg.screen], "flags": []}  # fmt: skip
        docs.append(doc)
        t = float((seg.screen_frame + n) / plan.fps)
        win = _layouts.window_at(seg, n)
        refs = [_screen_view(ctx["screen"], t, w, v, sinfo) for w in (_in_recording(seg, win), win)]
        if img is None or any(r is None for r in refs):
            doc["flags"].append("redaction_missing")  # fail closed: nothing to compare
            continue
        mask = _uncovered([v.x, v.y, v.x + v.w, v.y + v.h], seg.inset)
        for n0, n1, x, y, w, h, *_ in [*seg.redact, *seg.blur]:
            rect = _out_rect(seg, n, x, y, w, h) if n0 <= n <= n1 else None
            if rect:
                mask[rect[1] - v.y : rect[3] - v.y, rect[0] - v.x : rect[2] - v.x] = False
        shown = _grey(img[v.y : v.y + v.h, v.x : v.x + v.w].astype(float))
        cropped, whole = (
            round(float(np.abs(_grey(r) - shown)[mask].mean()) if mask.any() else 0.0, 2)
            for r in refs
        )
        doc |= {"diff": cropped, "diff_uncropped": whole}
        if not (cropped <= MAP_MIN or cropped + CROP_MARGIN <= whole):
            doc["flags"].append("redaction_missing")
    return docs


def _edge_share(patch: np.ndarray, seen: np.ndarray) -> float:
    """_redact_blur.edge_share of the px of `patch` in `seen` (not under the bubble)."""
    g = patch[..., 0] * 0.299 + patch[..., 1] * 0.587 + patch[..., 2] * 0.114
    if g.shape[0] < 2 or g.shape[1] < 2:
        return 0.0
    step = np.maximum(np.abs(np.diff(g, axis=1))[:-1, :], np.abs(np.diff(g, axis=0))[:, :-1])
    inner = seen[:-1, :-1] & seen[1:, :-1] & seen[:-1, 1:]
    return float((step >= _redact_blur.EDGE_LEVEL)[inner].mean()) if inner.any() else 0.0


def _shown_rect(seg: _layouts.Segment, rect: tuple[int, int, int, int]):
    """A rect (x, y, w, h) of the recording in px of the screen the segment shows (a crop
    moves its origin; what lies in the cropped band maps outside the view)."""
    x, y, w, h = rect
    return x - seg.origin[0], y - seg.origin[1], w, h


def _blur_rects(seg: _layouts.Segment, n: int) -> list[list[int]]:
    """Output rects of the blurred boxes of `seg` drawn at local frame n."""
    rects = (_out_rect(seg, n, x, y, w, h) for n0, n1, x, y, w, h, _ in seg.blur if n0 <= n <= n1)
    return [r for r in rects if r is not None]


def _seams(rects: list[list[int]], shape) -> np.ndarray:
    """Mask of the output px within REDACT_INSET of an edge of one of `rects`: where two
    overlapping blurred boxes meet, each blurred on its own, the step is no text."""
    mask = np.zeros(shape[:2], bool)
    k = REDACT_INSET
    for x0, y0, x1, y1 in rects:
        for a, b in ((y0, y0), (y1, y1)):
            mask[max(a - k, 0) : b + k, max(x0 - k, 0) : x1 + k] = True
        for a, b in ((x0, x0), (x1, x1)):
            mask[max(y0 - k, 0) : y1 + k, max(a - k, 0) : b + k] = True
    return mask


def _blurred(seg: _layouts.Segment, n: int, img: np.ndarray) -> np.ndarray:
    """Mask of the output px that lie in a blurred box of `seg` active at local frame n
    and show no sharp edge there (a blur that was not drawn shows the text's edges)."""
    mask = np.zeros(img.shape[:2], bool)
    rects = _blur_rects(seg, n)
    seams = _seams(rects, img.shape)
    for x0, y0, x1, y1 in rects:
        patch = img[y0:y1, x0:x1].astype(int)
        seen = _uncovered([x0, y0, x1, y1], seg.inset) & ~seams[y0:y1, x0:x1]
        if _edge_share(patch, seen) <= _redact_blur.EDGE_MAX:
            mask[y0:y1, x0:x1] = True
    return mask


def _src_rect(box, size) -> tuple[int, int, int, int]:
    """Normalised box -> source px (x, y, w, h), outward."""
    sw, sh = size
    x0, y0 = math.floor(box[0] * sw), math.floor(box[1] * sh)
    x1, y1 = math.ceil(box[2] * sw), math.ceil(box[3] * sh)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _track_boxes(c: dict, cam_t: float) -> list[tuple[list[float], str]]:
    """Where candidate `c` is at camera time cam_t by its own measurement: the text boxes
    of the track pieces covering it ("track"), or the drawn box of an "area" piece or of a
    candidate without a track ("area"); [] when it is not shown then."""
    hits = [p for p in c.get("track") or [] if p["t0"] - 1e-3 <= cam_t <= p["t1"] + 1e-3]
    if not c.get("track") and c["t0"] - 1e-3 <= cam_t <= c["t1"] + 1e-3:
        return [(c["box"], "area")]
    return [(p["text_box"], "track") if p.get("text_box") else (p["box"], "area") for p in hits]


def unchecked(cands: list[dict]) -> list[dict]:
    """Enforced candidates whose position cannot be trusted: an e-mail, phone or name
    without "text", or a text candidate not measured for its current text and area."""
    out = []
    for c in cands:
        why = None
        if "text" not in c and c.get("kind") in red_mod.TEXT_KINDS:
            why = "no text named, its position is not measured"
        elif "text" in c and not red_mod.is_measured(c):
            why = "not measured for its current text and area (`redact measure`)"
        if why:
            out.append({"id": c["id"], "source": "candidate", "where": why,
                        "flags": ["redaction_unchecked"]})  # fmt: skip
    return out


def coverage(reel: Path, ctx: dict, size: tuple[int, int], cands: list[dict]) -> list[dict]:
    """The personal data itself must be hidden: [{"id", "segment", "t", "source", "share",
    "flags"}] per checked place (see the module doc, "coverage")."""
    plan = ctx["plan"]
    fps = float(plan.fps)
    spans = []  # camera time of every screen segment
    for i, seg in ctx["segs"].items():
        cam0 = ctx["edl"].segments[plan.ranges[i].seg].start
        spans.append((cam0, cam0 + seg.frames / fps))
    shown = [
        c for c in cands
        if "asset" not in c and any(min(c["t1"], b) > max(c["t0"], a) for a, b in spans)
    ]  # fmt: skip
    texts = [c for c in shown if isinstance(c.get("text"), str)]
    docs = unchecked(shown)
    if not texts:
        return docs
    if not _ocr.available():
        return docs + [{"id": c["id"], "segment": None, "t": None, "source": "none",
                        "where": "text recognition not available",
                        "flags": ["redaction_unchecked"]} for c in texts]  # fmt: skip
    info, src = ctx["info"], ctx["screen"]
    picks: dict[tuple[int, int], set[int]] = {}  # (range index, local n) -> candidate idx
    for i, seg in ctx["segs"].items():
        start = plan.ranges[i]
        cam0 = ctx["edl"].segments[start.seg].start
        cam1 = cam0 + seg.frames / fps
        for j, c in enumerate(texts):
            a, b = max(c["t0"], cam0), min(c["t1"], cam1)
            if b <= a:
                continue
            n0, n1 = round((a - cam0) * fps), min(round((b - cam0) * fps), seg.frames - 1)
            k = min(LEAK_SAMPLES, n1 - n0 + 1)
            for m in range(k):
                n = round(n0 + (n1 - n0) * m / max(k - 1, 1))
                picks.setdefault((i, n), set()).add(j)
        step = max(1, round(REEL_OCR_STEP * fps))
        for n in range(step // 2, seg.frames, step):
            picks.setdefault((i, n), set()).update(range(len(texts)))
    bg = _rgb(ctx["bg"])

    def one(pick, img) -> list[dict]:
        (i, n), idx = pick
        seg = ctx["segs"][i]
        t_out = round((ctx["starts"][i] + n) / fps, 3)
        seg_no = plan.ranges[i].seg
        if img is None:
            return [{"id": texts[j]["id"], "segment": seg_no, "t": t_out, "source": "none",
                     "where": f"at {t_out} s (reel frame not readable)",
                     "flags": ["redaction_unchecked"]} for j in sorted(idx)]  # fmt: skip
        cam_t = ctx["edl"].segments[seg_no].start + n / fps
        needles = sorted({texts[j]["text"] for j in idx})
        frame = grab_screen(src, info, (seg.screen_frame + n) / fps, fps)
        found = _ocr.find_many(frame, needles) if frame is not None else {}
        v = seg.view
        shown = _ocr.find_many(np.ascontiguousarray(img[v.y : v.y + v.h, v.x : v.x + v.w]),
                               needles)  # fmt: skip
        out = []
        blurred = _blurred(seg, n, img)
        for j in sorted(idx):
            c = texts[j]
            # where the text is on the frame shown, and where the measurement says it is
            boxes = [(o["box"], "screen_ocr") for o in found.get(c["text"], [])]
            boxes += _track_boxes(c, cam_t)
            if frame is None and (seg.screen_frame + n) / fps <= info["duration"] - 0.1:
                out.append({"id": c["id"], "segment": seg_no, "t": t_out, "source": "none",
                            "where": f"at {t_out} s (screen frame not readable)",
                            "flags": ["redaction_unchecked"]})  # fmt: skip
            for box, source in boxes:
                rect = _out_rect(seg, n, *_shown_rect(seg, _src_rect(box, info["size"])))
                if rect is None:
                    continue  # zoomed out of the view, or cut off (crop)
                seen = _uncovered(rect, seg.inset)
                if not seen.any():
                    continue  # under the speaker bubble
                x0, y0, x1, y1 = rect
                patch = img[y0:y1, x0:x1].astype(int)
                hidden = (np.abs(patch - bg).max(axis=2) <= REDACT_TOL) | blurred[y0:y1, x0:x1]
                share = float(hidden[seen].mean())
                doc = {"id": c["id"], "segment": seg_no, "t": t_out, "source": source,
                       "box": rect, "share": round(share, 3), "flags": []}  # fmt: skip
                if share < LEAK_MIN_SHARE:
                    doc["flags"].append("redaction_leak")
                    doc["where"] = f"at {t_out} s ({source}, {share:.0%} covered)"
                out.append(doc)
            if shown.get(c["text"]):
                out.append({"id": c["id"], "segment": seg_no, "t": t_out, "source": "reel_ocr",
                            "flags": ["redaction_leak"],
                            "where": f"at {t_out} s (readable in the reel)"})  # fmt: skip
        return out

    by_frame: dict[int, list] = {}
    for pick in picks.items():
        (i, n), _ = pick
        by_frame.setdefault(ctx["starts"][i] + n, []).append(pick)
    chunk: list = []
    for f, img in _frame_stream(reel, list(by_frame), size):  # a chunk of frames at a time
        chunk += [(pick, img) for pick in by_frame[f]]
        if len(chunk) >= LEAK_CHUNK:
            docs += [d for ds in _ocr.pmap(lambda a: one(*a), chunk) for d in ds]
            chunk = []
    docs += [d for ds in _ocr.pmap(lambda a: one(*a), chunk) for d in ds]
    return docs


def asset_coverage(job: Path, data: dict, cands: list[dict]) -> list[dict]:
    """Screenshot candidates with "text": the text found on the image (local OCR) must lie
    inside a redact box of every screenshot_frame item showing it (its props.redact plus
    the measured boxes overlays adds); flag `redaction_leak` per item otherwise."""
    ovs = data.get("overlays") if isinstance(data, dict) else None
    items = [
        (j, o) for j, o in enumerate(ovs if isinstance(ovs, list) else [])
        if isinstance(o, dict) and o.get("scene") == "screenshot_frame"
        and isinstance(o.get("props"), dict)
    ]  # fmt: skip

    def showing(c):
        return [(j, o) for j, o in items if red_mod.same_asset(job, o["props"].get("file"),
                                                               c["asset"])]  # fmt: skip

    shots = [c for c in cands if "asset" in c and showing(c)]
    docs = unchecked(shots)
    for c in (c for c in shots if isinstance(c.get("text"), str)):
        shown = showing(c)
        if not _ocr.available():
            docs.append({"id": c["id"], "source": "none", "flags": ["redaction_unchecked"],
                         "where": "text recognition not available"})  # fmt: skip
            continue
        f = red_mod.asset_file(job, c["asset"])
        try:
            found = [o["box"] for o in _ocr.find(image_rgb(f), c["text"])]
        except (ToolError, _ocr.OcrError, OSError) as e:
            docs.append({"id": c["id"], "source": "none", "flags": ["redaction_unchecked"],
                         "where": f"cannot read {c['asset']}: {e}"})  # fmt: skip
            continue
        for j, o in shown:
            drawn = [b for b in o["props"].get("redact") or [] if red_mod._box(b)]
            drawn += red_mod.asset_boxes(c)
            for box in found:
                ok = any(red_mod._covers(d, box) for d in drawn)
                doc = {"id": c["id"], "overlay": j, "source": "asset_ocr", "box": box,
                       "flags": [] if ok else ["redaction_leak"]}  # fmt: skip
                if not ok:
                    doc["where"] = f"$.overlays[{j}]: the text is outside every redact box"
                docs.append(doc)
    return docs


def _uncovered(rect: list[int], inset) -> np.ndarray:
    """Mask of the output px of `rect` [x0, y0, x1, y1] not under the pip `inset` (a
    landscape/square speaker bubble lies over the screen view)."""
    x0, y0, x1, y1 = rect
    seen = np.ones((y1 - y0, x1 - x0), bool)
    if inset is not None:
        a, b = max(inset.x - x0, 0), max(inset.y - y0, 0)
        seen[b : max(inset.y + inset.h - y0, 0), a : max(inset.x + inset.w - x0, 0)] = False
    return seen


def _grey(img: np.ndarray) -> np.ndarray:
    return img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114


def _shrink(g: np.ndarray, w: int) -> np.ndarray:
    """Area-average grey image `g` down to about `w` columns."""
    k = max(1, g.shape[1] // w)
    h2, w2 = g.shape[0] // k, g.shape[1] // k
    return g[: h2 * k, : w2 * k].reshape(h2, k, w2, k).mean(axis=(1, 3))


def _screen_view(src: Path, t: float, win, view, sinfo: dict) -> np.ndarray | None:
    """The screen recording at screen time t, cut to window `win` and scaled to the view."""
    if t < 0 or t > sinfo["duration"]:
        return None
    t += sinfo.get("video_start") or 0.0  # screen time 0 = the first video frame
    sw, sh = sinfo["size"]
    x, y = max(0, int(win[0])), max(0, int(win[1]))
    w = min(int(win[2]), sw - x)
    h = min(int(win[2] * view.h / view.w), sh - y)  # the view aspect is rounded to even px
    out = _raw(
        ["-seek_timestamp", "1", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-an"]
        + ["-vf"]
        + [f"crop={w}:{h}:{x}:{y},scale={view.w}:{view.h},format=rgb24"]
        + ["-f", "rawvideo", "-"]
    )
    if len(out) != view.w * view.h * 3:
        return None
    return np.frombuffer(out, np.uint8).reshape(view.h, view.w, 3).astype(float)


def _in_recording(seg: _layouts.Segment, win):
    """A zoom window of the screen shown -> of the recording (a crop moves the origin)."""
    return (win[0] + seg.origin[0], win[1] + seg.origin[1], win[2])


def screen_time(reel: Path, ctx: dict, size: tuple[int, int]) -> list[dict]:
    """Per screen segment: grey difference of its middle frame to the screen recording at
    the mapped time and at MAP_OFFSETS, and `screen_time_mismatch` when one is clearly
    better. Redaction blocks are left out of the comparison."""
    plan, sinfo = ctx["plan"], ctx["info"]
    picks = {i: seg.frames // 2 for i, seg in ctx["segs"].items()}
    frames = _frames(reel, [ctx["starts"][i] + n for i, n in picks.items()], size)
    docs = []
    for i, seg in ctx["segs"].items():
        n = picks[i]
        img = frames.get(ctx["starts"][i] + n)
        if img is None:
            continue
        v = seg.view
        mask = _uncovered([v.x, v.y, v.x + v.w, v.y + v.h], seg.inset)
        for n0, n1, x, y, w, h, *_ in [*seg.redact, *seg.blur]:
            rect = _out_rect(seg, n, x, y, w, h) if n0 <= n <= n1 else None
            if rect:
                mask[rect[1] - v.y : rect[3] - v.y, rect[0] - v.x : rect[2] - v.x] = False
        reel_view = _grey(img[v.y : v.y + v.h, v.x : v.x + v.w].astype(float))
        win = _in_recording(seg, _layouts.window_at(seg, n))
        t = float((seg.screen_frame + n) / plan.fps)
        # each reference is one seek into the full-size recording (~0.5 s): read them at once
        offsets = (0.0, *MAP_OFFSETS)
        asks = [(t + dt, win, v) for dt in offsets]
        refs = _ocr.pmap(lambda a: _screen_view(ctx["screen"], *a, sinfo), asks)
        diffs = {}
        for dt, ref in zip(offsets, refs, strict=True):
            if ref is None:
                continue
            d = np.abs(_grey(ref) - reel_view)
            diffs[dt] = float(_shrink(np.where(mask, d, 0.0), MAP_W).mean())
        if 0.0 not in diffs:
            continue
        best = min(diffs, key=diffs.get)
        flags = []
        if best != 0.0 and diffs[0.0] >= MAP_MIN and diffs[best] + MAP_MARGIN < diffs[0.0]:
            flags.append("screen_time_mismatch")
        docs.append(
            {
                "segment": plan.ranges[i].seg,
                "layout": seg.layout,
                "t": round(float((ctx["starts"][i] + n) / plan.fps), 3),
                "screen_t": round(t, 3),
                "diff": {f"{k:+.1f}": round(d, 2) for k, d in sorted(diffs.items())},
                "flags": flags,
            }
        )
    return docs


def screen_spans(ctx: dict | None) -> list[tuple[float, float]]:
    """Output time spans of "screen" layout segments (no camera picture: a still screen
    is not a frozen video)."""
    if ctx is None:
        return []
    plan = ctx["plan"]
    return [
        (float(ctx["starts"][i] / plan.fps), float((ctx["starts"][i] + s.frames) / plan.fps))
        for i, s in ctx["segs"].items()
        if s.layout == "screen"
    ]


def bubble_spans(ctx: dict | None) -> list[tuple[float, float, list[int]]]:
    """Output time spans and boxes of the round speaker bubble (landscape/square pip): the
    rest of such a frame is a still screen, so the camera picture is judged frozen or not
    inside the bubble only."""
    if ctx is None:
        return []
    plan = ctx["plan"]
    return [
        (float(ctx["starts"][i] / plan.fps), float((ctx["starts"][i] + s.frames) / plan.fps),
         [int(v) for v in s.inset.box()])
        for i, s in ctx["segs"].items()
        if s.layout == "pip" and s.inset is not None and s.radius == _layouts.BUBBLE_RADIUS
    ]  # fmt: skip


def overlap_check(job: Path, data: dict) -> list[str]:
    return edl_mod.overlap_errors(data, edl_mod.read_sync(job / "work" / "sync.json"))


def clamped_info(render: dict) -> list[dict]:
    c = render.get("screen_zoom_clamped")
    return c if isinstance(c, list) else []


def load_views(job: Path) -> list[dict]:
    """The areas overlays keep clear in a product demo, from overlays.json "views":
    [{"t0", "t1", "screen": box}], a screen view that does not fill the frame and every
    zoom target ("focus"); the slide part of a stacked slide_split also carries its "seam"
    (y) and "content" box (verify.overlay_checks: captions on the seam)."""
    try:
        doc = json.loads((job / "work" / "overlays.json").read_text())
    except (OSError, ValueError):
        return []
    views = doc.get("views") if isinstance(doc, dict) else None
    out = []
    for v in views if isinstance(views, list) else []:
        if not isinstance(v, dict) or v.get("layout") == "motion":  # _verify_motion checks bands
            continue
        focus = v.get("focus") if isinstance(v.get("focus"), list) else []
        for b in [v, *(f for f in focus if isinstance(f, dict))]:
            box = b.get("screen", b.get("box"))
            if isinstance(box, list) and len(box) == 4 and _num(b.get("t0"), b.get("t1"), *box):
                out.append({"t0": b["t0"], "t1": b["t1"], "screen": box})
                content = b.get("content")
                if (
                    b is v and v.get("layout") == "slide_split" and _num(v.get("seam"))
                    and isinstance(content, list) and len(content) == 4 and _num(*content)
                ):  # fmt: skip
                    out[-1].update(seam=v["seam"], content=content)
    return out


def _num(*vals) -> bool:
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals
    )


def decision_gaps(job: Path, data: dict) -> list[dict]:
    """Candidates to redact (the human decision, or all) the EDL does not cover:
    [{"check": "decision", "id", "where", "flags": ["redaction_missing"]}]."""
    cands, _ = redaction_enforced(job)
    return [
        {"check": "decision", **g, "flags": ["redaction_missing"]}
        for g in red_mod.missing(cands, data, job)
    ]


def readability(ctx: dict) -> list[dict]:
    """UI text height per screen window of every non-face segment:
    [{"segment", "layout", "t", "zoom", "text_px", "readable_zoom", "flags"}]."""
    edl, screen = ctx["edl"], tuple(ctx["info"]["size"])
    return [
        {"segment": k, "layout": s.layout, **doc}
        for k, s in enumerate(edl.segments)
        if edl_mod.is_screen(s.layout)
        for doc in _layouts.readability(s, screen, tuple(edl.size))
    ]


def check(job: Path, data: dict, render: dict, reel: Path, size: tuple[int, int]) -> dict:
    """All product-demo checks: {"coverage", "redaction", "text_cut", "screen_time",
    "clamped", "readability", "overlap", "spans", "decision"}; only "decision" and the
    screenshot part of "coverage" for a face-only EDL."""
    try:
        gaps = decision_gaps(job, data)
        ctx = context(job, data, size)
    except (ToolError, edl_mod.EdlError, KeyError, TypeError) as e:
        return {"error": str(e)}
    try:
        cands = redaction_enforced(job)[0]
        shots = asset_coverage(job, data, cands)
        if ctx is None:
            return {k: v for k, v in (("decision", gaps), ("coverage", shots)) if v}
        red_docs, time_docs = redaction(reel, ctx, size), screen_time(reel, ctx, size)
        cut_docs = text_cut(reel, ctx, size)
        cover = coverage(reel, ctx, size, cands) + shots
    except (ToolError, ValueError, _ocr.OcrError) as e:  # fail closed: unchecked is not ok
        return {"error": f"screen checks failed: {e}"}
    return {
        "decision": gaps,
        "coverage": cover,
        "redaction": red_docs,
        "text_cut": cut_docs,
        "screen_time": time_docs,
        "clamped": clamped_info(render),
        "readability": readability(ctx),
        "overlap": overlap_check(job, data),
        "spans": screen_spans(ctx),
        "bubbles": bubble_spans(ctx),
    }
