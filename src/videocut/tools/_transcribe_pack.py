"""work/takes_packed.md: the phrase-level transcript Claude reads in full before cutting.

Format adapted from browser-use/video-use `helpers/pack_transcripts.py` (MIT, see
THIRD_PARTY_NOTICES.md): words grouped into phrases, one line per phrase with a fixed-width
`[start-end]` source-time prefix, so a few minutes of speech stay around 12 KB and every cut
can be addressed at word boundaries from text alone. Additions: inline pause markers,
filler candidate marks (from candidates.json) and a retake hint.
"""

from difflib import SequenceMatcher

from ._transcribe_energy import PAUSE_S, normalise

PHRASE_BREAK_S = 0.5  # as upstream: a silence this long starts a new phrase
SENTENCE_MIN_WORDS = 4  # also break after . ! ? once a phrase has this many words
RETAKE_SIMILARITY = 0.6
RETAKE_MIN_WORDS = 3
RETAKE_LOOKBACK = 2  # compare a phrase with this many phrases before it


def fmt_time(s: float) -> str:
    return f"{s:06.2f}"


def fmt_duration(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    return f"{m}m {s - 60 * m:04.1f}s"


def phrases(words: list[dict]) -> list[list[int]]:
    """Word indices grouped into phrases."""
    out: list[list[int]] = []
    for i, w in enumerate(words):
        if out:
            prev = words[out[-1][-1]]
            gap = w["t0"] - prev["t1"]
            ends_sentence = prev["w"].rstrip("\"')»”").endswith((".", "!", "?"))
            if gap < PHRASE_BREAK_S and not (ends_sentence and len(out[-1]) >= SENTENCE_MIN_WORDS):
                out[-1].append(i)
                continue
        out.append([i])
    return out


def _tokens(words: list[dict], idx: list[int], filler_idx: set[int]) -> list[str]:
    return [t for i in idx if i not in filler_idx and (t := normalise(words[i]["w"]))]


def retakes(words: list[dict], groups: list[list[int]], filler_idx: set[int]) -> dict[int, int]:
    """phrase index -> earlier phrase it probably repeats (a restart of the same sentence)."""
    toks = [_tokens(words, g, filler_idx) for g in groups]
    found = {}
    for j in range(1, len(groups)):
        for i in range(j - 1, max(-1, j - 1 - RETAKE_LOOKBACK), -1):
            a, b = toks[i], toks[j]
            if min(len(a), len(b)) < RETAKE_MIN_WORDS:
                continue
            m = SequenceMatcher(None, a, b, autojunk=False)
            same_start = m.find_longest_match(0, len(a), 0, len(b))
            restart = (
                same_start.a == 0
                and same_start.b == 0
                and same_start.size >= RETAKE_MIN_WORDS
                and same_start.size >= 0.8 * min(len(a), len(b))
            )
            similar = min(len(a), len(b)) > RETAKE_MIN_WORDS and m.ratio() >= RETAKE_SIMILARITY
            if similar or restart:
                found[j] = i
                break
    return found


def pack(words_doc: dict, candidates: dict | None = None) -> str:
    words = words_doc["words"]
    fillers = (candidates or {}).get("fillers", [])
    # Word-kind fillers mark the word itself; gap_energy ones become markers by time.
    filler_idx: set[int] = set()
    by_span = {(f["t0"], f["t1"], f["text"]) for f in fillers if f["kind"] == "word"}
    for i, w in enumerate(words):
        if (w["t0"], w["t1"], w["w"]) in by_span:
            filler_idx.add(i)
    voice = sorted((f for f in fillers if f["kind"] == "gap_energy"), key=lambda f: f["t0"])

    def voice_mark(f: dict) -> str:
        return f"{{~{f['t1'] - f['t0']:.2f}s {f['score']:.2f}}}"

    groups = phrases(words)
    again = retakes(words, groups, filler_idx)
    lines = [
        "# Packed transcript",
        "",
        f"model {words_doc.get('model')} · language {words_doc.get('language')} · "
        f"{fmt_duration(words_doc.get('duration', 0.0))} · {len(words)} words · "
        f"{len(groups)} phrases",
        "",
        f"One line per phrase (new phrase after a silence >= {PHRASE_BREAK_S:.1f} s or a "
        "sentence end), `[start-end]` in source seconds.",
        f"`<0.42>` pause >= {PAUSE_S:.1f} s · `{{äh}}` filler word · "
        "`{~0.25s 0.71}` voice without a word (likely a dropped filler: length, score) · "
        "`^ possible retake` = same sentence again (last complete take wins).",
    ]
    if candidates is None:
        lines.append("Filler marks: none yet (run `videocut tool candidates`).")
    lines.append("")

    vi = 0

    def marks_before(t: float) -> list[str]:
        nonlocal vi
        out = []
        while vi < len(voice) and voice[vi]["t0"] < t:
            out.append(voice_mark(voice[vi]))
            vi += 1
        return out

    prev_end = 0.0
    for p, g in enumerate(groups):
        first = words[g[0]]
        lead = marks_before(first["t0"])
        gap = first["t0"] - prev_end
        if lead or gap >= PAUSE_S:
            lines.append("  " + " ".join(([f"<{gap:.2f}>"] if gap >= PAUSE_S else []) + lead))
        parts = []
        for k, i in enumerate(g):
            w = words[i]
            if k:
                inner_gap = w["t0"] - words[g[k - 1]]["t1"]
                if inner_gap >= PAUSE_S:
                    parts.append(f"<{inner_gap:.2f}>")
                parts.extend(marks_before(w["t0"]))
            parts.append(f"{{{w['w']}}}" if i in filler_idx else w["w"])
            # Voice inside this word's own span (filler swallowed by an overlong word).
            parts.extend(marks_before(w["t1"]))
        end = words[g[-1]]["t1"]
        lines.append(f"[{fmt_time(first['t0'])}-{fmt_time(end)}] " + " ".join(parts))
        if p in again:
            q = groups[again[p]]
            lines.append(
                f"  ^ possible retake of [{fmt_time(words[q[0]]['t0'])}-"
                f"{fmt_time(words[q[-1]]['t1'])}] (last complete take wins)"
            )
        prev_end = end
    tail = marks_before(float("inf"))
    gap = words_doc.get("duration", prev_end) - prev_end
    if tail or gap >= PAUSE_S:
        lines.append("  " + " ".join(([f"<{gap:.2f}>"] if gap >= PAUSE_S else []) + tail))
    if not words:
        lines.append("_no speech detected_")
    return "\n".join(lines) + "\n"
