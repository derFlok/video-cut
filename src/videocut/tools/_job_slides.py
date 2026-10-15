"""Slides of a job (human settings): `videocut tool job slides` and `job slides-import`.

job.json "slides" = {"mode": "none" | "upload" | "auto" | "both", "wish": str}; absent =
none. `upload`: the human's own pages (PDF export or images) are matched to the moments
they belong to; `auto`: Claude proposes slides from brand templates for what is said,
following the wish; `both`: either. The wish (≤ WISH_MAX characters) is the human's
free-text request ("show the three steps as a flow").

Uploaded pages live in input/slides/page_NN.png (NN from 01), rasterised here on this
machine at LONG_EDGE px on the long side: a PDF through CoreGraphics (_pdf.py, in a child
process with a time limit), an image through ffmpeg. Files are judged by content (PDF
magic bytes, ffprobe for images), never by extension alone; nothing uploaded is executed.

The plan depends on these settings: a plan run records what it was made for (job.json
"plan_slides" {mode, wish, pages_sha256}); changing them afterwards makes the plan stale
(`run` plans again) and revokes an approval. Human only: refused inside an agent run.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import _pdf
from .common import JOB_FILE, ToolError, binary, read_job, resolve_job, run_tool, write_json

MODES = ("none", "upload", "auto", "both")
WISH_MAX = 2000
DIR = "input/slides"
LONG_EDGE = 1920
MAX_FILE_BYTES = 200 * 1024**2
MAX_PAGES = 60  # all pages of a job
PAGE_RE = r"page_(\d{2})\.png"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
FILE_EXT = IMAGE_EXT | {".pdf"}
IMAGE_FORMATS = {"png_pipe", "jpeg_pipe", "webp_pipe", "image2"}
IMAGE_CODECS = {"png", "mjpeg", "webp"}
AGENT_ENV = "VIDEOCUT_AGENT"
MODE_HELP = (
    "none (no slides), upload (your own pages: PDF or images), auto (Claude proposes slides "
    "from the brand's templates), both"
)


def _human_only(what: str) -> None:
    if os.environ.get(AGENT_ENV):
        raise ToolError(f"only a human {what} (not available inside an agent run)")


def parse_mode(value) -> str:
    v = str(value or "").strip().lower()
    if v not in MODES:
        raise ToolError(f"unknown slides mode {value!r}; one of: {', '.join(MODES)}")
    return v


def parse_wish(value) -> str:
    wish = " ".join(str(value or "").split())
    if len(wish) > WISH_MAX:
        raise ToolError(f"the slides wish is {len(wish)} characters long; at most {WISH_MAX}")
    return wish


def stored(data: dict) -> dict:
    """job.json "slides" as {mode, wish}; absent or invalid: none."""
    raw = data.get("slides")
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in MODES else "none"
    wish = raw.get("wish") if isinstance(raw.get("wish"), str) else ""
    return {"mode": mode, "wish": wish}


def pages(job: Path) -> list[str]:
    """The uploaded pages, relative to the job folder, in page order."""
    d = job / DIR
    if not d.is_dir():
        return []
    found = [p for p in d.iterdir() if re.fullmatch(PAGE_RE, p.name) and p.is_file()]
    return [f"{DIR}/{p.name}" for p in sorted(found)]


def pages_sha(job: Path) -> str | None:
    rels = pages(job)
    if not rels:
        return None
    h = hashlib.sha256()
    for rel in rels:
        h.update(rel.encode() + b"\0" + hashlib.sha256((job / rel).read_bytes()).digest())
    return h.hexdigest()


def signature(job: Path, data: dict | None = None) -> dict:
    """What a plan made now is made for."""
    data = read_job(job) if data is None else data
    return {**stored(data), "pages_sha256": pages_sha(job)}


def view(job: Path, data: dict | None = None) -> dict:
    """For `job show`, the app and the skills."""
    data = read_job(job) if data is None else data
    return {**stored(data), "pages": pages(job), "stale": plan_stale(job, data)}


def plan_stale(job: Path, data: dict | None = None) -> bool:
    """The plan (work/project.md) was made for other slide settings or pages. A plan
    without a record counts as made for the current ones (set/import record the old
    settings before changing them)."""
    if not (job / "work" / "project.md").is_file():
        return False
    data = read_job(job) if data is None else data
    rec = data.get("plan_slides")
    return isinstance(rec, dict) and rec != signature(job, data)


def stamp_plan(job: Path, made_for: dict | None = None) -> None:
    """Record the slide settings the current plan was made for (`run`, after a plan run,
    with the signature taken when the run started: a change during the run stays stale)."""
    data = read_job(job)
    data["plan_slides"] = made_for if made_for is not None else signature(job, data)
    write_json(job / JOB_FILE, data)


def _keep_plan_record(job: Path, data: dict) -> dict:
    """Before a change: a plan without a record was made for the settings as they are."""
    if (job / "work" / "project.md").is_file() and not isinstance(data.get("plan_slides"), dict):
        data["plan_slides"] = signature(job, data)
        write_json(job / JOB_FILE, data)
    return data


def _after_change(job: Path, say) -> dict:
    """Revoke an approval of a plan that no longer fits; the hint says what to do."""
    from .job import read_approval, revoke_approval

    data = read_job(job)
    stale = plan_stale(job, data)
    revoked = False
    if stale and read_approval(job):
        revoke_approval(job)
        revoked = True
    if stale:
        say(
            f"The plan of this job was made for other slides. Plan again and approve the new "
            f"plan: `videocut run {job.name} --replan`"
            + (" (the earlier approval no longer counts)" if revoked else "")
        )
    return {"plan_stale": stale, "approval_revoked": revoked}


def set_slides(job: Path, mode=None, wish=None, say=print) -> dict:
    """Set the mode and/or the wish (None: keep). Mode none keeps uploaded pages on disk
    (they are simply not used)."""
    data = _keep_plan_record(job, read_job(job))
    cur = stored(data)
    new = {
        "mode": cur["mode"] if mode is None else parse_mode(mode),
        "wish": cur["wish"] if wish is None else parse_wish(wish),
    }
    if new["mode"] == "none" and not new["wish"]:
        data.pop("slides", None)
    else:
        data["slides"] = new
    write_json(job / JOB_FILE, data)
    say(f"{job.name}: slides {new['mode']}" + (f", wish: {new['wish']}" if new["wish"] else ""))
    if new["mode"] in ("upload", "both") and not pages(job):
        say(f"no pages uploaded yet: `videocut tool job slides-import {job.name} FILE...`")
    return {"job": str(job), "id": job.name, **view(job), **_after_change(job, say)}


def slides_cmd(args) -> dict:
    if args.mode is None and args.wish is None:
        job = resolve_job(args.job_ref or args.job)
        v = view(job)
        print(f"{job.name}: slides {v['mode']}, {len(v['pages'])} page(s)")
        return {"job": str(job), "id": job.name, **v}
    _human_only("sets the slides of a job")
    return set_slides(resolve_job(args.job_ref or args.job), args.mode, args.wish)


# --------------------------------------------------------------- import


def _sub(argv: list[str], timeout: int, name: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ToolError(f"{name}: reading it took longer than {timeout} s") from None
    except OSError as e:
        raise ToolError(f"{name}: cannot be read ({e})") from None


def _probe_image(path: Path) -> tuple[int, int]:
    """(w, h) of a PNG/JPEG/WebP judged by content; ToolError otherwise."""
    r = _sub(
        [binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file",
         "-show_entries", "stream=codec_name,width,height:format=format_name",
         "-of", "json", str(path)],
        60, path.name,
    )  # fmt: skip
    try:
        info = json.loads(r.stdout or "{}")
        st = next(s for s in info.get("streams") or [] if s.get("codec_name") in IMAGE_CODECS)
        fmts = set(str((info.get("format") or {}).get("format_name") or "").split(","))
        w, h = int(st["width"]), int(st["height"])
    except (ValueError, StopIteration, KeyError, TypeError):
        raise ToolError(f"{path.name}: not a PNG, JPEG or WebP image") from None
    if r.returncode != 0 or not fmts & IMAGE_FORMATS or w < 16 or h < 16:
        raise ToolError(f"{path.name}: not a PNG, JPEG or WebP image")
    if w * h > 12000 * 12000:
        raise ToolError(f"{path.name}: the image is too large ({w}x{h})")
    return w, h


def is_pdf(path: Path) -> bool:
    with path.open("rb") as f:
        return b"%PDF-" in f.read(1024)


def _fit(w: int, h: int) -> tuple[int, int]:
    k = LONG_EDGE / max(w, h)
    return max(2, round(w * k / 2) * 2), max(2, round(h * k / 2) * 2)


def _image_page(src: Path, dst: Path) -> None:
    w, h = _fit(*_probe_image(src))
    r = _sub(
        [binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file",
         "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={w}:{h}:flags=lanczos,format=rgb24", str(dst)],
        120, src.name,
    )  # fmt: skip
    if r.returncode != 0 or not dst.is_file():
        raise ToolError(f"{src.name}: could not be converted ({r.stderr.strip()[-200:]})")


def _rasterise(src: Path, tmp: Path) -> list[Path]:
    """The pages of one file as PNGs in a fresh folder under `tmp`, in order."""
    out = Path(tempfile.mkdtemp(dir=tmp))
    if is_pdf(src):
        try:
            return _pdf.rasterise(src, out, LONG_EDGE)
        except _pdf.PdfError as e:
            raise ToolError(str(e)) from None
    if src.suffix.lower() not in IMAGE_EXT:
        raise ToolError(f"{src.name}: must be a PDF or an image ({', '.join(sorted(FILE_EXT))})")
    dst = out / "page_01.png"
    _image_page(src, dst)
    return [dst]


def import_slides(job: Path, files: list[Path], replace: bool = False, say=print) -> dict:
    """Rasterise `files` (PDF pages, images; in the given order) to input/slides/ after the
    pages already there (`replace`: instead of them). Mode none becomes upload, auto
    becomes both. All or nothing: a file that fails leaves the pages as they were."""
    if not files:
        raise ToolError("no files given")
    for f in files:
        if not f.is_file():
            raise ToolError(f"file not found: {f}")
        if f.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(f"{f.name}: larger than {MAX_FILE_BYTES // 1024**2} MB")
    data = _keep_plan_record(job, read_job(job))
    dest = job / DIR
    old = [] if replace else pages(job)
    start = len(old) + 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".slides-", dir=dest.parent))
    try:
        made: list[tuple[str, Path]] = []
        for f in files:
            for p in _rasterise(f, tmp):
                made.append((f.name, p))
                if len(old) + len(made) > MAX_PAGES:
                    raise ToolError(f"at most {MAX_PAGES} slide pages per job")
        if replace and dest.is_dir():
            dest.rename(tmp / "old")  # removed with tmp once the new pages are in place
        dest.mkdir(exist_ok=True)
        if not replace:  # renumber what is there (a gap after a manual delete)
            for k, rel in enumerate(old, 1):
                p = job / rel
                if p.name != f"page_{k:02d}.png":
                    p.rename(dest / f"page_{k:02d}.png")
        added = []
        for k, (name, p) in enumerate(made, start):
            target = dest / f"page_{k:02d}.png"
            p.replace(target)
            added.append({"page": f"{DIR}/{target.name}", "from": name})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    cur = stored(data)
    mode = {"none": "upload", "auto": "both"}.get(cur["mode"], cur["mode"])
    if mode != cur["mode"]:
        data = read_job(job)
        data["slides"] = {**cur, "mode": mode}
        write_json(job / JOB_FILE, data)
    say(f"{job.name}: {len(added)} slide page(s) added, {len(pages(job))} in total; slides {mode}")
    for a in added:
        say(f"  {a['page']}  ({a['from']})")
    return {"job": str(job), "id": job.name, "added": added, **view(job),
            **_after_change(job, say)}  # fmt: skip


def import_cmd(args) -> dict:
    _human_only("adds slides to a job")
    return import_slides(
        resolve_job(args.job_ref), [Path(f).expanduser() for f in args.files], args.replace
    )


def register(sub, job_parent) -> None:
    s = sub.add_parser(
        "slides",
        help="show or set the slides of a job: mode and wish (human only)",
        parents=[job_parent()],
    )
    s.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    s.add_argument("--mode", help=MODE_HELP)
    s.add_argument("--wish", help=f"what the slides should show, free text (≤ {WISH_MAX})")
    s.set_defaults(handler=run_tool("job slides", slides_cmd))
    i = sub.add_parser(
        "slides-import",
        help="add your own slides (PDF pages or images) to input/slides/ (human only)",
    )
    i.add_argument("job_ref", metavar="job", help="job folder or id")
    i.add_argument("files", nargs="+", metavar="FILE", help="PDF or PNG/JPG files, in order")
    i.add_argument("--replace", action="store_true", help="replace the pages already there")
    i.set_defaults(handler=run_tool("job slides-import", import_cmd))
