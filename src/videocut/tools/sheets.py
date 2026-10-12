"""`videocut tool sheet contact|burst|timeline`: the pictures Claude uses to "see".

contact:   3x3 contact sheets over work/proxy.mp4, one tile every N seconds
           -> work/sheets/contact_NN.jpg
burst:     frames around source time t (+-span) plus the waveform of the same window with a
           marker at t -> work/sheets/burst_<t>.jpg
timeline:  filmstrip + waveform + word labels (work/words.json) + candidate shading
           (work/candidates.json) for [t0, t1] -> work/sheets/timeline_<t0>_<t1>.jpg

Every tile carries its source time and frame number ("mm:ss.ss · f<frame>"). Sheets stay
within the image budget of docs/BRIEF.md §6 (<= 1080x1920, never more than 2000 px a side).
"""

import json
import tempfile
from pathlib import Path

from . import _image as im
from .common import ToolError, job_parent, resolve_job, run_tool

COLS = 3
WAVE_H = 140
STRIP_W = 1800
STRIP_TILES = 10
MAX_TIMELINE_S = 120.0
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
    for old in sheets_dir.glob("contact_*.jpg"):
        old.unlink()
    files = im.render_jpegs(
        [["-i", str(proxy)]], graph, sheets_dir, lambda k: f"contact_{k:02d}.jpg"
    )
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
    graph = (
        f"[0:v]select='{im.select_times(times, fps)}',scale={tw}:{th}"
        f"{_label(font, fps, max(14, tw // 15))},"
        f"tile={cols}x{rows}:padding=0:margin=0,format=rgb24,setpts=PTS-STARTPTS[g];"
        f"[1:a]{im.wave_filter(width, WAVE_H, (args.t - w0) / (w1 - w0))}"
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
    px = STRIP_W / span

    def x(t: float) -> int:
        return int(round((min(max(t, t0), t1) - t0) * px))

    n_tiles = max(1, min(STRIP_TILES, int(span * fps)))
    cell = span / n_tiles
    times = sorted({im.snap(t0 + (i + 0.5) * cell, fps) for i in range(n_tiles)})
    n_tiles = len(times)
    tw = STRIP_W // n_tiles // 2 * 2
    th = max(2, round(tw * v["height"] / v["width"] / 2) * 2)
    strip_w = tw * n_tiles
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
        lower = [f"[1:a]{im.wave_filter(strip_w, WAVE_H)},setpts=PTS-STARTPTS[wave]"]
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


def register(subparsers) -> None:
    p = subparsers.add_parser("sheet", help="contact, burst and timeline sheets")
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
