"""`videocut tool motion spans|stills|render|show|catalogue|plan-stills`: motion scenes.

A **motion moment** is approved with the plan (work/plan.json "motion", source time):
`{id, src: [s0, s1], components: [...], shows, beats: {beat: {word, n?, edge?, offset?}},
why}`. After the cut this tool turns each approved moment into a **span** in output time:

    work/motion/spans.json   [{id, t0, t1, region, scene, beats}]      (interface 3)
    work/motion/<id>/motion.json   the renderer spec (renderer/motion.js)
    work/motion/<id>/scene.js      the scene module (written by the overlays skill)
    work/motion/<id>/clip.mov      ProRes 4444 with alpha, region only (motion render)
    work/motion/<id>/events.json   [{t, kind, component, strength}] (output s; sfx reads it)
    work/motion/<id>/clip.json     the render report (place_at, size, frames, seconds)
    work/motion/<id>/stills/       PNG stills at the beats + sheet.jpg (motion stills)

Spans come only from the APPROVED plan.json (the approval record binds its SHA-256), so an
agent can neither add a moment nor change its words or beats. A span starts LEAD_S before
the first kept word of its moment and ends TAIL_S after the last one (never overlapping the
next span, inside the reel). The words of the span (scene time, 0 = span start) are mapped
through the EDL exactly like the captions. Its region (the band) runs over the frame width
inside the side safe zone, from the top safe zone down to the highest face top over the
span plus BAND_FACE_SHARE of the face height: the eye line (EYE_LINE_SHARE of the landmark
box, measured) minus EYE_CLEAR of the face height stays free, so hair and the upper forehead
may be covered, brows-to-eyes not. Components fit the band (`c.slot`), they shrink instead.
A moment whose words were cut, that falls on a layout without the speaker's face (slide,
screen, picture-in-picture, split, b-roll: the band needs the face below it), or that has
no room above the face is dropped and listed (verify `motion_dropped`).

The design is always the job's brand profile (job.json "brand", resolved tokens); there is
no other source. `motion render` renders every span whose scene exists with
`--region-only` (MOTION_JOBS pages at once), keeps a clip while its spec, scene, fonts and
renderer code are unchanged, and records the SHA-256 of clip and events outside the job
folder ($VIDEOCUT_HOME/state/<job>/motion_render.json), so verify and render believe only
clips this tool wrote.
"""

import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..edl import EdlError, load, read_sync
from ..paths import RENDERER, state_dir, tool_env
from . import _job_motion
from . import _render_plan as plan_mod
from ._geometry import face_track, framed, safe_zones, sized
from .common import (
    ToolError,
    binary,
    job_parent,
    read_job,
    resolve_job,
    run,
    run_tool,
    write_json,
)

DIR = "work/motion"
SPANS = f"{DIR}/spans.json"
LEAD_S = 0.15  # a span starts this long before its first word
TAIL_S = 0.45  # ... and ends this long after its last one
# Upper eyelids at 26 % of the face-landmark box (demo clip, 132 frames, 5-95 %: 24.7-28.5 %;
# brows at 15.5 %). The eye line plus EYE_CLEAR of the face height above it stays free.
EYE_LINE_SHARE = 0.26
EYE_CLEAR = 0.08
BAND_FACE_SHARE = EYE_LINE_SHARE - EYE_CLEAR  # the band may reach this far into the face box
CAPTION_FACE_SHARE = 0.72  # during a span, captions may cover the face below this share
RENDER_TIMEOUT_S = 900  # one span (≤ 60 s) takes ~2 s per second on SwiftShader, 3D ~4 s
CAPTURE_MARGIN = 0.06  # renderer/motion.js --region-only margin (share of the unit)
MIN_BAND_SHARE = 0.12  # a band lower than this share of the frame height is no band
MIN_SPAN_S, MAX_SPAN_S = 1.5, 60.0
STILL_AFTER_S = 0.3  # a beat still is taken this long after the beat
MOTION_JOBS = max(1, int(os.environ.get("VIDEOCUT_MOTION_JOBS", "2")))
ID_RE = re.compile(r"m[0-9]{1,3}")
EDGES = ("start", "end")
MAX_BEATS = 80
MAX_LABELS, MAX_LABEL_LEN = 8, 40  # neutral band labels the plan names (verify motion_invented)
FALLBACK_COMPONENTS = ("headline", "checklist", "counter", "strikeCard", "terminal")
CODE = ("motion.js", "lib/literals.js", "lib/ffmpeg.js")
RECORD_NAME = "motion_render.json"


# --- components ---------------------------------------------------------------------------


def components() -> tuple[str, ...]:
    """Component names of renderer/motion/CATALOGUE.md (the first column of the table under
    "## Components", rows "| `name` | ..."), else the spike's five."""
    cat = RENDERER / "motion" / "CATALOGUE.md"
    try:
        text = cat.read_text()
    except OSError:
        text = ""
    part = text.split("\n## Components", 1)[1] if "\n## Components" in text else ""
    part = part.split("\n## ", 1)[0]
    names = re.findall(r"^\|\s*`([A-Za-z][A-Za-z0-9_]*)`\s*\|", part, re.M)
    return tuple(dict.fromkeys(names)) or FALLBACK_COMPONENTS


