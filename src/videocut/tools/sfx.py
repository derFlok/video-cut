"""`videocut tool sfx`: the sound-effect track of the motion scenes -> work/sfx.wav + sfx.json.

    sfx build [job]        every span's work/motion/<id>/events.json (ids from
                           work/motion/spans.json, else every events.json found there)
                           -> work/sfx.wav (48 kHz stereo, the reel's output time) and
                           work/sfx.json (hits, sounds, sources, licences)
    sfx fetch [--kind k]   download CC0/PDM sounds from Openverse into the library (host
                           only, once per kind; _sfx_library.fetch)
    sfx list [--kind k]    the library: what each kind would use and where it came from
    sfx add <kind> <file>  the person's own sound for a kind (preferred over downloads)
    sfx remove <kind> <id>

An event is {t (output s), kind (_sfx_library.KINDS), component, strength (0..1, default
DEFAULT_STRENGTH)}; an unknown kind or a time outside the reel is skipped with a warning.

Per job one sound is picked per kind from the best origin that has one (upload > Openverse
> procedural), seeded by the job id, so a re-build gives the same track. Every hit varies
slightly (pitch +-PITCH_SEMITONES, gain +-GAIN_JITTER_DB, a small pan), seeded per hit.

Placement rules (plan_hits, pure):
    strength     gain = kind gain + 20*log10(0.4 + 0.6*strength); below MIN_STRENGTH: none
    spacing      two hits of one kind (or one family: whoosh, swoosh_out and impact are
                 "air") are at least their min_gap_s apart; any two hits MIN_ANY_GAP_S
    density      at most MAX_PER_S hits in any 1 s window; the density limit keeps the
                 higher-priority kinds (impact > whoosh/swoosh_out > check/strike > pop >
                 count/tick > type), then the stronger hit
    consonants   a hit whose transient would land in the first frame of a word onset
                 (onset - ONSET_PRE_S .. onset + 1 frame, word times from words.json through
                 the EDL) moves to ONSET_LEAD_S before the onset (at most one frame and a
                 bit earlier: still on the picture's event)

The track is at the library's "normal" reference; render scales it by the job's level
(job.json "sfx": off|subtle|normal, default from settings "sfx_default", else subtle),
ducks it under the voice and measures the mix (_sfx_mix).
"""

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .. import settings
from . import _sfx_library as lib
from .captions import map_words
from .common import ToolError, job_parent, read_job, resolve_job, run_tool, write_json

LEVELS = ("off", "subtle", "normal")
DEFAULT_LEVEL = "subtle"
DEFAULT_STRENGTH = 0.7
MIN_STRENGTH = 0.05
PITCH_SEMITONES = 0.6
GAIN_JITTER_DB = 1.5
PAN_MAX = 0.2
MAX_PER_S = 6
MIN_ANY_GAP_S = 0.04
ONSET_PRE_S = 0.015
ONSET_LEAD_S = 0.03
TRACK_PEAK_DBFS = -8.0  # the track alone never peaks above this (scaled down when it would)
VERSION = 1


# --------------------------------------------------------------------------- the level


def parse_level(value) -> str:
    v = value.strip().lower() if isinstance(value, str) else None
    if v not in LEVELS:
        raise ToolError(f"sfx level {value!r} is not one of {'|'.join(LEVELS)}")
    return v


def default_level() -> str:
    v = settings.load().get("sfx_default")
    return v if v in LEVELS else DEFAULT_LEVEL


def level(data: dict) -> str:
    """The level of job.json `data`: its "sfx", else the settings default. ToolError for an
    invalid value (fail closed)."""
    if data.get("sfx") is not None:
        try:
            return parse_level(data["sfx"])
        except ToolError as e:
            raise ToolError(f'job.json "sfx": {e}') from None
    return default_level()


# --------------------------------------------------------------------------- inputs


def motion_dir(job: Path) -> Path:
    return job / "work" / "motion"


def event_files(job: Path) -> list[Path]:
    """The events.json of every span (spans.json order), else every one found."""
    d = motion_dir(job)
    spans = d / "spans.json"
    if spans.is_file():
        try:
            ids = [str(s["id"]) for s in json.loads(spans.read_text())]
        except (ValueError, TypeError, KeyError):
            raise ToolError("work/motion/spans.json is malformed") from None
        files = []
        for sid in ids:
            p = (d / sid / "events.json").resolve()
            if not p.is_relative_to(d.resolve()):
                raise ToolError(f"span id {sid!r} leaves work/motion/")
            if p.is_file():
                files.append(p)
        return files
    return sorted(d.glob("*/events.json")) if d.is_dir() else []


