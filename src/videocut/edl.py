"""Edit decision list (EDL): the single source of truth for a cut (docs/BRIEF.md §6).

Segment times (`in`, `out`, `zoom_keys`) are in SOURCE time; captions, overlays and b-roll
are in OUTPUT time. Phase 1 uses segments only; captions/overlays/broll/music are kept as
plain dicts and validated loosely (shape and times), their detailed schema comes later.

Validation works on the raw JSON and reports every problem with a JSON path
(`$.segments[2].out`), so Claude can fix an EDL in one round.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ZOOM_MIN, ZOOM_MAX = 1.0, 2.0
FADE_MAX_MS = 200
DEFAULT_FADE_MS = 30
BLINK_SEARCH_MS = 150  # how far a blink-free cut point may be suggested from the original


class EdlError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class Crop:
    cx: float = 0.5
    cy: float = 0.5
    zoom: float = 1.0


@dataclass
class Segment:
    start: float  # "in" in JSON (source seconds)
    end: float  # "out" in JSON (source seconds)
    fade_ms: int = DEFAULT_FADE_MS
    crop: Crop | None = None
    zoom_keys: list[tuple[float, float]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Edl:
    fps: float
    size: tuple[int, int]
    source: str
    segments: list[Segment]
    captions: list[dict] = field(default_factory=list)
    overlays: list[dict] = field(default_factory=list)
    broll: list[dict] = field(default_factory=list)
    music: dict | None = None

    def output_duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def cut_points(self) -> list[float]:
        """Output times of every segment boundary, including 0 and the end.

        Interior cuts (the ones to verify) are `cut_points()[1:-1]`.
        """
        points, t = [0.0], 0.0
        for s in self.segments:
            t += s.duration
            points.append(round(t, 6))
        return points

    def to_dict(self) -> dict:
        segs = []
        for s in self.segments:
            d: dict = {"in": s.start, "out": s.end, "fade_ms": s.fade_ms}
            if s.crop is not None:
                d["crop"] = {"cx": s.crop.cx, "cy": s.crop.cy, "zoom": s.crop.zoom}
            if s.zoom_keys:
                d["zoom_keys"] = [list(k) for k in s.zoom_keys]
            segs.append(d)
        data: dict = {
            "fps": self.fps,
            "size": list(self.size),
            "source": self.source,
            "segments": segs,
        }
        for key in ("captions", "overlays", "broll"):
            if getattr(self, key):
                data[key] = getattr(self, key)
        if self.music is not None:
            data["music"] = self.music
        return data


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _rel_path(v) -> bool:
    """A path inside the job folder: relative, no '..'."""
    if not isinstance(v, str) or not v or "\\" in v:
        return False
    p = PurePosixPath(v)
    return not p.is_absolute() and ".." not in p.parts


def validate(data, source_duration: float | None = None) -> list[str]:
    """All problems in a raw EDL dict, each prefixed with its JSON path. Empty = valid."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["$: must be an object"]

    fps = data.get("fps")
    if not (_num(fps) and 0 < fps <= 240):
        errs.append("$.fps: must be a number in (0, 240]")
    size = data.get("size")
    if not (
        isinstance(size, list)
        and len(size) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size)
    ):
        errs.append("$.size: must be [width, height] with positive integers")
    if not _rel_path(data.get("source")):
        errs.append("$.source: must be a relative path inside the job folder")

    segs = data.get("segments")
    out_dur = 0.0
    if not isinstance(segs, list) or not segs:
        errs.append("$.segments: must be a non-empty list")
        segs = []
    for i, s in enumerate(segs):
        p = f"$.segments[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{p}: must be an object")
            continue
        a, b = s.get("in"), s.get("out")
        if not _num(a):
            errs.append(f"{p}.in: must be a number (source seconds)")
        if not _num(b):
            errs.append(f"{p}.out: must be a number (source seconds)")
        if _num(a) and _num(b):
            if a < 0:
                errs.append(f"{p}.in: must be >= 0")
            if a >= b:
                errs.append(f"{p}: in ({a}) must be < out ({b})")
            else:
                out_dur += b - a
            if source_duration is not None and b > source_duration + 1e-3:
                errs.append(f"{p}.out: {b} is beyond the source duration {source_duration}")
        fade = s.get("fade_ms", DEFAULT_FADE_MS)
        if not (_num(fade) and 0 <= fade <= FADE_MAX_MS):
            errs.append(f"{p}.fade_ms: must be a number in 0..{FADE_MAX_MS}")
        crop = s.get("crop")
        if crop is not None:
            if not isinstance(crop, dict):
                errs.append(f"{p}.crop: must be an object")
            else:
                for k in ("cx", "cy"):
                    v = crop.get(k, 0.5)
                    if not (_num(v) and 0 <= v <= 1):
                        errs.append(f"{p}.crop.{k}: must be a number in 0..1")
                z = crop.get("zoom", 1.0)
                if not (_num(z) and ZOOM_MIN <= z <= ZOOM_MAX):
                    errs.append(f"{p}.crop.zoom: must be a number in {ZOOM_MIN}..{ZOOM_MAX}")
        keys = s.get("zoom_keys", [])
        if not isinstance(keys, list):
            errs.append(f"{p}.zoom_keys: must be a list of [source_time, zoom]")
            keys = []
        for j, k in enumerate(keys):
            kp = f"{p}.zoom_keys[{j}]"
            if not (isinstance(k, list) and len(k) == 2 and _num(k[0]) and _num(k[1])):
                errs.append(f"{kp}: must be [source_time, zoom]")
                continue
            if _num(a) and _num(b) and not a <= k[0] <= b:
                errs.append(f"{kp}: time {k[0]} is outside the segment [{a}, {b}]")
            if not ZOOM_MIN <= k[1] <= ZOOM_MAX:
                errs.append(f"{kp}: zoom must be in {ZOOM_MIN}..{ZOOM_MAX}")

    # Loose checks for the later phases: shape, output-time ranges, paths inside the job.
    for key in ("captions", "overlays", "broll"):
        items = data.get(key, [])
        if not isinstance(items, list):
            errs.append(f"$.{key}: must be a list")
            continue
        for i, it in enumerate(items):
            p = f"$.{key}[{i}]"
            if not isinstance(it, dict):
                errs.append(f"{p}: must be an object")
                continue
            t0, t1 = it.get("t0"), it.get("t1")
            if not (_num(t0) and _num(t1) and 0 <= t0 < t1):
                errs.append(f"{p}: needs numbers 0 <= t0 < t1 (output seconds)")
            elif out_dur and t1 > out_dur + 1e-3:
                errs.append(f"{p}.t1: {t1} is beyond the output duration {round(out_dur, 3)}")
            if key == "broll" and not _rel_path(it.get("file")):
                errs.append(f"{p}.file: must be a relative path inside the job folder")
            if key == "overlays" and not isinstance(it.get("scene"), str):
                errs.append(f"{p}.scene: must be a scene name")
    music = data.get("music")
    if music is not None:
        if not isinstance(music, dict):
            errs.append("$.music: must be an object")
        else:
            if not _rel_path(music.get("file")):
                errs.append("$.music.file: must be a relative path")
            if "gain_db" in music and not _num(music["gain_db"]):
                errs.append("$.music.gain_db: must be a number")
    return errs


