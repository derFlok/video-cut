"""Target length of a job: how long the finished video should be.

job.json "target_s" is a number of seconds chosen by the human, or absent/null ("auto").
With a target the plan aims for target ± TOLERANCE. Without one the default rule applies
when the job is read (never written back):

    talking head (no screen recording, no screenshot assets)
        by video type: "thought" (thought of the day) 30-45 s, every other type 30-60 s
    product demo (job.json "screen" or "assets")
        no fixed range: as long as the content needs, tight but complete; the plan states
        its estimate

`target_range(job)` returns (min_s, max_s) or None (no fixed range). The plan keeps its
keep list inside the range, never pads: material that is too short for it is said so,
with the natural length proposed instead.
"""

import math
import re

AUTO = "auto"  # API/CLI value for "no explicit target: the default rule"
TOLERANCE = 0.15
MIN_TARGET_S, MAX_TARGET_S = 5, 3 * 3600
TYPE_RANGES = {"thought": (30, 45)}  # video type id (server/app.py TYPES) -> default range
TALK_RANGE = (30, 60)


class LengthError(ValueError):
    """A target length that cannot be read or is out of bounds."""


_NUM = r"(\d+(?:[.,]\d+)?)"
_UNITS = {"": 1, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "sek": 1,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "minuten": 60,
          "h": 3600}  # fmt: skip


def parse_target(value) -> float | int | None:
    """Seconds from user input, or None for "auto" (also "", null).

    Accepts 90, 90.5, "90", "90s", "90 s", "5m", "5 min", "2:30" (m:ss), "1:02:30" (h:mm:ss)
    and "auto". LengthError for anything else or outside MIN_TARGET_S..MAX_TARGET_S."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise LengthError(f"invalid target length {value!r}")
    if isinstance(value, (int, float)):
        secs = float(value)
    else:
        v = str(value).strip().lower()
        if v in ("", AUTO, "automatic", "automatisch"):
            return None
        if re.fullmatch(r"\d+(:[0-5]\d){1,2}", v):
            secs = 0.0
            for part in v.split(":"):
                secs = secs * 60 + int(part)
        elif m := re.fullmatch(_NUM + r"\s*([a-z]*)", v):
            unit = m.group(2)
            if unit not in _UNITS:
                raise LengthError(f"invalid target length {value!r} (e.g. 90, 90s, 5m, 2:30)")
            secs = float(m.group(1).replace(",", ".")) * _UNITS[unit]
        else:
            raise LengthError(f"invalid target length {value!r} (e.g. 90, 90s, 5m, 2:30 or auto)")
    if not math.isfinite(secs) or not MIN_TARGET_S <= secs <= MAX_TARGET_S:
        raise LengthError(
            f"target length {value!r} must be between {MIN_TARGET_S} s and {MAX_TARGET_S // 3600} h"
        )
    return int(secs) if secs == int(secs) else round(secs, 1)


def is_demo(job: dict) -> bool:
    """A product demo: a screen recording or screenshot assets."""
    return bool(job.get("screen") or job.get("assets"))


def job_target(job: dict) -> float | int | None:
    """job.json "target_s" (LengthError when invalid), None when absent ("auto")."""
    value = job.get("target_s")
    if value is not None and not isinstance(value, (int, float)):
        raise LengthError(f"target_s must be a number of seconds, not {value!r}")
    return parse_target(value)


def default_range(job: dict) -> tuple[int, int] | None:
    """The range without a target: None for a demo, else by video type."""
    if is_demo(job):
        return None
    return TYPE_RANGES.get(str(job.get("type") or ""), TALK_RANGE)


def target_range(job: dict) -> tuple[int, int] | None:
    """(min_s, max_s) the plan aims for, or None (no fixed range: as long as the content
    needs). With a target: target ± TOLERANCE (whole seconds, rounded outwards)."""
    target = job_target(job)
    if target is None:
        return default_range(job)
    lo, hi = round(target * (1 - TOLERANCE), 6), round(target * (1 + TOLERANCE), 6)
    return math.floor(lo), math.ceil(hi)


def label(seconds: float | int | None) -> str:
    """90 -> "90 s", 300 -> "5 min", 150 -> "2:30 min", None -> "auto"."""
    if seconds is None:
        return AUTO
    if seconds < 120 and seconds == int(seconds):
        return f"{int(seconds)} s"
    if seconds < 120:
        return f"{seconds} s"
    m, s = divmod(round(seconds), 60)
    return f"{m} min" if s == 0 else f"{m}:{s:02d} min"


def describe(job: dict) -> str:
    """One line for `job show` and the prompts."""
    target = job_target(job)
    rng = target_range(job)
    if target is not None:
        return f"{label(target)} (set; aim for {rng[0]}-{rng[1]} s)"
    if rng is None:
        return (
            "auto (default; product demo: no fixed length, as long as the content needs, "
            "tight but complete; state the estimate in the plan)"
        )
    return f"auto (default; {rng[0]}-{rng[1]} s for this video type)"
