"""`videocut tool slides check|stills|render [job]`: slides filled into brand templates.

Claude never writes slide code: it fills work/slides.json with data for one of the
TEMPLATES and the renderer (renderer/slides/<template>.js, brand tokens only) draws it.

    work/slides.json  {"slides": [{"id", "template", "content", "source": "auto"|"upload",
                                   "why"}]}

Templates and their content (`LIMITS`; text in the job's language):

    title       {title <= 60, subtitle? <= 90}
    bullets     {title? <= 60, items: 2-5 x <= 70}                   items build one by one
    big_number  {value <= 12 ("40 %", "3x", "12.000"), label <= 70, count: bool}
    compare     {left: {title <= 40, items: 1-4 x <= 60}, right: the same}   two builds
    steps       {title? <= 60, steps: 2-5 x <= 50}                   steps build in order
    timeline    {title? <= 60, points: 2-6 x {label <= 16, text <= 50}}
    bar_chart   {title? <= 60, unit? <= 12, bars: 2-6 x {label <= 24, value number >= 0}}
    quote       {text <= 160, author? <= 50}
    cta         {title <= 50, subtitle? <= 90, url? <= 60}
    image       {asset: "input/slides/<file>.png|jpg"}               an uploaded page

The EDL places them (edl.py): a segment with layout slide / slide_split / slide_full and
"slide": "<id>", optional "reveal" [s after the segment's in_src] for the build steps.

    check   slides.json schema and text limits, assets, and against work/edl.json (when
            there is one) the ids it uses, reveal counts and the reading time (READ_S_PER_WORD
            per word + READ_EXTRA_S on screen; a warning here, a flag in verify)
    stills  work/slides/stills/<id>.jpg: the final state at the output size in its layout
            (the EDL's, else plan.json slides[].layout, else "slide") with the speaker
            (a camera frame from the slide's time: its first segment, else plan.json
            slides[].t0, cropped as render crops it; a plain shape when there is none)
            and <id>_sm.jpg (long side <= STILL_MAX) for Claude and the app; the summary
            says per slide whether the text fits (scale >= 0.6)
    render  per slide the EDL shows: work/slides/<id>.mov (opaque, the slide's part of the
            frame, EDL fps, as long as the slide is on screen in total: a slide on several
            segments runs on), work/slides/<id>.json (layout report: scale, fits, every
            text with its box; the geometry; spec and clip sha256) and <id>.spec.json.
            A clip whose spec (content, reveal, size, brand tokens, renderer code) is
            unchanged is kept. The sha256 of every clip and report is recorded outside the
            job folder ($VIDEOCUT_HOME/state/<job>/slides_render.json); verify trusts only
            those. `tool render` runs this itself when a clip is missing or stale.
"""

import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path, PurePosixPath

from .. import edl as edl_mod
from ..paths import RENDERER, state_dir
from . import _layouts
from . import _render_plan as plan_mod
from ._geometry import job_size, sized
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
from .job import job_lang

TEMPLATES = (
    "title", "bullets", "big_number", "compare", "steps", "timeline", "bar_chart", "quote",
    "cta", "image",
)  # fmt: skip
SOURCES = ("auto", "upload")
STEPS = {"bullets": "items", "steps": "steps", "timeline": "points", "bar_chart": "bars"}
READ_S_PER_WORD = 0.25
READ_EXTRA_S = 1.0
MIN_SCALE = 0.6  # renderer/lib/slide.js MIN_SCALE: text never shrinks further
STILL_MAX = 960
DEFAULT_FPS = 30
DEFAULT_LAYOUT = "slide"
ASSET_DIR = "input/slides"
ASSET_EXT = (".png", ".jpg", ".jpeg")
SLIDE_KEYS = {"id", "template", "content", "source", "why"}
SLIDES_FILE = "work/slides.json"
RENDER_CODE = ("render.js", "lib/slide.js", "lib/card.js", "lib/style.js", "lib/layout.js",
               "lib/spring.js", "scenes/number_card.js")  # fmt: skip


# --- schema -------------------------------------------------------------------------------


