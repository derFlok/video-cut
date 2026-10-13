"""`videocut tool screen`: where things happen on the screen recording, and how far to zoom.

Reads the job's screen recording (job.json "screen"; work/screen_proxy.mp4 when it is
newer, it decodes faster), samples it as a grey stream (long side 960 px, 10 fps) through
an ffmpeg pipe and writes work/screen.json:

    size            [w, h] of the original recording (display orientation)
    fps_sampled     analysis rate
    activity        [{t, box, score}] changed regions per sample: the change against the
                    previous sample plus the persistent change against one second earlier
                    (so a typed word or a toggled button stays visible for a second);
                    pointer motion and click rings are not activity
    cursor          [{t, x, y}] where the pointer moved (reported, never a target)
    clicks          [{t, x, y, source}] "ring": an expanding/fading circle (the click
                    highlight of a screen recorder); "stop": the pointer stopped and a
                    change followed nearby
    scenes          [t] hard changes (most of the screen changed at once)
    text_density    [{t, box, score}] edge density per tile, once per scene
    zoom_targets    [{t0, t1, box, max_zoom, max_zoom_split, view, zoom, score, clicks}]
                    activity and clicks merged into regions that stay put for >= 1.2 s.
                    max_zoom keeps >= 1 source px per output px when the screen view
                    fills the reel width; view is the screen-shaped box to show at
                    zoom = min(1/box size, max_zoom)

All boxes are [x0, y0, x1, y1] normalised to the recording, times are screen seconds.
Idempotent: a screen.json newer than the recording with the same parameters is kept.
"""

import json
import math
import subprocess
from collections import deque
from pathlib import Path

import numpy as np

from ..paths import tool_env
from .common import (
    ToolError,
    binary,
    is_fresh,
    job_parent,
    read_job,
    resolve_job,
    run,
    run_tool,
    write_json,
)
from .ingest import _rotation

VERSION = 1
SAMPLE_FPS = 10.0
LONG_SIDE = 960
CELL = 8  # analysis px per grid cell
DIFF_LEVEL = 20  # grey levels a pixel must change by
CELL_MIN = 3  # changed pixels that make a cell active
MIN_BLOB_PX = 12
SCENE_FRAC = 0.3  # changed share of the frame that counts as a hard change
LONG_S = 1.0  # persistent change is measured against this far back
GROUP_PX = 2 * CELL  # changes closer than this form one region
# Pointer: a blob no larger than this share of the long side (two pointer images, old and
# new position, merged when it moved less than its own size).
CURSOR_FRAC = 0.035
STOP_S = 0.3  # no pointer motion for this long = the pointer stopped
STOP_REACH_S = 1.5  # a change this soon after a stop ...
STOP_NEAR = 0.04  # ... this close to the pointer counts as a click
# Click ring: square-ish hollow blob between these shares of the long side.
RING_MIN_FRAC, RING_MAX_FRAC = 0.012, 0.1
RING_LINK_S = 0.25
# Reel geometry the zoom cap is computed for (render owns the layouts; these mirror them):
# "screen" fills the 1080 px width, "split" fills the top 58 % of 1920 (cover).
OUT_W, OUT_H = 1080, 1920
SPLIT_TOP = 0.58
# Zoom targets.
TARGET_MIN_S = 1.2
TARGET_GAP_S = 1.0
TARGET_LEAD_S, TARGET_TAIL_S = 0.3, 0.2
TARGET_MAX = 0.75  # larger regions (normalised width or height) are not worth a zoom
TARGET_MIN_ZOOM = 1.15
CLICK_BOX = 0.08  # region attributed to a click, share of the width
TILES = (8, 5)  # text density grid
EDGE_LEVEL = 40


def screen_source(job: Path) -> Path:
    rel = read_job(job).get("screen")
    if not rel:
        raise ToolError('job.json has no screen recording ("screen")')
    p = (job / rel).resolve()
    if not p.is_relative_to(job) or not p.is_file():
        raise ToolError(f"screen {rel!r} is missing or outside the job folder")
    return p


