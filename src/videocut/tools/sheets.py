"""`videocut tool sheet contact|burst|timeline|screen`: the pictures Claude uses to "see".

contact:   3x3 contact sheets over work/proxy.mp4, one tile every N seconds
           -> work/sheets/contact_NN.jpg
burst:     frames around source time t (+-span) plus the waveform of the same window with a
           marker at t -> work/sheets/burst_<t>.jpg
timeline:  filmstrip + waveform + word labels (work/words.json) + candidate shading
           (work/candidates.json) for [t0, t1] -> work/sheets/timeline_<t0>_<t1>.jpg
screen:    contact strip of the screen recording over screen time [t0, t1] with the analysis of
           work/screen.json drawn in (activity yellow, clicks red, zoom targets green)
           -> work/sheets/screen_<t0>_<t1>.jpg

Every tile carries its source time and frame number ("mm:ss.ss · f<frame>"). Sheets stay
within the image budget of docs/BRIEF.md §6 (<= 1080x1920, never more than 2000 px a side).
"""

import json
import tempfile
from pathlib import Path

from . import _image as im
from .common import ToolError, is_fresh, job_parent, resolve_job, run_tool
from .screen import probe_screen, screen_source

COLS = 3
WAVE_H = 140
STRIP_W = 1800
STRIP_TILES = 10
MAX_TIMELINE_S = 120.0
SCREEN_FPS = 30  # macOS recordings are VFR: sample on a fixed grid before selecting
SCREEN_MAX_TILES = 10
ACTIVITY_COLOR, CLICK_COLOR, TARGET_COLOR = "0xffd400", "0xff2d2d", "0x2dff6e"
PAUSE_COLOR = "cyan@0.30"
FILLER_COLOR = "red@0.35"


def _proxy(job: Path) -> tuple[Path, dict]:
    proxy = job / "work" / "proxy.mp4"
    if not proxy.is_file():
        raise ToolError("work/proxy.mp4 is missing; run `videocut tool ingest` first")
    return proxy, im.probe_video(proxy)


def _audio(job: Path) -> Path:
    wav = job / "work" / "audio.wav"
    if not wav.is_file():
        raise ToolError("work/audio.wav is missing; run `videocut tool ingest` first")
    return wav


def _sheets_dir(job: Path) -> Path:
    d = job / "work" / "sheets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tag(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") or "0"


def _label(font: str | None, fps: float, size: int) -> str:
    if font is None:
        return ""
    return "," + im.drawtext(font, im.label_expr(fps), "4", "h-th-4", size)


