"""`videocut tool align`: refine every word's t0/t1 against the audio, locally.

ASR word timestamps are estimates: mlx-whisper chains most words end to end (no gap even
where the speaker paused) and often ends a word 40-330 ms before the voice does, or
stretches it over a pause. Cuts placed at those times clip words. Alignment moves each word
boundary to the acoustic onset/offset next to it (numpy only, on work/audio.wav):

- speech/silence from a 10 ms level envelope against the recording's own noise floor
  (a hysteresis pair of thresholds, so quiet word tails still count as speech; denoised
  audio that is digitally silent between words is handled);
- every word boundary looks for a silence within ±WINDOW_S of its ASR time, followed to
  its full extent; the best explained boundary per silence becomes a pin (earlier word
  ends where the voice stops, the later one starts where it resumes, so a word stretched
  over a pause shrinks back to its voice). Measured on mlx-whisper: its word ends come
  early, so a pause after the ASR boundary scores higher than one before it;
- chained words between two pins move with the nearer pin (the shift fades out over
  FOLLOW_S), then each boundary moves to a clear level valley within ±VALLEY_S;
- words with no voice near them (hallucinations in silence) keep their ASR times.

Measured against parakeet-tdt-0.6b-v3 as aligner (its token times sit on an 80 ms grid
and are chained as well): worse at every boundary, so it is not used here.

Result in work/words.json: t0/t1 are the aligned times, the ASR times stay in t0_asr/t1_asr
(alignment always starts from those, so re-running is idempotent). work/transcribe.json
gets "align": {"method", "version", "median_shift_ms", ...}.

align [--no-candidates]
    re-aligns an existing job without re-transcribing, then refreshes candidates.
"""

import json
import time
from pathlib import Path

import numpy as np

from . import _transcribe_energy as energy
from .common import ToolError, job_parent, resolve_job, run_tool, write_json

METHOD = "energy"
VERSION = 1
HOP_S = 0.01
WINDOW_S = 0.30  # max distance from the ASR time to a silence that may take the boundary
VALLEY_S = 0.10  # search radius for an energy valley between chained words
VALLEY_DB = 6.0  # a valley must be this much below the level at the ASR boundary
LATE_COST, EARLY_COST = 0.6, 1.2  # score per second between an ASR boundary and a pause
FOLLOW_S = 0.4  # how far (ASR time) a pin's shift carries into the chained words
VOICE_NEAR_S = 0.15  # a word with no voice this close to its ASR span is not aligned
JOINT_GAP_S = 2 * WINDOW_S  # ASR gaps shorter than this are one boundary, not two edges
MIN_GAP_S = 0.06  # shorter dips are stop closures inside a word, not a pause
MIN_WORD_S = 0.06  # a word never shrinks below this
MIN_RUN_S = 0.02  # quiet blips shorter than this are clicks
CLOSURE_S = 0.08  # max stop closure between a vowel and its quiet release burst
DIGITAL_SILENCE_DB = -95.0
MIN_SPEECH_OVER_NOISE_DB = 25.0
HIGH_BELOW_SPEECH_DB = 22.0  # a run counts as speech once it comes this close to speech level
LOW_BELOW_SPEECH_DB = 45.0  # ...and lasts while it stays above this


