"""Edit decision list (EDL): the single source of truth for a cut (docs/BRIEF.md §6).

Segment times (`in`, `out`, `zoom_keys`) are in SOURCE time; captions, overlays and b-roll
are in OUTPUT time. Phase 1 uses segments only; captions/overlays/broll/music are kept as
plain dicts and validated loosely (shape and times), their detailed schema comes later.

Product demos add a screen recording (work/sync.json aligns it): a segment may set a
`layout`, `screen_zoom` keyframes and `redact` blocks, all in CAMERA time like `in`/`out`;
the screen frame shown at camera time t is screen time `screen_time(t, sync)`.

Validation works on the raw JSON and reports every problem with a JSON path
(`$.segments[2].out`), so Claude can fix an EDL in one round.
"""

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ZOOM_MIN, ZOOM_MAX = 1.0, 2.0
FADE_MAX_MS = 200
DEFAULT_FADE_MS = 30  # legacy: render crossfades every cut (xfade_ms), fade_ms is ignored
XFADE_MAX_MS = 300
BLINK_SEARCH_MS = 150  # how far a blink-free cut point may be suggested from the original
LAYOUTS = ("face", "screen", "pip", "split")
DEFAULT_LAYOUT = "face"
MIN_BOX_SIDE = 0.02  # a screen box narrower than 2 % of the screen is a typo, not a zoom
SYNC_METHODS = ("audio_xcorr", "clap", "manual")


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
    xfade_ms: float | None = None  # crossfade at this segment's cuts (None: the cut style's)
    speech_cut_ok: str | None = None  # why a cut into speech is intended (edl check, verify)
    zoom_keys: list[tuple[float, float]] = field(default_factory=list)
    layout: str = DEFAULT_LAYOUT
    screen_zoom: list[dict] = field(default_factory=list)  # [{"t": camera_s, "box": [..]}]
    redact: list[dict] = field(default_factory=list)  # [{"t0", "t1": camera_s, "box": [..]}]

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
            if s.layout != DEFAULT_LAYOUT:
                d["layout"] = s.layout
            if s.screen_zoom:
                d["screen_zoom"] = s.screen_zoom
            if s.redact:
                d["redact"] = s.redact
            if s.xfade_ms is not None:
                d["xfade_ms"] = s.xfade_ms
            if s.speech_cut_ok:
                d["speech_cut_ok"] = s.speech_cut_ok
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


def _box_errors(box, p: str) -> list[str]:
    ok = isinstance(box, list) and len(box) == 4 and all(_num(v) and 0 <= v <= 1 for v in box)
    if not ok:
        return [f"{p}: must be [x0, y0, x1, y1], numbers in 0..1 (screen fractions)"]
    if box[2] - box[0] < MIN_BOX_SIDE or box[3] - box[1] < MIN_BOX_SIDE:
        return [f"{p}: box {box} is degenerate (x1 > x0 and y1 > y0, each side >= {MIN_BOX_SIDE})"]
    return []


def _screen_errors(s: dict, p: str, a, b) -> list[str]:
    """Layout, screen_zoom and redact of one segment (a, b = its in/out when numbers)."""
    errs: list[str] = []
    layout = s.get("layout", DEFAULT_LAYOUT)
    if layout not in LAYOUTS:
        errs.append(f"{p}.layout: must be one of {', '.join(LAYOUTS)}")
        layout = DEFAULT_LAYOUT
    inside = _num(a) and _num(b)
    zoom = s.get("screen_zoom", [])
    if not isinstance(zoom, list):
        errs.append(f"{p}.screen_zoom: must be a list of {{t, box}}")
        zoom = []
    if zoom and layout == "face":
        errs.append(f"{p}.screen_zoom: needs layout screen, pip or split (the screen is not shown)")
    last = -math.inf
    for j, k in enumerate(zoom):
        kp = f"{p}.screen_zoom[{j}]"
        if not isinstance(k, dict):
            errs.append(f"{kp}: must be an object {{t, box}}")
            continue
        t = k.get("t")
        if not _num(t):
            errs.append(f"{kp}.t: must be a number (camera seconds)")
        else:
            if inside and not a <= t <= b:
                errs.append(f"{kp}.t: {t} is outside the segment [{a}, {b}]")
            if t <= last:
                errs.append(f"{kp}.t: keyframes must be in increasing time order")
            last = t
        errs += _box_errors(k.get("box"), f"{kp}.box")
    red = s.get("redact", [])
    if not isinstance(red, list):
        errs.append(f"{p}.redact: must be a list of {{t0, t1, box}}")
        red = []
    if red and layout == "face":
        errs.append(f"{p}.redact: needs layout screen, pip or split (the screen is not shown)")
    for j, r in enumerate(red):
        rp = f"{p}.redact[{j}]"
        if not isinstance(r, dict):
            errs.append(f"{rp}: must be an object {{t0, t1, box}}")
            continue
        t0, t1 = r.get("t0"), r.get("t1")
        if not (_num(t0) and _num(t1) and t0 < t1):
            errs.append(f"{rp}: needs numbers t0 < t1 (camera seconds)")
        elif inside and not (a <= t0 and t1 <= b):
            errs.append(f"{rp}: [{t0}, {t1}] is outside the segment [{a}, {b}]")
        errs += _box_errors(r.get("box"), f"{rp}.box")
    return errs


