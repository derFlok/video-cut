"""`videocut tool captions [--force]` (pipeline step 8): EDL captions from the transcript.

    work/words.json + work/edl.json (+ work/emphasis.json) -> "captions" in work/edl.json

Mapping: every word of work/words.json (source time) that a segment keeps is moved to
OUTPUT time through the segments (segment starts snapped to the output frame grid like
render does), clipped at the segment edges. A word cut down to less than half of itself
or MIN_WORD_S is dropped, so a filler or slip the EDL removed never shows up as a caption.

Grouping (TikTok style): a new group starts after a pause >= PAUSE_S, after a word ending
in punctuation, and when the group would exceed MAX_WORDS words or MAX_CHARS characters.
A group lasts from its first word to its last one, at least MIN_GROUP_S, and is held up
to the next group across gaps shorter than PAUSE_S (no flicker between groups).

Emphasis (`emph`): a word is emphasised when a zoom key of the EDL falls on it (or on a
pause right before it), or when work/emphasis.json names it:
    {"times": [<source s>, ...]}   a word containing that time (or starting just after)

Captions already in the EDL are kept unless --force is given; every other EDL field is
left as it is.
"""

import json
import re
from fractions import Fraction
from pathlib import Path

from ..edl import EdlError, validate
from ..edl import from_dict as edl_from_dict
from .common import ToolError, job_parent, resolve_job, run_tool, write_json

PAUSE_S = 0.25
MAX_WORDS = 3
MAX_CHARS = 18
MIN_GROUP_S = 0.4
MIN_WORD_S = 0.06
EMPH_LOOKAHEAD_S = 0.3  # a time in a pause marks the word that starts this soon after it
BREAK_AFTER = re.compile(r"[.,!?;:…]['\"»«”“)]*$")


def _r(t: float) -> float:
    return round(t, 3)


def map_words(words: list[dict], segments: list, fps: float) -> list[dict]:
    """Kept words in output time: [{"w", "t0", "t1", "src_t0", "src_t1"}], in output order.
    `segments` are edl.Segment objects (source in/out)."""
    rate = Fraction(fps).limit_denominator(1001)
    out, t_out = [], 0.0
    for seg in segments:
        o = float(round(t_out * rate) / rate)  # where render starts this segment
        for w in words:
            a, b = max(w["t0"], seg.start), min(w["t1"], seg.end)
            kept = b - a
            if kept < MIN_WORD_S or kept < 0.5 * (w["t1"] - w["t0"]):
                continue
            out.append(
                {
                    "w": w["w"].strip(),
                    "t0": _r(o + a - seg.start),
                    "t1": _r(o + b - seg.start),
                    "src_t0": w["t0"],
                    "src_t1": w["t1"],
                }
            )
        t_out += seg.duration
    return [w for w in out if w["w"]]


def emphasis_times(edl, extra: list[float]) -> list[float]:
    """Source times that mark an emphasised word: every zoom key + work/emphasis.json."""
    return [t for s in edl.segments for t, _ in s.zoom_keys] + list(extra)


def mark_emph(words: list[dict], times: list[float]) -> None:
    """Set w["emph"] for the word containing one of `times` (source s) or, when the time
    lies in a pause, the word starting within EMPH_LOOKAHEAD_S after it."""
    for w in words:
        w["emph"] = False
    for t in times:
        hit = next((w for w in words if w["src_t0"] <= t <= w["src_t1"]), None)
        if hit is None:
            after = [w for w in words if 0 <= w["src_t0"] - t <= EMPH_LOOKAHEAD_S]
            hit = min(after, key=lambda w: w["src_t0"], default=None)
        if hit is not None:
            hit["emph"] = True


