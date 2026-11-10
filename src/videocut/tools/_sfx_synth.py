"""Procedural sound effects for the motion cue kinds (numpy only, no network, no files).

The fallback of the SFX library (_sfx_library) when there is no upload and no download for
a kind, and the default for tests and CI. Every sound is a pure function of (kind, variant):
the same call gives the same samples on every machine.

Design: short, clean, quiet in the speech band where it can be. Sine bodies with fast pitch
drops, band-limited noise with moving filters, exponential envelopes; no square waves, no
long tails, no "cartoon" boings.

    pop         a soft bubble: sine 520→260 Hz in 70 ms + a 2 ms click
    whoosh      noise through a resonant band-pass sweeping up, rise and fall, 380 ms
    tick        a 12 ms woody click (2.4 kHz sine + high-passed noise burst)
    check       a two-partial chime (a fifth apart) decaying over 320 ms
    strike      a pen stroke: band-passed noise sweeping down with a slight grain, 220 ms
    type        a 14 ms key click (low-passed noise + 1.1 kHz body)
    count       a 10 ms bright tick (3.6 kHz)
    impact      a low thump: sine 90→45 Hz + a low-passed noise burst, 420 ms
    swoosh_out  the whoosh's counterpart: a band-pass sweeping down, falling, 320 ms
"""

import numpy as np

RATE = 48000
VARIANTS = 3  # procedural variants per kind (slight pitch/length changes)


def _t(dur: float) -> np.ndarray:
    return np.arange(int(round(dur * RATE))) / RATE


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    """Linear attack over `attack` s, exponential decay with time constant `decay` s."""
    t = np.arange(n) / RATE
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(t - attack, 0) / decay)


def _sine_sweep(f0: float, f1: float, dur: float, curve: float = 6.0) -> np.ndarray:
    """A sine whose frequency glides f0→f1 exponentially (curve: how fast)."""
    t = _t(dur)
    f = f1 + (f0 - f1) * np.exp(-curve * t / dur)
    return np.sin(2 * np.pi * np.cumsum(f) / RATE)


def _noise(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n)


def _bandpass_sweep(x: np.ndarray, f0: float, f1: float, q: float) -> np.ndarray:
    """A state-variable band-pass whose centre moves f0→f1 (log) over the signal."""
    n = len(x)
    fc = np.exp(np.linspace(np.log(f0), np.log(f1), n))
    g = 2 * np.sin(np.pi * np.minimum(fc, RATE / 6) / RATE)
    damp = 1 / q
    low = band = 0.0
    out = np.empty(n)
    for i in range(n):  # short sounds (< 0.5 s): a plain loop is fast enough
        high = x[i] - low - damp * band
        band += g[i] * high
        low += g[i] * band
        out[i] = band
    return out


def _onepole(x: np.ndarray, fc: float, high: bool = False) -> np.ndarray:
    a = np.exp(-2 * np.pi * fc / RATE)
    y = np.empty(len(x))
    s = 0.0
    for i, v in enumerate(x):
        s = (1 - a) * v + a * s
        y[i] = s
    return x - y if high else y


def _fade(x: np.ndarray, out_s: float = 0.004) -> np.ndarray:
    k = min(len(x), int(out_s * RATE))
    if k:
        x[-k:] *= np.linspace(1, 0, k)
    return x


def _norm(x: np.ndarray, peak: float = 0.7) -> np.ndarray:
    m = float(np.abs(x).max()) if len(x) else 0.0
    return (x / m * peak if m > 0 else x).astype(np.float32)


def _pop(rng, v):
    s = 1 + 0.08 * (v - 1)
    body = _sine_sweep(520 * s, 260 * s, 0.07, 5) * _env(int(0.07 * RATE), 0.002, 0.018)
    click = _onepole(_noise(int(0.002 * RATE), rng), 4000) * 0.4
    body[: len(click)] += click
    return body


