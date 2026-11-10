"""The SFX mix of `videocut tool render`: work/sfx.wav under the voice, ducked, measured.

Graph (after the voice's loudnorm, so the voice keeps its -14 LUFS and the SFX sit at a
fixed distance below it):

    voice ─┬───────────────────────────────────────────┐
           └─ sidechain ─┐                             ├─ amix (no normalising) ─ limiter ─ [a]
    sfx.wav ─ level gain ─ EQ dip (CARVE) ─ sidechaincompress (DUCK) ┘

Levels (job.json "sfx"): LEVEL_DB {subtle, normal} on top of the track, which sits at the
library reference (_sfx_library.REF_RMS_DB, -20 dBFS RMS per sound). Ducking: while the voice
speaks the SFX are pressed down by up to ~DUCK_RANGE_DB (fast attack, a release that lets a
hit between two words through), with a LOOKAHEAD_S look-ahead on the sidechain. The SFX bus
also gets a broad EQ dip around 2.2 kHz (CARVE), where the consonants are.

Measurement (`analyse`, an audio-only second pass with the same graph): the normalised voice,
the ducked SFX bus and the mix before the limiter go to work/ as float wavs, then per hit
over its sound's length in 20 ms frames:

    margin_db   speech-band (300-4000 Hz) level of the voice minus that of the SFX, over the
                frames where the voice speaks (>= its speech median - SPEECH_DB); the minimum
                over the hit. None: the hit lies in a pause
    masks       margin_db < MASK_DB (verify: `sfx_masks_speech`)
    fit         render lowers each masking hit by the missing dB (to FIT_TO_DB) and builds
                the track again before the encode (render.FIT_ROUNDS)
    peak        the mix before the limiter; above LIMIT_DBFS + CLIP_DB in a hit window, or
                the SFX bus alone above BUS_PEAK_DBFS: `sfx_clipping`

and loudness: voice LUFS, SFX bus LUFS (integrated over the reel, sparse by nature) and the
SFX RMS while hits sound vs the voice RMS while it speaks.
"""

from pathlib import Path

import numpy as np

from . import _sfx_library as lib
from .common import binary, run

LEVEL_DB = {"subtle": 10.0, "normal": 15.0}
DUCK = {"threshold": 0.008, "ratio": 4, "attack": 5, "release": 200, "knee": 4}
DUCK_RANGE_DB = 10.0
LOOKAHEAD_S = 0.025
# the SFX bus leaves room for the voice: a broad dip where speech carries its consonants
CARVE = {"f": 2200, "t": "o", "w": 2, "g": -5}
LIMIT_DBFS = -1.2
CLIP_DB = 1.0
BUS_PEAK_DBFS = -1.0
MASK_DB = 6.0  # the voice's speech band at least this far above the SFX while it speaks
TARGET_MARGIN_DB = 10.0  # what the levels are set for (subtle)
FIT_TO_DB = 9.0  # render lowers a masking hit until its margin would reach this
SPEECH_DB = 12.0
BAND = (300.0, 4000.0)
FRAME = 960  # 20 ms
MIN_HIT_S, MAX_HIT_S = 0.03, 0.4


def mix_lines(voice: str, sfx_input: int, gain_db: float, duration: float,
              extra_out: bool = False) -> list[str]:  # fmt: skip
    """Filter lines: voice label `voice` (e.g. "vo") + input `sfx_input` -> [a]. With
    `extra_out`, also [m_voice], [m_sfx] and [m_pre] for the analysis pass."""
    d = f"{duration:.6f}"
    duck = ":".join(f"{k}={v}" for k, v in DUCK.items())
    carve = ":".join(f"{k}={v}" for k, v in CARVE.items())
    limit = 10 ** (LIMIT_DBFS / 20)
    vs = "[vmix][vsc][m_voice]" if extra_out else "[vmix][vsc]"
    lines = [
        f"[{voice}]asplit={3 if extra_out else 2}{vs};",
        # look-ahead: the ducker hears the voice LOOKAHEAD_S early, so it is down when a
        # word starts (a whoosh running into an onset otherwise covers its first frame)
        f"[vsc]atrim=start={LOOKAHEAD_S},asetpts=PTS-STARTPTS,apad=whole_dur={d}[vla];",
        f"[{sfx_input}:a]aresample={lib.RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"volume={gain_db:.2f}dB,equalizer={carve},apad=whole_dur={d},atrim=end={d},asetpts=PTS-STARTPTS[sfxg];",
        f"[sfxg][vla]sidechaincompress={duck}[sfxd];",
    ]
    if extra_out:
        lines += [
            "[sfxd]asplit=2[smix][m_sfx];",
            "[vmix][smix]amix=inputs=2:duration=first:normalize=0,asplit=2[pre][m_pre];",
            f"[pre]alimiter=limit={limit:.4f}:level=false:latency=1[a]",
        ]
    else:
        lines.append(
            f"[vmix][sfxd]amix=inputs=2:duration=first:normalize=0,"
            f"alimiter=limit={limit:.4f}:level=false:latency=1[a]"
        )
    return lines


