"""The FastAPI app: session, video types, jobs (create, inspect, plan, approve, feedback,
media, delete) and the UI's static files. Routes follow the shared API contract of the
local app (docs/BRIEF.md, Phase 3)."""

import asyncio
import functools
import json
import re
import shutil
from datetime import datetime
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__, agent, settings
from .. import redaction as red
from .. import run as pipeline
from ..tools import _format
from ..tools.common import ToolError, home, update_job
from ..tools.job import (
    approve_job,
    brand_id,
    is_approved,
    project_sha,
    read_approval,
    revoke_approval,
)
from . import brands, state
from .runs import Busy, Runs
from .security import Guard
from .uploads import ASSET_EXT, VIDEO_EXT, UploadError, check_image, check_video, read_form

# Video types offered as cards (BRIEF §7). `needs`/`optional` name the inputs of the job
# form: camera (the recording), screen (a screen recording of the same take), assets
# (screenshots or photos).
TYPES = [
    {
        "id": "thought",
        "name_de": "Gedanke des Tages",
        "name_en": "Thought of the day",
        "desc_de": "Eine These in 30–45 s, die Kernaussage groß eingeblendet.",
        "desc_en": "One thesis in 30–45 s, the key message shown large.",
        "needs": ["camera"],
        "optional": ["assets"],
    },
    {
        "id": "myth",
        "name_de": "Mythos vs. Realität",
        "name_en": "Myth vs. reality",
        "desc_de": "Ein verbreiteter Irrtum und was wirklich stimmt, als Gegenüberstellung.",
        "desc_en": "A common misconception and what is actually true, side by side.",
        "needs": ["camera"],
        "optional": ["assets"],
    },
    {
        "id": "question",
        "name_de": "Kundenfrage beantworten",
        "name_en": "Answer a customer question",
        "desc_de": "Die Frage als Chat-Blase, dann die Antwort.",
        "desc_en": "The question as a chat bubble, then the answer.",
        "needs": ["camera"],
        "optional": ["assets"],
    },
    {
        "id": "tips",
        "name_de": "Drei Tipps / Liste",
        "name_en": "Three tips / list",
        "desc_de": "Nummerierte Punkte, jeder mit eigener Einblendung.",
        "desc_en": "Numbered points, each with its own overlay.",
        "needs": ["camera"],
        "optional": ["assets"],
    },
    {
        "id": "diary",
        "name_de": "Tagebuch / Hinter den Kulissen",
        "name_en": "Diary / behind the scenes",
        "desc_de": "Ein Einblick in den Alltag, mit Kapitelmarken.",
        "desc_en": "A look behind the scenes, with chapter marks.",
        "needs": ["camera"],
        "optional": ["assets"],
    },
    {
        "id": "walkthrough",
        "name_de": "Produkt-Walkthrough",
        "name_en": "Product walkthrough",
        "desc_de": "Bildschirmaufnahme oder Screenshots, Zoom auf die besprochene Stelle.",
        "desc_en": "Screen recording or screenshots, zoomed to the part being discussed.",
        "needs": ["camera"],
        "optional": ["screen", "assets"],
    },
    {
        "id": "release",
        "name_de": "Neuheit / Release",
        "name_en": "News / release",
        "desc_de": "Was ist neu: vorher und nachher.",
        "desc_en": "What is new: before and after.",
        "needs": ["camera"],
        "optional": ["screen", "assets"],
    },
    {
        "id": "case",
        "name_de": "Kundenstimme / Fallbeispiel",
        "name_en": "Testimonial / case",
        "desc_de": "Ein Screenshot hervorgehoben, Namen und Nummern geschwärzt.",
        "desc_en": "A screenshot highlighted, names and numbers redacted.",
        "needs": ["camera"],
        "optional": ["screen", "assets"],
    },
]
FORMATS = ("auto", *_format.FORMATS)
MAX_TEXT = 4000  # plan feedback / feedback text
FALLBACK_PAGE = "web/index.html"
DIST = "web/dist"


# ------------------------------------------------------------------ pipeline adapters


class Pipeline:
    """How the API drives src/videocut/run.py. Tests replace it with a fake."""

    def start(self, job: Path, say, ask) -> int:
        return pipeline.run(str(job), say=say, ask=ask)

    def cont(self, job: Path, say, ask) -> int:
        """After /approve: the approval is recorded, so run() goes on to cut + overlays."""
        return pipeline.run(str(job), say=say, ask=ask)

    def plan_feedback(self, job: Path, text: str, say, ask) -> int:
        """`videocut run <job> --plan-feedback <text>`: the plan run revises the plan."""
        return pipeline.run(str(job), replan=True, plan_feedback=text, say=say, ask=ask)

    def feedback(self, job: Path, text: str, at_s: float | None, say, ask) -> int:
        """`videocut run <job> --feedback <text> [--at <output_s>]`: run.revise()."""
        return pipeline.run(str(job), feedback=text, at_s=at_s, say=say, ask=ask)


