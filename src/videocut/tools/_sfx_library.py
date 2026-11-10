"""The local sound-effect library of `videocut tool sfx`: $VIDEOCUT_HOME/sfx/<kind>/.

One sound per file pair: `<id>.wav` (48 kHz mono 16-bit, processed) and `<id>.json` (the
sidecar: origin, title, author, licence, source link, attribution, duration, sha256 of
the wav). Three origins, preferred in this order when a job picks its sound for a kind:

    upload      the person's own sound (Settings: own SFX per kind); licence "own"
    openverse   downloaded once from Openverse (CC0 / Public Domain Mark only, key-less,
                `fetch`), with the credit line in the sidecar
    procedural  generated locally (_sfx_synth), never stored; the offline, test and CI
                default, so the track can always be built

No sound is bundled with the code (CLAUDE.md rule 7). Every file that enters the library is
decoded by ffmpeg (untrusted input: the file protocol only) and processed the same way:
DC and rumble removed, leading and trailing silence trimmed, cut to the kind's maximum
length with a short fade, the active part normalised to REF_RMS_DB with the peak at most
PEAK_DBFS. A job keeps the sounds it used: sfx.json names their ids and sha256.
"""

import hashlib
import json
import re
import subprocess
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from . import _sfx_synth
from .common import ToolError, binary, home, write_json

RATE = 48000
REF_RMS_DB = -20.0  # the active part of every library sound (the "normal" reference)
PEAK_DBFS = -1.0
SILENCE_DB = -45.0  # relative to the sound's peak: quieter edges are trimmed
MIN_ACTIVE_S = 0.008
MAX_UPLOAD_BYTES = 20 * 1024**2
MAX_DECODE_S = 30.0  # a longer upload is refused (a sound effect, not a song)
UPLOAD_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff", ".caf"}
ORIGINS = ("upload", "openverse", "procedural")
ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Kind:
    max_s: float  # a library sound is cut to this
    min_gap_s: float  # two hits of this kind are at least this far apart
    priority: int  # the density limit drops lower ones first
    gain_db: float  # relative level of the kind in the track (clicks sit lower)
    query: str  # Openverse search words
    search_max_s: float  # Openverse hits longer than this are skipped (they would be cut)
    family: str = ""  # kinds of one family keep the larger min_gap_s between them too


# "air": the big transition sounds (whoosh in, swoosh out, impact): one per moment, since
# two broadband sounds stacked within a few hundred ms cover the voice (measured: 5 dB
# speech-band margin for a swoosh_out + whoosh 150 ms apart, 11+ dB for either alone).
KINDS: dict[str, Kind] = {
    "pop": Kind(0.35, 0.10, 2, -3.0, "pop", 1.0),
    "whoosh": Kind(0.8, 0.30, 4, 0.0, "whoosh", 1.6, "air"),
    "tick": Kind(0.12, 0.07, 1, -6.0, "tick", 0.6),
    "check": Kind(0.6, 0.20, 3, -5.0, "ding", 1.5),
    "strike": Kind(0.5, 0.30, 3, -3.0, "pen stroke", 1.2),
    "type": Kind(0.12, 0.05, 0, -8.0, "keyboard key", 0.6),
    "count": Kind(0.12, 0.05, 1, -7.0, "click", 0.5),
    "impact": Kind(0.9, 0.50, 5, 0.0, "impact hit", 2.0, "air"),
    "swoosh_out": Kind(0.6, 0.30, 4, -2.0, "swoosh", 1.4, "air"),
}


@dataclass
class Sound:
    id: str
    kind: str
    origin: str  # ORIGINS
    title: str
    duration_s: float
    sha256: str = ""
    author: str = ""
    licence: str = ""
    licence_url: str = ""
    source_url: str = ""
    attribution: str = ""
    file: str = ""  # relative to the library root ("" for procedural)

    def doc(self) -> dict:
        return asdict(self)


def root() -> Path:
    """$VIDEOCUT_HOME/sfx (read at call time: tests point VIDEOCUT_HOME elsewhere)."""
    return home() / "sfx"


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ToolError(f"unknown sound kind {kind!r} (one of {', '.join(KINDS)})")
    return kind


# --------------------------------------------------------------------------- audio I/O


def decode(path: Path, max_s: float = MAX_DECODE_S) -> np.ndarray:
    """Mono float32 at RATE of any audio file ffmpeg reads (file protocol only). ToolError
    when it is not audio or longer than `max_s`."""
    cmd = [
        binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-protocol_whitelist", "file", "-i", str(path), "-vn", "-sn", "-dn",
        "-t", f"{max_s + 0.5:.3f}", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-",
    ]  # fmt: skip
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ToolError(f"{path.name}: cannot be read ({e})") from None
    if r.returncode != 0 or not r.stdout:
        raise ToolError(f"{path.name}: not a readable audio file")
    x = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    if len(x) > max_s * RATE:
        raise ToolError(f"{path.name}: longer than {max_s:.0f} s (a sound effect is short)")
    if not np.isfinite(x).all():
        raise ToolError(f"{path.name}: has invalid samples")
    return x


