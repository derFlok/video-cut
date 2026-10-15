"""Multipart uploads streamed straight to disk (recordings are several GB; nothing is held
in memory), then checked: allowed field, allowed extension, size cap, and ffprobe must see
the expected stream. Uploaded files are data only: never executed, never served back
except through the fixed media/stills routes."""

import asyncio
import json
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

from ..tools._job_slides import FILE_EXT as SLIDE_EXT
from ..tools.common import binary, home
from ..tools.job import ASSET_EXT, SCREEN_EXT

VIDEO_EXT = SCREEN_EXT  # camera and screen recordings: .mov .mp4 .m4v .mkv .webm
MAX_FILE_BYTES = 40 * 1024**3  # one recording; 4K ProRes for a few minutes stays below
MAX_TEXT_BYTES = 16 * 1024  # a form text field (the brief is one line)
MAX_HEADER_BYTES = 4096
MAX_SLIDE_FILES = 30  # slide files (PDFs or images) per job
MAX_SLIDE_BYTES = 200 * 1024**2  # one slide file (tools/_job_slides.MAX_FILE_BYTES)
WRITE_BATCH = 1024**2  # bytes handed to the parser thread at once
NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Form:
    folder: Path  # staging folder; the caller removes it
    files: dict[str, list[Path]] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)

    def file(self, name: str) -> Path | None:
        got = self.files.get(name) or []
        return got[0] if got else None


def staging_dir() -> Path:
    """Next to the jobs (same disk), so `job new` can hard-link instead of copying."""
    d = home() / "uploads" / uuid.uuid4().hex
    d.mkdir(parents=True)
    return d