# ------------------------------------------------------------------ errors
# Every error body is {"detail": {"message": str, "code"?: str, "errors"?: [str]}}.
# 409 codes: busy (a run is queued or active), stale (the plan changed since it was read),
# state (the job is not at the step the request needs), exists, read_only.


def conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, {"message": message, "code": code})


async def _http_error(request: Request, exc) -> JSONResponse:
    d = exc.detail
    if not isinstance(d, dict):
        d = {"message": str(d)}
    elif "message" not in d:
        d = {"message": "; ".join(map(str, d.get("errors") or [])) or "error", **d}
    return JSONResponse({"detail": d}, status_code=exc.status_code, headers=exc.headers)


async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        f"{'.'.join(str(x) for x in e.get('loc', ())[1:]) or 'body'}: {e.get('msg')}"
        for e in exc.errors()
    ]
    return JSONResponse(
        {"detail": {"message": "invalid request: " + "; ".join(errors[:3]), "errors": errors}},
        status_code=422,
    )


# ------------------------------------------------------------------ request bodies


class PlanEdit(BaseModel):
    markdown: str = Field(max_length=500_000)
    sha: str | None = None


class TextBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class FeedbackBody(TextBody):
    at_s: float | None = Field(default=None, ge=0, le=36_000)


class Redaction(BaseModel):
    mode: str = Field(pattern="^(demo|redact_all|redact)$")
    ids: list[int] = Field(default_factory=list, max_length=1000)


class ApproveBody(BaseModel):
    plan_sha: str
    redaction: Redaction | None = None


# ------------------------------------------------------------------ app


def _ui_dir() -> Path | None:
    d = Path(str(resources.files("videocut").joinpath(DIST)))
    return d if (d / "index.html").is_file() else None


