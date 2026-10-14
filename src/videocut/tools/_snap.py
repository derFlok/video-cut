"""Acoustic snapping of EDL cut points (`videocut tool edl snap`, run by render too).

Word timestamps are estimates: mlx-whisper chains consecutive words (no gap between them)
and real word ends are often 40-330 ms after the transcript's t1. A cut placed at a word
edge therefore clips the word. Snapping moves every segment `in`/`out` to the nearest
acoustically quiet point (_audio_env) within the style's snap_window_s:

    bounds   an `out` never moves before the end of the last word the segment keeps
             (t1 - WORD_TOL_S) nor past the middle of the next word it drops; an `in` never
             moves after the start of the first kept word (t0 + WORD_TOL_S; with unaligned
             ASR times, which trail the speech, its middle) nor before the middle of the
             dropped word before it. Kept = the word's middle lies inside
             [in, out] (the original, unsnapped times).
    choice   the nearest point whose +-xfade/2 (at least +-20 ms) is quiet (room for the
             crossfade), else the nearest point quiet over +-20 ms, else the nearest point
             not in full speech; with none of these the edge stays (unresolved)

Two segments that continue each other in the source (out == next in) are a "join": the
audio runs on, so both edges stay. A segment with "speech_cut_ok" keeps its edges too.
The originals are kept as "in_src"/"out_src"; snapping again starts from them
(idempotent), unless the cut point was edited since (it no longer equals what snapping
in_src/out_src gives): then the edited value is the new original.

Legacy jobs (work/audio.json without "time_base": "video"): audio.wav and words.json start
at the audio stream's first sample, which may lie after the first video frame; the
envelope and the words are moved into video time (_audio_env.wav_offset), and so are the
unsnapped cut points, which were placed on those words (`author_offset`).
Source-time keys inside a segment (zoom_keys, screen_zoom, redact) that sat on a moved
edge follow it, the others are clamped into the segment.
"""

from dataclasses import dataclass

import numpy as np

from ._audio_env import HOP_S, NEAR_S, Envelope

# Until the cut-style module (_style.py) is in this branch: its suggested values.
_STYLES = {
    "calm": {"xfade_ms": 120, "snap_window_s": 0.35},
    "standard": {"xfade_ms": 80, "snap_window_s": 0.30},
    "tight": {"xfade_ms": 50, "snap_window_s": 0.25},
}


def cut_style(job: dict) -> dict:
    """The job's cut style (job.json dict): xfade_ms, snap_window_s, ..."""
    try:
        from . import _style
    except ImportError:
        name = job.get("cut_style")
        if name not in _STYLES:
            name = "calm" if job.get("screen") else "standard"
        return {"name": name, **_STYLES[name]}
    return {"name": _style.job_style(job), **_style.style(job)}


WORD_TOL_S = 0.02
JOIN_S = 0.001
MIN_SEGMENT_S = 0.1
EPS = 0.0005


@dataclass
class Edge:
    t: float
    tier: int  # 1 quiet with crossfade room, 2 quiet, 3 not in speech, 4 in speech
    level_db: float


def _mid(w: dict) -> float:
    return (w["t0"] + w["t1"]) / 2


def kept(words: list[dict], a: float, b: float) -> list[int]:
    """Indices of the words whose middle lies in [a, b]."""
    return [i for i, w in enumerate(words) if a <= _mid(w) <= b]


def joins(segs: list[dict]) -> list[bool]:
    """Per boundary between segment k and k+1: does k+1 continue k in the source?"""
    return [abs(b["in"] - a["out"]) <= JOIN_S for a, b in zip(segs, segs[1:], strict=False)]


def choose(env: Envelope, t: float, lo: float, hi: float, xhalf: float) -> Edge:
    """The best cut point in [lo, hi] for an edge at t (see module doc)."""
    lo, hi = min(lo, t), max(hi, t)
    n0, n1 = int(np.ceil(lo / HOP_S - 1e-9)), int(np.floor(hi / HOP_S + 1e-9))
    cands = sorted({round(t, 4), *(round(k * HOP_S, 4) for k in range(n0, n1 + 1))})
    cands = [c for c in cands if lo - 1e-9 <= c <= hi + 1e-9]
    wide = max(xhalf, NEAR_S)
    quiet, speech = env.quiet_db, env.speech_floor_db
    near = {c: env.level(c) for c in cands}
    tiers = []
    for c in cands:
        if near[c] <= quiet and env.level(c, wide) <= quiet:
            tiers.append(1)
        elif near[c] <= quiet:
            tiers.append(2)
        elif near[c] < speech:
            tiers.append(3)
        else:
            tiers.append(4)
    best = min(tiers)
    pool = [c for c, k in zip(cands, tiers, strict=True) if k == best]
    if best == 4:  # no point out of speech: a lower dip may lie inside a kept word, so stay
        c = round(t, 4)
    else:
        c = min(pool, key=lambda c: (abs(c - t), c))
    return Edge(c, best, round(near[c], 1))