def validate_sync(data) -> list[str]:
    """Problems in a work/sync.json dict. Empty = valid."""
    if not isinstance(data, dict):
        return ["sync: must be an object"]
    errs: list[str] = []
    if not _num(data.get("offset_s")):
        errs.append("sync.offset_s: must be a number (screen_time = camera_time + offset_s)")
    if data.get("method") not in SYNC_METHODS:
        errs.append(f"sync.method: must be one of {', '.join(SYNC_METHODS)}")
    c = data.get("confidence")
    if not (_num(c) and 0 <= c <= 1):
        errs.append("sync.confidence: must be a number in 0..1")
    d = data.get("drift_ppm")
    if d is not None and not _num(d):
        errs.append("sync.drift_ppm: must be a number or null")
    ref = data.get("drift_ref")
    if ref is not None and not _num(ref):
        errs.append("sync.drift_ref: must be a number")
    ov = data.get("overlap")
    if not (isinstance(ov, list) and len(ov) == 2 and all(_num(v) for v in ov) and ov[0] < ov[1]):
        errs.append("sync.overlap: must be [camera_t0, camera_t1] with t0 < t1")
    return errs


def read_sync(path: Path) -> dict | None:
    """A valid sync.json as dict; None when the file is missing, unreadable or invalid."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if not validate_sync(data) else None


def screen_time(camera_t: float, sync: dict) -> float:
    """Screen time of the frame shown at camera time `camera_t`. Drift (sync["drift_ppm"],
    at most a few frames per minute) is not applied: offset_s is exact at drift_ref."""
    return camera_t + sync["offset_s"]


def overlap_errors(data, sync: dict | None) -> list[str]:
    """Every non-face segment must lie inside sync["overlap"] (camera time), where both
    recordings have pictures; without a sync (`None`) any non-face segment is an error
    (a missing, unreadable or invalid work/sync.json all read as None)."""
    segs = data.get("segments") if isinstance(data, dict) else None
    errs: list[str] = []
    for i, s in enumerate(segs if isinstance(segs, list) else []):
        if not isinstance(s, dict) or s.get("layout", DEFAULT_LAYOUT) in ("face", None):
            continue
        a, b = s.get("in"), s.get("out")
        p = f"$.segments[{i}]"
        if sync is None:
            errs.append(
                f"{p}.layout: {s.get('layout')!r} needs a valid work/sync.json (run `tool sync`)"
            )
        elif _num(a) and _num(b):
            o0, o1 = sync["overlap"]
            if a < o0 - 1e-3 or b > o1 + 1e-3:
                errs.append(
                    f"{p}: [{a}, {b}] is outside the time both recordings cover [{o0}, {o1}]; "
                    "use layout face there"
                )
    return errs


def validate(
    data,
    source_duration: float | None = None,
    sync: dict | None = None,
    check_sync: bool = False,
) -> list[str]:
    """All problems in a raw EDL dict, each prefixed with its JSON path. Empty = valid.

    `check_sync` also checks that non-face segments lie inside the `sync` overlap
    (a missing sync then fails them)."""
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
        xf = s.get("xfade_ms")
        if xf is not None and not (_num(xf) and 0 <= xf <= XFADE_MAX_MS):
            errs.append(f"{p}.xfade_ms: must be a number in 0..{XFADE_MAX_MS}")
        for k in ("in_src", "out_src"):
            if k in s and not (_num(s[k]) and s[k] >= 0):
                errs.append(f"{p}.{k}: must be a number >= 0 (the cut point before `edl snap`)")
        ok = s.get("speech_cut_ok")
        if ok is not None and not (isinstance(ok, str) and ok.strip()):
            errs.append(f"{p}.speech_cut_ok: must be a reason (text)")
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
        errs += _screen_errors(s, p, a, b)

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
    if check_sync:
        errs += overlap_errors(data, sync)
    return errs


def from_dict(
    data, source_duration: float | None = None, sync: dict | None = None, check_sync: bool = False
) -> Edl:
    errors = validate(data, source_duration, sync, check_sync)
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
                xfade_ms=s.get("xfade_ms"),
                speech_cut_ok=s.get("speech_cut_ok"),
                layout=s.get("layout", DEFAULT_LAYOUT),
                screen_zoom=s.get("screen_zoom", []),
                redact=s.get("redact", []),
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


def load(
    path: Path, source_duration: float | None = None, sync: dict | None = None,
    check_sync: bool = False,
) -> Edl:  # fmt: skip
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise EdlError([f"$: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}"]) from e
    return from_dict(data, source_duration, sync, check_sync)


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


def output_spans(edl: Edl) -> list[tuple[float, float]]:
    """Output [t0, t1) of every segment, snapped to the output frame grid exactly as render
    places them (_render_plan.build: a segment ends at round(cumulative duration * fps))."""
    spans, t_out, f0 = [], 0.0, 0
    for s in edl.segments:
        t_out += s.duration
        f1 = round(t_out * edl.fps)
        spans.append((f0 / edl.fps, f1 / edl.fps))
        f0 = f1
    return spans


def output_to_source(edl: Edl, t_out: float) -> tuple[int, float] | None:
    """(segment index, source time) of the frame shown at output time `t_out`; None outside
    the cut (before 0 or at/after its end)."""
    for k, (a, b) in enumerate(output_spans(edl)):
        if a <= t_out < b:
            s = edl.segments[k]
            return k, min(s.start + (t_out - a), s.end)
    return None


def source_to_output(edl: Edl, t_src: float) -> list[tuple[int, float]]:
    """Every (segment index, output time) at which source time `t_src` is shown: none when
    the cut leaves it out, several when segments repeat it."""
    out = []
    for k, (a, b) in enumerate(output_spans(edl)):
        s = edl.segments[k]
        if s.start <= t_src < s.end and a + (t_src - s.start) < b:
            out.append((k, round(a + (t_src - s.start), 6)))
    return out


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


def remap_output_times(data: dict, before: list[dict], after: list[dict]) -> dict:
    """`data` (a raw EDL dict) with every output time (captions and their words, overlays,
    broll, cover) moved from the segments `before` to `after` (the same segments with other
    in/out, e.g. after `edl snap`): a time keeps its source time within its segment, clamped
    to the segment. Start times (t0, cover) belong to the segment they fall in, end times
    (t1) to the one they end."""

    def spans(segs):
        out, t = [], 0.0
        for s in segs:
            out.append((t, t + s["out"] - s["in"]))
            t = out[-1][1]
        return out

    old, new = spans(before), spans(after)
    if not old:
        return data

    def move(t, end: bool):
        if not _num(t):
            return t
        k = next(
            (i for i, (a, b) in enumerate(old) if (a < t <= b if end else a <= t < b)),
            0 if t <= 0 else len(old) - 1,
        )
        src = before[k]["in"] + (t - old[k][0])
        a, b = new[k]
        v = a + (src - after[k]["in"])
        if t >= old[-1][1]:  # at or after the end (a held tail): shift with the end
            v = new[-1][1] + (t - old[-1][1])
        return round(min(max(v, a), b) if t < old[-1][1] else v, 3)

    def item(it):
        if not isinstance(it, dict):
            return it
        it = {**it}
        if "t0" in it:
            it["t0"] = move(it["t0"], False)
        if "t1" in it:
            it["t1"] = move(it["t1"], True)
        if isinstance(it.get("words"), list):
            it["words"] = [item(w) for w in it["words"]]
        return it

    out = {**data}
    for key in ("captions", "overlays", "broll"):
        if isinstance(data.get(key), list):
            out[key] = [item(it) for it in data[key]]
    if _num(data.get("cover")):
        out["cover"] = move(data["cover"], False)
    return out


def shift_source_times(data: dict, offset: float, duration: float | None = None) -> dict:
    """Copy of the EDL dict with every cut point and zoom key moved by `offset` seconds
    (ingest's upgrade of a job whose words were in audio-stream time: the cut was placed on
    those words). `redact` blocks stay: they were measured on the video. Output times
    (captions, overlays) do not change, since every segment keeps its length; a segment
    that would leave [0, duration] is moved back inside as a whole."""

    segs = []
    for s in data.get("segments", []):
        s = dict(s)
        if _num(s.get("in")) and _num(s.get("out")):
            d = offset
            if s["in"] + d < 0:
                d = -s["in"]
            if duration is not None and s["out"] + d > duration:
                d = duration - s["out"]
            for k in ("in", "out", "in_src", "out_src"):
                if _num(s.get(k)):
                    s[k] = round(s[k] + d, 4)
            if isinstance(s.get("zoom_keys"), list):
                s["zoom_keys"] = [
                    [round(k[0] + d, 4), *k[1:]] if isinstance(k, list) and k and _num(k[0]) else k
                    for k in s["zoom_keys"]
                ]
            if isinstance(s.get("screen_zoom"), list):
                s["screen_zoom"] = [
                    {**k, "t": round(k["t"] + d, 4)} if isinstance(k, dict) and _num(k.get("t"))
                    else k
                    for k in s["screen_zoom"]
                ]  # fmt: skip
        segs.append(s)
    return {**data, "segments": segs}


def segments_sha(data: dict) -> str:
    """Hash of what makes the cut (source, fps, size, segments) of an EDL dict; captions and
    overlays are not part of it."""
    cut = {k: data.get(k) for k in ("source", "fps", "size", "segments")}
    return hashlib.sha256(json.dumps(cut, sort_keys=True).encode()).hexdigest()