def write_wav(path: Path, x: np.ndarray, channels: int = 1) -> None:
    """16-bit PCM at RATE (x: mono, or interleaved for `channels` > 1), atomically."""
    pcm = (np.clip(x, -1, 1) * 32767).round().astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


def read_wav(path: Path) -> np.ndarray:
    """Mono float32 of a library wav (16-bit, RATE, as write_wav wrote it)."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != RATE or w.getsampwidth() != 2:
            raise ToolError(f"{path.name}: not a library sound (48 kHz 16-bit)")
        ch = w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32767
    return x.reshape(-1, ch).mean(axis=1) if ch > 1 else x


def db(v: float) -> float:
    return 20 * np.log10(max(v, 1e-9))


def _frames_db(x: np.ndarray, frame: int = 240) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.array([db(float(np.sqrt((x**2).mean())))]) if len(x) else np.array([-180.0])
    p = (x[: n * frame].reshape(n, frame).astype(np.float64) ** 2).mean(axis=1)
    return 10 * np.log10(np.maximum(p, 1e-18))


def process(x: np.ndarray, kind: str) -> np.ndarray:
    """A raw sound made fit for the library (module docstring). ToolError when nothing
    audible is left."""
    spec = KINDS[check_kind(kind)]
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0:
        raise ToolError("the sound is empty")
    x = x - x.mean()
    # rumble and DC: a first-order high-pass at 30 Hz (a plain loop: a few seconds at most)
    a = np.exp(-2 * np.pi * 30 / RATE)
    y, s, prev = np.empty_like(x), 0.0, 0.0
    for i, v in enumerate(x):
        s = a * (s + v - prev)
        prev = v
        y[i] = s
    x = y
    frame = 240  # 5 ms
    fdb = _frames_db(x, frame)
    peak_db = float(fdb.max())
    if peak_db < -70:
        raise ToolError("the sound is silent")
    loud = np.nonzero(fdb >= peak_db + SILENCE_DB)[0]
    a0, a1 = int(loud[0]) * frame, min(len(x), (int(loud[-1]) + 1) * frame)
    x = x[max(0, a0 - 48) : a1]  # keep 1 ms before the onset
    if len(x) < MIN_ACTIVE_S * RATE:
        raise ToolError("the sound is too short")
    n_max = int(spec.max_s * RATE)
    if len(x) > n_max:
        x = x[:n_max]
        k = min(len(x) // 3, int(0.03 * RATE))
        x[-k:] *= np.linspace(1, 0, k) ** 2
    k_in = min(48, len(x) // 4)
    x[:k_in] *= np.linspace(0, 1, k_in)
    k_out = min(int(0.004 * RATE), len(x) // 4)
    x[-k_out:] *= np.linspace(1, 0, k_out)
    fdb = _frames_db(x, frame)
    active = fdb >= fdb.max() - 20
    p_active = float(np.mean(10 ** (fdb[active] / 10)))
    rms = np.sqrt(p_active)
    g = 10 ** (REF_RMS_DB / 20) / max(rms, 1e-9)
    peak = float(np.abs(x).max()) * g
    cap = 10 ** (PEAK_DBFS / 20)
    if peak > cap:
        g *= cap / peak
    return (x * g).astype(np.float32)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- the library


def _sidecar(path: Path) -> Sound | None:
    try:
        d = json.loads(path.read_text())
        s = Sound(**{k: d[k] for k in Sound.__dataclass_fields__ if k in d})
    except (OSError, ValueError, TypeError):
        return None
    wav = path.with_suffix(".wav")
    if (s.origin not in ("upload", "openverse") or s.kind != path.parent.name
            or not ID_RE.match(s.id) or s.id != path.stem or not wav.is_file()):  # fmt: skip
        return None
    return s


def procedural(kind: str) -> list[Sound]:
    out = []
    for v in range(_sfx_synth.VARIANTS):
        x = _sfx_synth.sound(kind, v)
        out.append(Sound(
            id=f"procedural-{kind}-{v + 1}", kind=kind, origin="procedural",
            title=f"{kind} (generated, variant {v + 1})", duration_s=round(len(x) / RATE, 3),
            licence="generated locally", attribution="",
        ))  # fmt: skip
    return out


def sounds(kind: str, include_procedural: bool = True) -> list[Sound]:
    """Every sound of `kind`: uploads, downloads (by id), then the procedural ones."""
    d = root() / check_kind(kind)
    found = [s for p in sorted(d.glob("*.json")) if (s := _sidecar(p))] if d.is_dir() else []
    found.sort(key=lambda s: (ORIGINS.index(s.origin), s.id))
    return found + (procedural(kind) if include_procedural else [])


def preferred(kind: str, allow: tuple[str, ...] = ORIGINS) -> list[Sound]:
    """The sounds of the best origin that has any (upload > openverse > procedural)."""
    every = sounds(kind)
    for origin in ORIGINS:
        tier = [s for s in every if s.origin == origin and origin in allow]
        if tier:
            return tier
    return procedural(kind)


def samples(s: Sound) -> np.ndarray:
    """The processed mono samples of library sound `s`."""
    if s.origin == "procedural":
        v = int(s.id.rsplit("-", 1)[1]) - 1
        return process(_sfx_synth.sound(s.kind, v), s.kind)
    path = root() / s.kind / f"{s.id}.wav"
    if s.sha256 and sha256_file(path) != s.sha256:
        raise ToolError(f"sfx/{s.kind}/{s.id}.wav changed since it was added")
    return read_wav(path)


def _store(kind: str, sid: str, x: np.ndarray, meta: dict) -> Sound:
    d = root() / kind
    wav = d / f"{sid}.wav"
    write_wav(wav, x)
    s = Sound(id=sid, kind=kind, duration_s=round(len(x) / RATE, 3), sha256=sha256_file(wav),
              file=f"{kind}/{sid}.wav", **meta)  # fmt: skip
    write_json(d / f"{sid}.json", s.doc())
    return s


def add_upload(kind: str, src: Path, title: str | None = None) -> Sound:
    """The person's own sound for `kind` (decoded, processed, stored as upload-<hash>)."""
    check_kind(kind)
    if src.stat().st_size > MAX_UPLOAD_BYTES:
        raise ToolError(f"{src.name}: larger than {MAX_UPLOAD_BYTES // 1024**2} MB")
    x = process(decode(src), kind)
    sid = "upload-" + hashlib.sha256(x.tobytes()).hexdigest()[:12]
    name = re.sub(r"\s+", " ", (title or src.stem)).strip()[:80] or "upload"
    return _store(kind, sid, x, {"origin": "upload", "title": name, "licence": "own upload"})


