"""Pause and filler candidates from word timestamps plus the audio itself (numpy only).

Whisper-style models tend to drop filler sounds ("äh", "ähm") from the transcript, so the
words alone miss most of them. This module looks for voice where no word is:

- pauses:     gaps between words (and before the first / after the last word) >= PAUSE_S;
- word:       transcribed words that are fillers (German/English list), or very short
              words standing alone between pauses (a filler the model wrote as "Im.");
- gap_energy: stretches inside those gaps (or inside a suspiciously long word) whose level
              stays above an adaptive noise floor for >= MIN_VOICE_S and that are mostly
              voiced (periodic, as vowels are; breaths and clicks are not).

A gap_energy candidate has `text` null when it lies in a gap, or the word's text when the
voice was found inside an overlong word (the filler was swallowed by the word's timestamps).
"""

import re
import wave
from pathlib import Path

import numpy as np

PAUSE_S = 0.3
MIN_VOICE_S = 0.12
HOP_S = 0.01  # frame step for level/voicing
EDGE_S = 0.03  # ignore this much next to a word: timestamps are not sample-exact
VOICED_MIN = 0.45  # normalised autocorrelation peak that counts as voiced
VOICED_FRACTION = 0.4  # share of voiced frames a candidate run needs
F0_RANGE = (70.0, 400.0)
SPILL_FACTOR = 0.4  # score multiplier for voice that touches a neighbouring word
ISOLATED_GAP_S = 0.25  # pause on both sides that makes a short word a filler suspect
ISOLATED_SCORE = 0.4
ANALYSIS_RATE = 16000

# Normalised forms (lowercase, punctuation stripped, repeated letters collapsed).
FILLERS = {
    # German
    "äh": 0.95, "ähm": 0.95, "äm": 0.9, "öh": 0.9, "öhm": 0.9, "eh": 0.7, "ehm": 0.9,
    "hm": 0.6, "mhm": 0.5, "hmhm": 0.5,
    # English
    "uh": 0.95, "um": 0.95, "uhm": 0.95, "erm": 0.9, "m": 0.5,
}  # fmt: skip


def normalise(word: str) -> str:
    w = re.sub(r"[^\w]", "", word.lower())
    return re.sub(r"(.)\1+", r"\1", w)


# Real words in German ("um zu", "eh schon"): fillers only in English recordings. Elsewhere
# they still count when they stand alone between pauses (see _isolated_short).
ENGLISH_ONLY = {"um", "eh"}


def filler_score(word: str, language: str | None = "de") -> float | None:
    w = normalise(word)
    if w in ENGLISH_ONLY and not (language or "").startswith("en"):
        return None
    return FILLERS.get(w)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Mono float32 samples in [-1, 1] and the sample rate (16-bit PCM WAV)."""
    with wave.open(str(path), "rb") as f:
        rate, ch, width = f.getframerate(), f.getnchannels(), f.getsampwidth()
        raw = f.readframes(f.getnframes())
    if width != 2:
        raise ValueError(f"{path.name}: expected 16-bit PCM, got {8 * width}-bit")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, rate


class Frames:
    """Per-frame level (dB) and lazily computed voicing for one recording."""

    def __init__(self, x: np.ndarray, rate: int):
        self.rate = rate
        hop = max(1, int(round(HOP_S * rate)))
        n = len(x) // hop
        self.n = n
        frames = x[: n * hop].reshape(n, hop) if n else np.zeros((0, hop), np.float32)
        rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) if n else np.zeros(0)
        self.db = 20 * np.log10(np.maximum(rms, 1e-5))  # floor at -100 dB
        # Crude 16 kHz copy for voicing: box filter then decimate (enough for F0 < 400 Hz).
        step = max(1, rate // ANALYSIS_RATE)
        if step > 1:
            m = len(x) // step
            x = x[: m * step].reshape(m, step).mean(axis=1)
        self._x16, self._rate16 = x.astype(np.float32), rate / step
        self._voicing: dict[int, float] = {}
        if n:
            self.noise = float(np.percentile(self.db, 10))
            self.speech = float(np.percentile(self.db, 95))
        else:
            self.noise = self.speech = -100.0
        # Adaptive threshold between the recording's own noise floor and speech level.
        self.threshold = self.noise + max(8.0, 0.3 * (self.speech - self.noise))

    def index(self, t: float) -> int:
        return int(min(max(round(t / HOP_S), 0), self.n))

    def voicing(self, i: int) -> float:
        """Peak of the normalised autocorrelation in the F0 lag range (0..1) for frame i."""
        if i in self._voicing:
            return self._voicing[i]
        r = self._rate16
        win = int(0.03 * r)  # 30 ms covers two periods at 70 Hz
        c = int((i + 0.5) * HOP_S * r)
        seg = self._x16[max(0, c - win // 2) : max(0, c - win // 2) + win].astype(np.float64)
        v = 0.0
        if len(seg) == win:
            seg = seg - seg.mean()
            nfft = 1 << (2 * win - 1).bit_length()
            spec = np.fft.rfft(seg, nfft)
            ac = np.fft.irfft(spec * np.conj(spec), nfft)[:win]
            if ac[0] > 1e-9:
                lo, hi = int(r / F0_RANGE[1]), min(int(r / F0_RANGE[0]), win - 1)
                # Unbiased normalisation, so long lags are not penalised by the window.
                lags = np.arange(lo, hi + 1)
                norm = ac[lags] / ac[0] * win / (win - lags)
                v = float(np.clip(norm.max(), 0.0, 1.0))
        self._voicing[i] = v
        return v

    def voice_runs(self, t0: float, t1: float) -> list[dict]:
        """Voiced stretches >= MIN_VOICE_S within [t0, t1]."""
        a, b = self.index(t0), self.index(t1)
        if b - a < int(MIN_VOICE_S / HOP_S):
            return []
        runs = []
        for s, e in _runs(self.db[a:b] > self.threshold, max_hole=2):  # bridge dips <= 20 ms
            if (e - s) * HOP_S >= MIN_VOICE_S:
                runs.append((a + s, a + e))
        out = []
        for s, e in runs:
            v = [self.voicing(i) for i in range(s, e)]
            frac = float(np.mean([x >= VOICED_MIN for x in v]))
            if frac < VOICED_FRACTION:
                continue  # breath, click, rustle
            level = float(np.mean(self.db[s:e]))
            span = max(self.speech - self.threshold, 1.0)
            score = (
                0.45 * min(1.0, (e - s) * HOP_S / 0.35)
                + 0.35 * frac
                + 0.2 * float(np.clip((level - self.threshold) / span, 0.0, 1.0))
            )
            out.append(
                {"t0": round(s * HOP_S, 3), "t1": round(e * HOP_S, 3), "score": round(score, 2)}
            )
        return out


def _runs(mask: np.ndarray, max_hole: int) -> list[tuple[int, int]]:
    """[start, end) index pairs of True stretches; holes of <= max_hole False frames are
    bridged."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    breaks = np.flatnonzero(np.diff(idx) > max_hole + 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]])) + 1
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def expected_word_s(word: str) -> float:
    """Rough upper-normal spoken duration of a word from its letters."""
    return 0.1 + 0.075 * len(normalise(word))