def safe_name(filename: str) -> str:
    """A plain file name from a browser-supplied one (no folders, no odd characters)."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = NAME_RE.sub("_", base).strip(" .")
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = (stem or "upload")[:80]
    return f"{stem}.{ext.lower()}" if ext else stem


async def read_form(
    request: Request,
    file_fields: dict[str, set[str]],
    text_fields: set[str],
    max_file_bytes: int | None = None,
    field_caps: dict[str, tuple[int, int]] | None = None,
) -> Form:
    """Parse a multipart body into a staging folder. `file_fields` maps a field name to its
    allowed extensions; a field may repeat (assets). Unknown fields are refused.
    `field_caps` {field: (max files, max bytes per file)} tightens the limits of a field."""
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise UploadError("expected multipart/form-data", 415)
    cap = MAX_FILE_BYTES if max_file_bytes is None else max_file_bytes
    form = Form(staging_dir())
    state: dict = {"headers": {}, "field": b"", "value": b"", "ended": False}

    def on_part_begin():
        state.update(headers={}, name=None, out=None, text=None, size=0, ended=False)

    def on_header_field(data, start, end):
        state["field"] += data[start:end]

    def on_header_value(data, start, end):
        state["value"] += data[start:end]

    def on_header_end():
        state["headers"][state["field"].lower()] = state["value"]
        state["field"], state["value"] = b"", b""

    def on_headers_finished():
        _, disp = parse_options_header(state["headers"].get(b"content-disposition", b""))
        name = disp.get(b"name", b"").decode("utf-8", "replace").removesuffix("[]")
        filename = disp.get(b"filename")
        state["name"] = name
        if filename is None:
            if name not in text_fields:
                raise UploadError(f"unexpected form field {name!r}")
            state["text"] = bytearray()
            return
        if name not in file_fields:
            raise UploadError(f"unexpected file field {name!r}")
        n_max, b_max = (field_caps or {}).get(name, (None, cap))
        if n_max is not None and len(form.files.get(name, [])) >= n_max:
            raise UploadError(f"{name}: at most {n_max} files", 413)
        state["cap"] = b_max
        fname = safe_name(filename.decode("utf-8", "replace"))
        if Path(fname).suffix.lower() not in file_fields[name]:
            allowed = ", ".join(sorted(file_fields[name]))
            raise UploadError(f"{name}: {fname} is not one of {allowed}", 415)
        sub = form.folder / name / str(len(form.files.get(name, [])))
        sub.mkdir(parents=True)
        dst = sub / fname
        form.files.setdefault(name, []).append(dst)
        state["out"] = dst.open("wb")

    def on_part_data(data, start, end):
        n = end - start
        state["size"] += n
        if state["text"] is not None:
            if state["size"] > MAX_TEXT_BYTES:
                raise UploadError(f"{state['name']}: text too long", 413)
            state["text"] += data[start:end]
        elif state["out"] is not None:
            if state["size"] > state.get("cap", cap):
                raise UploadError(f"{state['name']}: file larger than the limit", 413)
            state["out"].write(data[start:end])

    def on_part_end():
        state["ended"] = True
        if state.get("out") is not None:
            state["out"].close()
            state["out"] = None
        elif state.get("text") is not None:
            form.fields[state["name"]] = state["text"].decode("utf-8", "replace")

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
        max_header_size=MAX_HEADER_BYTES,
    )
    ok = False
    try:
        # Parsing and disk writes run in a worker thread, in batches, so a multi-GB upload
        # does not stall the event loop (SSE streams, other requests).
        batch, size = [], 0
        async for chunk in request.stream():
            batch.append(chunk)
            size += len(chunk)
            if size >= WRITE_BATCH:
                await asyncio.to_thread(parser.write, b"".join(batch))
                batch, size = [], 0
        if batch:
            await asyncio.to_thread(parser.write, b"".join(batch))
        parser.finalize()
        if not state.get("ended"):  # finalize() does not check the closing boundary
            raise UploadError("the upload is incomplete (the connection ended early)")
        ok = True
    except UploadError:
        raise
    except Exception as e:  # noqa: BLE001 — a malformed body or a dropped connection
        raise UploadError(f"could not read the upload: {e}") from None
    finally:
        if not ok:  # also on cancellation (client gone)
            _close(state)
            shutil.rmtree(form.folder, ignore_errors=True)
    return form


def _close(state: dict) -> None:
    out = state.get("out")
    if out is not None:
        out.close()


def probe(path: Path) -> dict:
    """ffprobe's view of a file (streams only); UploadError when it is not media."""
    try:
        r = subprocess.run(
            [
                binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file",
                "-show_entries", "stream=codec_type,codec_name,width,height:format=format_name",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UploadError(f"{path.name}: cannot be checked ({e})", 415) from None
    try:
        data = json.loads(r.stdout or "{}")
        streams = data.get("streams") or []
        fmt = set(str((data.get("format") or {}).get("format_name") or "").split(","))
    except (ValueError, AttributeError):
        streams, fmt = [], set()
    if r.returncode != 0 or not streams:
        raise UploadError(f"{path.name}: not a readable video or image file", 415)
    return {"streams": streams, "formats": fmt}


# Containers a recording may come in (ffprobe format names). Anything else (playlists,
# concat lists, raw streams) is refused: the format is judged by content, not extension.
VIDEO_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi"}
IMAGE_FORMATS = {"png_pipe", "jpeg_pipe", "webp_pipe", "image2"}


def check_video(path: Path, need_audio: bool) -> None:
    info = probe(path)
    if not info["formats"] & VIDEO_FORMATS:
        raise UploadError(f"{path.name}: not a video container (MOV, MP4, MKV, WebM, AVI)", 415)
    kinds = {s.get("codec_type") for s in info["streams"]}
    if "video" not in kinds:
        raise UploadError(f"{path.name}: has no video stream", 415)
    if need_audio and "audio" not in kinds:
        raise UploadError(f"{path.name}: has no audio (the camera recording needs sound)", 415)


IMAGE_CODECS = {"png", "mjpeg", "webp", "jpeg2000"}


def check_slide(path: Path) -> None:
    """A slide upload: a PDF (by its magic bytes; parsed later in a time-limited child
    process, tools/_pdf.py) or an image that probes as PNG, JPEG or WebP."""
    with path.open("rb") as f:
        head = f.read(1024)
    if path.suffix.lower() == ".pdf" or head.startswith(b"%PDF"):
        if b"%PDF-" not in head:
            raise UploadError(f"{path.name}: not a PDF file", 415)
        return
    check_image(path)


def check_image(path: Path) -> None:
    info = probe(path)
    streams = info["streams"]
    if not info["formats"] & IMAGE_FORMATS or not any(
        s.get("codec_name") in IMAGE_CODECS for s in streams
    ):
        raise UploadError(f"{path.name}: not a PNG, JPEG or WebP image", 415)


__all__ = ["ASSET_EXT", "SLIDE_EXT", "VIDEO_EXT", "Form", "UploadError", "read_form"]
