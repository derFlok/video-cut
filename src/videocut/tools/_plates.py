"""Full-frame plate scenes (the end card `cta_end`): when they may start, and the held
tail the reel needs for them.

A plate hides the speaker, so it starts only after the last spoken word of the reel
(+ AFTER_SPEECH_S). A plate the EDL places earlier is shifted to that moment, keeping its
length; when it then runs past the end of the cut, the reel is extended by a held tail:
the last frame repeats under the plate and the audio is padded with silence (after
loudnorm, so the loudness of the spoken part does not change). This is chosen over
rejecting the EDL because it always succeeds, also when the recording has no room after
the last word, and the agent has nothing to fix.

`videocut tool overlays` (which renders the plate over the tail), `videocut tool render`
(which appends the tail when it composites that overlay) and `videocut tool verify` (end
card checks) all compute the same schedule from work/edl.json + work/words.json.
"""

import math
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ..edl import Edl
from .captions import _words, map_words
from .common import ToolError

PLATE_SCENES = frozenset({"cta_end"})
AFTER_SPEECH_S = 0.2


@dataclass
class Schedule:
    overlays: list[dict]  # the EDL overlays, plates shifted to start after the speech
    tail_frames: int  # held frames appended after the cut (0: none)
    last_word: float | None  # output time the last spoken word ends
    shifted: list[dict] = field(default_factory=list)  # {"scene", "from", "to"} per moved plate


def is_plate(o) -> bool:
    return isinstance(o, dict) and o.get("scene") in PLATE_SCENES


def last_word_end(job: Path, edl: Edl) -> float | None:
    """Output time the last spoken word of the reel ends: work/words.json mapped through the
    segments (as `videocut tool captions` maps it), else the EDL's caption words."""
    try:
        words = map_words(_words(job), edl.segments, edl.fps)
    except ToolError:
        words = [
            w
            for c in edl.captions
            if isinstance(c, dict)
            for w in c.get("words") or []
            if isinstance(w, dict) and str(w.get("w", "")).strip()
        ]
    ends = [w["t1"] for w in words if isinstance(w.get("t1"), int | float)]
    return max(ends) if ends else None


def schedule(overlays: list, last_word: float | None, content_s: float, fps) -> Schedule:
    """Plates moved behind `last_word` and the tail (whole frames at `fps`) that makes room
    for every plate after `content_s` seconds of cut material."""
    start = None if last_word is None else round(last_word + AFTER_SPEECH_S, 3)
    out, shifted, end = [], [], content_s
    for o in overlays:
        if is_plate(o) and start is not None and o["t0"] < start:
            moved = {**o, "t0": start, "t1": round(start + o["t1"] - o["t0"], 3)}
            shifted.append({"scene": o["scene"], "from": o["t0"], "to": start})
            o = moved
        if is_plate(o):
            end = max(end, o["t1"])
        out.append(o)
    rate = Fraction(fps).limit_denominator(1001)
    need = (end - content_s) * rate
    tail = math.ceil(need - 1e-6) if need > 1e-6 else 0
    return Schedule(out, int(tail), last_word, shifted)


def for_job(job: Path, edl: Edl, content_s: float) -> Schedule:
    return schedule(edl.overlays, last_word_end(job, edl), content_s, edl.fps)
