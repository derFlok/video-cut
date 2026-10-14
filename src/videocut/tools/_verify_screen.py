"""Product-demo checks of `videocut tool verify` (EDL segments with layout screen/pip/split).

    redaction       every redact block, mapped through the drawn zoom window into output
                    px, is checked on sampled output frames of its range (first, middle,
                    last, up to REDACT_SAMPLES): at least REDACT_MIN_SHARE of its pixels
                    must be the brand background. Flag `redaction_missing` otherwise
                    (a block zoomed fully out of the view, or under a landscape/square
                    speaker bubble, has nothing to hide there; the bubble is left out)
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
                    window into output px, must be solid brand background
                    (>= LEAK_MIN_SHARE of its px), and so must the candidate's measured
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
from . import _layouts, _ocr
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
LEAK_SAMPLES = 5
LEAK_CHUNK = 32  # reel frames decoded at once by the coverage check
LEAK_MIN_SHARE = 0.98  # of the text region's px that must be the block colour
REEL_OCR_STEP = 1.0  # seconds between reel frames read for every candidate text
MAP_OFFSETS = (-1.0, -0.5, 0.5, 1.0)  # alternative screen times, seconds
MAP_W = 96  # compared at this width (grey)
MAP_MARGIN = 3.0  # grey levels another time must beat the mapped one by
MAP_MIN = 4.0  # ... and the mapped one must be at least this far off


def context(job: Path, data: dict, size: tuple[int, int]) -> dict | None:
    """Drawn segments of the EDL dict `data` at reel `size`, or None for a face-only EDL.
    Raises ToolError when the screen inputs are missing (render would have failed too)."""
    segs = data.get("segments") if isinstance(data, dict) else None
    if not any(isinstance(s, dict) and s.get("layout", "face") != "face" for s in segs or []):
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
    sel = "+".join(f"eq(n\\,{n})" for n in want)
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
    sel = "+".join(f"eq(n\\,{n})" for n in want)
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
    """Per redact block: the sampled output frames, the background share and flags."""
    plan = ctx["plan"]
    todo = []  # (doc, local n, output frame, rect, pip inset)
    docs = []
    for i, seg in ctx["segs"].items():
        r = plan.ranges[i]
        for n0, n1, x, y, w, h in seg.redact:
            k = min(REDACT_SAMPLES, n1 - n0 + 1)
            ns = sorted({round(n0 + (n1 - n0) * j / max(k - 1, 1)) for j in range(k)})
            doc = {
                "segment": r.seg,
                "t0": round(float((ctx["starts"][i] + n0) / plan.fps), 3),
                "t1": round(float((ctx["starts"][i] + n1 + 1) / plan.fps), 3),
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
                todo.append((doc, n, ctx["starts"][i] + n, rect, seg.inset))
    frames = _frames(reel, [f for _, _, f, _, _ in todo], size)
    bg = _rgb(ctx["bg"])
    for doc, _, f, rect, inset in todo:
        img = frames.get(f)
        if img is None:
            continue
        x0, y0, x1, y1 = rect
        patch = img[y0:y1, x0:x1].astype(int)
        seen = _uncovered(rect, inset)
        if not seen.any():  # wholly under the speaker bubble: nothing of it shows
            continue
        share = float((np.abs(patch - bg).max(axis=2) <= REDACT_TOL)[seen].mean())
        doc["samples"].append({"t": round(float(f / ctx["plan"].fps), 3), "box": rect,
                               "bg_share": round(share, 3)})  # fmt: skip
        if share < REDACT_MIN_SHARE and "redaction_missing" not in doc["flags"]:
            doc["flags"].append("redaction_missing")
    return docs


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
                rect = _out_rect(seg, n, *_src_rect(box, info["size"]))
                if rect is None:
                    continue  # zoomed out of the view
                seen = _uncovered(rect, seg.inset)
                if not seen.any():
                    continue  # under the speaker bubble
                x0, y0, x1, y1 = rect
                patch = img[y0:y1, x0:x1].astype(int)
                share = float((np.abs(patch - bg).max(axis=2) <= REDACT_TOL)[seen].mean())
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
        for n0, n1, x, y, w, h in seg.redact:
            rect = _out_rect(seg, n, x, y, w, h) if n0 <= n <= n1 else None
            if rect:
                mask[rect[1] - v.y : rect[3] - v.y, rect[0] - v.x : rect[2] - v.x] = False
        reel_view = _grey(img[v.y : v.y + v.h, v.x : v.x + v.w].astype(float))
        win = _layouts.window_at(seg, n)
        t = float((seg.screen_frame + n) / plan.fps)
        diffs = {}
        for dt in (0.0, *MAP_OFFSETS):
            ref = _screen_view(ctx["screen"], t + dt, win, v, sinfo)
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
    zoom target ("focus")."""
    try:
        doc = json.loads((job / "work" / "overlays.json").read_text())
    except (OSError, ValueError):
        return []
    views = doc.get("views") if isinstance(doc, dict) else None
    out = []
    for v in views if isinstance(views, list) else []:
        if not isinstance(v, dict):
            continue
        focus = v.get("focus") if isinstance(v.get("focus"), list) else []
        for b in [v, *(f for f in focus if isinstance(f, dict))]:
            box = b.get("screen", b.get("box"))
            if isinstance(box, list) and len(box) == 4 and _num(b.get("t0"), b.get("t1"), *box):
                out.append({"t0": b["t0"], "t1": b["t1"], "screen": box})
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
        if s.layout != "face"
        for doc in _layouts.readability(s, screen, tuple(edl.size))
    ]


def check(job: Path, data: dict, render: dict, reel: Path, size: tuple[int, int]) -> dict:
    """All product-demo checks: {"coverage", "redaction", "screen_time", "clamped", "readability",
    "overlap", "spans", "decision"}; only "decision" and the screenshot part of "coverage"
    for a face-only EDL."""
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
        cover = coverage(reel, ctx, size, cands) + shots
    except (ToolError, ValueError, _ocr.OcrError) as e:  # fail closed: unchecked is not ok
        return {"error": f"screen checks failed: {e}"}
    return {
        "decision": gaps,
        "coverage": cover,
        "redaction": red_docs,
        "screen_time": time_docs,
        "clamped": clamped_info(render),
        "readability": readability(ctx),
        "overlap": overlap_check(job, data),
        "spans": screen_spans(ctx),
        "bubbles": bubble_spans(ctx),
    }