def _bounds(edge: str, t: float, words: list[dict], keep: list[int], window: float, aligned: bool):
    lo, hi = t - window, t + window
    if edge == "out":
        if keep:
            last = keep[-1]
            lo = max(lo, min(t, words[last]["t1"] - WORD_TOL_S))
            if last + 1 < len(words):
                hi = min(hi, max(t, _mid(words[last + 1])))
        else:  # no word inside: never reach into a word around it
            nxt = [w for w in words if w["t0"] >= t]
            if nxt:
                hi = min(hi, max(t, _mid(nxt[0])))
    else:
        if keep:
            first = keep[0]
            w = words[first]
            # ASR start times trail the real ones less reliably than aligned ones
            hi = min(hi, max(t, w["t0"] + WORD_TOL_S if aligned else _mid(w)))
            if first > 0:
                lo = max(lo, min(t, _mid(words[first - 1])))
        else:
            prev = [w for w in words if w["t1"] <= t]
            if prev:
                lo = max(lo, min(t, _mid(prev[-1])))
    return lo, hi


def snap_edge(
    env: Envelope, edge: str, t: float, words: list[dict], keep: list[int], window: float,
    xhalf: float, limit: tuple[float, float], aligned: bool = True,
) -> Edge:  # fmt: skip
    lo, hi = _bounds(edge, t, words, keep, window, aligned)
    lo, hi = max(lo, limit[0]), min(hi, limit[1])
    if hi < lo:  # no room (a very short segment): stay
        return Edge(t, 4 if env.in_speech(t) else 3, round(env.level(t), 1))
    return choose(env, t, lo, hi, xhalf)


def _follow(v: float, old: tuple[float, float], new: tuple[float, float]) -> float:
    """A source time inside a segment after its edges moved from `old` to `new`."""
    if abs(v - old[0]) <= EPS:
        return new[0]
    if abs(v - old[1]) <= EPS:
        return new[1]
    return min(max(v, new[0]), new[1])


def _move_keys(seg: dict, old: tuple[float, float], new: tuple[float, float]) -> None:
    if old == new:
        return
    if isinstance(seg.get("zoom_keys"), list):
        seg["zoom_keys"] = [
            [round(_follow(k[0], old, new), 4), k[1]] if isinstance(k, list) and k else k
            for k in seg["zoom_keys"]
        ]
    if isinstance(seg.get("screen_zoom"), list):
        keys = []
        for k in seg["screen_zoom"]:
            if isinstance(k, dict) and isinstance(k.get("t"), int | float):
                k = {**k, "t": round(_follow(k["t"], old, new), 4)}
                if keys and isinstance(keys[-1], dict) and keys[-1].get("t") == k["t"]:
                    keys[-1] = k  # two keys clamped onto the same edge: the later one wins
                    continue
            keys.append(k)
        seg["screen_zoom"] = keys
    if isinstance(seg.get("redact"), list):
        out = []
        for r in seg["redact"]:
            if isinstance(r, dict) and all(isinstance(r.get(k), int | float) for k in ("t0", "t1")):
                t0, t1 = _follow(r["t0"], old, new), _follow(r["t1"], old, new)
                if r["t0"] <= old[0] + EPS:
                    t0 = new[0]  # a block from the start keeps covering the start
                if r["t1"] >= old[1] - EPS:
                    t1 = new[1]
                if t1 <= t0:
                    continue  # outside the segment now: nothing of it is shown
                r = {**r, "t0": round(t0, 4), "t1": round(t1, 4)}
            out.append(r)
        seg["redact"] = out