def events_sha(files: list[Path]) -> list[dict]:
    return [{"file": f"{p.parent.name}/{p.name}", "sha256": lib.sha256_file(p)} for p in files]


def read_events(files: list[Path], duration: float) -> tuple[list[dict], list[str]]:
    """Valid events of all files ({t, kind, component, strength, span}) and warnings."""
    out, warnings = [], []
    for p in files:
        span = p.parent.name
        try:
            evs = json.loads(p.read_text())
        except ValueError:
            warnings.append(f"{span}/events.json is not JSON; skipped")
            continue
        if not isinstance(evs, list):
            warnings.append(f"{span}/events.json is not a list; skipped")
            continue
        bad = 0
        for e in evs:
            if not isinstance(e, dict):
                bad += 1
                continue
            t, kind = e.get("t"), e.get("kind")
            s = e.get("strength", DEFAULT_STRENGTH)
            if (not isinstance(t, int | float) or isinstance(t, bool) or not math.isfinite(t)
                    or not 0 <= t < duration or kind not in lib.KINDS
                    or not isinstance(s, int | float) or isinstance(s, bool)
                    or not math.isfinite(s)):  # fmt: skip
                bad += 1
                continue
            comp = e.get("component")
            out.append({"t": float(t), "kind": kind, "strength": min(max(float(s), 0.0), 1.0),
                        "component": str(comp)[:40] if comp is not None else None,
                        "span": span})  # fmt: skip
        if bad:
            warnings.append(f"{span}/events.json: {bad} event(s) skipped (time or kind)")
    out.sort(key=lambda e: e["t"])
    return out, warnings


# --------------------------------------------------------------------------- placement