def remove(kind: str, sid: str) -> bool:
    """Remove a stored sound (upload or download); False when there is none."""
    check_kind(kind)
    if not ID_RE.match(sid) or sid.startswith("procedural-"):
        return False
    d = root() / kind
    gone = False
    for ext in (".wav", ".json"):
        p = d / f"{sid}{ext}"
        if p.is_file():
            p.unlink()
            gone = True
    return gone


SEARCH_PAUSE_S = 3.1  # Openverse without a key: 20 searches per minute


def fetch(kinds: list[str], per_kind: int = 2, search=None, fetch_file=None,
          sleep=time.sleep, say=print) -> dict:  # fmt: skip
    """Download up to `per_kind` CC0/PDM sounds per kind from Openverse into the library
    (once: a kind that already has `per_kind` downloads is skipped). `search(query,
    limit, max_s)` -> hits and `fetch_file(hit, dest)` default to the Openverse provider.
    Returns {kind: {"added": [...ids], "have": n, "error"?}}."""
    if search is None or fetch_file is None:
        from ._material_providers import openverse

        search = search or openverse.search_audio
        fetch_file = fetch_file or openverse.download_audio
    from ._material_providers import ProviderError

    report: dict = {}
    first = True
    for kind in kinds:
        spec = KINDS[check_kind(kind)]
        have = [s for s in sounds(kind, False) if s.origin == "openverse"]
        rep = report[kind] = {"added": [], "have": len(have)}
        if len(have) >= per_kind:
            continue
        if not first:
            sleep(SEARCH_PAUSE_S)
        first = False
        try:
            hits = search(spec.query, 20, spec.search_max_s)
        except ProviderError as e:
            rep["error"] = str(e)
            say(f"sfx {kind}: {e}")
            if "rate limit" in str(e):
                break
            continue
        known = {s.id for s in have}
        tmpdir = root() / ".download"
        for hit in hits:
            if rep["have"] >= per_kind:
                break
            sid = "openverse-" + re.sub(r"[^a-z0-9]", "", hit.id.removeprefix("openverse-"))[:12]
            if sid in known:
                continue
            tmp = tmpdir / f"{sid}.audio"
            try:
                fetch_file(hit, tmp)
                x = process(decode(tmp, 10.0), kind)
            except (ProviderError, ToolError) as e:
                say(f"sfx {kind}: skipped {hit.id} ({e})")
                continue
            finally:
                tmp.unlink(missing_ok=True)
            _store(kind, sid, x, {
                "origin": "openverse", "title": hit.attribution.split(" by ")[0][:80],
                "author": hit.author, "licence": hit.licence, "licence_url": hit.licence_url,
                "source_url": hit.source_url, "attribution": hit.attribution,
            })  # fmt: skip
            rep["added"].append(sid)
            rep["have"] += 1
            known.add(sid)
        say(f"sfx {kind}: {rep['have']} from Openverse")
    return report
