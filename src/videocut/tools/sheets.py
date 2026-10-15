"""`videocut tool sheet contact|burst|timeline|screen|plan|assets|slides`: the pictures
Claude uses to "see".

contact:   3x3 contact sheets over work/proxy.mp4, one tile every N seconds
           -> work/sheets/contact_NN.jpg
burst:     frames around source time t (+-span) plus the waveform of the same window with a
           marker at t -> work/sheets/burst_<t>.jpg
timeline:  filmstrip + waveform + word labels (work/words.json) + candidate shading
           (work/candidates.json) for [t0, t1] -> work/sheets/timeline_<t0>_<t1>.jpg
screen:    contact strip of the screen recording over screen time [t0, t1] with the analysis of
           work/screen.json drawn in (activity yellow, clicks red, zoom targets green)
           -> work/sheets/screen_<t0>_<t1>.jpg (without t0/t1: the whole recording, an
           overview of up to 10 tiles)
plan:      one still per view row and per personal-data candidate of work/plan.json (the
           screen or camera frame at the view's start, or at its zoom_t, with the zoom box
           drawn green; a candidate's screen frame or screenshot with the blocks render
           will draw red: its measured track at the middle of its first measured piece,
           else its box), at most PLAN_STILL_MAX px on the long side ->
           work/plan_stills/view_NN.jpg and pd_NN.jpg; their paths go back into plan.json
           ("still", and "still_t" / "still_boxes" for a candidate)
assets:    the job's images (job.json "assets", e.g. screenshots of a demo) as numbered
           thumbnails, ASSETS_PER_SHEET per sheet -> work/sheets/assets_NN.jpg; the summary
           maps each number to its file and size
slides:    the human's uploaded slide pages (input/slides/page_NN.png) the same way ->
           work/sheets/slides_NN.jpg; the summary adds the text on each page (local text
           recognition, macOS Vision; null without it)

Every tile carries its source time and frame number ("mm:ss.ss · f<frame>"). Sheets stay
within the image budget of docs/BRIEF.md §6 (<= 1080x1920, never more than 2000 px a side).
"""

import json
import tempfile
from pathlib import Path

from .. import redaction
from . import _image as im
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
    if args.t1 is not None and not args.t1 > args.t0 >= 0:
        raise ToolError("need 0 <= t0 < t1")
    job = resolve_job(args.job)
    src = _screen_input(job)
    info = probe_screen(src)
    w, h = info["size"]
    if args.t1 is None and not info["duration"]:
        raise ToolError("the screen recording has no duration; give t0 and t1")
    dur = info["duration"] or args.t1
    t0, t1 = args.t0, min(args.t1 if args.t1 is not None else dur, dur)
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


# ---------------------------------------------------------------- assets

ASSETS_PER_SHEET = 12