def snap(
    data: dict,
    env: Envelope,
    words: list[dict],
    window: float,
    xfade_s: float,
    source_duration: float | None = None,
    aligned: bool = True,
    author_offset: float = 0.0,
    overlap: tuple[float, float] | None = None,
) -> tuple[dict, dict]:
    """(snapped copy of the EDL dict, summary {moved, max_shift_ms, unresolved, edges}).

    `author_offset`: the unsnapped cut points were placed in a time base this much earlier
    than video time (a legacy job whose words.json is in audio-stream time); snapping
    starts from cut point + author_offset. in_src/out_src keep the numbers as written.
    `overlap`: work/sync.json "overlap"; a non-face segment never leaves it (or, when it
    already lies outside, never moves further out)."""
    segs = [dict(s) for s in data["segments"]]
    words = sorted(
        (w for w in words if isinstance(w.get("t0"), int | float) and
         isinstance(w.get("t1"), int | float)),
        key=lambda w: w["t0"],
    )  # fmt: skip
    end = min(source_duration or env.duration, env.duration)
    joined = joins(segs)
    off = author_offset
    if not env.has_speech:  # silent audio: nothing to snap to
        return {**data, "segments": segs}, {
            "moved": 0, "max_shift_ms": 0, "unresolved": [], "edges": 0
        }  # fmt: skip
    moved, max_shift, unresolved, edges = 0, 0.0, [], 0
    for i, s in enumerate(segs):
        old = (s["in"], s["out"])
        own = s.get("xfade_ms")
        xin = (own if own is not None else xfade_s * 1000) / 2000
        fixed = {"in": i > 0 and joined[i - 1], "out": i < len(segs) - 1 and joined[i]}
        if isinstance(s.get("speech_cut_ok"), str) and s["speech_cut_ok"].strip():
            fixed = {"in": True, "out": True}  # cut into speech on purpose: leave it
        res, srcs, tiers = {}, {}, {}
        for edge in ("in", "out"):
            cur = s[edge]
            if fixed[edge]:
                res[edge] = cur
                continue
            edges += 1
            base = s.get(f"{edge}_src", cur)
            # the original (as written) of both edges: from the last snap, or as they are
            orig = (s.get("in_src", s["in"]), s.get("out_src", s["out"]))
            for attempt, (a, b) in ((base, orig), (cur, old)):
                keep = kept(words, a + off, b + off)
                limit = (0.0, b + off - MIN_SEGMENT_S) if edge == "in" else (
                    a + off + MIN_SEGMENT_S, end)  # fmt: skip
                if overlap and s.get("layout", "face") not in ("face", None):
                    t = attempt + off
                    if edge == "in":
                        limit = (max(limit[0], min(t, overlap[0])), limit[1])
                    else:
                        limit = (limit[0], min(limit[1], max(t, overlap[1])))
                got = snap_edge(env, edge, attempt + off, words, keep, window, xin, limit, aligned)
                if attempt != cur and abs(got.t - cur) > EPS:
                    continue  # edited since the last snap: the edited value is the original
                break
            res[edge], srcs[edge], tiers[edge] = round(got.t, 3), attempt, got
        if res["out"] - res["in"] < MIN_SEGMENT_S:  # both edges squeezed it: keep the cut
            res, srcs, tiers = {"in": old[0], "out": old[1]}, {}, {}
        for edge, got in tiers.items():
            cur, src = s[edge], srcs[edge]
            if abs(res[edge] - cur) > EPS:  # moved by this run
                moved += 1
                max_shift = max(max_shift, abs(res[edge] - cur))
            if abs(res[edge] - src) > EPS or f"{edge}_src" in s:
                s[f"{edge}_src"] = round(src, 4)
            if got.tier == 4:
                unresolved.append(
                    {"segment": i, "edge": edge, "t": res[edge], "level_db": got.level_db}
                )
        s["in"], s["out"] = res["in"], res["out"]
        _move_keys(s, old, (s["in"], s["out"]))
    out = {**data, "segments": segs}
    summary = {
        "moved": moved,
        "max_shift_ms": round(max_shift * 1000),
        "unresolved": unresolved,
        "edges": edges,
    }
    return out, summary


def quiet_room(env: Envelope, t: float, limit: float) -> float:
    """How far (seconds, up to `limit`) the audio stays quiet on both sides of t: the
    crossfade at a cut point may be twice this long without fading speech."""
    h, step = 0.0, HOP_S / 2
    while h + step <= limit + 1e-9 and env.peak(t - h - step, t + h + step) <= env.quiet_db:
        h += step
    return h


def xfade_caps(data: dict, env: Envelope, limit: float) -> dict[int, float]:
    """Per segment k (> 0, not a join): the longest crossfade (seconds) the quiet room at
    segment k-1's out and segment k's in allows, at most 2 * limit."""
    segs = data["segments"]
    caps = {}
    for k, joined in enumerate(joins(segs), 1):
        if not joined:
            room = min(
                quiet_room(env, segs[k - 1]["out"], limit), quiet_room(env, segs[k]["in"], limit)
            )
            caps[k] = 2 * room
    return caps


def in_speech(data: dict, env: Envelope, source_duration: float | None = None) -> list[dict]:
    """Every audio cut point whose +-20 ms lies in full speech, unless the segment has
    "speech_cut_ok": "<reason>". Joins are no cuts, and neither are the very start and end
    of the source (nothing before or after them could be clipped)."""
    segs = data["segments"]
    if not env.has_speech:
        return []
    end = env.duration if source_duration is None else min(source_duration, env.duration)
    joined = joins(segs)
    out = []
    for i, s in enumerate(segs):
        ok = s.get("speech_cut_ok")
        if isinstance(ok, str) and ok.strip():
            continue
        for edge in ("in", "out"):
            if (edge == "in" and i > 0 and joined[i - 1]) or (
                edge == "out" and i < len(segs) - 1 and joined[i]
            ):
                continue
            if (edge == "in" and s[edge] <= JOIN_S) or (edge == "out" and s[edge] >= end - JOIN_S):
                continue
            lv = env.level(s[edge])
            if lv >= env.speech_floor_db:
                out.append({"segment": i, "edge": edge, "t": s[edge], "level_db": round(lv, 1)})
    return out