def _str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _num(v) -> bool:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:  # an integer beyond float range (JSON allows it)
        return False


def _text(c: dict, key: str, p: str, limit: int, required: bool = True) -> list[str]:
    v = c.get(key)
    if v is None and not required:
        return []
    if not _str(v):
        return [f"{p}.{key}: must be text"]
    if len(v.strip()) > limit:
        return [f"{p}.{key}: {len(v.strip())} characters, at most {limit} (shorten it)"]
    return []


def _items(c: dict, key: str, p: str, lo: int, hi: int, check) -> list[str]:
    v = c.get(key)
    if not isinstance(v, list) or not lo <= len(v) <= hi:
        return [f"{p}.{key}: must be a list of {lo}-{hi}"]
    errs = []
    for j, it in enumerate(v):
        errs += check(it, f"{p}.{key}[{j}]")
    return errs


def _line(limit: int):
    def check(v, p: str) -> list[str]:
        if not _str(v):
            return [f"{p}: must be text"]
        return (
            [f"{p}: {len(v.strip())} characters, at most {limit}"] if len(v.strip()) > limit else []
        )

    return check


def _obj(fields: dict):
    """Check of an object with the text fields {key: limit} (all required)."""

    def check(v, p: str) -> list[str]:
        if not isinstance(v, dict):
            return [f"{p}: must be an object {{{', '.join(fields)}}}"]
        errs = [e for k, n in fields.items() for e in _text(v, k, p, n)]
        extra = sorted(set(v) - set(fields))
        return errs + ([f"{p}: unknown field(s) {', '.join(extra)}"] if extra else [])

    return check


def _bar(v, p: str) -> list[str]:
    if not isinstance(v, dict):
        return [f"{p}: must be an object {{label, value}}"]
    errs = _text(v, "label", p, 24)
    if not (_num(v.get("value")) and v["value"] >= 0):
        errs.append(f"{p}.value: must be a number >= 0")
    extra = sorted(set(v) - {"label", "value"})
    return errs + ([f"{p}: unknown field(s) {', '.join(extra)}"] if extra else [])


def _column(v, p: str) -> list[str]:
    if not isinstance(v, dict):
        return [f"{p}: must be an object {{title, items}}"]
    errs = _text(v, "title", p, 40) + _items(v, "items", p, 1, 4, _line(60))
    extra = sorted(set(v) - {"title", "items"})
    return errs + ([f"{p}: unknown field(s) {', '.join(extra)}"] if extra else [])


def _asset_errors(v, p: str) -> list[str]:
    if not isinstance(v, str) or "\\" in v:
        return [f"{p}: must be a path {ASSET_DIR}/<file>.png|.jpg"]
    pp = PurePosixPath(v)
    ok = (not pp.is_absolute() and ".." not in pp.parts and str(pp.parent) == ASSET_DIR
          and pp.suffix.lower() in ASSET_EXT)  # fmt: skip
    return [] if ok else [f"{p}: must be a path {ASSET_DIR}/<file>.png|.jpg"]


# Per template: {field: check} with check(content, path) -> errors.
LIMITS = {
    "title": {"title": (60, True), "subtitle": (90, False)},
    "bullets": {"title": (60, False)},
    "big_number": {"value": (12, True), "label": (70, True)},
    "compare": {},
    "steps": {"title": (60, False)},
    "timeline": {"title": (60, False)},
    "bar_chart": {"title": (60, False), "unit": (12, False)},
    "quote": {"text": (160, True), "author": (50, False)},
    "cta": {"title": (50, True), "subtitle": (90, False), "url": (60, False)},
    "image": {},
}
LISTS = {
    "bullets": [("items", 2, 5, _line(70))],
    "steps": [("steps", 2, 5, _line(50))],
    "timeline": [("points", 2, 6, _obj({"label": 16, "text": 50}))],
    "bar_chart": [("bars", 2, 6, _bar)],
}
OTHER = {
    "big_number": {"count"},
    "compare": {"left", "right"},
    "image": {"asset"},
}