# --- plan validation (plan check) ---------------------------------------------------------


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def beat_errors(beats, p: str) -> list[str]:
    if not isinstance(beats, dict) or not beats:
        return [f"{p}: must be a non-empty object {{beat id: {{word, n?, edge?, offset?}}}}"]
    if len(beats) > MAX_BEATS:
        return [f"{p}: at most {MAX_BEATS} beats"]
    errs = []
    for k, b in beats.items():
        q = f"{p}.{k}"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", str(k)):
            errs.append(f"{q}: a beat id is a short identifier (letters, digits, _)")
        if not isinstance(b, dict) or not isinstance(b.get("word"), str) or not b["word"].strip():
            errs.append(f'{q}: must be {{"word": "<spoken word or phrase>", ...}}')
            continue
        if "n" in b and not (
            isinstance(b["n"], int) and not isinstance(b["n"], bool) and b["n"] >= 1
        ):
            errs.append(f"{q}.n: must be an integer >= 1")
        if "edge" in b and b["edge"] not in EDGES:
            errs.append(f"{q}.edge: must be start or end")
        if "offset" in b and not (_num(b["offset"]) and -2 <= b["offset"] <= 2):
            errs.append(f"{q}.offset: must be a number of seconds in -2..2")
    return errs


def plan_errors(data: dict, mode: str | None, words: list[dict] | None,
                keep: list[list[float]] | None) -> list[str]:  # fmt: skip
    """Problems of plan.json "motion". `mode`: the job's motion mode (None: not checked);
    `words`: work/words.json words (source time) to check that every beat word is spoken
    inside its moment; `keep`: the keep rows' src ranges."""
    items = data.get("motion", [])
    if items is None:
        items = []
    if not isinstance(items, list):
        return ["$.motion: must be a list"]
    if mode == "off" and items:
        return ["$.motion: the job's motion setting is off; no motion moments"]
    errs, ids, spans = [], set(), []
    names = components()
    for i, it in enumerate(items):
        p = f"$.motion[{i}]"
        if not isinstance(it, dict):
            errs.append(f"{p}: must be an object")
            continue
        mid = it.get("id")
        if not (isinstance(mid, str) and ID_RE.fullmatch(mid)):
            errs.append(f"{p}.id: must be m1, m2, ...")
        elif mid in ids:
            errs.append(f"{p}.id: {mid} is used twice")
        else:
            ids.add(mid)
        src = it.get("src")
        if not (
            isinstance(src, list) and len(src) == 2 and all(map(_num, src)) and src[0] < src[1]
        ):
            errs.append(f"{p}.src: must be [s0, s1] in source seconds with s0 < s1")
            src = None
        elif keep is not None and not any(
            a - 0.05 <= src[0] and src[1] <= b + 0.05 for a, b in keep
        ):
            # a moment may span several keep rows when they follow each other in the output
            if not _inside_rows(src, keep):
                errs.append(f"{p}.src: must lie inside the keep rows")
        if src:
            spans.append((src[0], src[1], p))
        comps = it.get("components")
        if not (isinstance(comps, list) and comps and all(isinstance(c, str) for c in comps)):
            errs.append(f"{p}.components: must be a non-empty list of component names")
        else:
            errs += [f"{p}.components: unknown component {c!r} (renderer/motion/CATALOGUE.md)"
                     for c in comps if c not in names]  # fmt: skip
        for k in ("shows", "why"):
            if not (isinstance(it.get(k), str) and it[k].strip()):
                errs.append(f"{p}.{k}: must be a non-empty string")
        be = beat_errors(it.get("beats"), f"{p}.beats")
        errs += be
        if not be and src and words is not None:
            ws = [w for w in words if src[0] - 0.05 <= w["t0"] and w["t1"] <= src[1] + 0.05]
            scene = [{"w": w["w"], "t0": w["t0"], "t1": w["t1"]} for w in ws]
            for k, b in it["beats"].items():
                try:
                    resolve_beats({k: b}, scene)
                except ToolError as e:
                    errs.append(f"{p}.beats.{k}: {e}")
        if "still" in it and not isinstance(it["still"], str | None):
            errs.append(f"{p}.still: must be a path")
        labels = it.get("labels", [])
        if not (
            isinstance(labels, list) and len(labels) <= MAX_LABELS
            and all(isinstance(x, str) and 0 < len(x.strip()) <= MAX_LABEL_LEN for x in labels)
        ):  # fmt: skip
            errs.append(f"{p}.labels: at most {MAX_LABELS} short texts (≤ {MAX_LABEL_LEN} chars)")
    spans.sort()
    for (_, a1, pa), (b0, _, pb) in zip(spans, spans[1:], strict=False):
        if b0 < a1:
            errs.append(f"{pb}.src: overlaps {pa} (one motion moment at a time)")
    return errs


def _inside_rows(src: list[float], keep: list[list[float]]) -> bool:
    """Both ends of [s0, s1] lie in keep rows (a moment may run over several rows; what was
    cut between them is simply not in the span)."""

    def inside(t: float) -> bool:
        return any(a - 0.05 <= t <= b + 0.05 for a, b in keep)

    return inside(src[0]) and inside(src[1])


# --- beats (the same rules as renderer/motion.js resolveBeats) ----------------------------


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+|_", "", str(s).casefold())


