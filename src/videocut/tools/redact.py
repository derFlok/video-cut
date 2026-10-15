"""`videocut tool redact text|measure`: where personal data is on the screen, measured.

The plan agent decides WHICH text is personal; these tools measure WHERE it is, with local
text recognition (macOS Vision, _ocr.py). Nothing leaves the machine.

    text      reads the text on the screen recording at every TEXT_STEP seconds and just
              after every scene change (work/screen.json) and writes work/screen_text.json
                  {"version", "source", "size", "step", "times": [screen s],
                   "lines": [{"t", "camera_t", "box", "text", "conf"}]}
              (t screen seconds, camera_t with a valid work/sync.json, box normalised to
              the screen). The summary lists every distinct text with the time it is seen
              and `suggest` (e-mail addresses, phone numbers and IBANs found by pattern).
              With screenshots (job.json "assets") or uploaded slide pages
              (input/slides/page_NN.png: untrusted, treated like screenshots), also
              `asset_texts` [{"asset", "text", "box", "conf"}] and their pattern hits.
    measure   for every candidate of work/redaction.json with "text": finds the text on
              the screen (inside the plan's time "range", and wherever else it is shown),
              follows it over time (samples every MEASURE_STEP s, halved where it moves or
              appears/disappears down to MIN_DT) and writes the measured "track" back into
              the candidate (redaction.py), plus work/redaction_measure.json with every
              sample {"<id>": [{"t", "camera_t", "boxes"}]}.
              Fail closed: where the text is not found inside the plan's range (or only
              outside the named area), the track covers the whole named area ("box",
              plus AREA_PAD) and says so ("measured": "partial" or "failed", "note"); a
              frame that cannot be read stops the tool. "measure_sig" ties the result to
              the text, box and range it was made for. A screenshot candidate with
              "text" is measured on its image.

Run measure after writing work/redaction.json and before plan.json (it can move t0/t1).
"""

import json
import math
import re
import threading
from pathlib import Path

import numpy as np

from .. import redaction as red_mod
from ..edl import read_sync
from . import _job_slides, _ocr
from ._layouts import probe_screen, screen_source
from .common import (
    ToolError,
    binary,
    is_fresh,
    job_parent,
    read_job,
    resolve_job,
    run,
    run_tool,
    write_json,
)

VERSION = 1
TEXT_STEP = 2.0  # seconds between `redact text` samples
SCENE_LAG = 0.2  # seconds after a scene change
MEASURE_STEP = 1.0  # seconds between measure samples (finer where the text moves)
MIN_DT = 1 / 15  # finest sample spacing (2 frames at 30 fps)
MOVE_TOL = 0.25  # of the text height: a larger move between two samples is sampled finer
GAP_FILL = 1.0  # seconds: a shorter gap between two sightings close together is covered
PAIR_DIST = 0.15  # normalised: sightings further apart are different places
MARGIN_K = 0.3  # margin around the measured text, of its height ...
MARGIN_PX = 6  # ... at least this many source px
AREA_PAD = 0.03  # normalised padding of a named area that could not be measured
MAX_SAMPLES = 900  # per candidate, at least; 4 per MEASURE_STEP of the recording
SUMMARY_TEXTS = 200
SEEK_EPS = 5e-4  # s: seek this far before a frame's pts (accurate seek keeps frames >= it)
END_SLACK = 0.1  # s: the very end of a recording may not decode; earlier frames must
FILE = "work/screen_text.json"
SAMPLES_FILE = "work/redaction_measure.json"

PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "phone": re.compile(r"(?<![\w])\+?\d[\d ()/.-]{5,}\d(?![\w])"),
    "customer": re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,}"),  # IBAN-like
}


_TIMES: dict[tuple, np.ndarray] = {}
_TIMES_LOCK = threading.Lock()