def probe_screen(src: Path) -> dict:
    """Display size and duration of the first video stream (audio is optional here)."""
    out = run(
        [binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_streams"]
        + ["-show_format", "-of", "json", str(src)]
    )
    info = json.loads(out.stdout)
    try:
        v = info["streams"][0]
        w, h = int(v["width"]), int(v["height"])
    except (KeyError, IndexError, ValueError) as e:
        raise ToolError(f"cannot read video properties of {src.name}: {e!r}") from None
    if _rotation(v) in (90, 270):
        w, h = h, w
    dur = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    return {"size": [w, h], "duration": dur}


def max_zoom(w: int, h: int, layout: str = "screen") -> float:
    """Largest zoom (1 / shown share of the screen) that keeps >= 1 source px per output
    px over the area the screen fills in the reel; rounded down to 0.01."""
    if layout == "split":
        area_w, area_h = OUT_W, round(OUT_H * SPLIT_TOP)
    else:
        area_w, area_h = OUT_W, OUT_W * h / w
    return math.floor(min(w / area_w, h / area_h) * 100 + 1e-6) / 100


def analysis_size(w: int, h: int, long_side: int = LONG_SIDE) -> tuple[int, int]:
    s = min(1.0, long_side / max(w, h))
    return max(2, round(w * s / 2) * 2), max(2, round(h * s / 2) * 2)


def decode(src: Path, aw: int, ah: int, fps: float):
    """Yield grey uint8 frames (ah x aw) sampled at `fps`."""
    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(src)]
    cmd += ["-an", "-sn", "-vf", f"fps={fps},scale={aw}:{ah}:flags=area,format=gray"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "gray", "-"]
    n = aw * ah
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=tool_env())
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, np.uint8).reshape(ah, aw)
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise ToolError(f"ffmpeg failed: {err.strip()[-500:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------- blobs


def _label(grid: np.ndarray) -> np.ndarray:
    """8-connected components of a small bool grid; 0 = background, else a component id."""
    h, w = grid.shape
    big = h * w
    lab = np.where(grid, np.arange(big).reshape(h, w), big)
    while True:
        p = np.pad(lab, 1, constant_values=big)
        m = lab.copy()
        for dy in range(3):
            for dx in range(3):
                np.minimum(m, p[dy : dy + h, dx : dx + w], out=m)
        m = np.where(grid, m, big)
        m = np.where(grid, np.append(m.ravel(), big)[m], big)  # pointer jump
        if np.array_equal(m, lab):
            return np.where(grid, lab + 1, 0)
        lab = m


def _is_ring(tight: np.ndarray, long_side: int) -> bool:
    h, w = tight.shape
    side = max(h, w)
    if not RING_MIN_FRAC * long_side <= side <= RING_MAX_FRAC * long_side:
        return False
    if not 0.7 <= w / h <= 1.4:
        return False
    fill = tight.mean()
    inner = tight[int(h * 0.3) : int(math.ceil(h * 0.7)), int(w * 0.3) : int(math.ceil(w * 0.7))]
    return 0.06 <= fill <= 0.6 and inner.size > 0 and inner.mean() < 0.15


def blobs(mask: np.ndarray) -> list[dict]:
    """Connected changed regions of a pixel mask: box (px, exclusive end), changed pixel
    count, and whether it looks like a click ring or the pointer."""
    h, w = mask.shape
    long_side = max(h, w)
    gh, gw = -(-h // CELL), -(-w // CELL)
    pad = np.zeros((gh * CELL, gw * CELL), bool)
    pad[:h, :w] = mask
    cells = pad.reshape(gh, CELL, gw, CELL).sum((1, 3)) >= CELL_MIN
    if not cells.any():
        return []
    lab = _label(cells)
    ys, xs = np.nonzero(lab)
    ks = lab[ys, xs]
    order = np.argsort(ks, kind="stable")
    ys, xs, ks = ys[order], xs[order], ks[order]
    cuts = np.flatnonzero(np.diff(ks)) + 1
    out = []
    for cy, cx in zip(np.split(ys, cuts), np.split(xs, cuts), strict=True):
        k = lab[cy[0], cx[0]]
        cy0, cy1, cx0, cx1 = cy.min(), cy.max() + 1, cx.min(), cx.max() + 1
        own = np.repeat(np.repeat(lab[cy0:cy1, cx0:cx1] == k, CELL, 0), CELL, 1)
        sub = pad[cy0 * CELL : cy1 * CELL, cx0 * CELL : cx1 * CELL] & own
        py, px = np.nonzero(sub)
        if len(py) < MIN_BLOB_PX:
            continue
        y0, y1, x0, x1 = py.min(), py.max() + 1, px.min(), px.max() + 1
        ring = _is_ring(sub[y0:y1, x0:x1], long_side)
        ox, oy = cx0 * CELL, cy0 * CELL
        out.append(
            {
                "box": (int(ox + x0), int(oy + y0), int(ox + x1), int(oy + y1)),
                "n": len(py),
                "ring": ring,
                "cursor": not ring and max(x1 - x0, y1 - y0) <= CURSOR_FRAC * long_side,
            }
        )
    return out


def merge_boxes(items: list[list], gap: float) -> list[list]:
    """Union [x0, y0, x1, y1, weight, flag] items whose boxes come closer than `gap`."""
    boxes = [list(b) for b in items]
    i = 0
    while i < len(boxes):
        a = boxes[i]
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            if a[0] - gap < b[2] and b[0] - gap < a[2] and a[1] - gap < b[3] and b[1] - gap < a[3]:
                boxes[i] = [
                    min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]),
                    a[4] + b[4], a[5] or b[5],
                ]  # fmt: skip
                del boxes[j]
                break
        else:
            i += 1
    return boxes


