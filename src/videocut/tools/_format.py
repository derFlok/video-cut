"""Output format of a job: aspect presets, the default rule, output size and safe zones.

job.json "format" is one of FORMATS. Absent, the default rule picks it when the job is read
(never written back): a job with a screen recording or screenshot assets is a product demo
and becomes 16:9, every other job (talking head, selfie) a 9:16 reel.

Safe zones (docs/BRIEF.md §8) are the margins platform UI may cover; captions and overlays
stay inside them. By the preset nearest to the frame's aspect, in px at the preset's size:

    9:16  1080x1920  top 250 (13 %)  bottom 350 (18 %)  sides 6 %   reel / story UI
    4:5   1080x1350  top 108 (8 %)   bottom 203 (15 %)  sides 5 %   feed post
    1:1   1080x1080  top 76 (7 %)    bottom 151 (14 %)  sides 5 %   feed post
    16:9  1920x1080  top 65 (6 %)    bottom 130 (12 %)  sides 4 %   player UI; captions
                                                                     sit above the bottom

Other sizes of the same aspect (e.g. a half-size preview) scale with the height.
"""

import math

FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
PORTRAIT, LANDSCAPE = "9:16", "16:9"
# top, bottom (px at the preset's height), side (fraction of the width)
SAFE = {
    "9:16": (250, 350, 0.06),
    "4:5": (108, 203, 0.05),
    "1:1": (76, 151, 0.05),
    "16:9": (65, 130, 0.04),
}


class FormatError(ValueError):
    """An output format that is not one of FORMATS."""


def parse_format(value: str) -> str:
    """A preset name from user input ("16:9", also "16x9" or "16/9"), else FormatError."""
    v = str(value).strip().lower().replace("x", ":").replace("/", ":")
    if v not in FORMATS:
        raise FormatError(
            f"unknown output format {value!r}; one of: "
            + ", ".join(f"{k} ({w}x{h})" for k, (w, h) in FORMATS.items())
        )
    return v


def default_format(job: dict) -> str:
    """16:9 for a product demo (a screen recording or screenshot assets), else 9:16."""
    return LANDSCAPE if job.get("screen") or job.get("assets") else PORTRAIT


def job_format(job: dict) -> str:
    """The job's format: job.json "format" when set (FormatError when invalid), else the
    default rule."""
    value = job.get("format")
    return default_format(job) if value is None else parse_format(value)


def output_size(job: dict) -> tuple[int, int]:
    return FORMATS[job_format(job)]


def aspect_class(size: tuple[int, int]) -> str:
    """ "portrait" (h/w > 1.2), "landscape" (w/h > 1.2) or "square" (the renderer's classes)."""
    w, h = size
    if h / w > 1.2:
        return "portrait"
    if w / h > 1.2:
        return "landscape"
    return "square"


def nearest_format(size: tuple[int, int]) -> str:
    """The preset whose aspect is closest to `size` (log ratio)."""
    r = math.log(size[0] / size[1])
    return min(FORMATS, key=lambda k: abs(math.log(FORMATS[k][0] / FORMATS[k][1]) - r))


def safe_zones(size: tuple[int, int]) -> dict:
    """Organic safe zones in px for an output of `size`: {"top", "bottom", "side"}."""
    w, h = size
    fmt = nearest_format(size)
    top, bottom, side = SAFE[fmt]
    k = h / FORMATS[fmt][1]
    return {"top": round(top * k), "bottom": round(bottom * k), "side": round(side * w)}