def frame_times(src: Path) -> np.ndarray:
    """Sorted pts (seconds, as stored) of every video frame of `src`, from the packets (no
    decoding); cached per file."""
    st = src.stat()
    key = (str(src), st.st_mtime_ns, st.st_size)
    with _TIMES_LOCK:
        if key not in _TIMES:
            out = run(
                [binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "packet=pts_time", "-of", "csv=p=0", str(src)]
            )  # fmt: skip
            vals = []
            for line in out.stdout.split():
                try:
                    vals.append(float(line.strip(",")))
                except ValueError:
                    continue
            _TIMES[key] = np.unique(np.array(vals, dtype=float))
        return _TIMES[key]


def shown_pts(src: Path, info: dict, t: float, fps: float = 30.0) -> float:
    """The pts of the frame shown at screen time t: the last frame at or before it (half
    an output frame of rounding), as the sample-and-hold grid of render and the proxy has
    it. A screen recording writes frames only when something changes, so seeking to t
    itself would give the NEXT change instead."""
    start = info.get("video_start") or 0.0
    times = frame_times(src)
    if not len(times):
        return t + start
    i = int(np.searchsorted(times, t + start + 0.5 / fps, side="right")) - 1
    return float(times[max(i, 0)])


def grab_screen(src: Path, info: dict, t: float, fps: float = 30.0) -> np.ndarray | None:
    """The screen frame shown at screen time t on an `fps` grid (full size, rgb); None
    outside the recording or when it cannot be decoded."""
    if t < 0 or t > info["duration"]:
        return None
    w, h = info["size"]
    pts = shown_pts(src, info, t, fps)
    out = run_bytes(  # -seek_timestamp: -ss is this pts, not an offset from the file start
        ["-seek_timestamp", "1", "-ss", f"{max(pts - SEEK_EPS, 0.0):.6f}", "-i", str(src),
         "-frames:v", "1", "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    )  # fmt: skip
    if len(out) != w * h * 3:
        return None
    return np.frombuffer(out, np.uint8).reshape(h, w, 3)


def run_bytes(args: list[str]) -> bytes:
    import subprocess

    from ..paths import tool_env

    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", *args]
    r = subprocess.run(cmd, capture_output=True, env=tool_env())
    if r.returncode != 0:
        raise ToolError(f"ffmpeg failed: {r.stderr.decode(errors='replace').strip()[-400:]}")
    return r.stdout


def image_rgb(path: Path) -> np.ndarray:
    """A still image (screenshot) as rgb."""
    out = run(
        [binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(path)]
    )  # fmt: skip
    try:
        st = json.loads(out.stdout)["streams"][0]
        w, h = int(st["width"]), int(st["height"])
    except (KeyError, IndexError, ValueError):
        raise ToolError(f"cannot read the image size of {path.name}") from None
    raw = run_bytes(["-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    if len(raw) != w * h * 3:
        raise ToolError(f"cannot decode {path.name}")
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


def _need_ocr() -> None:
    if not _ocr.available():
        raise ToolError("text recognition is not available here (needs macOS Vision)")


# ---------------------------------------------------------------- text


def _sample_times(job: Path, duration: float) -> list[float]:
    times = {round(k * TEXT_STEP, 3) for k in range(int(duration / TEXT_STEP) + 1)}
    try:
        scenes = json.loads((job / "work" / "screen.json").read_text()).get("scenes") or []
    except (OSError, ValueError, AttributeError):
        scenes = []
    for s in scenes:
        if isinstance(s, int | float) and 0 <= s + SCENE_LAG <= duration:
            times.add(round(s + SCENE_LAG, 3))
    return sorted(t for t in times if t <= max(duration - 0.05, 0))


def screen_text(job: Path, force: bool = False) -> dict:
    """work/screen_text.json (made or reused)."""
    _need_ocr()
    src = screen_source(job)
    out = job / FILE
    params = {"version": VERSION, "step": TEXT_STEP, "languages": list(_ocr.LANGUAGES)}
    if not force and is_fresh([out], [src]):
        try:
            doc = json.loads(out.read_text())
        except ValueError:
            doc = None
        if isinstance(doc, dict) and doc.get("params") == params:
            return doc
    info = probe_screen(src)
    sync = read_sync(job / "work" / "sync.json")
    times = _sample_times(job, info["duration"])

    def read(t):
        rgb = grab_screen(src, info, t)
        return [] if rgb is None else _ocr.recognize(rgb)

    lines = []
    for t, found in zip(times, _ocr.pmap(read, times), strict=True):
        for ln in found:
            item = {"t": t}
            if sync:
                item["camera_t"] = round(t - sync["offset_s"], 3)
            lines.append({**item, **ln})
    doc = {
        "version": VERSION,
        "params": params,
        "source": str(src.relative_to(job)),
        "size": info["size"],
        "step": TEXT_STEP,
        "times": times,
        "lines": lines,
    }
    write_json(out, doc)
    return doc


def _distinct(doc: dict) -> list[dict]:
    """One entry per distinct text (folded): first/last sighting, its box, count."""
    seen: dict[str, dict] = {}
    for ln in doc["lines"]:
        key = _ocr._fold(ln["text"])[0]
        if len(key) < 2:
            continue
        e = seen.get(key)
        if e is None:
            seen[key] = e = {"text": ln["text"], "t0": ln["t"], "t1": ln["t"], "box": ln["box"],
                             "n": 0}  # fmt: skip
            if "camera_t" in ln:
                e["camera_t0"] = ln["camera_t"]
        e["t1"], e["n"] = ln["t"], e["n"] + 1
        if "camera_t" in ln:
            e["camera_t1"] = ln["camera_t"]
    return sorted(seen.values(), key=lambda e: (e["t0"], e["box"][1], e["box"][0]))


def suggest(texts: list[dict]) -> list[dict]:
    """Pattern hits among the distinct texts: [{"kind", "text", "line", ...times, "box"}]."""
    out = []
    for e in texts:
        for kind, pat in PATTERNS.items():
            for m in pat.finditer(e["text"]):
                hit = m.group(0).strip()
                if kind == "phone" and sum(ch.isdigit() for ch in hit) < 7:
                    continue
                out.append({"kind": kind, "text": hit, "line": e["text"],
                            **{k: e[k] for k in ("t0", "t1", "camera_t0", "camera_t1", "box")
                               if k in e}})  # fmt: skip
    return out


def _assets(job: Path, meta: dict) -> list[str]:
    """The job's still images that may show personal data: its screenshots (job.json
    "assets") and its uploaded slide pages (input/slides/page_NN.png, untrusted)."""
    return [*(meta.get("assets") or []), *_job_slides.pages(job)]


def asset_texts(job: Path) -> list[dict]:
    """Text lines of the job's screenshots and slide pages (`_assets`): [{"asset", "text",
    "box", "conf"}], box normalised to the image."""
    out = []
    for rel in _assets(job, read_job(job)):
        f = red_mod.asset_file(job, rel)
        if f is None or not f.is_relative_to(job) or not f.is_file():
            continue
        try:
            rgb = image_rgb(f)
        except ToolError as e:
            print(f"warning: {rel}: {e}")
            continue
        out += [{"asset": rel, **ln} for ln in _ocr.recognize(rgb)]
    return out


def text_tool(args) -> dict:
    job = resolve_job(args.job).resolve()
    _need_ocr()
    meta = read_job(job)
    assets = _assets(job, meta)
    if not meta.get("screen") and not assets:
        raise ToolError("the job has no screen recording, no screenshots and no slide pages")
    out: dict = {}
    if meta.get("screen"):
        doc = screen_text(job, force=args.force)
        texts = _distinct(doc)
        hits = suggest(texts)
        print(f"{FILE}: {len(doc['lines'])} text lines at {len(doc['times'])} times, "
              f"{len(texts)} distinct, {len(hits)} pattern hit(s)")  # fmt: skip
        for h in hits:
            print(f"  {h['kind']}: {h['text']!r} ({h['t0']:.1f}-{h['t1']:.1f} s screen)")
        out = {
            "file": FILE,
            "times": len(doc["times"]),
            "texts": texts[:SUMMARY_TEXTS],
            "more_texts": max(0, len(texts) - SUMMARY_TEXTS),
            "suggest": hits,
        }
    if assets:
        lines = asset_texts(job)
        hits = [
            {**h, "asset": ln["asset"]}
            for ln in lines
            for h in suggest([{"text": ln["text"], "box": ln["box"]}])
        ]
        print(f"screenshots/slide pages: {len(lines)} text lines, {len(hits)} pattern hit(s)")
        out["asset_texts"] = lines[:SUMMARY_TEXTS]
        out["suggest"] = out.get("suggest", []) + hits
    return out


# ---------------------------------------------------------------- measure


def _center(b) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _union(boxes) -> list[float]:
    boxes = list(boxes)
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]  # fmt: skip


def _dist(a, b) -> float:
    (ax, ay), (bx, by) = _center(a), _center(b)
    return math.hypot(ax - bx, ay - by)


def _pairs(a: list, b: list) -> tuple[list[tuple], list, list]:
    """Greedy nearest pairing of the sightings at two sample times: (pairs, only_a, only_b)."""
    cand = sorted((_dist(x, y), i, j) for i, x in enumerate(a) for j, y in enumerate(b))
    ua, ub, pairs = set(), set(), []
    for d, i, j in cand:
        if d > PAIR_DIST or i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        pairs.append((a[i], b[j]))
    return (
        pairs,
        [x for i, x in enumerate(a) if i not in ua],
        [y for j, y in enumerate(b) if j not in ub],
    )


def _moved(pairs, size) -> bool:
    w, h = size
    for x, y in pairs:
        text_h = max((x[3] - x[1]) * h, 1.0)
        if max(abs(x[k] - y[k]) * (w if k % 2 == 0 else h) for k in range(4)) > MOVE_TOL * text_h:
            return True
    return False


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _pad(box, size) -> list[float]:
    w, h = size
    m = max(MARGIN_K * (box[3] - box[1]) * h, MARGIN_PX)
    return [max(0.0, box[0] - m / w), max(0.0, box[1] - m / h),
            min(1.0, box[2] + m / w), min(1.0, box[3] + m / h)]  # fmt: skip


def _pad_area(box) -> list[float]:
    return [max(0.0, box[0] - AREA_PAD), max(0.0, box[1] - AREA_PAD),
            min(1.0, box[2] + AREA_PAD), min(1.0, box[3] + AREA_PAD)]  # fmt: skip


class Tracker:
    """Samples one text over screen time. `look(t)` -> list of boxes (normalised) where it
    is seen at screen time t; `prefetch(ts)` (optional) reads many times at once before
    they are looked at."""

    def __init__(self, look, duration: float, size: tuple[int, int], prefetch=None):
        self.look, self.duration, self.size = look, duration, size
        self.prefetch = prefetch
        self.seen: dict[float, list] = {}
        self.limit = max(MAX_SAMPLES, math.ceil(4 * duration / MEASURE_STEP))

    def _t(self, t: float) -> float:
        return round(min(max(t, 0.0), self.duration), 4)

    def at(self, t: float) -> list:
        t = self._t(t)
        if t not in self.seen:
            if len(self.seen) >= self.limit:
                raise ToolError(f"more than {self.limit} samples for one text")
            self.seen[t] = self.look(t)
        return self.seen[t]

    def _fill(self, ts) -> None:
        todo = sorted({self._t(t) for t in ts} - set(self.seen))
        if self.prefetch and len(todo) > 1:
            self.prefetch(todo)

    def run(self, rng: tuple[float, float], seeds: list[float]) -> None:
        """Sample the range on the MEASURE_STEP grid, every seed, outward from every
        sighting until the text is gone, then finer where it moves or changes."""
        step = MEASURE_STEP
        last = math.floor(self.duration / step)
        k0, k1 = max(0, math.floor(rng[0] / step)), min(last, math.ceil(rng[1] / step))
        todo = list(range(k0, k1 + 1))
        self._fill([k * step for k in todo] + list(seeds))
        for s in seeds:
            self.at(s)
            todo += [math.floor(s / step), math.ceil(s / step)]
        done: set[int] = set()
        while todo:
            k = todo.pop()
            if k in done or k < 0 or k > last:
                continue
            done.add(k)
            if self.at(k * step):  # also two steps on: one recognition miss does not end it
                todo += [k - 2, k - 1, k + 1, k + 2]
        if self.duration - last * step > MIN_DT:
            self.at(self.duration)
        self.refine()

    def refine(self) -> None:
        while True:
            ts = sorted(self.seen)
            mids = []
            for a, b in zip(ts, ts[1:], strict=False):
                if b - a <= MIN_DT * 1.01:
                    continue
                sa, sb = self.seen[a], self.seen[b]
                if not sa and not sb:
                    continue
                pairs, only_a, only_b = _pairs(sa, sb)
                if only_a or only_b or _moved(pairs, self.size):
                    mids.append((a + b) / 2)
            if not mids:
                return
            self._fill(mids)
            for t in mids:
                self.at(t)

    def pieces(self, rng: tuple[float, float], area: list[float]) -> list[dict]:
        """[{"t0", "t1", "box", "text_box", "source"}] in screen time, consecutive. Every
        sighting is covered; inside `rng`, a time without a sighting in the named `area`
        (the text may be shown elsewhere too) gets the whole area (fail closed)."""
        ts = sorted(self.seen)
        out = []
        found = [t for t in ts if self.seen[t]]
        if found and found[0] > 0 and found[0] == ts[0]:  # seen from the very first sample
            out.append(("ocr", 0.0, found[0], self.seen[found[0]]))
        for a, b in zip(ts, ts[1:], strict=False):
            sa, sb = self.seen[a], self.seen[b]
            if sa or sb:
                pairs, only_a, only_b = _pairs(sa, sb)
                for x, y in pairs:
                    out.append(("ocr", a, b, [x, y]))
                for x in only_a + only_b:
                    out.append(("ocr", a, b, [x]))
                continue
            prev = [t for t in found if t <= a]
            nxt = [t for t in found if t >= b]
            if prev and nxt and nxt[0] - prev[-1] <= GAP_FILL:
                pairs, _, _ = _pairs(self.seen[prev[-1]], self.seen[nxt[0]])
                if pairs:  # a short miss between two sightings close together
                    for x, y in pairs:
                        out.append(("ocr", a, b, [x, y]))
                    continue
        if found and found[-1] == ts[-1] and ts[-1] < self.duration:
            out.append(("ocr", ts[-1], self.duration, self.seen[ts[-1]]))
        near = _pad_area(area)
        covered = sorted(
            (t0, t1) for _, t0, t1, boxes in out if any(_overlaps(b, near) for b in boxes)
        )
        lo = rng[0]
        for t0, t1 in covered + [(rng[1], rng[1])]:  # any part of the range left: the area
            if t0 > lo + 1e-6 and min(t0, rng[1]) > lo:
                out.append(("area", lo, min(t0, rng[1]), None))
            lo = max(lo, t1)
        docs = []
        for source, t0, t1, boxes in out:
            if source == "area":
                docs.append({"t0": t0, "t1": t1, "box": _pad_area(area), "source": "area"})
            else:
                tb = _union(boxes)
                docs.append({"t0": t0, "t1": t1, "box": _union(_pad(b, self.size) for b in boxes),
                             "text_box": tb, "source": "ocr"})  # fmt: skip
        return _merge(docs)


def _merge(docs: list[dict]) -> list[dict]:
    """Pieces with the same box and source that touch in time become one."""
    out: list[dict] = []
    for d in sorted(
        docs, key=lambda d: (d["source"], tuple(round(v, 4) for v in d["box"]), d["t0"])
    ):
        last = out[-1] if out else None
        if (
            last is not None
            and last["source"] == d["source"]
            and all(abs(x - y) < 1e-4 for x, y in zip(last["box"], d["box"], strict=True))
            and d["t0"] <= last["t1"] + 1e-6
        ):
            last["t1"] = max(last["t1"], d["t1"])
            continue
        out.append(dict(d))
    return sorted(out, key=lambda d: (d["t0"], d["source"]))


def _spans(pieces: list[dict], source: str) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for p in sorted((p for p in pieces if p["source"] == source), key=lambda p: p["t0"]):
        if out and p["t0"] <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], p["t1"])
        else:
            out.append([p["t0"], p["t1"]])
    return [tuple(s) for s in out]


def _clip(track: list[dict], cam_dur: float | None) -> list[dict]:
    """Pieces cut to the camera recording (screen time before or after it is never shown)."""
    hi = cam_dur if cam_dur is not None else math.inf
    return [{**p, "t0": round(max(p["t0"], 0.0), 4), "t1": round(min(p["t1"], hi), 4)}
            for p in track]  # fmt: skip


def _r(box) -> list[float]:
    return [round(v, 5) for v in box]


def _status(track: list[dict], cam: bool) -> tuple[str, str]:
    area = _spans(track, "area")
    if not any(p["source"] == "ocr" for p in track):
        return "failed", "text not found; the whole marked area is covered"
    if not area:
        return "ok", "position measured on the screen"
    when = ", ".join(f"{a:.1f}-{b:.1f} s" for a, b in area)
    where = "camera " if cam else ""
    return "partial", f"text not found at {where}{when}; the whole marked area is covered there"


def measure(job: Path) -> dict:
    """Measure every candidate with "text"; rewrite work/redaction.json. Summary dict."""
    path = job / red_mod.FILE
    try:
        data = json.loads(path.read_text())
    except OSError:
        raise ToolError(f"{red_mod.FILE} not found; write the candidates first") from None
    except ValueError as e:
        raise ToolError(f"{red_mod.FILE} is not valid JSON: {e}") from None
    errs = red_mod.validate(data)
    if errs:
        raise ToolError(f"{red_mod.FILE}: " + "; ".join(errs[:5]))
    cands = data["candidates"]
    todo = [c for c in cands if isinstance(c.get("text"), str) and c["text"].strip()]
    report, samples = [], {}
    warn = [
        f'candidate {c["id"]} ({c["kind"]}) has no "text": its box is drawn as it is (not measured)'
        for c in cands
        if c not in todo and c["kind"] in ("email", "phone", "name")
    ]
    if not todo:
        for w in warn:
            print(f"warning: {w}")
        return {"file": red_mod.FILE, "candidates": [], "warnings": warn}
    _need_ocr()
    screen = [c for c in todo if "asset" not in c]
    if screen:
        sync = read_sync(job / "work" / "sync.json")
        if sync is None:
            raise ToolError("measuring screen candidates needs a valid work/sync.json")
        off = sync["offset_s"]
        src = screen_source(job)
        info = probe_screen(src)
        doc = screen_text(job)
        size = tuple(info["size"])
        cache: dict[float, dict] = {}
        lock = threading.Lock()
        try:
            cam_dur = float(json.loads((job / "work" / "probe.json").read_text())["duration"])
        except (OSError, ValueError, KeyError, TypeError):
            cam_dur = None
        needles = [c["text"] for c in screen]

        def read(t):
            rgb = grab_screen(src, info, t)
            if rgb is None and t < info["duration"] - END_SLACK:
                raise ToolError(f"cannot read the screen recording at {t:.2f} s")
            found = _ocr.find_many(rgb, needles) if rgb is not None else {}
            with lock:
                cache[t] = found

        def prefetch(ts):
            _ocr.pmap(read, [t for t in ts if t not in cache])

        def look_for(needle):
            def look(t):
                if t not in cache:
                    read(t)
                return [o["box"] for o in cache[t].get(needle, [])]

            return look

        for c in screen:
            rng = c.get("range") or [c["t0"], c["t1"]]
            s_rng = (rng[0] + off, rng[1] + off)
            seeds = sorted({ln["t"] for ln in doc["lines"] if _ocr.matches(ln["text"], c["text"])})
            tr = Tracker(look_for(c["text"]), info["duration"], size, prefetch)
            tr.run(s_rng, seeds)
            track = []
            for p in tr.pieces(s_rng, c["box"]):
                q = {
                    "t0": round(p["t0"] - off, 4),
                    "t1": round(p["t1"] - off, 4),
                    "box": _r(p["box"]),
                }
                if "text_box" in p:
                    q["text_box"] = _r(p["text_box"])
                track.append({**q, "source": p["source"]})
            track = [p for p in _clip(track, cam_dur) if p["t1"] - p["t0"] > 1e-4]
            status, note = _status(track, cam=True)
            c["range"] = [rng[0], rng[1]]
            c["track"] = track
            c["t0"] = round(min([rng[0]] + [p["t0"] for p in track]), 4)
            c["t1"] = round(max([rng[1]] + [p["t1"] for p in track]), 4)
            c["measured"], c["note"] = status, note
            c["measure_sig"] = red_mod.measure_sig(c)
            samples[str(c["id"])] = [
                {"t": t, "camera_t": round(t - off, 4), "boxes": [_r(b) for b in bs]}
                for t, bs in sorted(tr.seen.items())
            ]
            report.append({"id": c["id"], "measured": status, "t0": c["t0"], "t1": c["t1"],
                           "pieces": len(track), "note": note})  # fmt: skip
    for c in (c for c in todo if "asset" in c):
        f = red_mod.asset_file(job, c["asset"])
        if f is None or not f.is_relative_to(job.resolve()) or not f.is_file():
            raise ToolError(f"candidate {c['id']}: {c['asset']} not found")
        rgb = image_rgb(f)
        size = (rgb.shape[1], rgb.shape[0])
        found = [o["box"] for o in _ocr.find(rgb, c["text"])]
        if found:
            c["track"] = [{"box": _r(_pad(b, size)), "text_box": _r(b), "source": "ocr"}
                          for b in found]  # fmt: skip
            c["measured"], c["note"] = "ok", "position measured on the screenshot"
            if not any(_overlaps(b, _pad_area(c["box"])) for b in found):
                c["track"].append({"box": _r(_pad_area(c["box"])), "source": "area"})
                c["measured"] = "partial"
                c["note"] = "text not found in the marked area; the whole area is covered too"
        else:
            c["track"] = [{"box": _r(_pad_area(c["box"])), "source": "area"}]
            c["measured"] = "failed"
            c["note"] = "text not found; the whole marked area is covered"
        c["measure_sig"] = red_mod.measure_sig(c)
        samples[str(c["id"])] = [{"boxes": [_r(b) for b in found]}]
        report.append({"id": c["id"], "measured": c["measured"], "pieces": len(c["track"]),
                       "note": c["note"]})  # fmt: skip
    errs = red_mod.validate(data)
    if errs:  # never write a list nobody can read
        raise ToolError("measured candidates are not valid: " + "; ".join(errs[:3]))
    write_json(path, data)
    write_json(job / SAMPLES_FILE, samples)
    for r in report:
        print(f"candidate {r['id']}: {r['measured']} ({r['pieces']} piece(s)) - {r['note']}")
    for w in warn:
        print(f"warning: {w}")
    return {"file": red_mod.FILE, "samples": SAMPLES_FILE, "candidates": report, "warnings": warn}


def measure_tool(args) -> dict:
    return measure(resolve_job(args.job).resolve())


def register(subparsers) -> None:
    p = subparsers.add_parser("redact", help="measure where personal data is on the screen")
    sub = p.add_subparsers(dest="redact_cmd", required=True)
    t = sub.add_parser("text", help="text on the screen recording (local OCR)",
                       parents=[job_parent()])  # fmt: skip
    t.add_argument("--force", action="store_true", help="read the screen again")
    t.set_defaults(handler=run_tool("redact text", text_tool))
    m = sub.add_parser(
        "measure", help="measured boxes for the candidates of work/redaction.json",
        parents=[job_parent()],
    )  # fmt: skip
    m.set_defaults(handler=run_tool("redact measure", measure_tool))