class Envelope:
    """10 ms level envelope of a recording with a speech/silence decision per frame."""

    def __init__(self, x: np.ndarray, rate: int):
        hop = max(1, int(round(HOP_S * rate)))
        n = len(x) // hop
        self.n = n
        if n:
            frames = x[: n * hop].reshape(n, hop).astype(np.float64)
            # 20 ms window per 10 ms hop (current + previous frame) steadies the level.
            p = np.mean(frames**2, axis=1)
            p = np.maximum(p, np.concatenate(([p[0]], p[:-1])) * 0.5 + p * 0.5)
            db = 10 * np.log10(np.maximum(p, 1e-10))
        else:
            db = np.zeros(0)
        self.db = db
        # Denoised audio is digitally silent between words; the noise floor is taken from
        # the frames that carry any signal at all.
        live = db[db > DIGITAL_SILENCE_DB] if n else db
        live = live if len(live) else db
        self.speech = float(np.percentile(db, 95)) if n else -100.0
        # Capped, so a recording that is nearly all speech still gets a usable floor.
        floor = float(np.percentile(live, 10)) if n else -100.0
        self.noise = min(floor, self.speech - MIN_SPEECH_OVER_NOISE_DB)
        span = max(self.speech - self.noise, 1.0)
        # Hysteresis: speech starts above `high` (a vowel near the speech level, not a
        # breath), continues down to `low` (decaying word tails, quiet fricatives).
        self.high = max(self.noise + max(9.0, 0.3 * span), self.speech - HIGH_BELOW_SPEECH_DB)
        self.low = max(self.noise + max(5.0, 0.15 * span), self.speech - LOW_BELOW_SPEECH_DB)
        self.low = min(self.low, self.high)
        self.voiced = self._mask()

    def _mask(self) -> np.ndarray:
        db, n = self.db, self.n
        mask = np.zeros(n, bool)
        on = db > self.high
        above_low = db > self.low
        if not on.any():
            return mask
        # Runs above `low`; a run that reaches `high` is speech (grown down to `low`).
        starts = np.flatnonzero(above_low & ~np.concatenate(([False], above_low[:-1])))
        ends = np.flatnonzero(above_low & ~np.concatenate((above_low[1:], [False]))) + 1
        csum = np.concatenate(([0], np.cumsum(on)))
        core = [(s, e) for s, e in zip(starts, ends, strict=True) if csum[e] - csum[s] > 0]
        for s, e in core:
            mask[s:e] = True
        # A quiet run right next to speech is part of the word: the release burst of a
        # final stop after its closure ("gebaut"), a fricative onset before the vowel.
        near = int(round(CLOSURE_S / HOP_S))
        for s, e in zip(starts, ends, strict=True):
            if mask[s] or (e - s) * HOP_S < MIN_RUN_S:
                continue
            if mask[max(s - near, 0) : s].any() or mask[e : e + near].any():
                mask[s:e] = True
        return mask

    def index(self, t: float) -> int:
        return int(min(max(round(t / HOP_S), 0), self.n))

    def silences(self, t0: float, t1: float) -> list[tuple[float, float]]:
        """Full extent of every silence >= MIN_GAP_S that overlaps [t0, t1]."""
        a, b = self.index(t0), self.index(t1)
        if b <= a:
            return []
        out = []
        i = a
        while i < b:
            if self.voiced[i]:
                i += 1
                continue
            s = i
            while s > 0 and not self.voiced[s - 1]:
                s -= 1
            e = i
            while e < self.n and not self.voiced[e]:
                e += 1
            if (e - s) * HOP_S >= MIN_GAP_S:
                out.append((s * HOP_S, e * HOP_S))
            i = e
        return out

    def level(self, t: float) -> float:
        i = min(self.index(t), self.n - 1)
        return float(self.db[max(i, 0)]) if self.n else -100.0

    def valley(self, t0: float, t1: float) -> tuple[float, float] | None:
        """Time and level of the quietest point in [t0, t1] (3-frame smoothed)."""
        a, b = self.index(t0), self.index(t1)
        if b - a < 1:
            return None
        lo, hi = max(a - 1, 0), min(b + 1, self.n)
        seg = self.db[lo:hi]
        if len(seg) < 3:
            return None
        sm = np.convolve(seg, np.ones(3) / 3, mode="same")
        k = int(np.argmin(sm[a - lo : b - lo])) + a - lo
        return (lo + k) * HOP_S + HOP_S / 2, float(sm[k])


def _silence(
    env: Envelope, a: float, b: float, lo: float, hi: float, duration: float
) -> tuple[float, float, float] | None:
    """The silence (clipped to [lo, hi]) that best explains an ASR boundary [a, b]: one
    within WINDOW_S of it, followed to its full extent; long and near wins. Returns
    (start, end, score)."""
    gaps = [
        (max(g0, lo), min(g1, hi))
        for g0, g1 in env.silences(max(a - WINDOW_S, lo), min(b + WINDOW_S, hi))
    ]
    # A file edge counts as silence however short the clipped piece is.
    gaps = [g for g in gaps if g[1] - g[0] >= MIN_GAP_S or g[0] <= 0 or g[1] >= duration]
    best = None
    for g0, g1 in gaps:
        # Measured: ASR word ends come early (typ. 120 ms), so the pause after an ASR
        # boundary is likelier than one before it.
        score = min(g1 - g0, 0.3) - LATE_COST * max(0.0, g0 - b) - EARLY_COST * max(0.0, a - g1)
        if best is None or score > best[2]:
            best = (g0, g1, score)
    return best