def create_app(
    token: str,
    port: int = 4300,
    hosts: set[str] | None = None,
    runner: Pipeline | None = None,
) -> FastAPI:
    app = FastAPI(title="Video Cut", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)  # fmt: skip
    runs = Runs()
    run_with = runner or Pipeline()
    app.state.runs = runs
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _invalid)
    app.state.ui_built = _ui_dir() is not None

    def job_or_404(job_id: str) -> Path:
        try:
            return state.job_dir(job_id)
        except state.NotFound:
            raise HTTPException(404, f"no job {job_id!r}") from None

    def submit(job: Path, kind: str, fn) -> dict:
        try:
            ch = runs.submit(job.name, kind, fn)
        except Busy:
            raise conflict("busy", "a run of this job is already queued or active") from None
        return {"id": job.name, "run": kind, "state": ch.state}

    def not_running(job: Path) -> None:
        if runs.busy(job.name):
            raise conflict("busy", "a run of this job is queued or active")

    def decision(fn):
        """A request that checks for runs, changes the job and may queue a run: one at a
        time, so nothing lands between the check and the queueing."""

        @functools.wraps(fn)
        def wrapper(*a, **k):
            with runs.decide:
                return fn(*a, **k)

        return wrapper

    # -------------------------------------------------------------- session, types

    @app.get("/api/session")
    def session() -> dict:
        conf = settings.load()
        installed = agent.shutil.which("claude") is not None
        how = "api_key" if conf.get("auth") == "apikey" else "subscription"
        ok = installed and (how == "subscription" or bool(_safe_api_key()))
        return {
            "version": __version__,
            "lang": conf.get("lang") or "de",
            "claude": {"ok": ok, "how": how if ok else None, "installed": installed},
            "active_run": runs.active(),
        }

    @app.get("/api/types")
    def types() -> list[dict]:
        return TYPES

    # -------------------------------------------------------------- jobs

    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        out = []
        for job in state.all_jobs():
            try:
                out.append(state.summary(job, runs.channels.get(job.name)))
            except (OSError, ValueError, ToolError):
                continue
        return sorted(out, key=lambda j: j.get("created") or "", reverse=True)

    @app.post("/api/jobs")
    async def create_job(request: Request) -> dict:
        try:
            form = await read_form(
                request,
                {"camera": VIDEO_EXT, "screen": VIDEO_EXT, "assets": ASSET_EXT},
                {"type", "brand", "format", "brief"},
            )
        except UploadError as e:
            raise HTTPException(e.status, str(e)) from None
        try:
            return await asyncio.to_thread(_new_job, form)
        finally:
            shutil.rmtree(form.folder, ignore_errors=True)

    @app.get("/api/jobs/{job_id}")
    def show_job(job_id: str) -> dict:
        job = job_or_404(job_id)
        return state.detail(job, runs.channels.get(job.name))

    @app.delete("/api/jobs/{job_id}")
    @decision
    def delete_job(job_id: str) -> dict:
        job = job_or_404(job_id)
        not_running(job)
        trash = home() / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        dst = trash / f"{job.name}-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.move(str(job), str(dst))
        runs.channels.pop(job.name, None)
        return {"id": job.name, "trash": str(dst)}

    @app.post("/api/jobs/{job_id}/start")
    @decision
    def start(job_id: str) -> dict:
        job = job_or_404(job_id)
        return submit(job, "start", lambda say, ask: run_with.start(job, say, ask))

    # -------------------------------------------------------------- events

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request, follow: bool = True):
        job = job_or_404(job_id)
        ch = runs.channel(job.name)

        def replay() -> tuple[list[dict], int]:
            evs, seq = ch.since(0)
            if ch.state == "idle" and not evs:
                data = json.loads((job / "job.json").read_text())
                _, stage = state.disk_stage(job, data)
                evs = [{"t": "stage", "stage": stage}]
                g = state.gate(job, data)
                if g:
                    evs.append({"t": "gate", "gate": g})
            return evs, seq

        async def stream():
            evs, seq = replay()
            yield "retry: 3000\n\n"
            for ev in evs:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if not follow:
                return
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    return
                new, seq = ch.since(seq)
                for ev in new:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if new:
                    idle = 0.0
                else:
                    idle += 0.25
                    if idle >= 15:
                        idle = 0.0
                        yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # -------------------------------------------------------------- plan

    @app.get("/api/jobs/{job_id}/plan")
    def get_plan(job_id: str) -> dict:
        job = job_or_404(job_id)
        project = job / "work" / "project.md"
        if not project.is_file():
            raise HTTPException(404, "no plan yet")
        return {
            "markdown": project.read_text(errors="replace"),
            "plan": state.plan_json(job),
            "sha": project_sha(job),
            "approved": is_approved(job),
        }

    @app.put("/api/jobs/{job_id}/plan")
    @decision
    def put_plan(job_id: str, body: PlanEdit) -> dict:
        job = job_or_404(job_id)
        not_running(job)
        project = job / "work" / "project.md"
        if not project.is_file():
            raise HTTPException(404, "no plan yet")
        if body.sha != project_sha(job):
            raise conflict("stale", "the plan changed since you loaded it; reload it")
        tmp = project.with_name(project.name + ".tmp")
        tmp.write_text(body.markdown)
        tmp.replace(project)  # an earlier approval no longer matches (sha-bound)
        return get_plan(job_id)

    @app.post("/api/jobs/{job_id}/plan/feedback")
    @decision
    def plan_feedback(job_id: str, body: TextBody) -> dict:
        job = job_or_404(job_id)
        if not (job / "work" / "project.md").is_file():
            raise conflict("state", "there is no plan to revise yet; start the job first")
        not_running(job)
        if read_approval(job):
            revoke_approval(job)  # the plan will change; approve the new one
        text = body.text.strip()
        return submit(
            job, "plan_feedback", lambda say, ask: run_with.plan_feedback(job, text, say, ask)
        )

    # -------------------------------------------------------------- approve, feedback

    @app.post("/api/jobs/{job_id}/approve")
    @decision
    def approve(job_id: str, body: ApproveBody) -> dict:
        job = job_or_404(job_id)
        not_running(job)
        sha = project_sha(job)
        if sha is None:
            raise conflict("state", "there is no plan to approve yet")
        if body.plan_sha != sha:
            raise conflict("stale", "the plan changed since you read it; read it again")
        try:
            cands = red.load(job) or []
        except red.RedactionError as e:
            raise conflict("state", f"cannot approve: {e}") from None
        ids: list[int] | None = None
        if cands:
            r = body.redaction
            if r is None:
                raise HTTPException(
                    400, "the plan found personal data: decide what is demo data first"
                )
            ids = [] if r.mode == "demo" else None if r.mode == "redact_all" else r.ids
            known = {c["id"] for c in cands}
            if ids is not None and not set(ids) <= known:
                raise HTTPException(
                    400, f"unknown candidate number(s); the list has {sorted(known)}"
                )
        try:
            approve_job(job)  # same record as `videocut tool job approve`
            if cands:
                red.decide(job, ids, "app")  # same store as `videocut tool job redaction`
        except (ToolError, red.RedactionError) as e:
            raise conflict("state", str(e)) from None
        out = submit(job, "approve", lambda say, ask: run_with.cont(job, say, ask))
        return {**out, "approved": True, "redaction": state.redaction_view(job)["decision"]}

    @app.post("/api/jobs/{job_id}/feedback")
    @decision
    def feedback(job_id: str, body: FeedbackBody) -> dict:
        job = job_or_404(job_id)
        if not is_approved(job) or not (job / "out" / "reel.mp4").is_file():
            raise conflict("state", "feedback needs an approved plan and a finished reel")
        text, at_s = body.text.strip(), body.at_s
        return submit(
            job, "feedback", lambda say, ask: run_with.feedback(job, text, at_s, say, ask)
        )

    # -------------------------------------------------------------- files

    @app.get("/api/jobs/{job_id}/stills/{name:path}")
    def still(job_id: str, name: str) -> FileResponse:
        job = job_or_404(job_id)
        try:
            p = state.still_path(job, name)
        except state.NotFound:
            raise HTTPException(404, "no such still") from None
        return FileResponse(p, media_type=state.STILL_EXT[p.suffix.lower()])

    @app.get("/api/jobs/{job_id}/media/{name:path}")
    def media(job_id: str, name: str) -> FileResponse:
        job = job_or_404(job_id)
        try:
            p = state.media_path(job, name)
        except state.NotFound:
            raise HTTPException(404, "no such media") from None
        ctype = "image/jpeg" if p.suffix == ".jpg" else "video/mp4"
        return FileResponse(p, media_type=ctype, headers={"Cache-Control": "no-cache"})

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str) -> FileResponse:
        job = job_or_404(job_id)
        try:
            p = state.media_path(job, "reel")
        except state.NotFound:
            raise HTTPException(404, "the reel is not rendered yet") from None
        data = json.loads((job / "job.json").read_text())
        _, fmt = state._format(data)
        name = re.sub(r"[^A-Za-z0-9]+", "-", state.title(job, data)).strip("-")[:60] or job.name
        filename = f"{name}-{(fmt or 'video').replace(':', 'x')}.mp4"
        return FileResponse(p, media_type="video/mp4", filename=filename)

    app.include_router(brands.router)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def api_404(rest: str):
        raise HTTPException(404, "not found")

    # -------------------------------------------------------------- UI

    ui = _ui_dir()
    if ui is not None:
        app.mount("/", StaticFiles(directory=ui, html=True), name="ui")
    else:

        @app.get("/{rest:path}", include_in_schema=False)
        def not_built(rest: str) -> HTMLResponse:
            if rest == "api" or rest.startswith("api/"):
                raise HTTPException(404, "not found")
            page = resources.files("videocut").joinpath(FALLBACK_PAGE).read_text()
            return HTMLResponse(page, status_code=503)

    app.add_middleware(Guard, token=token, port=port, hosts=hosts)
    return app


