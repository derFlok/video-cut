"""Sound effects of the motion scenes: the level per job and the sound library (Settings).

GET  /api/settings/sfx                       {"sfx_default", "levels", "kinds": {kind: {"uses":
     origin, "sounds": [{id, kind, origin, title, author, licence, licence_url, source_url,
     attribution, duration_s}]}}} (tools/sfx.library_view; procedural sounds included)
PUT  /api/settings/sfx                       {"sfx_default": "off"|"subtle"|"normal"}
POST /api/settings/sfx/{kind}                multipart field `sound` (one audio file, at most
     20 MB / 30 s; decoded and processed by _sfx_library.add_upload), optional `title`:
     the person's own sound for that kind, preferred over downloads
DELETE /api/settings/sfx/{kind}/{sound_id}   remove an upload or a download
POST /api/settings/sfx/fetch                 {"kind"?: one kind} download CC0/PDM sounds from
     Openverse into the library (once per kind; host only, about 40 s for all kinds)
GET  /api/settings/sfx/{kind}/{sound_id}/audio   the processed sound as WAV (preview)
PUT  /api/jobs/{id}/sfx                      {"sfx": "off"|"subtle"|"normal"|"default"}; a
     reel mixed at another level goes stale (cut_stale, the app offers /recut)

The job view (state.job_view) carries "sfx" (job.json value or null), "sfx_resolved" and
"sfx_events" (whether the job has motion events at all).
"""

import io
import shutil

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .. import settings
from ..tools import _sfx_library as lib
from ..tools import sfx as sfx_tool
from ..tools.common import ToolError
from ..tools.job import set_sfx
from . import state
from .uploads import UploadError, read_form

LEVEL_RE = "^(" + "|".join(sfx_tool.LEVELS) + ")$"


class SfxDefault(BaseModel):
    sfx_default: str = Field(pattern=LEVEL_RE)


class JobSfx(BaseModel):
    sfx: str = Field(pattern="^(" + "|".join((*sfx_tool.LEVELS, "default")) + ")$")


class FetchBody(BaseModel):
    kind: str | None = None


def settings_view() -> dict:
    return {
        "sfx_default": sfx_tool.default_level(),
        "levels": list(sfx_tool.LEVELS),
        "kinds": sfx_tool.library_view(),
    }


def put_settings(body: SfxDefault) -> dict:
    settings.save(sfx_default=body.sfx_default)
    return settings_view()


def _kind(kind: str) -> str:
    if kind not in lib.KINDS:
        raise HTTPException(404, f"no sound kind {kind!r}")
    return kind


async def upload(kind: str, request: Request) -> dict:
    _kind(kind)
    try:
        form = await read_form(
            request, {"sound": lib.UPLOAD_EXT}, {"title"}, max_file_bytes=lib.MAX_UPLOAD_BYTES
        )
    except UploadError as e:
        raise HTTPException(e.status, str(e)) from None
    try:
        src = form.file("sound")
        if src is None or len(form.files.get("sound") or []) != 1:
            raise HTTPException(400, "send exactly one file (field sound)")
        try:
            added = lib.add_upload(kind, src, form.fields.get("title"))
        except ToolError as e:
            raise HTTPException(415, str(e)) from None
    finally:
        shutil.rmtree(form.folder, ignore_errors=True)
    return {**settings_view(), "added": added.doc()}


def delete(kind: str, sound_id: str) -> dict:
    _kind(kind)
    if not lib.remove(kind, sound_id):
        raise HTTPException(404, f"no stored sound {sound_id!r} for {kind}")
    return settings_view()


def fetch(body: FetchBody | None = None) -> dict:
    kinds = [_kind(body.kind)] if body and body.kind else list(lib.KINDS)
    try:
        report = lib.fetch(kinds, say=lambda _: None)
    except ToolError as e:
        raise HTTPException(400, str(e)) from None
    return {**settings_view(), "fetched": report}


def audio(kind: str, sound_id: str):
    _kind(kind)
    found = next((s for s in lib.sounds(kind) if s.id == sound_id), None)
    if found is None:
        raise HTTPException(404, f"no sound {sound_id!r} for {kind}")
    if found.origin != "procedural":
        return FileResponse(lib.root() / kind / f"{found.id}.wav", media_type="audio/wav")
    import wave

    x = lib.samples(found)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(lib.RATE)
        w.writeframes((x.clip(-1, 1) * 32767).round().astype("<i2").tobytes())
    return Response(buf.getvalue(), media_type="audio/wav")


def register(app, job_or_404, not_running, decision) -> None:
    app.get("/api/settings/sfx")(settings_view)
    app.put("/api/settings/sfx")(put_settings)
    app.post("/api/settings/sfx/fetch")(fetch)  # before /{kind}: "fetch" is no kind
    app.post("/api/settings/sfx/{kind}")(upload)
    app.delete("/api/settings/sfx/{kind}/{sound_id}")(delete)
    app.get("/api/settings/sfx/{kind}/{sound_id}/audio")(audio)

    @app.put("/api/jobs/{job_id}/sfx")
    @decision
    def put_job_sfx(job_id: str, body: JobSfx) -> dict:
        """Store the job's sound-effect level; a reel mixed at another level goes stale."""
        job = job_or_404(job_id)
        not_running(job)
        try:
            res = set_sfx(job, body.sfx, say=lambda _: None)
        except ToolError as e:
            raise HTTPException(400, str(e)) from None
        return {"id": job.name, "sfx": res["sfx"], "sfx_resolved": res["sfx_resolved"],
                "previous": res["previous"], "cut_stale": state.cut_stale(job)}  # fmt: skip