def _read_f32(path: Path) -> np.ndarray:
    """[samples, channels] of a float32 wav ffmpeg wrote (pcm_f32le; LIST chunks skipped)."""
    raw = path.read_bytes()
    i, ch = 12, 1
    while i + 8 <= len(raw):
        cid, size = raw[i : i + 4], int.from_bytes(raw[i + 4 : i + 8], "little")
        if cid == b"fmt ":
            ch = int.from_bytes(raw[i + 10 : i + 12], "little")
        if cid == b"data":
            return np.frombuffer(raw[i + 8 : i + 8 + size], dtype="<f4").reshape(-1, ch)
        i += 8 + size + (size & 1)
    raise ValueError(f"{path.name}: no data chunk")


def band_db(x: np.ndarray, rate: int = lib.RATE) -> np.ndarray:
    """Speech-band level (dB) per FRAME of mono `x`."""
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0)
    fr = x[: n * FRAME].reshape(n, FRAME).astype(np.float64) * np.hanning(FRAME)
    spec = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    f = np.fft.rfftfreq(FRAME, 1 / rate)
    sel = (f >= BAND[0]) & (f <= BAND[1])
    p = spec[:, sel].sum(axis=1) / (FRAME * FRAME)
    return 10 * np.log10(np.maximum(p, 1e-14))


def _rms_db(x: np.ndarray) -> float | None:
    return round(lib.db(float(np.sqrt((x.astype(np.float64) ** 2).mean()))), 1) if len(x) else None


def hit_len(h: dict, sound_s: dict[str, float]) -> float:
    return min(max(sound_s.get(h["kind"], MIN_HIT_S) / max(h.get("pitch", 1), 1e-3),
                   MIN_HIT_S), MAX_HIT_S)  # fmt: skip


def _loudest_db(x: np.ndarray, k: int = 2400) -> float | None:
    """The loudest `k`-sample (50 ms) RMS of `x` in dB (the whole RMS when shorter)."""
    if len(x) < k:
        return _rms_db(x)
    p = np.convolve(x.astype(np.float64) ** 2, np.ones(k) / k, "valid")
    return round(lib.db(float(np.sqrt(max(p.max(), 0.0)))), 1)