def _safe_api_key() -> str | None:
    try:
        return settings.api_key()
    except Exception:  # noqa: BLE001 — no keychain (CI, Linux): no key
        return None


def _new_job(form) -> dict:
    """Check the staged uploads and create the job through `videocut tool job new`."""
    f = form.fields
    camera = form.file("camera")
    if camera is None:
        raise HTTPException(400, "the camera recording (field camera) is missing")
    if len(form.files.get("camera", [])) > 1 or len(form.files.get("screen", [])) > 1:
        raise HTTPException(400, "one camera recording and at most one screen recording")
    type_ = (f.get("type") or "").strip() or None
    if type_ is not None and not pipeline.TYPE_RE.fullmatch(type_):
        raise HTTPException(400, f"invalid video type {type_!r}")
    fmt = (f.get("format") or "auto").strip()
    if fmt not in FORMATS:
        raise HTTPException(400, f"format must be one of {', '.join(FORMATS)}")
    brief = " ".join((f.get("brief") or "").split())[:2000] or None
    brand_ref = (f.get("brand") or "").strip() or None
    try:
        brand_ref = brand_id(brand_ref) if brand_ref else None
    except ToolError as e:
        raise HTTPException(400, str(e)) from None
    screen = form.file("screen")
    assets = form.files.get("assets", [])
    try:
        check_video(camera, need_audio=True)
        if screen is not None:
            check_video(screen, need_audio=False)
        for a in assets:
            check_image(a)
    except UploadError as e:
        raise HTTPException(e.status, str(e)) from None
    try:
        job = pipeline.new_job(
            camera,
            type_,
            brief,
            assets=[str(a) for a in assets] or None,
            screen=str(screen) if screen else None,
            format_=None if fmt == "auto" else fmt,
        )
    except pipeline.RunError as e:
        raise HTTPException(400, str(e)) from None
    if brand_ref:
        update_job(job, brand=brand_ref)
    return {"id": job.name}
