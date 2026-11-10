"""Glass material, pipeline side (brand motion.depth "glass"; the material itself is
renderer/lib/glass.js).

The renderers capture with alpha and never see the video, so a glass plate cannot blur
what is under it by itself. Each render also writes a GLASS MASK (gray FFV1: the alpha of
every glass plate per frame, with its fades and blur-ins) plus `<mask>.json` with the frost
parameters. `render` frosts the video through that mask before it lays the overlay on top:
the picture is shrunk to a quarter, blurred (Gaussian sigma from the mask json),
saturated and brightened a little, scaled back up, given the mask as alpha and laid over
the picture; then the overlay (tint, specular edge, grain, content) goes on top.

Vibrancy: `backdrop_range` measures the luminance the frost can have in this job (the
source's keyframes, tonemapped like the render, shrunk and blurred like the frost; the
0.5th and 99.5th percentile of relative luminance). The renderers take it as
tokens["glass_backdrop"] and pick a tint dense enough that text keeps its contrast over
the darkest and the lightest frost actually in the video (lib/glass.js `tint`).
"""

import json
import subprocess
from fractions import Fraction
from pathlib import Path

from ._color import to_sdr
from .common import binary

SAMPLE_W = 96  # px of the measuring frames (the frost has no detail to lose)
FROST_SIGMA = 0.032  # share of the short side, as renderer/lib/glass.js FROST_SIGMA
PERCENTILES = (0.005, 0.995)
MAX_FRAMES = 48
CACHE = "work/glass_backdrop.json"
DOWN = 4  # the frost is blurred at a quarter of the size


def is_glass(tokens: dict) -> bool:
    return str((tokens.get("motion") or {}).get("depth", "")).lower() == "glass"


def _lin(c: float) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def _lum(r: int, g: int, b: int) -> float:
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def backdrop_range(job: Path, src: Path, info: dict) -> list[float] | None:
    """[lmin, lmax] relative luminance of the frost over the job's video (cached in
    work/glass_backdrop.json per source file, size and mtime); None when it cannot be
    measured (the renderers then guard against black and white)."""
    st = src.stat()
    key = {"source": str(src.resolve()), "size": st.st_size, "mtime": st.st_mtime}
    cache = job / CACHE
    try:
        doc = json.loads(cache.read_text())
        if doc.get("key") == key:
            return doc["range"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    w, h = (int(v) for v in info["size"])
    sh = max(2, round(SAMPLE_W * h / w / 2) * 2)
    sigma = round(FROST_SIGMA * min(SAMPLE_W, sh), 2)
    sdr = to_sdr(info)
    steps = (sdr, f"scale={SAMPLE_W}:{sh}:flags=area", f"gblur=sigma={sigma}", "format=rgb24")
    vf = ",".join(p for p in steps if p)
    r = subprocess.run(
        [binary("ffmpeg"), "-v", "error", "-skip_frame", "nokey", "-i", str(src), "-an",
         "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
        capture_output=True,
    )  # fmt: skip
    frame = SAMPLE_W * sh * 3
    n = len(r.stdout) // frame
    if r.returncode != 0 or n == 0:
        return None
    pick = sorted({round(i * (n - 1) / max(1, MAX_FRAMES - 1)) for i in range(min(n, MAX_FRAMES))})
    lums: list[float] = []
    for i in pick:
        buf = r.stdout[i * frame : (i + 1) * frame]
        lums += [_lum(buf[k], buf[k + 1], buf[k + 2]) for k in range(0, frame, 3)]
    lums.sort()
    lo, hi = (lums[min(len(lums) - 1, int(p * len(lums)))] for p in PERCENTILES)
    rng = [round(lo, 4), round(hi, 4)]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"key": key, "frames": len(pick), "range": rng}) + "\n")
    return rng


def read_params(mask: Path) -> dict | None:
    """The frost parameters the renderer wrote next to `mask` (None when unusable)."""
    try:
        p = json.loads(Path(f"{mask}.json").read_text())
        if all(isinstance(p.get(k), int | float) for k in ("sigma", "saturation", "brightness")):
            return p
    except (OSError, ValueError, AttributeError):
        pass
    return None


def frost_chain(w: int, h: int, params: dict, scale: float = 1.0) -> str:
    """Filter chain (no labels) turning a w x h picture into its frost at output `scale`
    (the mask json's sigma is in renderer px)."""
    dw, dh = max(2, w // DOWN), max(2, h // DOWN)
    sigma = max(0.5, params["sigma"] * scale / DOWN)
    return (
        f"scale={dw}:{dh}:flags=area,gblur=sigma={sigma:.2f},"
        f"eq=saturation={params['saturation']}:brightness={params['brightness']},"
        f"scale={w}:{h}:flags=bicubic,format=yuv444p"
    )


def mask_input(index: int, fps, w: int, h: int, start: int, label: str) -> str:
    """Filter line: mask input `index` on the output frame grid from frame `start`, scaled
    to w x h, as gray [label]."""
    fr = Fraction(fps).limit_denominator(1001)
    tb = f"{fr.denominator}/{fr.numerator}"
    return (
        f"[{index}:v]setpts=PTS-STARTPTS,fps={fps},settb={tb},setpts=N+{start},"
        f"scale={w}:{h}:flags=bilinear,format=gray[{label}]"
    )
