"""`videocut tool captions [--force]` (pipeline step 8): EDL captions from the transcript.

    work/words.json + work/edl.json (+ work/emphasis.json) -> "captions" in work/edl.json

Mapping: every word of work/words.json (source time) that a segment keeps is moved to
OUTPUT time through the segments (segment starts snapped to the output frame grid like
render does), clipped at the segment edges. A word cut down to less than half of itself
or MIN_WORD_S is dropped, so a filler or slip the EDL removed never shows up as a caption.
A word the human hid (tools/transcript.py, "hidden") never shows; an edited word shows its
edited text ("w"), also when it holds several words.

Grouping by the brand's captions.pace (calm when the job has no brand or the brand sets none):
    calm    phrase blocks of 1-2 lines, at most 8 words / 38 characters (two caption lines at
            the caption size), on screen at least 1.0 s
    normal  at most 5 words / 28 characters, at least 0.7 s
    fast    TikTok style, at most 3 words / 18 characters, at least 0.4 s (the old behaviour)
A block always ends at a sentence end and at a pause long enough to hide it (calm 0.6 s,
normal 0.4 s). When a block is full it breaks at its last comma or short pause (>= PAUSE_S)
so a phrase stays together, else before the word that does not fit. Blocks shorter than the
minimum merge with a neighbour across a short gap when the two fit together, else they are
stretched into the gap. A block stays on screen until the next one starts when the gap is
shorter than the hide pause (no flicker), and is hidden in longer pauses. The spoken word
is highlighted inside the block by the renderer (karaoke); the block itself stays. fast
breaks at every pause >= PAUSE_S and every punctuation mark, like before.

Emphasis (`emph`): a word is emphasised when a zoom key of the EDL falls on it (or on a
pause right before it), or when work/emphasis.json names it:
    {"times": [<source s>, ...]}   a word containing that time (or starting just after)

Captions already in the EDL are kept unless --force is given; every other EDL field is
left as it is.
"""

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .. import brand
from ..edl import EdlError, validate
from ..edl import from_dict as edl_from_dict
from .common import ToolError, job_parent, read_job, resolve_job, run_tool, write_json

PAUSE_S = 0.25
MAX_WORDS = 3  # fast
MAX_CHARS = 18  # fast
MIN_GROUP_S = 0.4  # fast


@dataclass(frozen=True)
class Pace:
    max_words: int
    max_chars: int
    min_s: float  # shortest time a block is on screen
    hide_gap: float  # a pause this long hides the block (and always ends it)
    every_break: bool  # break at every pause >= PAUSE_S and punctuation (fast)


PACES = {
    "calm": Pace(8, 38, 1.0, 0.6, False),
    "normal": Pace(5, 28, 0.7, 0.4, False),
    "fast": Pace(MAX_WORDS, MAX_CHARS, MIN_GROUP_S, PAUSE_S, True),
}
DEFAULT_PACE = "calm"
SENTENCE_END = re.compile(r"[.!?…]['\"»«”“)]*$")
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
    words = [w for w in words if not w.get("hidden")]
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
        hit = next((w for w in words if w["src_t0"] <= t < w["src_t1"]), None)
        if hit is None:
            after = [w for w in words if 0 <= w["src_t0"] - t <= EMPH_LOOKAHEAD_S]
            hit = min(after, key=lambda w: w["src_t0"], default=None)
        if hit is not None:
            hit["emph"] = True


def _text(ws: list[dict]) -> str:
    return " ".join(x["w"] for x in ws)


def _split(words: list[dict], pace: Pace) -> list[list[dict]]:
    """Words -> blocks (lists of words) by the break rules of `pace`."""
    blocks: list[list[dict]] = []
    for w in words:
        cur = blocks[-1] if blocks else None
        if cur is None:
            blocks.append([w])
            continue
        gap = w["t0"] - cur[-1]["t1"]
        if pace.every_break:
            hard = gap >= PAUSE_S or BREAK_AFTER.search(cur[-1]["w"])
        else:
            hard = gap >= pace.hide_gap or SENTENCE_END.search(cur[-1]["w"])
        if hard:
            blocks.append([w])
            continue
        if len(cur) < pace.max_words and len(_text([*cur, w])) <= pace.max_chars:
            cur.append(w)
            continue
        # Full: break after the last comma or short pause, keeping that phrase together.
        k = None
        if not pace.every_break:
            for i in range(len(cur) - 1, 0, -1):
                if (
                    BREAK_AFTER.search(cur[i - 1]["w"])
                    or cur[i]["t0"] - cur[i - 1]["t1"] >= PAUSE_S
                ):
                    k = i
                    break
        rest = cur[k:] + [w] if k else [w]
        if k and len(rest) <= pace.max_words and len(_text(rest)) <= pace.max_chars:
            del cur[k:]
            blocks.append(rest)
        else:
            blocks.append([w])
    return blocks