def _anchors(
    asr: list[tuple[float, float]], env: Envelope, duration: float
) -> dict[int, tuple[float, float]]:
    """Boundaries pinned to silence: {k: (end of word k-1, start of word k)}.

    Boundary k sits between word k-1 and word k; 0 and n are the file edges, always
    pinned (to the silence found there, else the ASR time), like a long ASR gap whose
    two sides find their own silences. A chained boundary is pinned only when a silence
    lies within WINDOW_S; where several claim the same stretch, the best explained wins,
    and every pin leaves the words between pins at least MIN_WORD_S each.
    """
    n = len(asr)
    fixed: dict[int, tuple[float, float]] = {}
    optional: list[tuple[float, int, tuple[float, float]]] = []
    for k in range(n + 1):
        a = asr[k - 1][1] if k else None  # ASR end of the earlier word
        b = asr[k][0] if k < n else None  # ASR start of the later word
        lo = asr[k - 1][0] + MIN_WORD_S if k else 0.0
        # The later word's end may itself still move up to WINDOW_S later.
        hi = min(asr[k][1] + WINDOW_S - MIN_WORD_S, duration) if k < n else duration
        if hi <= lo:
            continue
        if a is not None and b is not None and b - a < JOINT_GAP_S:
            gap = _silence(env, min(a, b), max(a, b), lo, hi, duration)
            if gap is not None:
                optional.append((gap[2], k, (gap[0], gap[1])))
            continue
        # A long ASR gap or a file edge: each side looks for its own silence.
        end, start = a, b
        if a is not None:
            gap = _silence(env, a, a, lo, b if b is not None else duration, duration)
            end = gap[0] if gap else a
        if b is not None:
            gap = _silence(env, b, b, a if a is not None else 0.0, hi, duration)
            start = gap[1] if gap else b
        end = start if end is None else end
        start = end if start is None else start
        fixed[k] = (min(end, start), max(end, start))

    def fits(k: int, pin: tuple[float, float], pins: dict) -> bool:
        before = [j for j in pins if j < k]
        after = [j for j in pins if j > k]
        if before:
            j = max(before)
            if pin[0] < pins[j][1] + MIN_WORD_S * (k - j) - 1e-9:
                return False
        if after:
            j = min(after)
            if pins[j][0] < pin[1] + MIN_WORD_S * (j - k) - 1e-9:
                return False
        return True

    pins: dict[int, tuple[float, float]] = {}
    for k, pin in sorted(fixed.items()):
        pins[k] = pin if fits(k, pin, pins) else (asr[k - 1][1] if k else asr[0][0],) * 2
    for _, k, pin in sorted(optional, key=lambda o: -o[0]):
        if fits(k, pin, pins):
            pins[k] = pin
    # The file edges are always pinned (the run structure needs them).
    pins.setdefault(0, (asr[0][0],) * 2)
    pins.setdefault(n, (asr[-1][1],) * 2)
    return pins


def _align_times(
    asr: list[tuple[float, float]], env: Envelope, duration: float
) -> tuple[list[float], list[float]]:
    n = len(asr)
    t0, t1 = [0.0] * n, [0.0] * n
    if not n:
        return t0, t1
    pins = _anchors(asr, env, duration)
    ks = sorted(pins)
    # Words between two pins move with the nearer pin: the pin's shift fades out with
    # ASR distance (FOLLOW_S), so a phrase the model placed early moves as a whole while
    # words far from any pause keep their ASR times.
    for p, q in zip(ks, ks[1:], strict=False):
        s0, s1 = asr[p][0], asr[q - 1][1]
        T0, T1 = pins[p][1], pins[q][0]
        d0, d1 = T0 - s0, T1 - s1
        t0[p], t1[q - 1] = T0, T1
        for j in range(p + 1, q):
            u = (asr[j - 1][1] + asr[j][0]) / 2
            m = u + d0 * np.exp(-max(u - s0, 0) / FOLLOW_S)
            m += d1 * np.exp(-max(s1 - u, 0) / FOLLOW_S)
            # Room for MIN_WORD_S per word on both sides.
            m = min(max(m, T0 + MIN_WORD_S * (j - p)), T1 - MIN_WORD_S * (q - j))
            t1[j - 1] = t0[j] = max(m, t0[j - 1] + MIN_WORD_S)
        # Chained words in continuous speech: split at a clear level valley nearby; each
        # word keeps at least a third of itself on either side.
        bounds = [T0] + t0[p + 1 : q] + [T1]
        for j in range(p + 1, q):
            prev, c, nxt = bounds[j - p - 1], bounds[j - p], bounds[j - p + 1]
            vlo = max(c - VALLEY_S, c - (c - prev) / 3)
            vhi = min(c + VALLEY_S, c + (nxt - c) / 3)
            v = env.valley(vlo, vhi) if vhi - vlo >= 2 * HOP_S else None
            if v is not None and v[1] < env.level(c) - VALLEY_DB:
                t1[j - 1] = t0[j] = v[0]
    return t0, t1