def _load(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        raise ToolError(f"{path.name} is not valid JSON: {e}") from None


# ---------------------------------------------------------------- contact


def contact(args) -> dict:
    if args.every <= 0:
        raise ToolError("--every must be > 0 seconds")
    job = resolve_job(args.job)
    proxy, v = _proxy(job)
    fps = v["fps"]
    step = max(1, round(args.every * fps))
    tiles = -(-v["frames"] // step)
    n_sheets = -(-tiles // (COLS * COLS))
    tw, th = im.tile_size(v["width"], v["height"])
    font = im.find_font()
    graph = (
        f"[0:v]select='not(mod(n\\,{step}))',scale={tw}:{th}{_label(font, fps, 24)},"
        f"tile={COLS}x{COLS}:padding=0:margin=0[out]"
    )
    sheets_dir = _sheets_dir(job)
    files = im.render_jpegs(
        [["-i", str(proxy)]], graph, sheets_dir, lambda k: f"contact_{k:02d}.jpg"
    )
    for old in sheets_dir.glob("contact_*.jpg"):
        if old not in files:
            old.unlink()
    per = COLS * COLS
    listing = []
    for k, f in enumerate(files):
        first, last = k * per, min((k + 1) * per, tiles) - 1
        listing.append(
            {
                "file": str(f.relative_to(job)),
                "t0": round(first * step / fps, 2),
                "t1": round(last * step / fps, 2),
                "tiles": last - first + 1,
            }
        )
    print(f"{len(files)} contact sheet(s), one tile per {step / fps:.2f}s, {tw}x{th} tiles")
    for s in listing:
        print(
            f"  {s['file']}: {im.fmt_time(s['t0'])} .. {im.fmt_time(s['t1'])} ({s['tiles']} tiles)"
        )
    return {
        "sheets": listing,
        "count": len(files),
        "every": round(step / fps, 3),
        "expected": n_sheets,
        "tile": [tw, th],
        "labels": font is not None,
    }


# ---------------------------------------------------------------- burst


def _burst_frames(t: float, span: float, v: dict) -> list[int]:
    fps, last = v["fps"], v["frames"] - 1
    lo = max(0, -(-round((t - span) * fps * 1e6) // 10**6))
    hi = min(last, round((t + span) * fps * 1e6) // 10**6)
    center = min(max(round(t * fps), 0), last)
    if hi < lo:
        lo = hi = center
    idx = list(range(lo, hi + 1))
    if len(idx) > COLS * COLS:
        n = COLS * COLS
        idx = [lo + round(i * (hi - lo) / (n - 1)) for i in range(n)]
        idx[min(range(n), key=lambda i: abs(idx[i] - center))] = center
        idx = sorted(set(idx))
    return idx


def _fit_tile(v: dict, rows: int, wave_h: int) -> tuple[int, int]:
    """Tile size so that rows of tiles plus the waveform strip fit the sheet budget."""
    tw, th = im.tile_size(v["width"], v["height"])
    limit = 1920 if v["height"] >= v["width"] else 1080
    s = min(1.0, (limit - wave_h) / (rows * th))
    return max(2, int(tw * s) // 2 * 2), max(2, int(th * s) // 2 * 2)


def burst(args) -> dict:
    if args.span <= 0:
        raise ToolError("--span must be > 0 seconds")
    if args.t < 0:
        raise ToolError("t must be >= 0")
    job = resolve_job(args.job)
    proxy, v = _proxy(job)
    wav = _audio(job)
    if args.t > v["duration"]:
        raise ToolError(f"t={args.t} is after the end of the video ({v['duration']:.2f}s)")
    fps = v["fps"]
    idx = _burst_frames(args.t, args.span, v)
    times = [i / fps for i in idx]
    cols = min(COLS, len(idx))
    rows = -(-len(idx) // cols)
    tw, th = _fit_tile(v, rows, WAVE_H)
    width = cols * tw
    w0 = max(0.0, args.t - args.span)
    w1 = min(v["duration"], args.t + args.span)
    font = im.find_font()
    seek = max(0.0, times[0] - 0.5)
    gain = im.wave_gain(im.window_peak(wav, w0, w1))
    graph = (
        f"[0:v]select='{im.select_times(times, fps)}',scale={tw}:{th}"
        f"{_label(font, fps, max(14, tw // 15))},"
        f"tile={cols}x{rows}:padding=0:margin=0,format=rgb24,setpts=PTS-STARTPTS[g];"
        f"[1:a]{im.wave_filter(width, WAVE_H, (args.t - w0) / (w1 - w0), gain)}"
    )
    if font is not None:
        graph += "," + im.drawtext(
            font, "t " + im.fmt_time(args.t).replace(":", "\\:"), "6", "6", 16
        )
    graph += ",setpts=PTS-STARTPTS[w];[g][w]vstack=inputs=2[out]"
    name = f"burst_{_tag(args.t)}.jpg"
    (f,) = im.render_jpegs(
        [
            ["-ss", f"{seek:.4f}", "-copyts", "-i", str(proxy)],
            ["-ss", f"{w0:.4f}", "-t", f"{w1 - w0:.4f}", "-i", str(wav)],
        ],
        graph,
        _sheets_dir(job),
        [name],
    )
    print(
        f"{f.relative_to(job)}: {len(idx)} frames {im.fmt_time(times[0])}..{im.fmt_time(times[-1])}"
    )
    return {
        "file": str(f.relative_to(job)),
        "t": args.t,
        "frames": idx,
        "times": [round(x, 3) for x in times],
        "wave_window": [round(w0, 3), round(w1, 3)],
        "labels": font is not None,
    }


# ---------------------------------------------------------------- timeline


def _tick_step(span: float) -> float:
    for step in (0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60):
        if span / step <= 12:
            return step
    return 120


def timeline(args) -> dict:
    if not args.t1 > args.t0 >= 0:
        raise ToolError("need 0 <= t0 < t1")
    job = resolve_job(args.job)
    proxy, v = _proxy(job)
    wav = _audio(job)
    fps = v["fps"]
    t0 = args.t0
    t1 = min(args.t1, v["duration"])
    if t1 - t0 < 1 / fps:
        raise ToolError(f"window {args.t0}..{args.t1} is outside the video ({v['duration']:.2f}s)")
    if t1 - t0 > MAX_TIMELINE_S:
        raise ToolError(
            f"window is {t1 - t0:.0f}s; timeline sheets take at most {MAX_TIMELINE_S:.0f}s"
        )
    span = t1 - t0

    def x(t: float) -> int:
        return int(round((min(max(t, t0), t1) - t0) * px))

    n_tiles = max(1, min(STRIP_TILES, int(span * fps)))
    cell = span / n_tiles
    times = sorted({im.snap(t0 + (i + 0.5) * cell, fps) for i in range(n_tiles)})
    n_tiles = len(times)
    tw = STRIP_W // n_tiles // 2 * 2
    th = max(2, round(tw * v["height"] / v["width"] / 2) * 2)
    strip_w = tw * n_tiles
    px = strip_w / span
    font = im.find_font()

    words_doc = _load(job / "work" / "words.json")
    cand_doc = _load(job / "work" / "candidates.json")
    words = [w for w in (words_doc or {}).get("words", []) if w["t1"] > t0 and w["t0"] < t1]
    pauses = [p for p in (cand_doc or {}).get("pauses", []) if p["t1"] > t0 and p["t0"] < t1]
    fillers = [p for p in (cand_doc or {}).get("fillers", []) if p["t1"] > t0 and p["t0"] < t1]

    words_h = 64 if words_doc is not None else 0
    axis_h = 22
    lower_h = WAVE_H + words_h + axis_h

    with tempfile.TemporaryDirectory() as tmp:
        gain = im.wave_gain(im.window_peak(wav, t0, t1))
        lower = [f"[1:a]{im.wave_filter(strip_w, WAVE_H, None, gain)},setpts=PTS-STARTPTS[wave]"]
        stack = ["[wave]"]
        if words_doc is not None:
            lower.append(f"color=c=0x181818:s={strip_w}x{words_h}:r=1,format=rgb24[wbg]")
            chain, prev = [], "wbg"
            for k, w in enumerate(words):
                if font is None:
                    break
                tf = Path(tmp) / f"w{k}.txt"
                tf.write_text(w["w"])
                row = 4 + (k % 2) * 30
                x0, x1 = x(w["t0"]), max(x(w["t1"]), x(w["t0"]) + 2)
                chain.append(f"drawbox=x={x0}:y={row + 22}:w={x1 - x0}:h=2:color=white@0.8:t=fill")
                chain.append(im.textfile_drawtext(font, tf, str(x0), str(row), 20))
            if chain:
                lower.append(f"[{prev}]" + ",".join(chain) + ",setpts=PTS-STARTPTS[wtrack]")
                stack.append("[wtrack]")
            else:
                stack.append("[wbg]")
        lower.append(f"color=c=0x101010:s={strip_w}x{axis_h}:r=1,format=rgb24[abg]")
        ticks, step = [], _tick_step(span)
        k = int(t0 / step) + (0 if abs(t0 / step - round(t0 / step)) < 1e-9 else 1)
        while k * step <= t1 + 1e-9 and font is not None:
            tx = x(k * step)
            ticks.append(im.drawtext(font, _tag(k * step), str(min(tx, strip_w - 40)), "2", 14))
            k += 1
        lower.append(
            "[abg]" + (",".join(ticks) if ticks else "null") + ",setpts=PTS-STARTPTS[axis]"
        )
        stack.append("[axis]")
        shade = [
            f"drawbox=x={x(p['t0'])}:y=0:w={max(1, x(p['t1']) - x(p['t0']))}:h={lower_h}"
            f":color={PAUSE_COLOR}:t=fill"
            for p in pauses
        ] + [
            f"drawbox=x={x(p['t0'])}:y=0:w={max(1, x(p['t1']) - x(p['t0']))}:h={lower_h}"
            f":color={FILLER_COLOR}:t=fill"
            for p in fillers
        ]
        lower.append(
            "".join(stack) + f"vstack=inputs={len(stack)}," + ",".join(shade + ["null"]) + "[low]"
        )
        film = (
            f"[0:v]select='{im.select_times(times, fps)}',scale={tw}:{th}"
            f"{_label(font, fps, 13)},tile={n_tiles}x1:padding=0:margin=0,format=rgb24,"
            "setpts=PTS-STARTPTS[film]"
        )
        graph = film + ";" + ";".join(lower) + ";[film][low]vstack=inputs=2[out]"
        name = f"timeline_{_tag(t0)}_{_tag(t1)}.jpg"
        (f,) = im.render_jpegs(
            [
                ["-ss", f"{max(0.0, times[0] - 0.5):.4f}", "-copyts", "-i", str(proxy)],
                ["-ss", f"{t0:.4f}", "-t", f"{span:.4f}", "-i", str(wav)],
            ],
            graph,
            _sheets_dir(job),
            [name],
        )
    print(
        f"{f.relative_to(job)}: {im.fmt_time(t0)}..{im.fmt_time(t1)}, {n_tiles} frames, "
        f"{len(words)} words, {len(pauses)} pauses, {len(fillers)} filler candidates"
    )
    return {
        "file": str(f.relative_to(job)),
        "t0": round(t0, 3),
        "t1": round(t1, 3),
        "frames": n_tiles,
        "words": len(words) if words_doc is not None else None,
        "pauses": len(pauses) if cand_doc is not None else None,
        "fillers": len(fillers) if cand_doc is not None else None,
        "shading": {"pause": PAUSE_COLOR.split("@")[0], "filler": FILLER_COLOR.split("@")[0]},
        "labels": font is not None,
    }


# ---------------------------------------------------------------- screen


def _screen_input(job: Path) -> Path:
    src = screen_source(job)
    proxy = job / "work" / "screen_proxy.mp4"
    return proxy if proxy.is_file() and is_fresh([proxy], [src]) else src


def _box_filters(doc: dict | None, t: float, tw: int, th: int) -> list[str]:
    """drawbox filters for what screen.json says about the frame at screen time t."""
    if not doc:
        return []
    half = 0.5 / SCREEN_FPS
    on = f"between(t\\,{t - half:.6f}\\,{t + half - 1e-6:.6f})"

    def rect(box, color, thick):
        x0, y0 = int(box[0] * tw), int(box[1] * th)
        w, h = max(2, int(box[2] * tw) - x0), max(2, int(box[3] * th) - y0)
        return f"drawbox=x={x0}:y={y0}:w={w}:h={h}:color={color}:t={thick}:enable='{on}'"

    out = []
    for z in doc.get("zoom_targets", []):
        if z["t0"] - half <= t <= z["t1"] + half:
            out.append(rect(z["box"], TARGET_COLOR, 4))
    for a in doc.get("activity", []):
        if abs(a["t"] - t) <= 0.35:
            out.append(rect(a["box"], ACTIVITY_COLOR, 2))
    r = 0.025
    for c in doc.get("clicks", []):
        if -0.2 <= t - c["t"] <= 0.6:
            box = [c["x"] - r, c["y"] - r * tw / th, c["x"] + r, c["y"] + r * tw / th]
            out.append(rect(box, CLICK_COLOR, 3))
    return out


def screen_sheet(args) -> dict:
    if not args.t1 > args.t0 >= 0:
        raise ToolError("need 0 <= t0 < t1")
    job = resolve_job(args.job)
    src = _screen_input(job)
    info = probe_screen(src)
    w, h = info["size"]
    dur = info["duration"] or args.t1
    t0, t1 = args.t0, min(args.t1, dur)
    if t1 <= t0:
        raise ToolError(f"window {args.t0}..{args.t1} is outside the recording ({dur:.2f}s)")
    doc = _load(job / "work" / "screen.json")
    span = t1 - t0
    n = args.tiles or min(SCREEN_MAX_TILES, max(2, round(span * 2)))
    n = max(1, min(n, SCREEN_MAX_TILES, int(span * SCREEN_FPS) or 1))
    cell = span / n
    times = sorted({im.snap(t0 + (i + 0.5) * cell, SCREEN_FPS) for i in range(n)})
    n = len(times)
    cols = min(n, 2 if w >= h else 3)
    rows = -(-n // cols)
    tw = 1080 // cols // 2 * 2
    th = max(2, round(tw * h / w / 2) * 2)
    if rows * th > 1920:
        th = 1920 // rows // 2 * 2
        tw = max(2, round(th * w / h / 2) * 2)
    font = im.find_font()
    # Per tile: its own boxes (enabled for exactly that frame), applied after the selection.
    chains = []
    for t in times:
        chains.append(_box_filters(doc, t, tw, th))
    boxes = [f for c in chains for f in c]
    graph = (
        f"[0:v]fps={SCREEN_FPS},select='{im.select_times(times, SCREEN_FPS)}',scale={tw}:{th}"
        + ("," + ",".join(boxes) if boxes else "")
        + _label(font, SCREEN_FPS, max(14, tw // 22))
        + f",tile={cols}x{rows}:padding=0:margin=0[out]"
    )
    name = f"screen_{_tag(t0)}_{_tag(t1)}.jpg"
    (f,) = im.render_jpegs(
        [["-ss", f"{max(0.0, times[0] - 0.5):.4f}", "-copyts", "-i", str(src)]],
        graph,
        _sheets_dir(job),
        [name],
    )
    marks = {
        "activity": sum(1 for c in chains for b in c if ACTIVITY_COLOR in b),
        "clicks": sum(1 for c in chains for b in c if CLICK_COLOR in b),
        "zoom_targets": sum(1 for c in chains for b in c if TARGET_COLOR in b),
    }
    print(
        f"{f.relative_to(job)}: screen {im.fmt_time(times[0])}..{im.fmt_time(times[-1])}, "
        f"{n} tiles {tw}x{th}"
        + ("" if doc else " (no work/screen.json: run `videocut tool screen` for the overlays)")
    )
    return {
        "file": str(f.relative_to(job)),
        "t0": round(t0, 3),
        "t1": round(t1, 3),
        "times": [round(x, 3) for x in times],
        "tiles": n,
        "tile": [tw, th],
        "overlays": doc is not None,
        "marks": marks,
        "colors": {"activity": "yellow", "click": "red", "zoom_target": "green"},
        "labels": font is not None,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser("sheet", help="contact, burst, timeline and screen sheets")
    sub = p.add_subparsers(dest="sheet_cmd", required=True)
    c = sub.add_parser("contact", help="3x3 contact sheets", parents=[job_parent()])
    c.add_argument("--every", type=float, default=2.0, metavar="S", help="seconds between tiles")
    c.set_defaults(handler=run_tool("sheet contact", contact))
    b = sub.add_parser("burst", help="frames around one moment", parents=[job_parent()])
    b.add_argument("t", type=float, help="source time in seconds")
    b.add_argument("--span", type=float, default=0.5, help="seconds before and after t")
    b.set_defaults(handler=run_tool("sheet burst", burst))
    t = sub.add_parser("timeline", help="filmstrip + waveform + words", parents=[job_parent()])
    t.add_argument("t0", type=float)
    t.add_argument("t1", type=float)
    t.set_defaults(handler=run_tool("sheet timeline", timeline))
    sc = sub.add_parser(
        "screen", help="screen recording strip with activity, clicks and zoom targets",
        parents=[job_parent()],
    )  # fmt: skip
    sc.add_argument("t0", type=float, help="screen time in seconds")
    sc.add_argument("t1", type=float)
    sc.add_argument(
        "--tiles", type=int, default=0, metavar="N", help="tiles (default: auto, <= 10)"
    )
    sc.set_defaults(handler=run_tool("sheet screen", screen_sheet))
