"""Shared helpers for the image tools (sheets, verify): ffmpeg filter snippets and probes.

All pictures are drawn by ffmpeg (select, scale, drawtext, tile, showwavespic, drawbox,
stack) and written as JPEG, so there is no image library. Text needs a font file; none is
bundled, one is looked up among the macOS system fonts at runtime. Without a font the
pictures are still made, just without text (callers report `labels: false`).
"""

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..paths import tool_env
from .common import ToolError, binary, run

# Image budget (docs/BRIEF.md §6): sheets stay within 1080x1920 (portrait) or 1920x1080
# (landscape), so never more than 2000 px on a side.
TILE_BOX = (360, 640)  # portrait tile; landscape is the transposed box
WAVE_COLOR = "0x4db8ff"
MARK_COLOR = "red"
# Waveforms are peak-normalised per picture: normal speech peaks around -15 dBFS and would
# draw as a nearly flat line. The gain is capped so a quiet pause does not turn into "speech".
WAVE_PEAK = 0.9
WAVE_MAX_GAIN = 20.0  # +26 dB

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/Library/Fonts/Arial.ttf",
)


def find_font() -> str | None:
    """First usable system font file, or None. `VIDEOCUT_FONT` overrides (tests, odd setups)."""
    env = os.environ.get("VIDEOCUT_FONT")
    if env is not None:
        return env if Path(env).is_file() else None
    return next((f for f in _FONT_CANDIDATES if Path(f).is_file()), None)


def esc_path(p: str | Path) -> str:
    """Escape a path for use as a filter option value."""
    return re.sub(r"([\\':,\[\];])", r"\\\1", str(p))


def drawtext(font: str, text: str, x: str, y: str, size: int, color: str = "white") -> str:
    """drawtext filter with a dark box; `text` may use %{...} expansions (ffmpeg syntax)."""
    return (
        f"drawtext=fontfile={esc_path(font)}:fontsize={size}:fontcolor={color}"
        f":box=1:boxcolor=black@0.6:boxborderw=3:x={x}:y={y}:text='{text}'"
    )


def textfile_drawtext(
    font: str, path: Path, x: str, y: str, size: int, color: str = "white"
) -> str:
    """drawtext reading a literal text from a file (no escaping or % expansion issues)."""
    return (
        f"drawtext=fontfile={esc_path(font)}:fontsize={size}:fontcolor={color}"
        f":box=1:boxcolor=black@0.6:boxborderw=2:x={x}:y={y}"
        f":textfile={esc_path(path)}:expansion=none"
    )


def label_expr(fps: float) -> str:
    """drawtext expansion giving `mm:ss.ss · f<frame>` for the frame's own timestamp."""
    cs = "round(t*100)"
    return (
        f"%{{eif\\:trunc({cs}/6000)\\:d\\:2}}\\:"
        f"%{{eif\\:trunc(mod({cs}\\,6000)/100)\\:d\\:2}}."
        f"%{{eif\\:mod({cs}\\,100)\\:d\\:2}} · f%{{eif\\:round(t*{fps:.6f})\\:d}}"
    )


def fmt_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


# ffmpeg's expression parser refuses nesting deeper than 100 (a flat "a+b+c+..." of about
# 100 terms fails with "Cannot allocate memory"); the trees built here stay far below that.
MAX_EXPR_DEPTH = 40


def any_of(terms: list[str]) -> str:
    """Sum of ffmpeg expression terms as a balanced tree: nesting log2(n), not n, so a
    select over hundreds of frames still parses (select_times, _verify_screen)."""
    if not terms:
        return "0"
    if len(terms) == 1:
        return terms[0]
    mid = len(terms) // 2
    return f"({any_of(terms[:mid])}+{any_of(terms[mid:])})"


def expr_depth(expr: str) -> int:
    """Nesting of an expression as ffmpeg's parser sees it: parentheses plus one level per
    chained "+" (escaped commas and quotes do not matter)."""
    depth = best = 0
    chain = [0]
    for ch in expr:
        if ch == "(":
            depth += 1
            chain.append(0)
        elif ch == ")":
            depth -= 1
            chain.pop()
        elif ch == "+":
            chain[-1] += 1
        best = max(best, depth + sum(chain))
    return best


def select_times(times: list[float], fps: float) -> str:
    """select expression keeping exactly the frames nearest to the given times."""
    half = 0.5 / fps
    return any_of([f"between(t\\,{t - half:.6f}\\,{t + half - 1e-6:.6f})" for t in times])


def select_frames(numbers: list[int]) -> str:
    """select expression keeping exactly the frames with these numbers (0-based)."""
    return any_of([f"eq(n\\,{n})" for n in numbers])