def resolve_beats(beats: dict, words: list[dict]) -> dict[str, float]:
    """{beat id: seconds} on `words` [{w, t0, t1}] (n-th occurrence of the word or phrase,
    its start or end, plus offset); ToolError when a word is not spoken there."""
    ws = [_norm(w["w"]) for w in words]
    out = {}
    for bid, b in (beats or {}).items():
        parts = [x for x in (_norm(p) for p in str(b.get("word", "")).split()) if x]
        want = b.get("n") if isinstance(b.get("n"), int) and b.get("n", 0) > 0 else 1
        seen, hit = 0, None
        for i in range(len(ws) - len(parts) + 1 if parts else 0):
            if all(ws[i + k] == p for k, p in enumerate(parts)):
                seen += 1
                if seen == want:
                    j = i + len(parts) - 1
                    hit = words[j]["t1"] if b.get("edge") == "end" else words[i]["t0"]
                    break
        if hit is None:
            raise ToolError(f'beat "{bid}": "{b.get("word")}" (#{want}) is not spoken there')
        off = b.get("offset") if _num(b.get("offset")) else 0
        out[bid] = round(hit + off, 3)
    return out


# --- spans --------------------------------------------------------------------------------


def _approved_moments(job: Path) -> tuple[list[dict], str | None]:
    """The motion moments of the approved work/plan.json, or ([], why there are none)."""
    from .job import approved_plan_json, is_approved

    data = read_job(job)
    if _job_motion.stored(data)["mode"] == "off":
        return [], "motion is off for this job"
    if not is_approved(job) or not approved_plan_json(job):
        return [], "the plan (work/plan.json) is not approved"
    try:
        plan = json.loads((job / "work" / "plan.json").read_text())
    except (OSError, ValueError):
        return [], "work/plan.json is unreadable"
    items = [m for m in plan.get("motion") or [] if isinstance(m, dict)]
    if plan_errors({"motion": items}, None, None, None):
        return [], "the approved plan's motion moments are invalid"
    return items, None


def _job_words(job: Path) -> list[dict]:
    from .captions import _words

    return _words(job)


def _edl_ctx(job: Path):
    """(edl, render plan, src size, face) like `overlays` builds them."""
    from .ingest import probe

    edl_path = job / "work" / "edl.json"
    if not edl_path.is_file():
        raise ToolError("work/edl.json is missing (motion spans need the cut)")
    rel = json.loads(edl_path.read_text()).get("source")
    src = (job / rel).resolve() if isinstance(rel, str) and rel else None
    if src is None or not src.is_relative_to(job.resolve()) or not src.is_file():
        raise ToolError(f"EDL source {rel!r} is missing or outside the job folder")
    info = probe(src)
    try:
        edl = load(edl_path, source_duration=info["duration"],
                   sync=read_sync(job / "work" / "sync.json"), check_sync=True)  # fmt: skip
    except EdlError as e:
        raise ToolError(
            f"{len(e.errors)} problem(s) in edl.json: " + "; ".join(e.errors[:5])
        ) from None
    size = (int(info["size"][0]), int(info["size"][1]))
    edl, _ = sized(job, edl)
    edl, _, face = framed(job, edl)
    layouts = [s.layout for s in edl.segments]
    use = any(lay != "face" for lay in layouts)
    plan = plan_mod.build(edl, size, edl.size, layouts if use else None)
    return edl, plan, size, face


def off_face_spans(edl) -> list[tuple[float, float, str]]:
    """(t0, t1, layout) in output time of every segment that does not show the speaker
    full frame (layout other than face)."""
    out, t = [], 0.0
    for s in edl.segments:
        if (s.layout or "face") != "face":
            out.append((t, t + s.duration, s.layout))
        t += s.duration
    return out


def band_region(track: list[dict], t0: float, t1: float, size: tuple[int, int]) -> list[int]:
    """[x0, y0, x1, y1] of the band over [t0, t1]: side and top safe zones, down to the
    highest face top + BAND_FACE_SHARE of its height (a third of the frame without a face)."""
    w, h = size
    safe = safe_zones(size)
    faces = [f["box"] for f in track if t0 <= f["t"] <= t1]
    limit = min((b[1] + BAND_FACE_SHARE * (b[3] - b[1]) for b in faces), default=h / 3)
    return [int(safe["side"]), int(safe["top"]), int(w - safe["side"]), int(limit)]