def group(words: list[dict], duration: float) -> list[dict]:
    """TikTok-style caption groups (see module docstring) of output-time words."""
    groups: list[list[dict]] = []
    for w in words:
        cur = groups[-1] if groups else None
        if (
            cur is None
            or w["t0"] - cur[-1]["t1"] >= PAUSE_S
            or BREAK_AFTER.search(cur[-1]["w"])
            or len(cur) >= MAX_WORDS
            or len(" ".join(x["w"] for x in cur + [w])) > MAX_CHARS
        ):
            groups.append([w])
        else:
            cur.append(w)
    out = []
    for i, g in enumerate(groups):
        t0, t1 = g[0]["t0"], g[-1]["t1"]
        nxt = groups[i + 1][0]["t0"] if i + 1 < len(groups) else duration
        if nxt - t1 < PAUSE_S:
            t1 = nxt  # hold until the next group
        t1 = min(max(t1, t0 + MIN_GROUP_S), max(nxt, t1), duration)
        out.append(
            {
                "t0": _r(t0),
                "t1": _r(t1),
                "words": [
                    {"w": x["w"], "t0": x["t0"], "t1": min(x["t1"], _r(t1)), "emph": x["emph"]}
                    for x in g
                ],
            }
        )
    return [g for g in out if g["t1"] > g["t0"]]


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        raise ToolError(f"{what} is not valid JSON: {e}") from None


def _words(job: Path) -> list[dict]:
    path = job / "work" / "words.json"
    if not path.is_file():
        raise ToolError("work/words.json is missing; run `videocut tool transcribe` first")
    data = _read_json(path, "work/words.json")
    words = data.get("words") if isinstance(data, dict) else None
    if not isinstance(words, list):
        raise ToolError("work/words.json has no words list")
    ok = [
        w
        for w in words
        if isinstance(w, dict)
        and isinstance(w.get("w"), str)
        and all(
            isinstance(w.get(k), int | float) and not isinstance(w.get(k), bool)
            for k in ("t0", "t1")
        )
        and w["t1"] > w["t0"]
    ]
    return sorted(ok, key=lambda w: w["t0"])


def _emphasis(job: Path) -> list[float]:
    path = job / "work" / "emphasis.json"
    if not path.is_file():
        return []
    data = _read_json(path, "work/emphasis.json")
    times = data.get("times") if isinstance(data, dict) else None
    if not isinstance(times, list):
        raise ToolError('work/emphasis.json must be {"times": [<source seconds>, ...]}')
    return [float(t) for t in times if isinstance(t, int | float) and not isinstance(t, bool)]


def captions(args) -> dict:
    job = resolve_job(args.job)
    edl_path = job / "work" / "edl.json"
    if not edl_path.is_file():
        raise ToolError("work/edl.json is missing")
    data = _read_json(edl_path, "work/edl.json")
    try:
        edl = edl_from_dict(data)
    except EdlError as e:
        raise ToolError(
            f"{len(e.errors)} problem(s) in edl.json: " + "; ".join(e.errors[:5])
        ) from None
    if edl.captions and not args.force:
        print("edl.json already has captions; kept (use --force to rebuild them)")
        return {"job": str(job), "written": False, "groups": len(edl.captions)}

    duration = edl.output_duration()
    words = map_words(_words(job), edl.segments, edl.fps)
    mark_emph(words, emphasis_times(edl, _emphasis(job)))
    groups = group(words, duration)
    data["captions"] = groups
    if errors := validate(data):
        raise ToolError("captions would make edl.json invalid: " + "; ".join(errors[:5]))
    write_json(edl_path, data)
    n_emph = sum(w["emph"] for g in groups for w in g["words"])
    print(f"{len(groups)} caption groups, {len(words)} words ({n_emph} emphasised)")
    return {
        "job": str(job),
        "written": True,
        "file": "work/edl.json",
        "groups": len(groups),
        "words": len(words),
        "emph": n_emph,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "captions", help="caption groups from words.json -> work/edl.json", parents=[job_parent()]
    )
    p.add_argument("--force", action="store_true", help="replace captions already in the EDL")
    p.set_defaults(handler=run_tool("captions", captions))