def content_errors(template: str, c, p: str) -> list[str]:
    """Problems of one slide's content for `template` (paths start with `p`)."""
    if not isinstance(c, dict):
        return [f"{p}: must be an object"]
    errs = []
    for key, (limit, required) in LIMITS[template].items():
        errs += _text(c, key, p, limit, required)
    for key, lo, hi, check in LISTS.get(template, []):
        errs += _items(c, key, p, lo, hi, check)
    if template == "big_number" and not isinstance(c.get("count", False), bool):
        errs.append(f"{p}.count: must be true or false")
    if template == "compare":
        errs += _column(c.get("left"), f"{p}.left") + _column(c.get("right"), f"{p}.right")
    if template == "image":
        errs += _asset_errors(c.get("asset"), f"{p}.asset")
    known = set(LIMITS[template]) | {k for k, *_ in LISTS.get(template, [])}
    extra = sorted(set(c) - known - OTHER.get(template, set()))
    if extra:
        errs.append(f"{p}: unknown field(s) {', '.join(extra)} for template {template}")
    return errs


def validate_doc(doc) -> tuple[list[str], list[str]]:
    """(errors, warnings) of a work/slides.json document (schema and text limits)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("slides"), list):
        return ['$: must be {"slides": [...]}'], []
    errs, warns, seen = [], [], set()
    for i, s in enumerate(doc["slides"]):
        p = f"$.slides[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{p}: must be an object")
            continue
        sid = s.get("id")
        if not (isinstance(sid, str) and edl_mod.SLIDE_ID_RE.fullmatch(sid)):
            errs.append(f"{p}.id: letters, digits, _ and -, up to 40 (e.g. s1)")
        elif sid in seen:
            errs.append(f"{p}.id: {sid} is used twice")
        else:
            seen.add(sid)
        t = s.get("template")
        if t not in TEMPLATES:
            errs.append(f"{p}.template: must be one of {', '.join(TEMPLATES)}")
        else:
            errs += content_errors(t, s.get("content"), f"{p}.content")
        if s.get("source", "auto") not in SOURCES:
            errs.append(f"{p}.source: must be auto or upload")
        if "why" in s and not isinstance(s["why"], str):
            errs.append(f"{p}.why: must be text")
        extra = sorted(set(s) - SLIDE_KEYS)
        if extra:
            warns.append(f"{p}: unknown field(s) {', '.join(extra)} are ignored")
    return errs, warns


def slide_texts(slide: dict) -> list[str]:
    """Every text a slide shows (for the reading time)."""
    c, t = slide.get("content") or {}, slide.get("template")
    out = [c.get(k) for k in ("title", "subtitle", "value", "label", "text", "author", "url")]
    out += list(c.get("items") or []) + list(c.get("steps") or [])
    for col in (c.get("left"), c.get("right")) if t == "compare" else ():
        out += [col.get("title"), *(col.get("items") or [])] if isinstance(col, dict) else []
    for pt in c.get("points") or []:
        out += [pt.get("label"), pt.get("text")] if isinstance(pt, dict) else []
    for b in c.get("bars") or []:
        out += [b.get("label"), str(b.get("value"))] if isinstance(b, dict) else []
    return [x for x in out if isinstance(x, str) and x.strip()]


def words(slide: dict) -> int:
    return sum(len(x.split()) for x in slide_texts(slide))


def reading_s(slide: dict) -> float:
    """Seconds a slide must be on screen: READ_S_PER_WORD per word + READ_EXTRA_S."""
    return round(READ_S_PER_WORD * words(slide) + READ_EXTRA_S, 3)


def step_count(slide: dict) -> int:
    t, c = slide.get("template"), slide.get("content") or {}
    if t == "compare":
        return 2
    key = STEPS.get(t)
    return len(c.get(key) or []) if key else 0


# --- job inputs ---------------------------------------------------------------------------


def load_doc(job: Path) -> dict:
    """work/slides.json, validated; ToolError listing the problems otherwise."""
    path = job / SLIDES_FILE
    if not path.is_file():
        raise ToolError(f"{SLIDES_FILE} is missing")
    try:
        doc = json.loads(path.read_text())
    except ValueError as e:
        raise ToolError(f"{SLIDES_FILE} is not valid JSON: {e}") from None
    errs, _ = validate_doc(doc)
    if errs:
        for e in errs:
            print(e)
        raise ToolError(f"{len(errs)} problem(s) in {SLIDES_FILE}: " + "; ".join(errs[:5]))
    return doc


def by_id(doc: dict) -> dict[str, dict]:
    return {s["id"]: s for s in doc["slides"]}


def asset_errors(job: Path, doc: dict) -> list[str]:
    """Image slides whose file is missing or outside the job folder."""
    errs = []
    for i, s in enumerate(doc["slides"]):
        if s["template"] != "image":
            continue
        f = (job / s["content"]["asset"]).resolve()
        if not (f.is_relative_to(job.resolve()) and f.is_file()):
            errs.append(f"$.slides[{i}].content.asset: {s['content']['asset']} is missing")
    return errs


def _edl(job: Path) -> tuple[edl_mod.Edl | None, dict | None]:
    """(work/edl.json at the job's size, raw dict), (None, None) when there is none; a
    ToolError when it is invalid."""
    path = job / "work" / "edl.json"
    if not path.is_file():
        return None, None
    try:
        raw = json.loads(path.read_text())
        e = edl_mod.from_dict(raw)
    except ValueError as err:
        raise ToolError(
            f"work/edl.json is invalid ({err}); run `videocut tool edl check`"
        ) from None
    return sized(job, e)[0], raw


def edl_problems(doc: dict, raw_edl: dict) -> list[str]:
    """Slide ids the EDL uses that slides.json lacks; reveal lists on templates without
    build steps or with more times than the slide has steps (counted over all segments that
    show the slide: one clock, one build)."""
    slides, errs, given = by_id(doc), [], {}
    for i, s in enumerate(raw_edl.get("segments") or []):
        if not isinstance(s, dict) or not edl_mod.is_slide(s.get("layout")):
            continue
        sid, p = s.get("slide"), f"$.segments[{i}]"
        if sid not in slides:
            errs.append(f"{p}.slide: {sid!r} is not in {SLIDES_FILE}")
            continue
        rev = s.get("reveal") if isinstance(s.get("reveal"), list) else []
        n = step_count(slides[sid])
        before = given.get(sid, 0)
        given[sid] = before + len(rev)
        if rev and not n:
            errs.append(f"{p}.reveal: template {slides[sid]['template']} has no build steps")
        elif given[sid] > n >= before:
            more = f" ({before} in earlier segments)" if before else ""
            errs.append(f"{p}.reveal: {given[sid]} times for {n} build steps of slide {sid}{more}")
    return errs


def _plan(edl: edl_mod.Edl):
    return plan_mod.build(edl, (16, 16), tuple(edl.size), [s.layout for s in edl.segments])


def placements(edl: edl_mod.Edl) -> dict[str, dict]:
    """Per slide id the EDL shows: {"layout", "runs" (_layouts.slide_runs), "frames" (in
    total), "seconds", "reveal" (clip seconds of the build steps the segments give)}."""
    plan = _plan(edl)
    fps = Fraction(plan.fps)
    out = {}
    for sid, runs in _layouts.slide_runs(edl, plan).items():
        reveal = []
        for r in runs:
            seg = edl.segments[r["segment"]]
            c0 = r["clip_frame"] / fps
            for ts in seg.reveal_src():
                t = float(c0) + (ts - seg.start)
                reveal.append(round(min(max(t, float(c0)), float(c0 + r["frames"] / fps)), 4))
        total = sum(r["frames"] for r in runs)
        out[sid] = {"layout": runs[0]["layout"], "runs": runs, "frames": total,
                    "seconds": round(float(total / fps), 4), "reveal": sorted(reveal)}  # fmt: skip
    return out


def brand_tokens(job: Path) -> dict:
    from .overlays import DEFAULT_BRAND
    from .overlays import brand_tokens as tokens_of

    return tokens_of(read_job(job).get("brand") or DEFAULT_BRAND)


def _plan_slides(job: Path) -> dict[str, dict]:
    """{id: {"layout"?, "t0"?}} from slides[] of work/plan.json (read leniently: the plan
    run writes it)."""
    try:
        doc = json.loads((job / "work" / "plan.json").read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for s in doc.get("slides") or [] if isinstance(doc, dict) else []:
        if not (isinstance(s, dict) and isinstance(s.get("id"), str)):
            continue
        d = out.setdefault(s["id"], {})
        if edl_mod.is_slide(s.get("layout")):
            d["layout"] = s["layout"]
        if _num(s.get("t0")) and s["t0"] >= 0:
            d["t0"] = float(s["t0"])
    return out


def speaker_still(
    job: Path, layout: str, out: tuple[int, int], tokens: dict, t: float, dst: Path
) -> bool:
    """Write the speaker's picture of a slide still (the camera frame at source time `t`,
    cropped and scaled as render draws the bubble / the speaker part) to `dst` (a PNG).
    False when there is no speaker or no usable source frame."""
    from ._color import to_sdr
    from .face import load_face, segment_box
    from .ingest import probe

    part = _layouts.slide_geometry(layout, out, tokens).speaker
    rel = read_job(job).get("source")
    src = (job / rel).resolve() if isinstance(rel, str) and rel else None
    if part is None or src is None or not (src.is_relative_to(job.resolve()) and src.is_file()):
        return False
    try:
        info = probe(src, need_audio=False)
        t = min(max(t, 0.0), max(info["duration"] - 0.1, 0.0))
        face = load_face(job, rel)
        box = segment_box(face, t - 1.0, t + 1.0) if face else None
        w = _layouts.speaker_crop(tuple(info["size"]), (part.w, part.h), box, 0.5, 0.5, 1.0)
        vf = ",".join(f for f in [to_sdr(info), f"crop={w.w}:{w.h}:{w.x}:{w.y}",
                                  f"scale={part.w}:{part.h}"] if f)  # fmt: skip
        run([binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
             "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-vf", vf, str(dst)])  # fmt: skip
    except (ToolError, OSError, ValueError, KeyError):
        return False
    return dst.is_file()


def code_sha(template: str) -> str:
    """Hash of the renderer code that draws `template` (a change renders clips again)."""
    h = hashlib.sha256()
    for rel in (*RENDER_CODE, f"slides/{template}.js"):
        f = RENDERER / rel
        h.update(rel.encode() + b"\0" + (f.read_bytes() if f.is_file() else b""))
    return h.hexdigest()


def spec(
    slide: dict, layout: str, out: tuple[int, int], fps: float, duration: float,
    reveal: list[float], tokens: dict, lang: str, with_frame: bool = False,
) -> dict:  # fmt: skip
    """The renderer spec ("mode": "slide") of one slide in `layout` at output size `out`."""
    geo = _layouts.slide_geometry(layout, out, tokens)
    r = geo.rect

    def shift(b: list[int]) -> list[int]:  # frame px -> clip px
        return [b[0] - r.x, b[1] - r.y, b[2] - r.x, b[3] - r.y]

    doc = {
        "mode": "slide",
        "size": [r.w, r.h],
        "fps": fps,
        "duration": round(duration, 6),
        "unit": min(out),
        "lang": lang,
        "tokens": tokens,
        "slide": {
            "id": slide["id"],
            "template": slide["template"],
            "content": slide["content"],
            "area": shift(geo.area),
            "areas": [shift(b) for b in geo.areas or [geo.area]],
            "reveal": reveal,
        },
        "code": code_sha(slide["template"]),
    }
    if with_frame:
        sp = geo.speaker
        doc["frame"] = {
            "size": list(out),
            "rect": [r.x, r.y, r.w, r.h],
            "speaker": {"box": sp.box(), "round": geo.round} if sp else None,
        }
    return doc


def geometry_doc(layout: str, out: tuple[int, int], tokens: dict) -> dict:
    g = _layouts.slide_geometry(layout, out, tokens)
    return {
        "layout": layout,
        "size": list(out),
        "rect": g.rect.box(),
        "area": g.area,
        "speaker": g.speaker.box() if g.speaker else None,
        "round": g.round,
    }


def _sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _input_files(doc: dict, job: Path) -> list[Path]:
    """The files a spec draws besides its own text: an image slide's page and the brand's
    font files (their bytes can change under the same path)."""
    files = []
    asset = (doc.get("slide") or {}).get("content", {}).get("asset")
    if isinstance(asset, str):
        files.append(job / asset)
    fonts = (doc.get("tokens") or {}).get("fonts") or {}
    paths = list((fonts.get("files") or {}).values()) if isinstance(fonts, dict) else []
    paths += [v for k, v in fonts.items() if k.endswith("_file")] if isinstance(fonts, dict) else []
    files += [Path(p) for p in sorted({p for p in paths if isinstance(p, str)})]
    return files


def spec_sha(doc: dict, job: Path) -> str:
    """SHA-256 of a spec plus the bytes of the files it draws (`_input_files`)."""
    h = hashlib.sha256(json.dumps(doc, sort_keys=True).encode())
    for f in _input_files(doc, job):
        h.update(b"\0" + str(f).encode() + b"\0" + (_sha(f) or "-").encode())
    return h.hexdigest()


def _renderer(argv: list[str], job: Path) -> dict:
    from .overlays import run_renderer

    return run_renderer(argv, job)


def _jpg(src: Path, dst: Path, scale: str | None = None) -> None:
    vf = ["-vf", scale] if scale else []
    run([binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i",
         str(src), *vf, "-q:v", "2", str(dst)])  # fmt: skip


# --- trust record -------------------------------------------------------------------------


def record_path(job: Path) -> Path:
    """The slide render record of a job folder (outside it, like the overlay record)."""
    return state_dir() / job.resolve().name / "slides_render.json"


def _write_record(job: Path, files: list[str]) -> None:
    rec = record_path(job)
    rec.parent.mkdir(parents=True, exist_ok=True)
    write_json(rec, {"job": str(job.resolve()), "sha256": {f: _sha(job / f) for f in files}})


def check_record(job: Path, files: list[str]) -> str | None:
    """None when every file in `files` (relative to the job) is what `slides render`
    recorded, else what is wrong."""
    try:
        rec = json.loads(record_path(job).read_text())
    except (OSError, ValueError):
        return "no record of a slide render by `videocut tool slides render`"
    if not isinstance(rec, dict) or rec.get("job") != str(job.resolve()):
        return "the slide record belongs to another job folder"
    want = rec.get("sha256") if isinstance(rec.get("sha256"), dict) else {}
    changed = [f for f in files if want.get(f) is None or want.get(f) != _sha(job / f)]
    return ("changed since `videocut tool slides render` wrote them: " + ", ".join(changed)
            if changed else None)  # fmt: skip


# --- render -------------------------------------------------------------------------------


def ensure_clips(job: Path, edl: edl_mod.Edl | None = None, force: bool = False) -> dict:
    """Render work/slides/<id>.mov + <id>.json for every slide the EDL shows (kept when its
    spec is unchanged). Returns {"clips": {id: Path}, "rendered": [ids], "kept": [ids],
    "slides": [per-slide summary]}."""
    doc = load_doc(job)
    if edl is None:
        edl, raw = _edl(job)
        if edl is None:
            raise ToolError("work/edl.json is missing; the EDL places the slides")
    else:
        raw = edl.to_dict()
    errs = edl_problems(doc, raw) + asset_errors(job, doc)
    if errs:
        for e in errs:
            print(e)
        raise ToolError(f"{len(errs)} slide problem(s): " + "; ".join(errs[:5]))
    slides, places = by_id(doc), placements(edl)
    out, fps = tuple(edl.size), edl.fps
    tokens, lang = brand_tokens(job), job_lang(read_job(job))
    sdir = job / "work" / "slides"
    sdir.mkdir(parents=True, exist_ok=True)
    clips, rendered, kept, summary, files = {}, [], [], [], []
    for sid, pl in places.items():
        s = slides[sid]
        sp = spec(s, pl["layout"], out, fps, pl["seconds"], pl["reveal"], tokens, lang)
        sha = spec_sha(sp, job)
        mov, rep, spf = sdir / f"{sid}.mov", sdir / f"{sid}.json", sdir / f"{sid}.spec.json"
        try:
            old = json.loads(rep.read_text())
        except (OSError, ValueError):
            old = {}
        # kept only while clip and report are the ones this tool recorded: an edited report
        # must not be blessed by the record written below
        fresh = (not force and isinstance(old, dict) and old.get("spec_sha256") == sha
                 and mov.is_file() and old.get("mov_sha256") == _sha(mov)
                 and check_record(job, [f"work/slides/{sid}.mov",
                                        f"work/slides/{sid}.json"]) is None)  # fmt: skip
        if fresh:
            kept.append(sid)
        else:
            write_json(spf, sp)
            res = _renderer([str(spf), "--out", str(mov), "--report", str(rep)], job)
            report = json.loads(rep.read_text())
            write_json(rep, {
                **report,
                "id": sid,
                "geometry": geometry_doc(pl["layout"], out, tokens),
                "frames": pl["frames"],
                "fps": fps,
                "seconds": pl["seconds"],
                "reveal": pl["reveal"],
                "segments": [r["segment"] for r in pl["runs"]],
                "reading_s": reading_s(s),
                "words_counted": words(s),
                "spec_sha256": sha,
                "mov_sha256": _sha(mov),
                "render_seconds": res.get("seconds"),
            })  # fmt: skip
            rendered.append(sid)
        rep_doc = json.loads(rep.read_text())
        clips[sid] = mov
        files += [f"work/slides/{sid}.mov", f"work/slides/{sid}.json"]
        summary.append(
            {"id": sid, "template": s["template"], "layout": pl["layout"],
             "seconds": pl["seconds"], "fits": rep_doc.get("fits"),
             "scale": rep_doc.get("scale"), "clip": f"work/slides/{sid}.mov"}
        )  # fmt: skip
    _write_record(job, files)
    return {"clips": clips, "rendered": rendered, "kept": kept, "slides": summary}


# --- commands -----------------------------------------------------------------------------


def _job(args) -> Path:
    return resolve_job(getattr(args, "job_pos", None) or args.job)


def check(args) -> dict:
    job = _job(args)
    doc = load_doc(job)
    _, warns = validate_doc(doc)
    errs = asset_errors(job, doc)
    edl, raw = _edl(job)
    used, reading = [], []
    if edl is not None:
        errs += edl_problems(doc, raw)
        slides = by_id(doc)
        for sid, pl in placements(edl).items():
            used.append(sid)
            if sid in slides and pl["seconds"] + 1e-6 < (need := reading_s(slides[sid])):
                reading.append({"id": sid, "seconds": pl["seconds"], "need": need})
                warns.append(
                    f"slide {sid}: on screen {pl['seconds']} s, its {words(slides[sid])} "
                    f"words need {need} s (slide_reading_time in verify)"
                )
    if errs:
        for e in errs:
            print(e)
        raise ToolError(f"{len(errs)} problem(s): " + "; ".join(errs[:5]))
    for w in warns:
        print(f"warning: {w}")
    ids = [s["id"] for s in doc["slides"]]
    return {
        "job": str(job),
        "slides": len(ids),
        "used": used,
        "unused": [i for i in ids if i not in used] if edl is not None else None,
        "reading_time": reading,
        "warnings": warns,
    }


def stills(args) -> dict:
    job = _job(args)
    doc = load_doc(job)
    errs = asset_errors(job, doc)
    if errs:
        raise ToolError("; ".join(errs))
    if args.id and args.id not in by_id(doc):
        raise ToolError(f"no slide {args.id!r} in {SLIDES_FILE}")
    try:
        edl, _ = _edl(job)
    except ToolError:
        edl = None  # the plan gate comes before any cut: stills need no EDL
    places = placements(edl) if edl is not None else {}
    planned = _plan_slides(job)
    out = edl.size if edl is not None else job_size(job)
    fps = edl.fps if edl is not None else DEFAULT_FPS
    tokens, lang = brand_tokens(job), job_lang(read_job(job))
    sdir = job / "work" / "slides" / "stills"
    sdir.mkdir(parents=True, exist_ok=True)
    done = []
    for s in doc["slides"]:
        if args.id and s["id"] != args.id:
            continue
        pl = places.get(s["id"])
        plan_doc = planned.get(s["id"], {})
        layout = pl["layout"] if pl else plan_doc.get("layout", DEFAULT_LAYOUT)
        reveal = pl["reveal"] if pl else []
        t_src = edl.segments[pl["runs"][0]["segment"]].start + 0.5 if pl else plan_doc.get("t0")
        # the final state: every build step in (automatic pace without reveal times) + 2 s
        last = max([*reveal, 0.5 + 0.7 * max(step_count(s) - 1, 0)])
        dur = round(max(last + 3.0, 4.0 if s["template"] != "image" else 9.0), 3)
        sp = spec(s, layout, out, fps, dur, reveal, tokens, lang, with_frame=True)
        face_png = sdir / f"{s['id']}_speaker.png"
        face_png.unlink(missing_ok=True)
        if sp["frame"]["speaker"] and t_src is not None:
            if speaker_still(job, layout, out, tokens, t_src, face_png):
                sp["frame"]["speaker"]["image"] = str(face_png.relative_to(job))
        spf = sdir / f"{s['id']}.spec.json"
        write_json(spf, sp)
        png = sdir / f"{s['id']}.png"
        rep = sdir / f"{s['id']}.layout.json"
        _renderer([str(spf), "--still", str(dur - 1 / fps), str(png), "--report", str(rep)], job)
        full, small = sdir / f"{s['id']}.jpg", sdir / f"{s['id']}_sm.jpg"
        _jpg(png, full)
        k = STILL_MAX / max(out)
        _jpg(
            png,
            small,
            f"scale={round(out[0] * min(k, 1) / 2) * 2}:{round(out[1] * min(k, 1) / 2) * 2}",
        )
        png.unlink(missing_ok=True)
        face_png.unlink(missing_ok=True)
        r = json.loads(rep.read_text())
        done.append({
            "id": s["id"], "template": s["template"], "layout": layout,
            "still": str(full.relative_to(job)), "small": str(small.relative_to(job)),
            "fits": r.get("fits"), "scale": r.get("scale"), "words": words(s),
            "reading_s": reading_s(s),
        })  # fmt: skip
        if not r.get("fits"):
            print(f"warning: slide {s['id']}: the text does not fit even at "
                  f"{int(MIN_SCALE * 100)} %; shorten it")  # fmt: skip
    return {"job": str(job), "size": list(out), "stills": done,
            "not_fitting": [d["id"] for d in done if not d["fits"]]}  # fmt: skip


def render(args) -> dict:
    job = _job(args)
    res = ensure_clips(job, force=args.force)
    for s in res["slides"]:
        if not s["fits"]:
            print(
                f"warning: slide {s['id']}: the text does not fit even at {int(MIN_SCALE * 100)} %"
            )
    return {"job": str(job), "rendered": res["rendered"], "kept": res["kept"],
            "slides": res["slides"]}  # fmt: skip


def register(subparsers) -> None:
    p = subparsers.add_parser("slides", help="slides from brand templates (work/slides.json)")
    sub = p.add_subparsers(dest="slides_cmd", required=True)
    c = sub.add_parser("check", help="validate work/slides.json (+ the EDL's slides)",
                       parents=[job_parent()])  # fmt: skip
    c.add_argument("job_pos", nargs="?", metavar="job", help="job folder or id")
    c.set_defaults(handler=run_tool("slides check", check))
    s = sub.add_parser("stills", help="final-state stills of the slides", parents=[job_parent()])
    s.add_argument("job_pos", nargs="?", metavar="job", help="job folder or id")
    s.add_argument("--id", help="only this slide")
    s.set_defaults(handler=run_tool("slides stills", stills))
    r = sub.add_parser("render", help="opaque slide clips for render", parents=[job_parent()])
    r.add_argument("job_pos", nargs="?", metavar="job", help="job folder or id")
    r.add_argument("--force", action="store_true", help="render every clip again")
    r.set_defaults(handler=run_tool("slides render", render))