def build_spans(job: Path, ctx=None) -> tuple[list[dict], list[dict], dict]:
    """(spans, dropped, meta) for the job's current EDL and approved moments. Each span:
    {id, t0, t1, region, scene, beats, words, components, shows, beat_times}."""
    moments, why = _approved_moments(job)
    if not moments:
        return [], [], {"why": why}
    from .captions import map_words

    edl, plan, src_size, face = ctx or _edl_ctx(job)
    words = map_words(_job_words(job), edl.segments, edl.fps)
    track = face_track(plan, face, src_size)
    duration = float(plan.duration)
    off_face = off_face_spans(edl)
    size = tuple(edl.size)
    spans, dropped = [], []
    # the kept words of each moment, in output order (the cut may reorder source ranges)
    kept = []
    for m in moments:
        s0, s1 = m["src"]
        ws = [w for w in words if s0 - 0.05 <= w["src_t0"] and w["src_t1"] <= s1 + 0.05]
        if ws:
            kept.append((m, ws))
        else:
            dropped.append({"id": m["id"], "why": "its words were cut"})
    kept.sort(key=lambda k: k[1][0]["t0"])
    prev_end = 0.0
    for i, (m, ws) in enumerate(kept):
        nxt = kept[i + 1][1][0]["t0"] - LEAD_S if i + 1 < len(kept) else duration
        t0 = max(prev_end, ws[0]["t0"] - LEAD_S, 0.0)
        t1 = min(duration, ws[-1]["t1"] + TAIL_S, max(nxt, ws[-1]["t1"]))
        if t1 - t0 < MIN_SPAN_S:
            dropped.append({"id": m["id"], "why": f"shorter than {MIN_SPAN_S} s after the cut"})
            continue
        if t1 - t0 > MAX_SPAN_S:
            dropped.append({"id": m["id"], "why": f"longer than {MAX_SPAN_S} s"})
            continue
        hit = next((lay for a, b, lay in off_face if a < t1 and t0 < b), None)
        if hit:
            dropped.append({"id": m["id"], "why": f"it falls on a {hit} layout (no face below)"})
            continue
        region = band_region(track, t0, t1, size)
        if region[3] - region[1] < MIN_BAND_SHARE * size[1]:
            dropped.append({"id": m["id"], "why": "no room above the face for a band"})
            continue
        scene_words = [  # the moment's own words only: beats resolve on them
            {"w": w["w"], "t0": round(w["t0"] - t0, 3), "t1": round(w["t1"] - t0, 3)} for w in ws
        ]
        try:
            beat_times = resolve_beats(m["beats"], scene_words)
        except ToolError as e:
            dropped.append({"id": m["id"], "why": str(e)})
            continue
        spans.append({
            "id": m["id"], "t0": round(t0, 3), "t1": round(t1, 3), "region": region,
            "scene": f"{DIR}/{m['id']}/scene.js", "beats": m["beats"],
            "beat_times": beat_times, "words": scene_words,
            "components": m.get("components"), "shows": m.get("shows"),
            "labels": [x for x in m.get("labels") or [] if isinstance(x, str)],
        })  # fmt: skip
        prev_end = t1
    return spans, dropped, {"size": list(size), "fps": edl.fps, "duration": duration}


def public_spans(spans: list[dict]) -> list[dict]:
    """spans.json entries (interface 3)."""
    return [{k: s[k] for k in ("id", "t0", "t1", "region", "scene", "beats")} for s in spans]


def caption_track(track: list[dict], spans: list[dict]) -> list[dict]:
    """The face track for the caption layout: during a motion span the face counts only
    down to CAPTION_FACE_SHARE of its height (captions may cover chin and neck, the band
    takes the top)."""
    if not spans:
        return track
    out = []
    for f in track:
        if any(s["t0"] <= f["t"] <= s["t1"] for s in spans):
            x0, y0, x1, y1 = f["box"]
            f = {**f, "box": [x0, y0, x1, round(y0 + CAPTION_FACE_SHARE * (y1 - y0))]}
        out.append(f)
    return out


def views(spans: list[dict]) -> list[dict]:
    """overlays.json views: each band is a hard block for captions and overlay items."""
    return [{"t0": s["t0"], "t1": s["t1"], "screen": s["region"], "layout": "motion",
             "motion": s["id"]} for s in spans]  # fmt: skip


def brand_tokens(job: Path) -> dict:
    from . import _glass
    from .ingest import probe
    from .slides import brand_tokens as tokens_of

    tokens = tokens_of(job)
    if _glass.is_glass(tokens):  # the frost this video can have (vibrancy guard)
        src = job / read_job(job)["source"]
        tokens["glass_backdrop"] = _glass.backdrop_range(job, src, probe(src, need_audio=False))
    return tokens


def stage_region(region: list[int], unit: int) -> list[int]:
    """The renderer's stage inside the band: `--region-only` captures the stage plus
    CAPTURE_MARGIN of the unit on every side (shadows, glow, overshoot), so the stage ends
    that margin above the band's bottom edge and nothing drawn reaches below it (the eyes).
    Measured: a stat card's glow and pulse on a full-band stage reached 65 px below the band
    and covered 9 % of the face box."""
    x0, y0, x1, y1 = region
    m = round(unit * CAPTURE_MARGIN)
    return [x0, y0, x1, max(y0 + 2, y1 - m)]


def spec(span: dict, meta: dict, tokens: dict) -> dict:
    """renderer/motion.js spec of one span."""
    w, h = meta["size"]
    unit = min(w, h)
    return {
        "mode": "motion", "id": span["id"], "size": [w, h], "fps": meta["fps"],
        "duration": round(span["t1"] - span["t0"], 3), "offset": span["t0"],
        "unit": unit, "region": stage_region(span["region"], unit), "scene": span["scene"],
        "seed": 1,
        "words": span["words"], "beats": span["beats"], "tokens": tokens,
    }  # fmt: skip


def write_spans(job: Path) -> dict:
    """Write spans.json and every span's motion.json; returns a summary."""
    spans, dropped, meta = build_spans(job)
    d = job / DIR
    d.mkdir(parents=True, exist_ok=True)
    write_json(job / SPANS, public_spans(spans))
    tokens = brand_tokens(job) if spans else {}
    for s in spans:
        (d / s["id"]).mkdir(exist_ok=True)
        write_json(d / s["id"] / "motion.json", spec(s, meta, tokens))
    return {"spans": spans, "dropped": dropped, "meta": meta}


# --- trust record and freshness -----------------------------------------------------------


def _sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def code_sha() -> str:
    h = hashlib.sha256()
    files = [RENDERER / c for c in CODE] + sorted((RENDERER / "motion").glob("*.js"))
    files.append(RENDERER / "package-lock.json")  # pinned anime.js / three: a bump re-renders
    for f in files:
        h.update(f.name.encode() + b"\0" + (_sha(f) or "-").encode())
    return h.hexdigest()