def _image_size(path: Path) -> tuple[int, int]:
    out = run(
        [binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries"]
        + ["stream=width,height", "-of", "json", str(path)]
    )
    try:
        st = json.loads(out.stdout)["streams"][0]
        return int(st["width"]), int(st["height"])
    except (KeyError, IndexError, ValueError) as e:
        raise ToolError(f"cannot read the size of {path.name}: {e!r}") from None


def assets_sheet(args) -> dict:
    job = resolve_job(args.job)
    rels = read_job(job).get("assets") or []
    files = []
    for rel in rels if isinstance(rels, list) else []:
        p = (job / str(rel)).resolve()
        if p.is_relative_to(job) and p.is_file():
            files.append((str(rel), p))
    if not files:
        raise ToolError('the job has no images (job.json "assets"; `job new --asset FILE...`)')
    items, sheets, labels = _numbered_sheets(job, files, "assets")
    print(f"{len(files)} images on {len(sheets)} sheet(s): " + ", ".join(sheets))
    for it in items:
        print(f"  {it['n']:>2}  {it['file']}  {it['size'][0]}x{it['size'][1]} {it['orientation']}")
    return {"sheets": sheets, "images": items, "labels": labels}


def _numbered_sheets(job: Path, files: list[tuple[str, Path]], prefix: str):
    """Numbered thumbnails of `files` [(rel, path)], ASSETS_PER_SHEET per sheet, as
    work/sheets/<prefix>_NN.jpg; (items, sheet paths, labels drawn)."""
    font = im.find_font()
    dest = _sheets_dir(job)
    for old in dest.glob(f"{prefix}_*.jpg"):
        old.unlink()
    items, sheets = [], []
    for k0 in range(0, len(files), ASSETS_PER_SHEET):
        part = files[k0 : k0 + ASSETS_PER_SHEET]
        n = len(part)
        cols = 1 if n == 1 else 2 if n <= 4 else 3
        rows = -(-n // cols)
        tw = 1080 // cols // 2 * 2
        th = min(1920 // rows, round(tw * 4 / 3)) // 2 * 2
        chains, inputs = [], []
        for j, (rel, p) in enumerate(part):
            num = k0 + j + 1
            w, h = _image_size(p)
            items.append({"n": num, "file": rel, "size": [w, h],
                          "orientation": "portrait" if h > w else "landscape"})  # fmt: skip
            label = "," + im.drawtext(font, str(num), "8", "8", max(24, tw // 9)) if font else ""
            inputs.append(["-i", str(p)])
            chains.append(
                f"[{j}:v]scale={tw - 8}:{th - 8}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=0x3A3A3A,format=yuv420p{label}[a{j}]"
            )
        layout = "|".join(f"{(j % cols) * tw}_{(j // cols) * th}" for j in range(n))
        stack = (
            "".join(f"[a{j}]" for j in range(n))
            + f"xstack=inputs={n}:layout={layout}:fill=0x3A3A3A[out]"
            if n > 1
            else "[a0]null[out]"
        )
        name = f"{prefix}_{len(sheets) + 1:02d}.jpg"
        (f,) = im.render_jpegs(inputs, ";".join(chains) + ";" + stack, dest, [name])
        sheets.append(str(f.relative_to(job)))
    return items, sheets, font is not None


SLIDE_TEXT_MAX = 600  # characters of recognised text per page in the summary


def _page_text(path: Path) -> str | None:
    """The text on an uploaded page (local text recognition), None without it."""
    from . import _ocr
    from .redact import image_rgb

    if not _ocr.available():
        return None
    try:
        lines = _ocr.recognize(image_rgb(path))
    except (_ocr.OcrError, ToolError, ValueError, OSError):
        return None
    text = " / ".join(x["text"].strip() for x in lines if x.get("text", "").strip())
    return text[:SLIDE_TEXT_MAX]


def slides_sheet(args) -> dict:
    """The human's uploaded slide pages (input/slides/page_NN.png) as numbered thumbnails
    (work/sheets/slides_NN.jpg) plus the text on each page, read locally, so the plan can
    match every page to the moment it belongs to with one image (or none)."""
    from . import _job_slides

    job = resolve_job(args.job)
    rels = _job_slides.pages(job)
    if not rels:
        raise ToolError(
            "the job has no uploaded slides (input/slides/; a human adds them with "
            "`videocut tool job slides-import`)"
        )
    items, sheets, labels = _numbered_sheets(job, [(r, job / r) for r in rels], "slides")
    texts = _ocr_pages(job, rels)
    for it, text in zip(items, texts, strict=True):
        it["page"] = it.pop("file")
        it["text"] = text
    print(f"{len(items)} slide page(s) on {len(sheets)} sheet(s): " + ", ".join(sheets))
    for it in items:
        print(f"  {it['n']:>2}  {it['page']}  {it['orientation']}  {it['text'] or ''}")
    return {"sheets": sheets, "pages": items, "labels": labels, "text": texts[0] is not None}


def _ocr_pages(job: Path, rels: list[str]) -> list[str | None]:
    from . import _ocr

    return _ocr.pmap(lambda r: _page_text(job / r), rels)


# ---------------------------------------------------------------- plan stills

PLAN_STILL_MAX = 960
PD_COLOR = CLICK_COLOR


def _fit(w: int, h: int, limit: int = PLAN_STILL_MAX) -> tuple[int, int]:
    k = min(1.0, limit / max(w, h))
    return max(2, round(w * k / 2) * 2), max(2, round(h * k / 2) * 2)


def _still(job: Path, inputs: list[str], size: tuple[int, int], boxes, color: str, name: str):
    w, h = _fit(*size)
    graph = f"[0:v]scale={w}:{h}"
    for box in boxes:
        x0, y0 = int(box[0] * w), int(box[1] * h)
        bw, bh = max(2, int(box[2] * w) - x0), max(2, int(box[3] * h) - y0)
        graph += f",drawbox=x={x0}:y={y0}:w={bw}:h={bh}:color={color}:t=4"
    (f,) = im.render_jpegs([inputs], graph + "[out]", job / "work" / "plan_stills", [name])
    return str(f.relative_to(job))


def _pd_view(c: dict) -> tuple[float | None, list[list[float]]]:
    """(camera time, boxes) of a personal-data still: what render will cover. A screen
    candidate is shown at the middle of its first measured piece inside the plan's range
    (else of its range) with every block drawn then; a screenshot with all its boxes."""
    if "asset" in c:
        return None, redaction.asset_boxes(c)
    lo, hi = c.get("range") or [c["t0"], c["t1"]]
    pieces = redaction.pieces(c)
    inside = [p for p in pieces if min(p["t1"], hi) > max(p["t0"], lo)]
    first = next((p for p in inside if not _is_area(c, p)), inside[0] if inside else None)
    if first is None:
        t = (lo + hi) / 2
    else:
        t = (max(first["t0"], lo) + min(first["t1"], hi)) / 2
    boxes = [list(p["box"]) for p in pieces if p["t0"] <= t <= p["t1"]] or [list(c["box"])]
    return t, boxes


def _is_area(c: dict, p: dict) -> bool:
    return any(k.get("source") == "area" and k["box"] == p["box"] for k in c.get("track") or [])


def plan_stills(args) -> dict:
    from ..edl import read_sync, screen_time
    from ..redaction import asset_file
    from .plan import FILE, STILLS, check_job

    job = resolve_job(args.job).resolve()
    data, errs = check_job(job)
    if errs:
        raise ToolError("work/plan.json is not valid (`videocut tool plan check`): " + errs[0])
    dest = job / STILLS
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.jpg"):
        old.unlink()
    sync = read_sync(job / "work" / "sync.json")
    screen = cam = None
    warnings = []

    def screen_frame(t_cam: float):
        nonlocal screen
        if sync is None:
            return None
        if screen is None:
            src = _screen_input(job)
            screen = (src, tuple(probe_screen(src)["size"]))
        return ["-ss", f"{max(0.0, screen_time(t_cam, sync)):.4f}", "-i", str(screen[0])], screen[1]

    def camera_frame(t: float):
        nonlocal cam
        if cam is None:
            src, v = _proxy(job)
            cam = (src, (v["width"], v["height"]))
        return ["-ss", f"{t:.4f}", "-i", str(cam[0])], cam[1]

    made = []
    for v in data.get("views") or []:
        t = v["src"][0] if v.get("zoom_t") is None else v["zoom_t"]
        name = f"view_{v['n']:02d}.jpg"
        got = camera_frame(t) if v["layout"] == "face" else screen_frame(t)
        if got is None:
            warnings.append(f"view {v['n']}: no valid work/sync.json, no screen still")
            v["still"] = None
            continue
        zoom = [v["zoom_box"]] if v.get("zoom_box") else []
        v["still"] = _still(job, got[0], got[1], zoom, TARGET_COLOR, name)
        made.append(v["still"])
    measured = {c["id"]: c for c in redaction.load(job) or []}
    for c in data.get("personal_data") or []:
        name = f"pd_{c['id']:02d}.jpg"
        t, boxes = _pd_view(measured.get(c["id"], c))
        if "asset" in c:
            f = asset_file(job, c["asset"])
            if f is None or not f.is_relative_to(job) or not f.is_file():
                warnings.append(f"personal data {c['id']}: {c['asset']} not found")
                c["still"] = None
                continue
            got = (["-i", str(f)], _image_size(f))
        else:
            got = screen_frame(t)
            if got is None:
                warnings.append(f"personal data {c['id']}: no valid work/sync.json, no still")
                c["still"] = None
                continue
            c["still_t"] = round(t, 3)
        c["still"] = _still(job, got[0], got[1], boxes, PD_COLOR, name)
        c["still_boxes"] = boxes
        made.append(c["still"])
    write_json(job / FILE, data)
    for w in warnings:
        print(f"warning: {w}")
    print(f"{STILLS}: {len(made)} still(s)")
    return {"dir": STILLS, "stills": made, "warnings": warnings}


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
    sc.add_argument(
        "t0", type=float, nargs="?", default=0.0, help="screen time in seconds (default 0)"
    )
    sc.add_argument("t1", type=float, nargs="?", help="(default: the end of the recording)")
    sc.add_argument(
        "--tiles", type=int, default=0, metavar="N", help="tiles (default: auto, <= 10)"
    )
    sc.set_defaults(handler=run_tool("sheet screen", screen_sheet))
    a = sub.add_parser(
        "assets", help="numbered thumbnails of the job's images (screenshots)",
        parents=[job_parent()],
    )  # fmt: skip
    a.set_defaults(handler=run_tool("sheet assets", assets_sheet))
    sl = sub.add_parser(
        "slides", help="numbered thumbnails and text of the human's uploaded slide pages",
        parents=[job_parent()],
    )  # fmt: skip
    sl.set_defaults(handler=run_tool("sheet slides", slides_sheet))
    pl = sub.add_parser(
        "plan", help="stills of the plan's views and personal-data candidates",
        parents=[job_parent()],
    )  # fmt: skip
    pl.set_defaults(handler=run_tool("sheet plan", plan_stills))