def snap(t: float, fps: float) -> float:
    """Timestamp of the frame containing t."""
    return round(t * fps) / fps


def tile_size(width: int, height: int) -> tuple[int, int]:
    """Contact-sheet tile: portrait 360x640, landscape 640x360, otherwise proportional."""
    box_w, box_h = TILE_BOX if height >= width else TILE_BOX[::-1]
    scale = min(box_w / width, box_h / height)
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def probe_video(path: Path) -> dict:
    """Stored size (our proxies and reels are upright), fps, frames, duration of video 0."""
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_packets",
            "-show_entries",
            "stream=width,height,avg_frame_rate,nb_read_packets,duration:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    info = json.loads(out.stdout)
    try:
        s = info["streams"][0]
        num, den = s["avg_frame_rate"].split("/")
        fps = float(num) / float(den)
        duration = float(s.get("duration") or info["format"]["duration"])
        frames = int(s["nb_read_packets"])
    except (KeyError, IndexError, ValueError, ZeroDivisionError) as e:
        raise ToolError(f"cannot read video properties of {path.name}: {e!r}") from None
    return {
        "width": int(s["width"]),
        "height": int(s["height"]),
        "fps": fps,
        "frames": frames,
        "duration": duration,
    }


def wave_gain(peak: float) -> float:
    """Display gain that brings a window's sample peak to WAVE_PEAK (capped)."""
    return min(WAVE_MAX_GAIN, WAVE_PEAK / max(peak, 1e-6)) if peak > 0 else 1.0


def window_peak(audio: Path, t0: float, t1: float) -> float:
    """Largest absolute sample (0..1) of `audio` in [t0, t1] seconds."""
    r = subprocess.run(
        [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error"]
        + ["-ss", f"{t0:.4f}", "-t", f"{max(t1 - t0, 0.001):.4f}", "-i", str(audio)]
        + ["-vn", "-ac", "1", "-f", "f32le", "-"],
        capture_output=True,
        env=tool_env(),
    )
    if r.returncode != 0:
        raise ToolError(f"ffmpeg failed: {r.stderr.decode(errors='replace').strip()[-500:]}")
    x = np.frombuffer(r.stdout, np.float32)
    return float(np.abs(x).max()) if len(x) else 0.0


def wave_filter(width: int, height: int, mark: float | None = None, gain: float = 1.0) -> str:
    """Audio filter chain: waveform picture with an optional marker at `mark` (0..1 of
    the window width); `gain` scales the samples for display only (see wave_gain)."""
    f = (
        "aformat=channel_layouts=mono,"
        f"volume={gain:.4f}:precision=float,"
        f"showwavespic=s={width}x{height}:colors={WAVE_COLOR}:scale=lin:filter=peak,format=rgb24"
    )
    f += f",drawbox=x=0:y={height // 2}:w={width}:h=1:color=gray@0.5:t=fill"
    if mark is not None:
        x = max(0, min(width - 2, int(mark * width) - 1))
        f += f",drawbox=x={x}:y=0:w=2:h={height}:color={MARK_COLOR}:t=fill"
    return f


def render_jpegs(
    inputs: list[list[str]], graph: str, dest: Path, names: list[str] | Callable[[int], str]
) -> list[Path]:
    """Run one ffmpeg graph whose `[out]` frames become JPEGs in `dest`.

    `inputs` is a list of argument groups, each ending in `-i file`. `names` is the list of
    target file names (one per expected frame) or a function from the 1-based frame number
    to a name (then every produced frame is kept). Files appear only when ffmpeg succeeded.
    """
    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    for group in inputs:
        cmd += group
    cmd += ["-filter_complex", graph, "-map", "[out]", "-fps_mode", "passthrough"]
    if isinstance(names, list):
        cmd += ["-frames:v", str(len(names))]
    cmd += ["-q:v", "3", "-pix_fmt", "yuvj420p", "-start_number", "1"]
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest, prefix=".tmp-") as tmp:
        r = subprocess.run(
            cmd + [str(Path(tmp) / "f_%03d.jpg")], capture_output=True, text=True, env=tool_env()
        )
        if r.returncode != 0:
            raise ToolError(f"ffmpeg failed: {r.stderr.strip()[-600:]}")
        made = sorted(Path(tmp).glob("f_*.jpg"))
        if not made:
            raise ToolError("ffmpeg produced no picture (no frame in the requested range?)")
        out = []
        for k, f in enumerate(made, 1):
            name = names(k) if callable(names) else names[k - 1]
            f.replace(dest / name)
            out.append(dest / name)
    return out