def _whoosh(rng, v):
    dur = 0.38 + 0.04 * (v - 1)
    n = int(dur * RATE)
    x = _bandpass_sweep(_noise(n, rng), 300, 2400 + 300 * v, 2.2)
    t = np.linspace(0, 1, n)
    env = np.sin(np.pi * t**0.7) ** 2  # rises to ~60 %, then falls
    return x * env


def _tick(rng, v):
    n = int(0.012 * RATE)
    f = 2400 * (1 + 0.06 * (v - 1))
    body = np.sin(2 * np.pi * f * np.arange(n) / RATE) * _env(n, 0.0005, 0.0025)
    burst = _onepole(_noise(n, rng), 3000, high=True) * _env(n, 0.0002, 0.0012) * 0.5
    return body + burst


def _check(rng, v):
    dur = 0.32
    n = int(dur * RATE)
    f = 880 * (1 + 0.05 * (v - 1))
    t = np.arange(n) / RATE
    e1, e2 = _env(n, 0.003, 0.09), _env(n, 0.003, 0.06)
    tone = 0.7 * np.sin(2 * np.pi * f * t) * e1 + 0.45 * np.sin(2 * np.pi * f * 1.5 * t) * e2
    # the second partial enters 60 ms later: a rising "da-ding"
    lag = int(0.06 * RATE)
    second = np.zeros(n)
    second[lag:] = np.sin(2 * np.pi * f * 2 * t[: n - lag]) * _env(n - lag, 0.003, 0.08)
    return tone + 0.5 * second


def _strike(rng, v):
    dur = 0.22 + 0.02 * (v - 1)
    n = int(dur * RATE)
    x = _bandpass_sweep(_noise(n, rng), 3200, 1200, 3.0)
    grain = 1 + 0.35 * np.sin(2 * np.pi * (70 + 10 * v) * np.arange(n) / RATE)
    t = np.linspace(0, 1, n)
    env = np.minimum(t / 0.08, 1) * (1 - t) ** 1.5
    return x * grain * env


def _type(rng, v):
    n = int(0.014 * RATE)
    f = 1100 * (1 + 0.07 * (v - 1))
    body = np.sin(2 * np.pi * f * np.arange(n) / RATE) * _env(n, 0.0004, 0.003)
    noise = _onepole(_noise(n, rng), 5000) * _env(n, 0.0002, 0.002) * 0.8
    return body * 0.6 + noise


def _count(rng, v):
    n = int(0.010 * RATE)
    f = 3600 * (1 + 0.05 * (v - 1))
    return np.sin(2 * np.pi * f * np.arange(n) / RATE) * _env(n, 0.0003, 0.002)


def _impact(rng, v):
    dur = 0.42
    n = int(dur * RATE)
    s = 1 + 0.07 * (v - 1)
    thump = _sine_sweep(90 * s, 45 * s, dur, 4) * _env(n, 0.002, 0.11)
    burst = _onepole(_noise(n, rng), 900) * _env(n, 0.001, 0.03) * 2.0
    return thump + burst


def _swoosh_out(rng, v):
    dur = 0.32 + 0.03 * (v - 1)
    n = int(dur * RATE)
    x = _bandpass_sweep(_noise(n, rng), 2200 + 200 * v, 350, 2.2)
    t = np.linspace(0, 1, n)
    env = np.minimum(t / 0.05, 1) * (1 - t) ** 2
    return x * env


MAKERS = {
    "pop": _pop,
    "whoosh": _whoosh,
    "tick": _tick,
    "check": _check,
    "strike": _strike,
    "type": _type,
    "count": _count,
    "impact": _impact,
    "swoosh_out": _swoosh_out,
}


def sound(kind: str, variant: int = 0) -> np.ndarray:
    """Mono float32 samples at RATE of procedural `kind` (MAKERS), variant 0..VARIANTS-1."""
    if kind not in MAKERS:
        raise KeyError(kind)
    v = int(variant) % VARIANTS
    rng = np.random.default_rng(1000 * (list(MAKERS).index(kind) + 1) + v)
    x = MAKERS[kind](rng, v).astype(np.float64)
    return _norm(_fade(x))
