"""`speech_clipped`: words cut off mid-phoneme at the cuts of a rendered reel (numpy only).

Works on the OUTPUT audio (mono, 48 kHz) only, so it checks what the viewer hears, after
snapping and crossfading. Per inner cut at output time t:

    level_db  RMS over t +- 20 ms (the crossfade centre: both sides at -3 dB)
    pre_db    RMS over [t - 60, t - 10] ms: the outgoing segment (its offset)
    post_db   RMS over [t + 10, t + 60] ms: the incoming segment (its onset)
    flux      spectral flux at the cut (20 ms Hann frames, 10 ms hop, log magnitude,
              summed rise and fall) relative to its median over t +- 60 ms

The reel's speech median S and noise floor come from its own audio (_audio_env.estimate,
digital silence left out). A cut is clipped when speech runs across it:

    level_db >= S - IN_SPEECH_DB (12)                       a word goes on through the cut
    level_db >= S - NEAR_SPEECH_DB (18) and one side >= S - 12, or flux >= FLUX_ABRUPT
                                                            a phoneme ends or starts at the
                                                            cut instead of decaying
    level_db >= S - EDGE_DB (21) and one side >= S - 8      full speech right up to the cut

Calibrated on a real 47-segment reel against the source audio at each cut point (33 of 37
cuts with speech at a source cut point flagged, none of the 9 clean ones; the clean cuts
lay at S - 24 dB and below) and on generated clips with known word positions.
`side` says which side is in speech: "out" (the outgoing word is cut off), "in" (the
incoming word starts mid-phoneme) or "both".

Joins (the next segment continues the source) are no cuts. A segment with
"speech_cut_ok": "<reason>" turns its cuts' finding into info (`speech_cut_ok`).
"""

import numpy as np

from ._audio_env import IN_SPEECH_DB, block_power, estimate, frame_db

NEAR_SPEECH_DB = 18.0
EDGE_DB, EDGE_SIDE_DB = 21.0, 8.0
FLUX_ABRUPT = 2.5
HALF_S, SIDE_S, GAP_S = 0.02, 0.06, 0.01
FRAME, HOP = 960, 480  # 20 ms / 10 ms at 48 kHz


def _rms_db(x: np.ndarray, rate: int, t0: float, t1: float) -> float:
    a, b = max(0, int(round(t0 * rate))), min(len(x), int(round(t1 * rate)))
    if b <= a:
        return -120.0
    p = float((x[a:b].astype(np.float64) ** 2).mean())
    return round(10 * np.log10(max(p, 1e-12)), 1)


def flux_ratio(x: np.ndarray, rate: int, t: float) -> float | None:
    """Spectral flux at t vs. its median over t +- SIDE_S (None at the reel's edges)."""
    c = int(round(t * rate))
    span = int(round((SIDE_S + 0.03) * rate))
    a, b = c - span, c + span
    if a < 0 or b > len(x):
        return None
    seg = x[a:b].astype(np.float64)
    n = (len(seg) - FRAME) // HOP + 1
    if n < 5:
        return None
    win = np.hanning(FRAME)
    frames = np.stack([seg[i * HOP : i * HOP + FRAME] * win for i in range(n)])
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 100)
    flux = np.abs(np.diff(mag, axis=0)).sum(axis=1)  # frame i -> i+1, centred between them
    centres = (np.arange(1, n) * HOP + FRAME / 2) / rate + a / rate
    near = np.abs(centres - t) <= HALF_S + 1e-9
    around = np.abs(centres - t) <= SIDE_S + 1e-9
    base = float(np.median(flux[around & ~near])) if (around & ~near).any() else 0.0
    peak = float(flux[near].max()) if near.any() else 0.0
    return round(peak / max(base, 1e-6), 2)


def reel_levels(x: np.ndarray, rate: int) -> tuple[float, float]:
    """(noise_db, speech_db) of the reel's own audio."""
    return estimate(frame_db(block_power(x, rate)))


def cut_numbers(x: np.ndarray, rate: int, t: float) -> dict:
    return {
        "level_db": _rms_db(x, rate, t - HALF_S, t + HALF_S),
        "pre_db": _rms_db(x, rate, t - SIDE_S, t - GAP_S),
        "post_db": _rms_db(x, rate, t + GAP_S, t + SIDE_S),
        "flux": flux_ratio(x, rate, t),
    }


def is_clipped(n: dict, speech_db: float) -> str | None:
    """The side in speech ("out", "in", "both") when the cut clips a word, else None."""
    if speech_db <= -120.0:  # a silent reel: no speech to clip
        return None
    lv, side = n["level_db"], max(n["pre_db"], n["post_db"])
    abrupt = n["flux"] is not None and n["flux"] >= FLUX_ABRUPT
    if not (
        lv >= speech_db - IN_SPEECH_DB
        or (lv >= speech_db - NEAR_SPEECH_DB and (side >= speech_db - IN_SPEECH_DB or abrupt))
        or (lv >= speech_db - EDGE_DB and side >= speech_db - EDGE_SIDE_DB)
    ):
        return None
    floor = speech_db - NEAR_SPEECH_DB
    out, inn = n["pre_db"] >= floor, n["post_db"] >= floor
    if out and inn:
        return "both"
    return "out" if out else "in" if inn else "both"


def check(
    x: np.ndarray, rate: int, cuts: list[float], joins: list[bool] | None = None,
    ok_reasons: list[str | None] | None = None,
) -> dict:  # fmt: skip
    """Per inner cut (output seconds; cut i joins segment i-1 and i, 1-based like verify)
    its numbers and verdict. `joins[i-1]`: cut i is a join (skipped); `ok_reasons[i-1]`:
    the speech_cut_ok reason of the two segments, if any."""
    noise, speech = reel_levels(x, rate)
    docs, flagged = [], []
    for i, t in enumerate(cuts, 1):
        if joins and joins[i - 1]:
            continue
        n = cut_numbers(x, rate, t)
        side = is_clipped(n, speech)
        d = {"cut": i, "t": round(t, 3), "segments": [i - 1, i], **n, "clipped": side}
        reason = ok_reasons[i - 1] if ok_reasons else None
        if side and reason:
            d["speech_cut_ok"] = reason
        elif side:
            flagged.append(d)
        docs.append(d)
    return {
        "speech_db": round(speech, 1),
        "noise_db": round(noise, 1),
        "cuts": docs,
        "flagged": flagged,
    }
