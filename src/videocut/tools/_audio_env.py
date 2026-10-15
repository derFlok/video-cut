"""Energy envelope of a job's audio (numpy only): where speech is and where it is quiet.

The envelope is the mean power of every 10 ms block (HOP_S) of work/audio.wav (mono or the
mean of its channels); a level is the RMS in dBFS over a stretch of blocks. Two levels are
estimated per job from 20 ms windows (WIN_S); a denoised recording has digital silence in
its pauses, so windows below SILENT_DB are left out before taking percentiles:

    noise_db   the NOISE_PCT percentile of the non-silent windows (room tone, breaths)
    speech_db  the median of the windows at least SPEECH_ABOVE_DB above noise_db

A point is "in speech" when its level (RMS over +-NEAR_S) is within IN_SPEECH_DB of
speech_db; it is "quiet" at or below quiet_db (noise_db + QUIET_ABOVE_DB, at most
speech_db - QUIET_BELOW_SPEECH_DB, at least speech_db - QUIET_FLOOR_DB).
"""

import json
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HOP_S = 0.01
WIN_S = 0.02  # two blocks
SILENT_DB = -90.0  # digital silence (denoised pauses)
NOISE_PCT = 10
SPEECH_ABOVE_DB = 15.0
IN_SPEECH_DB = 12.0  # within this of the speech median = full speech (cut_in_speech)
NEAR_S = 0.02  # +-20 ms around a cut point
QUIET_ABOVE_DB = 8.0
QUIET_BELOW_SPEECH_DB = 25.0
QUIET_FLOOR_DB = 45.0
FLOOR_DB = -120.0


@dataclass
class Envelope:
    power: np.ndarray  # mean power of each HOP_S block; block i is [i * HOP_S, (i+1) * HOP_S)
    noise_db: float
    speech_db: float
    duration: float  # source seconds covered (offset included)
    offset: float = 0.0  # source time of the first sample (audio stream start - video start)

    def __post_init__(self):
        self._cum = np.concatenate([[0.0], np.cumsum(self.power, dtype=np.float64)])

    @property
    def quiet_db(self) -> float:
        q = min(self.noise_db + QUIET_ABOVE_DB, self.speech_db - QUIET_BELOW_SPEECH_DB)
        return max(q, self.speech_db - QUIET_FLOOR_DB)

    @property
    def speech_floor_db(self) -> float:
        """At or above this level a point is in full speech."""
        return self.speech_db - IN_SPEECH_DB

    def _span(self, t0: float, t1: float) -> tuple[int, int]:
        """Blocks whose centre lies in [t0, t1] source seconds (at least one)."""
        t0, t1 = t0 - self.offset, t1 - self.offset
        a = max(0, int(np.ceil(t0 / HOP_S - 0.5)))
        b = min(len(self.power), int(np.floor(t1 / HOP_S - 0.5)) + 1)
        return a, max(b, a + 1)

    def level(self, t: float, half: float = NEAR_S) -> float:
        """RMS level (dBFS) of [t - half, t + half] source seconds (outside the audio:
        FLOOR_DB)."""
        if t + half < self.offset:
            return FLOOR_DB
        a, b = self._span(t - half, t + half)
        if a >= len(self.power):
            return FLOOR_DB
        b = min(b, len(self.power))
        p = (self._cum[b] - self._cum[a]) / (b - a)
        return float(10 * np.log10(max(p, 1e-12)))

    def peak(self, t0: float, t1: float) -> float:
        """Loudest block (dBFS) with its centre in [t0, t1] source seconds."""
        if t1 < self.offset:
            return FLOOR_DB
        a, b = self._span(t0, t1)
        if a >= len(self.power):
            return FLOOR_DB
        return float(
            10 * np.log10(max(float(self.power[a : min(b, len(self.power))].max()), 1e-12))
        )

    @property
    def has_speech(self) -> bool:
        """False for (nearly) silent audio: nothing can be cut into speech then."""
        return self.speech_db > FLOOR_DB

    def in_speech(self, t: float) -> bool:
        return self.has_speech and self.level(t) >= self.speech_floor_db


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    """Samples as float32 in -1..1 and the sample rate of a PCM WAV (16/24/32 bit)."""
    with wave.open(str(path), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v >= 1 << 23, v - (1 << 24), v) / float(1 << 23)).astype(np.float32)
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, rate


def block_power(x: np.ndarray, rate: int) -> np.ndarray:
    """Mean power of every HOP_S block."""
    hop = int(round(HOP_S * rate))
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    return (x[: n * hop].astype(np.float64).reshape(n, hop) ** 2).mean(axis=1)


def frame_db(power: np.ndarray) -> np.ndarray:
    """RMS level (dBFS) of a WIN_S window (two blocks) every HOP_S."""
    if len(power) < 2:
        return 10 * np.log10(np.maximum(power, 1e-12))
    return 10 * np.log10(np.maximum((power[:-1] + power[1:]) / 2, 1e-12))


def estimate(db: np.ndarray) -> tuple[float, float]:
    """(noise_db, speech_db) of an envelope; digital silence left out."""
    live = db[db > SILENT_DB]
    if len(live) < 10:
        return FLOOR_DB, FLOOR_DB
    noise = float(np.percentile(live, NOISE_PCT))
    loud = live[live >= noise + SPEECH_ABOVE_DB]
    speech = float(np.median(loud)) if len(loud) else float(np.percentile(live, 90))
    return noise, speech


def from_samples(x: np.ndarray, rate: int, offset: float = 0.0) -> Envelope:
    power = block_power(x, rate)
    noise, speech = estimate(frame_db(power))
    return Envelope(power, noise, speech, offset + len(x) / rate, offset)


def load(path: Path, offset: float = 0.0) -> Envelope:
    x, rate = read_mono(path)
    return from_samples(x, rate, offset)


def wav_offset(job: Path) -> float:
    """Video time of the first sample of work/audio.wav (and of words.json's time 0). A job
    whose work/audio.json says "time_base": "video" is in video time already (0); a legacy
    job's wav starts at the audio stream's first sample: work/probe.json audio_start -
    video_start."""
    try:
        if json.loads((job / "work" / "audio.json").read_text()).get("time_base") == "video":
            return 0.0
    except (OSError, ValueError, AttributeError):
        pass
    try:
        p = json.loads((job / "work" / "probe.json").read_text())
        return float(p.get("audio_start", 0) or 0) - float(p.get("video_start", 0) or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def job_envelope(job: Path) -> Envelope | None:
    """Envelope of work/audio.wav in source time; None when there is none."""
    wav = job / "work" / "audio.wav"
    if not wav.is_file():
        return None
    try:
        return load(wav, wav_offset(job))
    except (OSError, ValueError, EOFError, wave.Error):
        return None