def _merge_short(blocks: list[list[dict]], pace: Pace) -> list[list[dict]]:
    """Blocks spoken in less than pace.min_s merge with a neighbour across a gap shorter than
    pace.hide_gap when the two fit one block (the closer neighbour first)."""
    blocks = [list(b) for b in blocks]

    def fits(a, b):
        both = a + b
        return (
            b[0]["t0"] - a[-1]["t1"] < pace.hide_gap
            and len(both) <= pace.max_words
            and len(_text(both)) <= pace.max_chars
        )

    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b[-1]["t1"] - b[0]["t0"] >= pace.min_s:
            i += 1
            continue
        options = []
        if i > 0 and fits(blocks[i - 1], b):
            options.append((b[0]["t0"] - blocks[i - 1][-1]["t1"], i - 1))
        if i + 1 < len(blocks) and fits(b, blocks[i + 1]):
            options.append((blocks[i + 1][0]["t0"] - b[-1]["t1"], i))
        if not options:
            # No neighbour takes it whole: share the words with the closer one instead.
            for j in sorted(
                (k for k in (i - 1, i) if 0 <= k and k + 1 < len(blocks)),
                key=lambda k: blocks[k + 1][0]["t0"] - blocks[k][-1]["t1"],
            ):
                pair = _rebalance(blocks[j], blocks[j + 1], pace)
                if pair:
                    blocks[j : j + 2] = pair
                    break
            i += 1
            continue
        j = min(options)[1]
        blocks[j : j + 2] = [blocks[j] + blocks[j + 1]]
        i = j  # the merged block may still be short
    return blocks


def _rebalance(a: list[dict], b: list[dict], pace: Pace) -> list[list[dict]] | None:
    """Two neighbouring blocks re-split so the shorter one lasts as long as possible (both
    fitting), at a comma or short pause if that costs nothing, else as evenly as possible;
    None when no split beats the current one."""
    if b[0]["t0"] - a[-1]["t1"] >= pace.hide_gap:
        return None

    def dur(x):
        return x[-1]["t1"] - x[0]["t0"]

    both = a + b
    best, now = None, min(dur(a), dur(b))
    for i in range(1, len(both)):
        left, right = both[:i], both[i:]
        if not all(
            len(x) <= pace.max_words and len(_text(x)) <= pace.max_chars for x in (left, right)
        ):
            continue
        shortest = min(dur(left), dur(right), pace.min_s)
        soft = BREAK_AFTER.search(left[-1]["w"]) or right[0]["t0"] - left[-1]["t1"] >= PAUSE_S
        key = (-round(shortest, 3), not soft, abs(len(_text(left)) - len(_text(right))))
        if shortest > now + 1e-9 and (best is None or key < best[0]):
            best = (key, [left, right])
    return best[1] if best else None


def group(words: list[dict], duration: float, pace: str = "fast") -> list[dict]:
    """Caption blocks (see module docstring) of output-time words at `pace`."""
    p = PACES[pace]
    blocks = _split(words, p)
    if not p.every_break:
        blocks = _merge_short(blocks, p)
    out = []
    for i, g in enumerate(blocks):
        t0, t1 = g[0]["t0"], g[-1]["t1"]
        nxt = blocks[i + 1][0]["t0"] if i + 1 < len(blocks) else duration
        if nxt - t1 < p.hide_gap:
            t1 = nxt  # hold until the next block
        t1 = min(max(t1, t0 + p.min_s), max(nxt, t1), duration)
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


def job_pace(job: Path) -> str:
    """captions.pace of the job's brand (DEFAULT_PACE without a brand or an unknown one)."""
    ref = read_job(job).get("brand")
    if not isinstance(ref, str) or not ref:
        return DEFAULT_PACE
    try:
        pace = brand.load(ref).get("captions", {}).get("pace")
    except brand.BrandError as e:
        raise ToolError(str(e)) from None
    return pace if pace in PACES else DEFAULT_PACE


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
    pace = job_pace(job)
    groups = group(words, duration, pace)
    data["captions"] = groups
    if errors := validate(data):
        raise ToolError("captions would make edl.json invalid: " + "; ".join(errors[:5]))
    write_json(edl_path, data)
    n_emph = sum(w["emph"] for g in groups for w in g["words"])
    print(f"{len(groups)} caption groups ({pace}), {len(words)} words ({n_emph} emphasised)")
    return {
        "job": str(job),
        "written": True,
        "file": "work/edl.json",
        "groups": len(groups),
        "pace": pace,
        "words": len(words),
        "emph": n_emph,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "captions", help="caption groups from words.json -> work/edl.json", parents=[job_parent()]
    )
    p.add_argument("--force", action="store_true", help="replace captions already in the EDL")
    p.set_defaults(handler=run_tool("captions", captions))