def from_dict(data, source_duration: float | None = None) -> Edl:
    errors = validate(data, source_duration)
    if errors:
        raise EdlError(errors)
    segs = []
    for s in data["segments"]:
        crop = s.get("crop")
        segs.append(
            Segment(
                start=float(s["in"]),
                end=float(s["out"]),
                fade_ms=s.get("fade_ms", DEFAULT_FADE_MS),
                crop=Crop(**{k: float(crop[k]) for k in ("cx", "cy", "zoom") if k in crop})
                if crop is not None
                else None,
                zoom_keys=[(float(t), float(z)) for t, z in s.get("zoom_keys", [])],
            )
        )
    return Edl(
        fps=data["fps"],
        size=(data["size"][0], data["size"][1]),
        source=data["source"],
        segments=segs,
        captions=data.get("captions", []),
        overlays=data.get("overlays", []),
        broll=data.get("broll", []),
        music=data.get("music"),
    )


def load(path: Path, source_duration: float | None = None) -> Edl:
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise EdlError([f"$: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}"]) from e
    return from_dict(data, source_duration)


def save(edl: Edl, path: Path, source_duration: float | None = None) -> None:
    """Validate, then write atomically."""
    data = edl.to_dict()
    errors = validate(data, source_duration)
    if errors:
        raise EdlError(errors)
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def blink_warnings(
    edl: Edl,
    blinks: list[dict],
    words: list[dict] | None = None,
    source_duration: float | None = None,
) -> list[dict]:
    """Every segment boundary (in or out, source time) inside a blink interval +-1 output
    frame, with the nearest time within +-BLINK_SEARCH_MS that is outside every blink (+-1
    frame) and every word (`suggest`, None when there is none). `blinks` come from
    work/face.json, `words` from work/words.json. Warnings, never errors."""
    pad = 1 / edl.fps
    words = words or []

    def blink_at(t: float) -> dict | None:
        return next((b for b in blinks if b["t0"] - pad <= t <= b["t1"] + pad), None)

    def free(t: float) -> bool:
        return not blink_at(t) and not any(w["t0"] < t < w["t1"] for w in words)

    def valid(edge: str, s: Segment, c: float) -> bool:
        if edge == "in":
            return 0 <= c < s.end
        return c > s.start and (source_duration is None or c <= source_duration)

    out = []
    # 5 ms grid, nearest first
    steps = sorted(range(-BLINK_SEARCH_MS, BLINK_SEARCH_MS + 1, 5), key=abs)
    for i, s in enumerate(edl.segments):
        for edge, t in (("in", s.start), ("out", s.end)):
            b = blink_at(t)
            if b is None:
                continue
            cands = (round(t + ms / 1000, 3) for ms in steps)
            suggest = next((c for c in cands if valid(edge, s, c) and free(c)), None)
            out.append(
                {
                    "path": f"$.segments[{i}].{edge}",
                    "t": t,
                    "blink": [b["t0"], b["t1"]],
                    "suggest": suggest,
                }
            )
    return out