def spec_sha(doc: dict, job: Path) -> str:
    """SHA-256 of a spec + its scene module + brand font files + the renderer code."""
    h = hashlib.sha256(json.dumps(doc, sort_keys=True).encode())
    h.update(b"\0scene\0" + (_sha(job / doc["scene"]) or "-").encode())
    fonts = (doc.get("tokens") or {}).get("fonts") or {}
    paths = sorted({p for p in (fonts.get("files") or {}).values() if isinstance(p, str)})
    for p in paths:
        h.update(b"\0" + p.encode() + b"\0" + (_sha(Path(p)) or "-").encode())
    h.update(b"\0code\0" + code_sha().encode())
    return h.hexdigest()


def record_path(job: Path) -> Path:
    return state_dir() / job.resolve().name / RECORD_NAME


def read_record(job: Path) -> dict:
    try:
        rec = json.loads(record_path(job).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(rec, dict) or rec.get("job") != str(job.resolve()):
        return {}
    return rec.get("spans") if isinstance(rec.get("spans"), dict) else {}


def _write_record(job: Path, spans: dict) -> None:
    rec = record_path(job)
    rec.parent.mkdir(parents=True, exist_ok=True)
    write_json(rec, {"job": str(job.resolve()), "spans": spans})


# the glass mask and its frost parameters exist only for a glass brand (absent: None)
GLASS_FILES = ("glass.mkv", "glass.mkv.json")


def _files(sid: str) -> dict[str, str]:
    return {k: f"{DIR}/{sid}/{k}" for k in ("clip.mov", "events.json", "clip.json", *GLASS_FILES)}


def status(job: Path, span: dict, meta: dict, tokens: dict, rec: dict | None = None) -> str:
    """ok | no_scene | missing | stale | untrusted for one span."""
    rec = read_record(job) if rec is None else rec
    if not (job / span["scene"]).is_file():
        return "no_scene"
    files = _files(span["id"])
    if not (job / files["clip.mov"]).is_file():
        return "missing"
    r = rec.get(span["id"]) if isinstance(rec.get(span["id"]), dict) else None
    if r is None:
        return "untrusted"
    for k, rel in files.items():
        if r.get(k) != _sha(job / rel):
            return "untrusted"
    if r.get("spec_sha256") != spec_sha(spec(span, meta, tokens), job):
        return "stale"
    return "ok"


# --- render -------------------------------------------------------------------------------


def renderer_cmd() -> list[str]:
    script = RENDERER / "motion.js"
    if not script.is_file():
        raise ToolError("renderer/motion.js is missing (motion renderer not installed)")
    return [binary("node"), str(script)]


def _run_renderer(argv: list[str], job: Path) -> dict:
    env = {**tool_env(), "VIDEOCUT_JOB_DIR": str(job)}
    try:
        r = subprocess.run(renderer_cmd() + argv, capture_output=True, text=True, env=env,
                           cwd=job, timeout=RENDER_TIMEOUT_S)  # fmt: skip
    except subprocess.TimeoutExpired:
        raise ToolError(f"motion renderer did not finish in {RENDER_TIMEOUT_S} s") from None
    lines = r.stdout.strip().splitlines()
    try:
        res = json.loads(lines[-1]) if lines else {}
    except ValueError:
        res = {}
    if r.returncode != 0 or not isinstance(res, dict) or not res.get("ok", True):
        tail = (res.get("error") if isinstance(res, dict) else None) or (r.stderr or r.stdout)
        raise ToolError(f"motion renderer failed: {str(tail).strip()[-600:]}")
    return res


def _render_one(job: Path, span: dict, meta: dict, tokens: dict) -> dict:
    d = job / DIR / span["id"]
    d.mkdir(parents=True, exist_ok=True)
    doc = spec(span, meta, tokens)
    spf = d / "motion.json"
    write_json(spf, doc)
    for f in ("clip.mov", "events.json", "clip.json", *GLASS_FILES):
        (d / f).unlink(missing_ok=True)
    res = _run_renderer([str(spf), "--region-only", "--out", str(d / "clip.mov"),
                         "--events", str(d / "events.json"), "--mask", str(d / "glass.mkv")],
                        job)  # fmt: skip
    if not (d / "clip.mov").is_file():
        raise ToolError(f"motion {span['id']}: the renderer wrote no clip.mov")
    if not (d / "events.json").is_file():
        write_json(d / "events.json", [])  # a renderer without cues: no sound events
    report = {
        "id": span["id"], "t0": span["t0"], "t1": span["t1"], "region": span["region"],
        "place_at": res.get("place_at") or [0, 0], "out_size": res.get("out_size") or meta["size"],
        "frames": res.get("frames"), "fps": meta["fps"], "render_seconds": res.get("seconds"),
        "frames_per_second": res.get("frames_per_second"), "beats": res.get("beats"),
        "spec_sha256": spec_sha(doc, job), "glass": res.get("glass"),
    }  # fmt: skip
    write_json(d / "clip.json", report)
    return report


def ensure_clips(job: Path, force: bool = False, ids: list[str] | None = None,
                 jobs: int | None = None) -> dict:  # fmt: skip
    """Render every span with a scene whose clip is not ok (force: all). Returns
    {spans, dropped, rendered, kept, no_scene, clips: [{id, t0, t1, clip, place_at, size,
    events}], seconds}."""
    import time

    built = write_spans(job)
    spans, meta = built["spans"], built["meta"]
    if ids:
        unknown = sorted(set(ids) - {s["id"] for s in spans})
        if unknown:
            raise ToolError(f"no motion span {', '.join(unknown)}")
    tokens = brand_tokens(job) if spans else {}
    rec = read_record(job)
    todo, kept, no_scene = [], [], []
    for s in spans:
        st = status(job, s, meta, tokens, rec)
        if st == "no_scene":
            no_scene.append(s["id"])
        elif force and (not ids or s["id"] in ids) or st != "ok":
            todo.append(s)
        else:
            kept.append(s["id"])
    t_start = time.monotonic()
    errors = {}
    if todo:
        with ThreadPoolExecutor(max_workers=jobs or MOTION_JOBS) as pool:
            futs = {s["id"]: pool.submit(_render_one, job, s, meta, tokens) for s in todo}
        for sid, f in futs.items():
            try:
                f.result()
            except ToolError as e:
                errors[sid] = str(e)
    rec = {k: v for k, v in rec.items() if k in {s["id"] for s in spans}}
    for s in todo:
        if s["id"] in errors:
            rec.pop(s["id"], None)
            continue
        files = _files(s["id"])
        rep = json.loads((job / files["clip.json"]).read_text())
        rec[s["id"]] = {k: _sha(job / rel) for k, rel in files.items()} | {
            "spec_sha256": rep["spec_sha256"]
        }
    _write_record(job, rec)
    if errors:
        raise ToolError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    clips = []
    for s in spans:
        if s["id"] in no_scene:
            continue
        rep = json.loads((job / DIR / s["id"] / "clip.json").read_text())
        clips.append({
            "id": s["id"], "t0": s["t0"], "t1": s["t1"], "clip": job / DIR / s["id"] / "clip.mov",
            "place_at": rep["place_at"], "size": rep["out_size"], "frames": rep.get("frames"),
            "mask": job / DIR / s["id"] / "glass.mkv"
            if (job / DIR / s["id"] / "glass.mkv").is_file() else None,
            "events": f"{DIR}/{s['id']}/events.json", "render_seconds": rep.get("render_seconds"),
        })  # fmt: skip
    return {
        "spans": spans, "dropped": built["dropped"], "rendered": [s["id"] for s in todo],
        "kept": kept, "no_scene": no_scene, "clips": clips,
        "seconds": round(time.monotonic() - t_start, 2), "meta": meta,
    }  # fmt: skip


def composite_graph(clips: list[dict], first_index: int, fps, scale: float,
                    v_in: str, matrix: str = "bt709") -> tuple[str, str]:  # fmt: skip
    """Filter lines laying each motion clip over `v_in` at its offset and place: on the
    output frame grid (pts = frame number, first frame at round(t0 * fps)), BT.709 →
    `matrix`, alpha blended in 4:4:4. A clip with a glass mask (`mask`, its input right
    after the clip's) first frosts the picture under it through the mask, only for the
    clip's own frames (_glass). Returns (graph text, output label); the inputs are the
    clips in order, each followed by its mask when it has one (`inputs(clips)`)."""
    from fractions import Fraction

    from . import _glass

    fr = Fraction(fps).limit_denominator(1001)
    tb = f"{fr.denominator}/{fr.numerator}"
    text, cur, idx = "", v_in, first_index
    for k, c in enumerate(clips):
        f0 = round(c["t0"] * fr)
        w, h = (max(2, round(v * scale) // 2 * 2) for v in c["size"])
        x, y = (round(v * scale) for v in c["place_at"])
        lab = f"[mo{k}]"
        ci = idx
        idx += 1
        text += (
            f";\n[{ci}:v]setpts=PTS-STARTPTS,fps={fps},settb={tb},setpts=N+{f0},"
            f"scale={w}:{h}:flags=lanczos:in_color_matrix=bt709:in_range=tv:"
            f"out_color_matrix={matrix}:out_range=tv,format=yuva444p[mc{k}];\n"
        )
        params = _glass.read_params(c["mask"]) if c.get("mask") and c.get("frames") else None
        if params:
            mi = idx
            idx += 1
            n = int(c["frames"])
            text += (
                f"{cur}settb={tb},format=yuv444p,split[mb{k}][ms{k}];\n"
                f"[ms{k}]trim=start_frame={f0}:end_frame={f0 + n},crop={w}:{h}:{x}:{y},"
                f"{_glass.frost_chain(w, h, params, scale)}[mz{k}];\n"
                + _glass.mask_input(mi, fps, w, h, f0, f"mm{k}")
                + ";\n"
                f"[mz{k}][mm{k}]alphamerge[mg{k}];\n"
                f"[mb{k}][mg{k}]overlay={x}:{y}:format=yuv444:eof_action=pass:shortest=0[mf{k}];\n"
                f"[mf{k}][mc{k}]overlay={x}:{y}:format=yuv444:eof_action=pass:shortest=0,"
                f"format=yuv420p{lab}"
            )
        else:
            text += (
                f"{cur}settb={tb},format=yuv444p[mb{k}];\n"
                f"[mb{k}][mc{k}]overlay={x}:{y}:format=yuv444:eof_action=pass:shortest=0,"
                f"format=yuv420p{lab}"
            )
        cur = lab
    return text + ("\n" if text else ""), cur


def inputs(clips: list[dict]) -> list[str]:
    """ffmpeg -i arguments in the order composite_graph expects them."""
    from . import _glass

    out: list[str] = []
    for c in clips:
        out += ["-i", str(c["clip"])]
        if c.get("mask") and c.get("frames") and _glass.read_params(c["mask"]):
            out += ["-i", str(c["mask"])]
    return out


# --- stills -------------------------------------------------------------------------------


def stills(job: Path, sid: str | None, times: list[float] | None) -> dict:
    built = write_spans(job)
    spans = [s for s in built["spans"] if sid in (None, s["id"])]
    if sid and not spans:
        raise ToolError(f"no motion span {sid}")
    out = []
    for s in spans:
        if not (job / s["scene"]).is_file():
            out.append({"id": s["id"], "error": f"{s['scene']} is missing"})
            continue
        dur = s["t1"] - s["t0"]
        ts = times or sorted({round(min(dur - 0.05, b + STILL_AFTER_S), 2)
                              for b in s["beat_times"].values()})  # fmt: skip
        d = job / DIR / s["id"] / "stills"
        if d.is_dir():
            for f in d.glob("*"):
                f.unlink()
        res = _run_renderer([str(job / DIR / s["id"] / "motion.json"), "--stills", str(d),
                             ",".join(f"{t:.2f}" for t in ts)], job)  # fmt: skip
        files = sorted(d.glob("t*.png"))
        sheet = d / "sheet.jpg"
        if files:
            _sheet(files, sheet, s["region"])
        out.append({"id": s["id"], "times": ts, "stills": len(files),
                    "sheet": str(sheet.relative_to(job)) if sheet.is_file() else None,
                    "beats": res.get("beats")})  # fmt: skip
    return {"spans": out, "dropped": built["dropped"]}


def _sheet(files: list[Path], dst: Path, region: list[int]) -> None:
    """A contact sheet of the band part of each still, 3 per row, labelled with its time,
    on a mid grey (the stills are transparent around the scene)."""
    x0, y0, x1, y1 = region
    pad = 40
    cols = 3
    args, chains = [], []
    for i, f in enumerate(files):
        args += ["-i", str(f)]
        label = f.stem[1:].lstrip("0") or "0"
        chains.append(
            f"[{i}:v]crop={x1 - x0 + 2 * pad}:{y1 - y0 + 2 * pad}:{max(0, x0 - pad)}:"
            f"{max(0, y0 - pad)},scale=480:-2,pad=iw+8:ih+8:4:4:color=gray,"
            f"drawtext=text='{label}s':x=8:y=8:fontsize=22:fontcolor=white:box=1:"
            f"boxcolor=black@0.6[s{i}]"
        )
    n = len(files)
    layout = "|".join(
        ("0" if i % cols == 0 else "+".join(["w0"] * (i % cols)))
        + "_"
        + ("0" if i // cols == 0 else "+".join(["h0"] * (i // cols)))
        for i in range(n)
    )
    g = ";".join(chains) + ";" + "".join(f"[s{i}]" for i in range(n))
    g += f"xstack=inputs={n}:layout={layout}:fill=gray" if n > 1 else "null"
    g += ",format=yuvj420p[out]"
    ff = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    run([*ff, *args, "-filter_complex", g, "-map", "[out]", "-frames:v", "1", "-q:v", "3",
         str(dst)])  # fmt: skip


# --- plan stills (the plan gate) ----------------------------------------------------------


def plan_stills(job: Path) -> dict:
    """work/plan_stills/motion_<id>.jpg: the source frame in the middle of each moment of
    work/plan.json, cropped to the output aspect around the face, with the band drawn where
    the scene will run; the path is written back into plan.json motion[].still."""
    from ._format import output_size
    from .face import load_face
    from .ingest import probe

    path = job / "work" / "plan.json"
    plan = json.loads(path.read_text())
    items = [m for m in plan.get("motion") or [] if isinstance(m, dict)]
    if not items:
        return {"stills": 0}
    src = job / read_job(job)["source"]
    info = probe(src)
    sw, sh = int(info["size"][0]), int(info["size"][1])
    ow, oh = output_size(read_job(job))
    face = load_face(job) or {}
    frames = [f for f in face.get("frames", []) if f.get("found") and f.get("box")]
    out_dir = job / "work" / "plan_stills"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for m in items:
        s0, s1 = m["src"]
        t = (s0 + s1) / 2
        near = [f["box"] for f in frames if s0 <= f["t"] <= s1]
        cx = sum((b[0] + b[2]) / 2 for b in near) / len(near) if near else 0.5
        cw = min(sw, round(sh * ow / oh)) // 2 * 2
        ch = min(sh, round(cw * oh / ow)) // 2 * 2
        x = max(0, min(sw - cw, round(cx * sw - cw / 2)))
        y = (sh - ch) // 2
        # band in output px -> crop px (scale k)
        k = cw / ow
        safe = safe_zones((ow, oh))
        top_face = min(((b[1] * sh - y) / k for b in near), default=oh / 3 / (1 + BAND_FACE_SHARE))
        hgt = min(((b[3] - b[1]) * sh / k for b in near), default=0)
        bottom = top_face + BAND_FACE_SHARE * hgt
        bx0, by0, bx1 = safe["side"] * k, safe["top"] * k, (ow - safe["side"]) * k
        by1 = max(by0 + 4, bottom * k)
        dst = out_dir / f"motion_{m['id']}.jpg"
        vf = (f"crop={cw}:{ch}:{x}:{y},"
              f"drawbox=x={bx0:.0f}:y={by0:.0f}:w={bx1 - bx0:.0f}:h={by1 - by0:.0f}:"
              f"color=white@0.35:t=fill,"
              f"drawbox=x={bx0:.0f}:y={by0:.0f}:w={bx1 - bx0:.0f}:h={by1 - by0:.0f}:"
              f"color=white@0.9:t=4,scale=-2:960")  # fmt: skip
        try:
            run([binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                 "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-vf", vf, "-q:v", "3",
                 str(dst)])  # fmt: skip
        except ToolError:
            continue
        m["still"] = str(dst.relative_to(job))
        n += 1
    write_json(path, plan)
    return {"stills": n}


# --- commands -----------------------------------------------------------------------------


def _job(args) -> Path:
    return resolve_job(getattr(args, "job_pos", None) or args.job)


def _span_line(s: dict) -> str:
    r = s["region"]
    return (f"{s['id']}: {s['t0']:.2f}-{s['t1']:.2f} s, band {r[0]},{r[1]}-{r[2]},{r[3]}, "
            f"{len(s['beat_times'])} beats, scene {s['scene']}")  # fmt: skip


def spans_cmd(args) -> dict:
    job = _job(args)
    built = write_spans(job)
    for s in built["spans"]:
        print(_span_line(s))
    for d in built["dropped"]:
        print(f"{d['id']}: dropped ({d['why']})")
    if not built["spans"] and built["meta"].get("why"):
        print(f"no motion spans: {built['meta']['why']}")
    return {
        "job": str(job), "file": SPANS,
        "spans": [{**{k: s[k] for k in ("id", "t0", "t1", "region", "scene")},
                   "beat_times": s["beat_times"], "words": " ".join(w["w"] for w in s["words"]),
                   "components": s["components"], "shows": s["shows"]}
                  for s in built["spans"]],
        "dropped": built["dropped"], "components": list(components()),
    }  # fmt: skip


def stills_cmd(args) -> dict:
    job = _job(args)
    times = [float(t) for t in args.at.split(",")] if args.at else None
    res = stills(job, args.id, times)
    for s in res["spans"]:
        print(f"{s['id']}: " + (s.get("error") or f"{s['stills']} stills, {s['sheet']}"))
    return {"job": str(job), **res}


def render_cmd(args) -> dict:
    job = _job(args)
    res = ensure_clips(job, force=args.force, ids=[args.id] if args.id else None, jobs=args.jobs)
    for sid in res["no_scene"]:
        print(f"{sid}: no scene yet ({DIR}/{sid}/scene.js)")
    print(f"motion: rendered {len(res['rendered'])}, kept {len(res['kept'])} "
          f"in {res['seconds']} s")  # fmt: skip
    return {
        "job": str(job), "rendered": res["rendered"], "kept": res["kept"],
        "no_scene": res["no_scene"], "dropped": res["dropped"], "seconds": res["seconds"],
        "clips": [{**c, "clip": str(Path(c["clip"]).relative_to(job))} for c in res["clips"]],
    }  # fmt: skip


def show_cmd(args) -> dict:
    job = _job(args)
    spans, dropped, meta = build_spans(job)
    tokens = brand_tokens(job) if spans else {}
    rec = read_record(job)
    out = []
    for s in spans:
        st = status(job, s, meta, tokens, rec)
        print(_span_line(s) + f" [{st}]")
        out.append({"id": s["id"], "t0": s["t0"], "t1": s["t1"], "status": st})
    return {"job": str(job), "spans": out, "dropped": dropped,
            "why": meta.get("why"), "mode": _job_motion.stored(read_job(job))["mode"]}  # fmt: skip


def catalogue_cmd(args) -> dict:
    """Print renderer/motion/CATALOGUE.md (agent runs read only the job folder)."""
    cat = RENDERER / "motion" / "CATALOGUE.md"
    text = cat.read_text() if cat.is_file() else ""
    print(text or "no catalogue; components: " + ", ".join(components()))
    return {"components": list(components()), "chars": len(text)}


def plan_stills_cmd(args) -> dict:
    job = _job(args)
    return {"job": str(job), **plan_stills(job)}


def register(subparsers) -> None:
    p = subparsers.add_parser("motion", help="motion scenes: spans, stills, render")
    sub = p.add_subparsers(dest="motion_cmd", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_, parents=[job_parent()])
        s.add_argument("job_pos", nargs="?", metavar="job", help="job folder or id")
        s.set_defaults(handler=run_tool(f"motion {name}", fn))
        return s

    add("spans", spans_cmd, "work/motion/spans.json from the approved plan and the cut")
    s = add("stills", stills_cmd, "render stills at every beat of a span + a contact sheet")
    s.add_argument("--id", help="only this span")
    s.add_argument("--at", help="scene seconds t,t,... instead of the beats")
    s = add("render", render_cmd, "render clip.mov + events.json of every span (region only)")
    s.add_argument("--id", help="only this span")
    s.add_argument("--force", action="store_true", help="render again even when unchanged")
    s.add_argument("--jobs", type=int, help=f"pages rendering at once (default {MOTION_JOBS})")
    add("show", show_cmd, "spans and the state of their clips")
    sub.add_parser("catalogue", help="print the component catalogue").set_defaults(
        handler=run_tool("motion catalogue", catalogue_cmd)
    )
    add("plan-stills", plan_stills_cmd, "plan gate stills of the motion moments (plan run)")