def _seed(*parts) -> int:
    h = hashlib.sha256("\x00".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big")


def _too_close(a: dict, b: dict) -> bool:
    """Two hits of one kind (or one family) closer than their kinds' min_gap_s."""
    ka, kb = lib.KINDS[a["kind"]], lib.KINDS[b["kind"]]
    same = a["kind"] == b["kind"] or (ka.family and ka.family == kb.family)
    return bool(same) and abs(a["t"] - b["t"]) < max(ka.min_gap_s, kb.min_gap_s)


def plan_hits(events: list[dict], onsets: list[float], fps: float, seed: int,
              duration: float) -> tuple[list[dict], list[dict]]:  # fmt: skip
    """(hits, skipped) for `events` (module docstring: strength, consonants, spacing,
    density). Pure: the same input gives the same hits."""
    frame = 1 / fps
    onsets = sorted(onsets)
    cands, skipped = [], []
    for i, e in enumerate(events):
        if e["strength"] < MIN_STRENGTH:
            skipped.append({"t": e["t"], "kind": e["kind"], "reason": "strength"})
            continue
        t, shift = e["t"], 0.0
        for o in onsets:
            if o - ONSET_PRE_S <= t <= o + frame + 1e-9:
                shift = (o - ONSET_LEAD_S) - t
                t = max(0.0, o - ONSET_LEAD_S)
                break
            if o > t + frame:
                break
        rng = np.random.default_rng(_seed(seed, "hit", i, e["kind"], round(e["t"], 4)))
        spec = lib.KINDS[e["kind"]]
        gain = (spec.gain_db + 20 * math.log10(0.4 + 0.6 * e["strength"])
                + rng.uniform(-GAIN_JITTER_DB, GAIN_JITTER_DB))  # fmt: skip
        cands.append({
            "t": round(t, 4), "event_t": round(e["t"], 4), "kind": e["kind"],
            "span": e["span"], "component": e["component"], "strength": e["strength"],
            "gain_db": round(gain, 2),
            "pitch": round(2 ** (rng.uniform(-PITCH_SEMITONES, PITCH_SEMITONES) / 12), 4),
            "pan": round(rng.uniform(-PAN_MAX, PAN_MAX), 3),
            "shifted_ms": round(shift * 1000) if shift else 0,
        })  # fmt: skip
    order = sorted(cands, key=lambda h: (-lib.KINDS[h["kind"]].priority, -h["strength"], h["t"]))
    kept: list[dict] = []
    for h in order:
        reason = None
        if h["t"] >= duration:
            reason = "after the end"
        elif any(_too_close(k, h) for k in kept):
            reason = "spacing"
        elif any(abs(k["t"] - h["t"]) < MIN_ANY_GAP_S for k in kept):
            reason = "spacing"
        else:
            near = sorted([k["t"] for k in kept if abs(k["t"] - h["t"]) < 1.0] + [h["t"]])
            # the fullest 1 s window around h starts at one of the hits in it
            if max(sum(1 for y in near if x <= y < x + 1.0) for x in near) > MAX_PER_S:
                reason = "density"
        if reason:
            skipped.append({"t": h["event_t"], "kind": h["kind"], "reason": reason})
        else:
            kept.append(h)
    kept.sort(key=lambda h: h["t"])
    skipped.sort(key=lambda s: s["t"])
    return kept, skipped


# --------------------------------------------------------------------------- the track


def _resample(x: np.ndarray, factor: float) -> np.ndarray:
    """Pitch by `factor` (> 1 higher and shorter), linear interpolation."""
    if abs(factor - 1) < 1e-4:
        return x
    n = max(1, int(len(x) / factor))
    return np.interp(np.arange(n) * factor, np.arange(len(x)), x).astype(np.float32)


def render_track(hits: list[dict], sounds: dict[str, np.ndarray], duration: float) -> tuple[
        np.ndarray, float]:  # fmt: skip
    """(stereo float32 [n, 2], the scale applied to keep TRACK_PEAK_DBFS)."""
    n = int(round(duration * lib.RATE))
    out = np.zeros((n, 2), dtype=np.float64)
    for h in hits:
        x = _resample(sounds[h["kind"]], h["pitch"]) * 10 ** (h["gain_db"] / 20)
        a = int(round(h["t"] * lib.RATE))
        b = min(n, a + len(x))
        if b <= a:
            continue
        ang = (h["pan"] + 1) * math.pi / 4  # constant power
        out[a:b, 0] += x[: b - a] * math.cos(ang) * math.sqrt(2)
        out[a:b, 1] += x[: b - a] * math.sin(ang) * math.sqrt(2)
    peak = float(np.abs(out).max()) if n else 0.0
    cap = 10 ** (TRACK_PEAK_DBFS / 20)
    scale = cap / peak if peak > cap else 1.0
    return (out * scale).astype(np.float32), scale


def _words(job: Path, edl) -> list[dict]:
    p = job / "work" / "words.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text())
        words = data["words"] if isinstance(data, dict) else data
        return map_words(words, edl.segments, edl.fps)
    except (ValueError, KeyError, TypeError):
        return []


def hit_key(h: dict) -> str:
    return f"{h['t']:.4f}:{h['kind']}"


def build(job: Path, say=print, fit: dict[str, float] | None = None) -> dict:
    """work/sfx.wav + work/sfx.json from the spans' events (module docstring). `fit`
    {hit_key: dB}: extra gain per hit (render lowers hits that would cover speech); kept in
    sfx.json "fit" and in each hit's "fit_db"."""
    from ..edl import EdlError, load

    work = job / "work"
    try:
        edl = load(work / "edl.json")
    except (EdlError, OSError, ValueError) as e:
        raise ToolError(f"work/edl.json cannot be read: {e}") from None
    duration = edl.output_duration()
    files = event_files(job)
    events, warnings = read_events(files, duration)
    words = _words(job, edl)
    seed = _seed("sfx", job.name)
    hits, skipped = plan_hits(events, [w["t0"] for w in words], float(edl.fps), seed, duration)
    fit = {k: float(v) for k, v in (fit or {}).items()}
    for h in hits:
        if (f := fit.get(hit_key(h))) is not None:
            h["fit_db"] = round(f, 2)
            h["gain_db"] = round(h["gain_db"] + f, 2)
    used: dict[str, lib.Sound] = {}
    samples: dict[str, np.ndarray] = {}
    for kind in sorted({h["kind"] for h in hits}):
        pool = lib.preferred(kind)
        pick = pool[_seed(seed, "pick", kind) % len(pool)]
        try:
            samples[kind] = lib.samples(pick)
        except ToolError as e:  # a library file that changed: fall back to procedural
            warnings.append(f"{kind}: {e}; using a generated sound")
            pick = lib.procedural(kind)[_seed(seed, "pick", kind) % 3]
            samples[kind] = lib.samples(pick)
        used[kind] = pick
    track, scale = render_track(hits, samples, duration)
    wav = work / "sfx.wav"
    lib.write_wav(wav, track.reshape(-1), channels=2)
    peak = float(np.abs(track).max()) if len(track) else 0.0
    counts = {k: sum(1 for h in hits if h["kind"] == k) for k in lib.KINDS}
    doc = {
        "version": VERSION,
        "seed": seed,
        "duration_s": round(duration, 3),
        "rate": lib.RATE,
        "events": events_sha(files),
        "wav_sha256": lib.sha256_file(wav),
        "sounds": {k: s.doc() for k, s in used.items()},
        "hits": hits,
        "skipped": skipped,
        "counts": {k: v for k, v in counts.items() if v},
        "scale": round(scale, 4),
        "peak_dbfs": round(lib.db(peak), 2) if hits else None,
        "fit": {k: round(v, 2) for k, v in fit.items()},
        "warnings": warnings,
    }
    write_json(work / "sfx.json", doc)
    for w in warnings:
        say(f"warning: {w}")
    say(f"sfx: {len(hits)} hits from {len(events)} events ({len(skipped)} skipped)")
    return doc


def is_current(job: Path) -> bool:
    """work/sfx.wav + sfx.json match today's events (same files, same hashes)."""
    work = job / "work"
    try:
        doc = json.loads((work / "sfx.json").read_text())
    except (OSError, ValueError):
        return False
    wav = work / "sfx.wav"
    if not isinstance(doc, dict) or doc.get("version") != VERSION or not wav.is_file():
        return False
    try:
        return doc.get("events") == events_sha(event_files(job)) and doc.get(
            "wav_sha256"
        ) == lib.sha256_file(wav)
    except ToolError:
        return False


def read(job: Path) -> dict | None:
    try:
        doc = json.loads((job / "work" / "sfx.json").read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


# --------------------------------------------------------------------------- CLI


def _job(args) -> Path:
    return resolve_job(getattr(args, "job_pos", None) or args.job)


def cmd_build(args) -> dict:
    job = _job(args)
    doc = build(job)
    return {
        "job": str(job), "file": "work/sfx.wav", "hits": len(doc["hits"]),
        "counts": doc["counts"], "skipped": len(doc["skipped"]),
        "sounds": {k: {"id": s["id"], "origin": s["origin"]} for k, s in doc["sounds"].items()},
        "level": level(read_job(job)), "warnings": doc["warnings"],
    }  # fmt: skip


def _kinds(args) -> list[str]:
    return [lib.check_kind(args.kind)] if getattr(args, "kind", None) else list(lib.KINDS)


def cmd_fetch(args) -> dict:
    from ._material_providers import host_only

    try:
        host_only()
    except Exception as e:  # ProviderError: inside an agent run
        raise ToolError(str(e)) from None
    report = lib.fetch(_kinds(args), per_kind=args.per_kind)
    return {"library": str(lib.root()), "kinds": report}


def library_view(kinds: list[str] | None = None) -> dict:
    """{kind: {"uses": origin, "sounds": [Sound docs, procedural included]}}."""
    out = {}
    for k in kinds or list(lib.KINDS):
        out[k] = {"uses": lib.preferred(k)[0].origin, "sounds": [s.doc() for s in lib.sounds(k)]}
    return out


def cmd_list(args) -> dict:
    return {"library": str(lib.root()), "kinds": library_view(_kinds(args))}


def cmd_add(args) -> dict:
    s = lib.add_upload(args.kind, Path(args.file).expanduser(), args.title)
    return {"added": s.doc()}


def cmd_remove(args) -> dict:
    if not lib.remove(args.kind, args.id):
        raise ToolError(f"no stored sound {args.id!r} for {args.kind}")
    return {"removed": args.id, "kind": args.kind}


def register(subparsers) -> None:
    p = subparsers.add_parser("sfx", help="sound effects of the motion scenes (work/sfx.wav)")
    sub = p.add_subparsers(dest="sfx_cmd", required=True)
    b = sub.add_parser("build", help="events.json -> work/sfx.wav", parents=[job_parent()])
    b.add_argument("job_pos", nargs="?", metavar="job", help="job folder or id")
    b.set_defaults(handler=run_tool("sfx build", cmd_build))
    f = sub.add_parser("fetch", help="download CC0 sounds from Openverse into the library")
    f.add_argument("--kind", help="only this kind")
    f.add_argument("--per-kind", type=int, default=2, help="sounds per kind (default 2)")
    f.set_defaults(handler=run_tool("sfx fetch", cmd_fetch))
    ls = sub.add_parser("list", help="the sound library")
    ls.add_argument("--kind", help="only this kind")
    ls.set_defaults(handler=run_tool("sfx list", cmd_list))
    a = sub.add_parser("add", help="your own sound for a kind")
    a.add_argument("kind", choices=list(lib.KINDS))
    a.add_argument("file")
    a.add_argument("--title")
    a.set_defaults(handler=run_tool("sfx add", cmd_add))
    r = sub.add_parser("remove", help="remove a stored sound")
    r.add_argument("kind", choices=list(lib.KINDS))
    r.add_argument("id")
    r.set_defaults(handler=run_tool("sfx remove", cmd_remove))