def analyse_arrays(voice: np.ndarray, sfx: np.ndarray, pre_peak: np.ndarray, hits: list[dict],
                   sound_s: dict[str, float]) -> dict:  # fmt: skip
    """The numbers of the module docstring from the three buses (mono voice/sfx, per-sample
    peak of the pre-limiter mix)."""
    vb, sb = band_db(voice), band_db(sfx)
    m = min(len(vb), len(sb))
    vb, sb = vb[:m], sb[:m]
    loud = vb[vb > -80]
    speech_db = float(np.percentile(loud, 70)) if len(loud) else -120.0
    speaking = vb >= speech_db - SPEECH_DB
    per_hit, margins = [], []
    limit = LIMIT_DBFS + CLIP_DB
    sfx_on = np.zeros(len(sfx), dtype=bool)
    for h in hits:
        dur = hit_len(h, sound_s)
        a, b = int(h["t"] * lib.RATE), int((h["t"] + dur) * lib.RATE)
        sfx_on[a:b] = True
        fa, fb = a // FRAME, max(a // FRAME + 1, -(-b // FRAME))
        sel = [i for i in range(fa, min(fb, m)) if speaking[i]]
        margin = round(float(min(vb[i] - sb[i] for i in sel)), 1) if sel else None
        seg = pre_peak[a:b]
        peak = round(lib.db(float(seg.max())), 1) if len(seg) else None
        doc = {"t": h["t"], "kind": h["kind"], "margin_db": margin, "peak_dbfs": peak,
               "loud_db": _loudest_db(sfx[a:b]),
               "masks": bool(margin is not None and margin < MASK_DB),
               "clips": bool(peak is not None and peak > limit)}  # fmt: skip
        if margin is not None:
            margins.append(margin)
        per_hit.append(doc)
    v_on = np.repeat(speaking, FRAME)[: len(voice)]
    voice_db = _rms_db(voice[: len(v_on)][v_on])
    hl = [d["loud_db"] for d in per_hit if d["loud_db"] is not None]
    hit_db = round(float(np.median(hl)), 1) if hl else None
    bus_peak = float(np.abs(sfx).max()) if len(sfx) else 0.0
    return {
        "hits": per_hit,
        "speech_band_db": round(speech_db, 1),
        "min_margin_db": min(margins) if margins else None,
        "median_margin_db": round(float(np.median(margins)), 1) if margins else None,
        "hits_over_speech": len(margins),
        "masked": [d for d in per_hit if d["masks"]],
        "clipped": [d for d in per_hit if d["clips"]],
        "sfx_rms_during_hits_db": _rms_db(sfx[sfx_on[: len(sfx)]]),
        # the loudest 50 ms of each hit after ducking (median over hits) and its distance to
        # the voice while it speaks: how far under the voice a hit sits
        "hit_loudness_db": hit_db,
        "voice_rms_while_speaking_db": voice_db,
        "hit_vs_voice_db": round(hit_db - voice_db, 1) if hit_db is not None and voice_db else None,
        "sfx_bus_peak_dbfs": round(lib.db(bus_peak), 1) if len(sfx) else None,
        "bus_clips": bool(bus_peak > 10 ** (BUS_PEAK_DBFS / 20)),
        "pre_limiter_peak_dbfs": round(lib.db(float(pre_peak.max())), 1) if len(pre_peak) else None,
    }


def analyse(inputs: list[str], graph_file: Path, work: Path, hits: list[dict],
            sound_s: dict[str, float], measure) -> dict:  # fmt: skip
    """Run the analysis pass (graph from mix_lines(extra_out=True), with [a] discarded) and
    return analyse_arrays + LUFS. `measure(path)` -> render.measure's dict."""
    out = {k: work / f"sfx_bus_{k}.wav" for k in ("voice", "sfx", "pre")}
    run([
        binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *inputs,
        "-/filter_complex", str(graph_file),
        "-map", "[a]", "-f", "null", "-",
        "-map", "[m_voice]", "-c:a", "pcm_f32le", str(out["voice"]),
        "-map", "[m_sfx]", "-c:a", "pcm_f32le", str(out["sfx"]),
        "-map", "[m_pre]", "-c:a", "pcm_f32le", str(out["pre"]),
    ])  # fmt: skip
    try:
        voice = _read_f32(out["voice"]).mean(axis=1)
        sfx = _read_f32(out["sfx"]).mean(axis=1)
        pre = np.abs(_read_f32(out["pre"])).max(axis=1)
        doc = analyse_arrays(voice, sfx, pre, hits, sound_s)
        doc["voice_lufs"] = measure(out["voice"]).get("integrated_lufs")
        doc["sfx_lufs"] = measure(out["sfx"]).get("integrated_lufs")
    finally:
        for p in out.values():
            p.unlink(missing_ok=True)
    return doc


def flags(report: dict | None) -> list[str]:
    """`sfx_masks_speech` / `sfx_clipping` of render.json's "sfx" report."""
    if not report or not report.get("mixed"):
        return []
    f = []
    a = report.get("analysis") or {}
    if a.get("masked"):
        f.append("sfx_masks_speech")
    if a.get("clipped") or a.get("bus_clips"):
        f.append("sfx_clipping")
    return f


def sound_lengths(sfx_doc: dict) -> dict[str, float]:
    return {k: float(s.get("duration_s") or MIN_HIT_S) for k, s in
            (sfx_doc.get("sounds") or {}).items()}  # fmt: skip