def align_words(words: list[dict], env: Envelope, duration: float) -> list[dict]:
    """Aligned copies of `words`; ASR times are read from t0_asr/t1_asr when present.

    Words with no voice anywhere near their ASR span (a model hallucination in silence,
    typically) are left out of the alignment and keep their ASR times, squeezed into
    the room their aligned neighbours leave.
    """
    asr = [(float(w.get("t0_asr", w["t0"])), float(w.get("t1_asr", w["t1"]))) for w in words]
    n = len(asr)
    voiced = [
        bool(env.voiced[env.index(a - VOICE_NEAR_S) : env.index(b + VOICE_NEAR_S) + 1].any())
        for a, b in asr
    ]
    idx = [i for i in range(n) if voiced[i]]
    a0, a1 = _align_times([asr[i] for i in idx], env, duration)
    t0 = [a for a, _ in asr]
    t1 = [b for _, b in asr]
    for i, x, y in zip(idx, a0, a1, strict=True):
        t0[i], t1[i] = x, y
    # Words stay in order and never overlap (aligned words win over unvoiced ones).
    for i in range(n):
        if not voiced[i]:
            later = [t0[j] for j in idx if j > i]
            t0[i] = min(t0[i], later[0]) if later else t0[i]
        t0[i] = min(max(t0[i], t0[i - 1] if i else 0.0), duration)
    for i in range(n):
        t1[i] = min(max(t1[i], t0[i]), t0[i + 1] if i + 1 < n else duration)
    out = []
    for w, (x0, x1), y0, y1 in zip(words, asr, t0, t1, strict=True):
        d = {k: v for k, v in w.items() if k not in ("w", "t0", "t1", "t0_asr", "t1_asr")}
        out.append(
            {
                "w": w["w"],
                "t0": round(float(y0), 3),
                "t1": round(float(y1), 3),
                **d,
                "t0_asr": round(x0, 3),
                "t1_asr": round(x1, 3),
            }
        )
    return out


def shift_stats(words: list[dict]) -> dict:
    shifts = [
        abs(w[k] - w[f"{k}_asr"]) * 1000 for w in words for k in ("t0", "t1") if f"{k}_asr" in w
    ]
    pairs = list(zip(words, words[1:], strict=False))
    chained = sum(1 for p, q in pairs if q["t0"] - p["t1"] <= 0.0005)
    return {
        "median_shift_ms": round(float(np.median(shifts)), 1) if shifts else 0.0,
        "p90_shift_ms": round(float(np.percentile(shifts, 90)), 1) if shifts else 0.0,
        "zero_gap_share": round(chained / len(pairs), 3) if pairs else 0.0,
    }


def strip(words: list[dict]) -> list[dict]:
    """Words with the ASR times restored as t0/t1 (undo an alignment)."""
    out = []
    for w in words:
        d = dict(w)
        if "t0_asr" in d:
            d["t0"], d["t1"] = d.pop("t0_asr"), d.pop("t1_asr", d["t1"])
        out.append(d)
    return out


def align_work(work: Path) -> dict:
    """Align work/words.json in place and record it in work/transcribe.json."""
    words_file, meta_file = work / "words.json", work / "transcribe.json"
    if not words_file.is_file():
        raise ToolError("work/words.json is missing; run `videocut tool transcribe` first")
    started = time.monotonic()
    doc = json.loads(words_file.read_text())
    x, rate = energy.read_wav(work / "audio.wav")
    duration = float(doc.get("duration") or len(x) / rate)
    env = Envelope(x, rate)
    doc["words"] = align_words(doc["words"], env, duration)
    stats = shift_stats(doc["words"])
    info = {
        "method": METHOD,
        "version": VERSION,
        "median_shift_ms": stats["median_shift_ms"],
        "p90_shift_ms": stats["p90_shift_ms"],
        "zero_gap_share": stats["zero_gap_share"],
        "seconds": round(time.monotonic() - started, 2),
    }
    write_json(words_file, doc)
    try:
        meta = json.loads(meta_file.read_text())
    except (OSError, ValueError):
        meta = {}
    meta["align"] = info
    write_json(meta_file, meta)
    return info


def align(args) -> dict:
    from .transcribe import _work, make_candidates

    job = resolve_job(args.job)
    work = _work(job)
    info = align_work(work)
    print(
        f"aligned words: median shift {info['median_shift_ms']} ms, "
        f"zero-gap pairs {info['zero_gap_share']:.0%}"
    )
    fields = {"job": str(job), **info}
    if not args.no_candidates:
        fields.update(make_candidates(work))
    return fields


def register(subparsers) -> None:
    a = subparsers.add_parser(
        "align",
        help="refine word times in work/words.json against the audio (no re-transcription)",
        parents=[job_parent()],
    )
    a.add_argument(
        "--no-candidates", action="store_true", help="do not refresh candidates.json afterwards"
    )
    a.set_defaults(handler=run_tool("align", align))