def _diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(a.astype(np.int16) - b.astype(np.int16)) > DIFF_LEVEL


def _center(box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def text_density(frame: np.ndarray, t: float) -> list[dict]:
    """Share of edge pixels per tile; text-heavy tiles score high."""
    f = frame.astype(np.int16)
    edges = np.zeros(f.shape, bool)
    edges[:, 1:] |= np.abs(np.diff(f, axis=1)) > EDGE_LEVEL
    edges[1:, :] |= np.abs(np.diff(f, axis=0)) > EDGE_LEVEL
    h, w = f.shape
    cols, rows = TILES
    out = []
    for r in range(rows):
        for c in range(cols):
            y0, y1 = h * r // rows, h * (r + 1) // rows
            x0, x1 = w * c // cols, w * (c + 1) // cols
            score = float(edges[y0:y1, x0:x1].mean())
            if score >= 0.04:
                box = [round(c / cols, 4), round(r / rows, 4)]
                box += [round((c + 1) / cols, 4), round((r + 1) / rows, 4)]
                out.append({"t": round(t, 2), "box": box, "score": round(score, 3)})
    return out


# ---------------------------------------------------------------- analysis


def analyse(frames, aw: int, ah: int, fps: float) -> dict:
    """Activity, pointer, click candidates, scenes and text density of a frame stream."""
    norm = np.array([aw, ah, aw, ah], float)
    n_long = max(1, round(LONG_S * fps))
    hist: deque = deque(maxlen=n_long + 1)
    activity, cursor, rings, stops, scenes, density = [], [], [], [], [], []
    regions = []  # (t, box_px, short) for the stop fallback
    prev = None
    last_pos, last_seen, moving = None, None, False
    density_due = 0.0
    t = 0.0
    for k, f in enumerate(frames):
        t = k / fps
        if density_due is not None and t >= density_due:
            density += text_density(f, t)
            density_due = None
        if prev is None:
            prev = f
            hist.append(f)
            continue
        d = _diff(f, prev)
        prev = f
        if d.mean() > SCENE_FRAC:
            scenes.append(round(t, 2))
            hist.clear()
            hist.append(f)
            last_pos, last_seen, moving = None, None, False
            density_due = t + 0.5
            continue
        hist.append(f)
        short = blobs(d)
        items = []
        pointer = []
        for b in short:
            if b["ring"]:
                cx, cy = _center(b["box"])
                rings.append((t, cx / aw, cy / ah, (b["box"][2] - b["box"][0]) / aw))
            elif b["cursor"]:
                pointer.append(_center(b["box"]))
            else:
                items.append([*b["box"], b["n"], True])
        if len(hist) > 1:
            for b in blobs(_diff(f, hist[0])):
                if not b["ring"] and not b["cursor"]:
                    items.append([*b["box"], b["n"], False])
        for x0, y0, x1, y1, n, has_short in merge_boxes(items, GROUP_PX):
            box = [round(float(v), 4) for v in np.array([x0, y0, x1, y1]) / norm]
            score = min(1.0, n / max(1, (x1 - x0) * (y1 - y0)))
            activity.append({"t": round(t, 2), "box": box, "score": round(score, 3)})
            regions.append((t, box, has_short))
        if pointer:
            if last_pos is not None and len(pointer) > 1:
                pos = max(pointer, key=lambda p: math.dist(p, last_pos))
            else:
                pos = pointer[-1]
            last_pos, last_seen, moving = pos, t, True
            cursor.append(
                {"t": round(t, 2), "x": round(pos[0] / aw, 4), "y": round(pos[1] / ah, 4)}
            )
        elif moving and t - last_seen >= STOP_S - 1e-9:
            stops.append((last_seen, last_pos[0] / aw, last_pos[1] / ah))
            moving = False
    return {
        "activity": activity,
        "cursor": cursor,
        "clicks": _clicks(rings, stops, regions),
        "scenes": scenes,
        "text_density": density,
        "end": t,
    }


def _clicks(rings, stops, regions) -> list[dict]:
    clicks = []
    groups: list[list] = []
    for r in sorted(rings):
        g = next(
            (
                g
                for g in groups
                if r[0] - g[-1][0] <= RING_LINK_S
                and math.dist(r[1:3], g[-1][1:3]) <= max(0.02, 0.6 * max(r[3], g[-1][3]))
            ),
            None,
        )
        if g is None:
            groups.append([r])
        else:
            g.append(r)
    for g in groups:
        if len(g) >= 2:
            t, x, y, _ = g[0]
            clicks.append({"t": round(t, 2), "x": round(x, 4), "y": round(y, 4), "source": "ring"})
    for ts, x, y in stops:
        hit = next(
            (
                (t, box)
                for t, box, has_short in regions
                if has_short
                and ts < t <= ts + STOP_REACH_S
                and box[0] - STOP_NEAR <= x <= box[2] + STOP_NEAR
                and box[1] - STOP_NEAR <= y <= box[3] + STOP_NEAR
            ),
            None,
        )
        if hit is None:
            continue
        t = max(ts, hit[0] - 1 / SAMPLE_FPS)
        if any(abs(c["t"] - t) <= 1.0 and math.dist((c["x"], c["y"]), (x, y)) <= 0.05
               for c in clicks):  # fmt: skip
            continue
        clicks.append({"t": round(t, 2), "x": round(x, 4), "y": round(y, 4), "source": "stop"})
    return sorted(clicks, key=lambda c: c["t"])


# ---------------------------------------------------------------- zoom targets


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _near(a, b, pad: float = 0.02) -> bool:
    return a[0] - pad < b[2] and b[0] - pad < a[2] and a[1] - pad < b[3] and b[1] - pad < a[3]


def _small(box) -> bool:
    return box[2] - box[0] <= TARGET_MAX and box[3] - box[1] <= TARGET_MAX


def zoom_targets(doc: dict, duration: float) -> list[dict]:
    """Merge activity and clicks into regions that stay put, each with its zoom cap."""
    w, h = doc["size"]
    cap, cap_split = max_zoom(w, h), max_zoom(w, h, "split")
    scenes = doc["scenes"]
    events = [(a["t"], a["box"], a["score"] / SAMPLE_FPS, 0) for a in doc["activity"]]
    hw, hh = CLICK_BOX / 2, CLICK_BOX / 2 * w / h
    for c in doc["clicks"]:
        box = [max(0.0, c["x"] - hw), max(0.0, c["y"] - hh)]
        box += [min(1.0, c["x"] + hw), min(1.0, c["y"] + hh)]
        events.append((c["t"], box, 0.5, 1))
    events = sorted((e for e in events if _small(e[1])), key=lambda e: e[0])

    def scene_between(t0, t1):
        return any(t0 < s <= t1 for s in scenes)

    tracks: list[dict] = []
    for t, box, score, click in events:
        tr = next(
            (
                tr
                for tr in reversed(tracks)
                if t - tr["t1"] <= TARGET_GAP_S
                and not scene_between(tr["t1"], t)
                and _near(tr["box"], box)
                and _small(_union(tr["box"], box))
            ),
            None,
        )
        if tr is None:
            tracks.append({"t0": t, "t1": t, "box": list(box), "score": score, "clicks": click})
        else:
            tr["t1"] = max(tr["t1"], t)
            tr["box"] = _union(tr["box"], box)
            tr["score"] += score
            tr["clicks"] += click
    merged = True
    while merged:  # tracks that grew into each other
        merged = False
        for i, a in enumerate(tracks):
            for b in tracks[i + 1 :]:
                if (
                    b["t0"] - a["t1"] <= TARGET_GAP_S
                    and a["t0"] - b["t1"] <= TARGET_GAP_S
                    and not scene_between(min(a["t1"], b["t1"]), max(a["t0"], b["t0"]))
                    and _near(a["box"], b["box"])
                    and _small(_union(a["box"], b["box"]))
                ):
                    a.update(
                        t0=min(a["t0"], b["t0"]),
                        t1=max(a["t1"], b["t1"]),
                        box=_union(a["box"], b["box"]),
                        score=a["score"] + b["score"],
                        clicks=a["clicks"] + b["clicks"],
                    )
                    tracks.remove(b)
                    merged = True
                    break
            if merged:
                break
    out = []
    for tr in tracks:
        lo = max([0.0] + [s for s in scenes if s <= tr["t0"]])
        hi = min([duration] + [s for s in scenes if s > tr["t1"]])
        t0 = max(lo, tr["t0"] - TARGET_LEAD_S)
        t1 = min(hi, tr["t1"] + TARGET_TAIL_S)
        if t1 - t0 < TARGET_MIN_S:
            t1 = min(hi, t0 + TARGET_MIN_S)
            t0 = max(lo, t1 - TARGET_MIN_S)
        if t1 - t0 < TARGET_MIN_S - 1e-6:
            continue
        x0, y0, x1, y1 = tr["box"]
        pad = 0.1 * max(x1 - x0, y1 - y0) + 0.01
        box = [max(0.0, x0 - pad), max(0.0, y0 - pad), min(1.0, x1 + pad), min(1.0, y1 + pad)]
        s = min(1.0, max(box[2] - box[0], box[3] - box[1], 1 / cap))
        if s > 1 / TARGET_MIN_ZOOM:
            continue
        cx = min(max((box[0] + box[2]) / 2, s / 2), 1 - s / 2)
        cy = min(max((box[1] + box[3]) / 2, s / 2), 1 - s / 2)
        view = [cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2]
        out.append(
            {
                "t0": round(t0, 2),
                "t1": round(t1, 2),
                "box": [round(v, 4) for v in box],
                "max_zoom": cap,
                "max_zoom_split": cap_split,
                "view": [round(v, 4) for v in view],
                "zoom": round(1 / s, 2),
                "score": round(tr["score"], 3),
                "clicks": tr["clicks"],
            }
        )
    return sorted(out, key=lambda z: z["t0"])


# ---------------------------------------------------------------- tool


def screen(args) -> dict:
    job = resolve_job(args.job)
    src = screen_source(job)
    work = job / "work"
    work.mkdir(exist_ok=True)
    out = work / "screen.json"
    params = {"version": VERSION, "fps": SAMPLE_FPS, "long_side": LONG_SIDE}
    if not args.force and is_fresh([out], [src]):
        try:
            doc = json.loads(out.read_text())
        except ValueError:
            doc = None
        if doc and doc.get("params") == params:
            print(f"{out.relative_to(job)} is up to date")
            return _summary(job, out, doc, reused=True)
    info = probe_screen(src)
    w, h = info["size"]
    aw, ah = analysis_size(w, h)
    proxy = work / "screen_proxy.mp4"
    decode_from = proxy if is_fresh([proxy], [src]) else src
    res = analyse(decode(decode_from, aw, ah, SAMPLE_FPS), aw, ah, SAMPLE_FPS)
    duration = max(info["duration"], res.pop("end"))
    doc = {
        "size": [w, h],
        "fps_sampled": SAMPLE_FPS,
        "analysis_size": [aw, ah],
        "duration": round(duration, 3),
        "source": str(src.relative_to(job)),
        "params": params,
        "max_zoom": {"screen": max_zoom(w, h), "split": max_zoom(w, h, "split")},
        **res,
    }
    doc["zoom_targets"] = zoom_targets(doc, duration)
    write_json(out, doc)
    print(
        f"{out.relative_to(job)}: {len(doc['activity'])} activity samples, "
        f"{len(doc['clicks'])} clicks, {len(doc['scenes'])} scene changes, "
        f"{len(doc['zoom_targets'])} zoom targets (max zoom {doc['max_zoom']['screen']}x)"
    )
    for z in doc["zoom_targets"]:
        print(f"  {z['t0']:.2f}-{z['t1']:.2f}s box {z['box']} zoom {z['zoom']}x")
    return _summary(job, out, doc, reused=False)


def _summary(job: Path, out: Path, doc: dict, reused: bool) -> dict:
    return {
        "file": str(out.relative_to(job)),
        "reused": reused,
        "size": doc["size"],
        "max_zoom": doc.get("max_zoom"),
        "activity": len(doc["activity"]),
        "clicks": len(doc["clicks"]),
        "scenes": doc["scenes"],
        "zoom_targets": [
            {k: z[k] for k in ("t0", "t1", "box", "zoom")} for z in doc["zoom_targets"][:20]
        ],
    }


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "screen", help="activity, clicks and zoom targets of the screen recording",
        parents=[job_parent()],
    )  # fmt: skip
    p.add_argument("--force", action="store_true", help="analyse again even if up to date")
    p.set_defaults(handler=run_tool("screen", screen))