def find(words: list[dict], frames: Frames, duration: float, language: str | None = "de") -> dict:
    """candidates.json content for `words` (each {"w", "t0", "t1", ...}) on `frames`."""
    pauses, fillers = [], []
    # Gaps: start of file -> first word, between words, last word -> end of file.
    # None marks a file edge (no word there, so no timestamp slack to allow for).
    ends = [None] + [w["t1"] for w in words]
    starts = [w["t0"] for w in words] + [None]
    for prev_end, next_start in zip(ends, starts, strict=True):
        g0 = 0.0 if prev_end is None else prev_end
        g1 = duration if next_start is None else next_start
        if g1 - g0 >= PAUSE_S:
            pauses.append({"t0": round(g0, 3), "t1": round(g1, 3), "dur": round(g1 - g0, 3)})
        a = g0 + (0.0 if prev_end is None else EDGE_S)
        b = g1 - (0.0 if next_start is None else EDGE_S)
        for run in frames.voice_runs(a, b):
            # Voice running straight out of / into a word is usually that word's own tail
            # or onset (word stamps are often ~0.1-0.3 s short), not a separate filler.
            spill = (prev_end is not None and run["t0"] <= a + 1.5 * HOP_S) or (
                next_start is not None and run["t1"] >= b - 1.5 * HOP_S
            )
            score = round(run["score"] * (SPILL_FACTOR if spill else 1.0), 2)
            fillers.append({**run, "score": score, "kind": "gap_energy", "text": None})
    for k, w in enumerate(words):
        score = filler_score(w["w"], language)
        if score is None and _isolated_short(words, k):
            score = ISOLATED_SCORE
        if score is not None:
            fillers.append(
                {"t0": w["t0"], "t1": w["t1"], "kind": "word", "text": w["w"], "score": score}
            )
            continue
        dur, exp = w["t1"] - w["t0"], expected_word_s(w["w"])
        if dur < max(0.6, 2.0 * exp):
            continue
        # Overlong word: a filler may hide in its timestamps. Look for a separate voiced run
        # after the word proper (the usual case: "Also, äh" stamped as one long "Also,").
        runs = frames.voice_runs(w["t0"], w["t1"])
        if len(runs) >= 2:
            tail = runs[-1]
            score = round(0.8 * tail["score"], 2)
            fillers.append({**tail, "score": score, "kind": "gap_energy", "text": w["w"]})
        elif runs and runs[0]["t1"] - runs[0]["t0"] > 1.6 * exp:
            fillers.append(
                {"t0": w["t0"], "t1": w["t1"], "kind": "gap_energy", "text": w["w"], "score": 0.25}
            )
    fillers.sort(key=lambda f: f["t0"])
    return {"pauses": pauses, "fillers": fillers}


def _isolated_short(words: list[dict], k: int) -> bool:
    """A word of <= 3 letters standing alone between pauses: often a filler the model
    wrote as a real word ("Im.", "Em.")."""
    if not 0 < len(normalise(words[k]["w"])) <= 3:
        return False
    before = words[k]["t0"] - words[k - 1]["t1"] if k else ISOLATED_GAP_S
    after = words[k + 1]["t0"] - words[k]["t1"] if k + 1 < len(words) else ISOLATED_GAP_S
    return before >= ISOLATED_GAP_S and after >= ISOLATED_GAP_S
