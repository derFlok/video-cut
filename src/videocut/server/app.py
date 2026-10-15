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
from ..tools import _format, _job_slides, _length, _redact_style, _style, transcript, vocab
from ..tools.common import ToolError, home, update_job
from ..tools.job import (
    LANGS,
    TITLE_MAX,
    approve_job,
    approved_plan_json,
    brand_id,
    duplicate_job,
    is_approved,
    project_sha,
    read_approval,
    revoke_approval,
    set_cut_style,
    set_redact_style,
    set_target,
    set_title,
)
from . import brands, state
from .runs import Busy, Runs
from .security import Guard
from .uploads import (
    ASSET_EXT,
    MAX_SLIDE_BYTES,
    MAX_SLIDE_FILES,
    SLIDE_EXT,
    VIDEO_EXT,
    UploadError,
    check_image,
    check_slide,
    check_video,
    read_form,
)

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
CUT_STYLES = (_style.AUTO, *_style.STYLES)
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

    def plan_feedback(self, job: Path, text: str, say, ask, slide: str | None = None) -> int:
        """`videocut run <job> --plan-feedback <text>`: the plan run revises the plan
        (`slide`: the id of the plan's slide the feedback is about)."""
        return pipeline.run(
            str(job), replan=True, plan_feedback=text, plan_feedback_slide=slide, say=say,
            ask=ask,
        )  # fmt: skip

    def feedback(self, job: Path, text: str, at_s: float | None, say, ask) -> int:
        """`videocut run <job> --feedback <text> [--at <output_s>]`: run.revise()."""
        return pipeline.run(str(job), feedback=text, at_s=at_s, say=say, ask=ask)

    def recut(self, job: Path, say, ask) -> int:
        """`videocut run <job> --recut`: cut + overlays again with the approved plan."""
        return pipeline.recut(str(job), say=say, ask=ask)

    def overlays(self, job: Path, say, ask) -> int:
        """`videocut run <job>` after the overlays went stale (a vocabulary applied to the
        captions): the cut is kept, the overlay stage runs again."""
        return pipeline.run(str(job), say=say, ask=ask)

    def captions(self, job: Path, say, ask) -> int:
        """The captions with the human's transcript edits: tool steps only (captions,
        overlays, render, verify), no Claude run (tools/transcript.refresh)."""
        return transcript.refresh(job, say=say)


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


class PlanFeedbackBody(TextBody):
    slide: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


class FeedbackBody(TextBody):
    at_s: float | None = Field(default=None, ge=0, le=36_000)


class Redaction(BaseModel):
    mode: str = Field(pattern="^(demo|redact_all|redact)$")
    ids: list[int] = Field(default_factory=list, max_length=1000)


class CutStyleBody(BaseModel):
    cut_style: str = Field(pattern="^(" + "|".join(CUT_STYLES) + ")$")


class RedactStyleBody(BaseModel):
    redact_style: str = Field(pattern="^(" + "|".join(_redact_style.STYLES) + ")$")


class TargetBody(BaseModel):
    """Seconds, "auto" or null (the default rule); strings like "5m" or "2:30" too."""

    target_s: float | str | None = Field(default=None)


class DuplicateBody(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    keep_approval: bool = False


class TitleBody(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)


class VocabularyBody(BaseModel):
    """The job's own vocabulary (tools/vocab.py), validated by vocab.check."""

    vocabulary: list = Field(default_factory=list, max_length=vocab.MAX_ENTRIES)


class TranscriptEdit(BaseModel):
    """One word's new text (tools/transcript.py); "" hides the word from the captions.
    `t0` (the word's source start as read) guards against a transcript changed since."""

    i: int = Field(ge=0, le=1_000_000)
    text: str = Field(max_length=transcript.MAX_TEXT)
    t0: float | None = Field(default=None, ge=0, le=360_000)


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
                {
                    "camera": VIDEO_EXT,
                    "screen": VIDEO_EXT,
                    "assets": ASSET_EXT,
                    "slides": SLIDE_EXT,
                },  # fmt: skip
                {
                    "type",
                    "brand",
                    "format",
                    "brief",
                    "cut_style",
                    "lang",
                    "target_s",
                    "vocabulary",
                    "redact_style",
                    "slides_mode",
                    "slides_wish",
                },  # fmt: skip
                field_caps={"slides": (MAX_SLIDE_FILES, MAX_SLIDE_BYTES)},
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

    @app.post("/api/jobs/{job_id}/duplicate")
    @decision
    def duplicate(job_id: str, body: DuplicateBody) -> dict:
        """Copy the job to <id>-copy-N. The approval and the personal-data decision move
        along only with keep_approval (the human ticks it); else the copy needs its own."""
        job = job_or_404(job_id)
        not_running(job)
        try:
            res = duplicate_job(job, body.title, body.keep_approval)
        except ToolError as e:
            raise HTTPException(400, str(e)) from None
        copy = state.job_dir(res["id"])
        return state.summary(copy, runs.channels.get(copy.name))

    @app.put("/api/jobs/{job_id}/title")
    @decision
    def put_title(job_id: str, body: TitleBody) -> dict:
        """The display title (job.json "title"); it overrides the plan's title."""
        job = job_or_404(job_id)
        not_running(job)
        try:
            set_title(job, body.title)
        except ToolError as e:
            raise HTTPException(400, str(e)) from None
        return {
            "id": job.name,
            "title": state.title(job, json.loads((job / "job.json").read_text())),
        }

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
        # After approval only the plan.json the human saw is shown (an agent run may write
        # work/); a changed one falls back to the approved markdown.
        plan = state.plan_json(job)
        if plan is not None and is_approved(job) and not approved_plan_json(job):
            plan = None
        if plan is not None:
            plan = state.with_slide_stills(job, plan)
        return {
            "markdown": project.read_text(errors="replace"),
            "plan": plan,
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
        # The structured twin no longer says what the edited markdown says: drop it, the app
        # shows the markdown (the human's text is the plan now).
        (job / "work" / "plan.json").unlink(missing_ok=True)
        return get_plan(job_id)

    @app.post("/api/jobs/{job_id}/plan/feedback")
    @decision
    def plan_feedback(job_id: str, body: PlanFeedbackBody) -> dict:
        job = job_or_404(job_id)
        if not (job / "work" / "project.md").is_file():
            raise conflict("state", "there is no plan to revise yet; start the job first")
        slide = body.slide
        if slide is not None:
            ids = [s.get("id") for s in (state.plan_json(job) or {}).get("slides") or []
                   if isinstance(s, dict)]  # fmt: skip
            if slide not in ids:
                raise HTTPException(400, f"the plan has no slide {slide!r}")
        not_running(job)
        if read_approval(job):
            revoke_approval(job)  # the plan will change; approve the new one
        text = body.text.strip()

        def go(say, ask):
            if slide is None:
                return run_with.plan_feedback(job, text, say, ask)
            return run_with.plan_feedback(job, text, say, ask, slide=slide)

        return submit(job, "plan_feedback", go)

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
            if ids is not None and r.mode == "redact" and not ids:
                raise HTTPException(400, 'mode "redact" needs the ids to redact (or "demo")')
            if ids is not None and len(set(ids)) != len(ids):
                raise HTTPException(400, "duplicate candidate numbers")
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
        if not (is_approved(job) and pipeline.cut_done(job)):
            raise conflict("state", "feedback needs an approved plan and a finished reel")
        text, at_s = body.text.strip(), body.at_s
        return submit(
            job, "feedback", lambda say, ask: run_with.feedback(job, text, at_s, say, ask)
        )

    # -------------------------------------------------------------- cut style, recut

    @app.put("/api/jobs/{job_id}/cut-style")
    @decision
    def put_cut_style(job_id: str, body: CutStyleBody) -> dict:
        """Store the cut style; a finished cut of another style goes stale (cut_stale), the
        app then offers /recut. The approval stays (the plan did not change)."""
        job = job_or_404(job_id)
        not_running(job)
        try:
            res = set_cut_style(job, body.cut_style, say=lambda _: None)
        except ToolError as e:
            raise HTTPException(400, str(e)) from None
        data = json.loads((job / "job.json").read_text())
        return {
            "id": job.name,
            "cut_style": data.get("cut_style") or _style.AUTO,
            "cut_style_resolved": res["cut_style"],
            "previous": res["previous"],
            "cut_stale": state.cut_stale(job),
        }

    # -------------------------------------------------------------- redaction style

    @app.put("/api/jobs/{job_id}/redact-style")
    @decision
    def put_redact_style(job_id: str, body: RedactStyleBody) -> dict:
        """Store how personal data is hidden; a reel that hid it in another style goes
        stale (cut_stale), the app then offers /recut. Plan and approval stay."""
        job = job_or_404(job_id)
        not_running(job)
        try:
            res = set_redact_style(job, body.redact_style, say=lambda _: None)
        except ToolError as e:
            raise HTTPException(400, str(e)) from None
        return {"id": job.name, "redact_style": res["redact_style"],
                "previous": res["previous"], "cut_stale": state.cut_stale(job)}  # fmt: skip

    # -------------------------------------------------------------- target length

    @app.put("/api/jobs/{job_id}/target")
    @decision
    def put_target(job_id: str, body: TargetBody) -> dict:
        """Store the target length. A plan made for another length goes stale and an
        approval is revoked (the plan's content depends on it, unlike the cut style): the
        app then offers to plan again (POST plan/feedback, or /start for a fresh plan)."""
        job = job_or_404(job_id)
        not_running(job)
        value = _length.AUTO if body.target_s is None else body.target_s
        try:
            res = set_target(job, value, say=lambda _: None)
        except ToolError as e:
            raise HTTPException(400, str(e)) from None
        return {
            "id": job.name,
            "target_s": res["target_s"],
            "target_resolved": res["target_range"],
            "previous": res["previous"],
            "plan_stale": res["plan_stale"],
            "approval_revoked": res["approval_revoked"],
            "reel_stale": res["reel_stale"],
            "approved": is_approved(job),
        }

    @app.post("/api/jobs/{job_id}/recut")
    @decision
    def recut(job_id: str) -> dict:
        """Cut again with the approved plan (cut + overlays), no new plan approval."""
        job = job_or_404(job_id)
        if not is_approved(job):
            raise conflict("state", "cutting again needs an approved plan")
        if not (job / "out" / "reel.mp4").is_file():
            raise conflict("state", "there is no cut yet; start the job")
        return submit(job, "recut", lambda say, ask: run_with.recut(job, say, ask))

    # -------------------------------------------------------------- vocabulary

    def vocabulary_view(job: Path) -> dict:
        src = vocab.sources(job)
        meta = state.transcribe_meta(job)
        words = state.words(job)
        return {
            "id": job.name,
            "vocabulary": src["job"],
            "brand": src["brand_id"],
            "brand_vocabulary": src["brand"],
            "effective": src["effective"],
            "applied": meta.get("vocabulary"),
            "has_transcript": words is not None,
            "suggestions": vocab.suggestions(words, src["effective"]) if words else [],
            "overlays_stale": state.overlays_stale(job),
        }

    @app.get("/api/jobs/{job_id}/vocabulary")
    def get_vocabulary(job_id: str) -> dict:
        return vocabulary_view(job_or_404(job_id))

    @app.put("/api/jobs/{job_id}/vocabulary")
    @decision
    def put_vocabulary(job_id: str, body: VocabularyBody) -> dict:
        """Store the job's own vocabulary; it takes effect on the next transcription or
        /vocabulary/apply."""
        job = job_or_404(job_id)
        not_running(job)
        entries, errors = vocab.check(body.vocabulary)
        if errors:
            raise HTTPException(422, {"message": "the vocabulary has problems", "errors": errors})
        update_job(job, vocabulary=entries)
        return vocabulary_view(job)

    @app.post("/api/jobs/{job_id}/vocabulary/apply")
    @decision
    def apply_vocabulary(job_id: str) -> dict:
        """Apply the effective vocabulary to the existing transcript (and captions); when
        the captions changed the overlays are stale and the app offers /overlays."""
        job = job_or_404(job_id)
        not_running(job)
        try:
            res = vocab.apply_job(job)
        except ToolError as e:
            raise conflict("state", str(e)) from None
        return {**vocabulary_view(job), "result": res}

    @app.post("/api/jobs/{job_id}/overlays")
    @decision
    def redo_overlays(job_id: str) -> dict:
        """Set the captions and overlays again on the kept cut (after a vocabulary was
        applied to the captions); no new plan approval, no new cut."""
        job = job_or_404(job_id)
        if not state.overlays_stale(job):
            raise conflict("state", "the overlays of this job are not stale")
        return submit(job, "overlays", lambda say, ask: run_with.overlays(job, say, ask))

    # -------------------------------------------------------------- transcript

    def transcript_view(job: Path, offset: int = 0, limit: int | None = None) -> dict:
        words = state.words(job)
        if words is None:
            raise conflict("state", "the job has no transcript yet")
        reel = state.overlays_stale(job) or pipeline.cut_done(job)
        edl = state._json(job / "work" / "edl.json") if reel else None
        sel = words[offset : None if limit is None else offset + limit]
        times = transcript.output_times(sel, edl)
        rows = []
        for k, (w, tt) in enumerate(zip(sel, times, strict=True)):
            row = {"i": offset + k, "w": w["w"], "t0": w["t0"], "t1": w["t1"],
                   "out_t0": tt[0] if tt else None, "out_t1": tt[1] if tt else None}  # fmt: skip
            if "edit" in w:
                row["edited"] = True
                row["asr"] = transcript.asr(w)
            if w.get("hidden"):
                row["hidden"] = True
            rows.append(row)
        log = transcript.read_log(job)
        return {
            "id": job.name,
            "total": len(words),
            "offset": offset,
            "words": rows,
            "has_reel": reel,
            "edits": sum("edit" in w for w in words),
            "can_undo": bool(transcript.replay(log)[1]),
            "edits_at": log[-1].get("at") if log else None,
            "drift": transcript.drift(job, words),
            "captions_stale": state.captions_stale(job),
            "overlays_stale": state.overlays_stale(job),
        }

    @app.get("/api/jobs/{job_id}/transcript")
    def get_transcript(job_id: str, offset: int = 0, limit: int | None = None) -> dict:
        """The words with their source time and, when the finished reel keeps them, their
        time in the reel (output seconds); `offset`/`limit` page through them."""
        if offset < 0 or (limit is not None and not 1 <= limit <= 100_000):
            raise HTTPException(422, "offset >= 0, 1 <= limit <= 100000")
        return transcript_view(job_or_404(job_id), offset, limit)

    @app.patch("/api/jobs/{job_id}/transcript")
    @decision
    def patch_transcript(job_id: str, body: TranscriptEdit) -> dict:
        """The human's text for one word; the reel's captions go stale until
        /captions/refresh."""
        job = job_or_404(job_id)
        not_running(job)
        try:
            text = transcript.clean_text(body.text)
            res = transcript.edit(job, body.i, text, body.t0)
        except ToolError as e:
            if "changed since" in str(e):
                raise conflict("stale", str(e)) from None
            raise HTTPException(422, {"message": str(e)}) from None
        return {**transcript_view(job), "word": {"i": res["i"], "changed": res["changed"]}}

    @app.post("/api/jobs/{job_id}/transcript/undo")
    @decision
    def undo_transcript(job_id: str) -> dict:
        job = job_or_404(job_id)
        not_running(job)
        try:
            res = transcript.undo(job)
        except ToolError as e:
            raise conflict("state", str(e)) from None
        return {**transcript_view(job), "undone": {"i": res["i"]}}

    @app.post("/api/jobs/{job_id}/captions/refresh")
    @decision
    def refresh_captions(job_id: str) -> dict:
        """Set the captions again with the edited transcript: a run of tool steps (no
        Claude), its stages and log over /events like any run."""
        job = job_or_404(job_id)
        if not state.overlays_stale(job):
            raise conflict("state", "the captions of this job are up to date")
        return submit(job, "captions", lambda say, ask: run_with.captions(job, say, ask))

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
        # letters of any script stay (the header carries them as RFC 5987 filename*)
        name = re.sub(r"[\W_]+", "-", state.title(job, data)).strip("-")[:60] or job.name
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
    cut_style = (f.get("cut_style") or _style.AUTO).strip().lower()
    if cut_style not in CUT_STYLES:
        raise HTTPException(400, f"cut_style must be one of {', '.join(CUT_STYLES)}")
    if (f.get("redact_style") or _redact_style.AUTO).strip().lower() not in _redact_style.STYLES:
        raise HTTPException(400, f"redact_style must be one of {', '.join(_redact_style.STYLES)}")
    try:
        target = _length.parse_target(f.get("target_s"))
    except _length.LengthError as e:
        raise HTTPException(400, f"target_s: {e}") from None
    ui_lang = str(settings.load().get("lang") or "").lower()
    lang = (f.get("lang") or (ui_lang if ui_lang in LANGS else "de")).strip().lower()
    if lang not in LANGS:
        raise HTTPException(400, f"lang must be one of {', '.join(LANGS)}")
    brief = " ".join((f.get("brief") or "").split())[:2000] or None
    try:
        raw = json.loads(f.get("vocabulary") or "[]")
    except ValueError:
        raise HTTPException(400, "vocabulary: expected a JSON list of {term, variants}") from None
    words, errors = vocab.check(raw)
    if errors:
        raise HTTPException(422, {"message": "the vocabulary has problems", "errors": errors})
    brand_ref = (f.get("brand") or "").strip() or None
    try:
        brand_ref = brand_id(brand_ref) if brand_ref else None
    except ToolError as e:
        raise HTTPException(400, str(e)) from None
    screen = form.file("screen")
    assets = form.files.get("assets", [])
    slide_files = form.files.get("slides", [])
    try:
        slides_mode = _job_slides.parse_mode(
            f.get("slides_mode") or ("upload" if slide_files else "none")
        )
        slides_wish = _job_slides.parse_wish(f.get("slides_wish"))
    except ToolError as e:
        raise HTTPException(400, str(e)) from None
    if slide_files and slides_mode in ("none", "auto"):
        raise HTTPException(400, f"slide files need slides_mode upload or both, not {slides_mode}")
    try:
        check_video(camera, need_audio=True)
        if screen is not None:
            check_video(screen, need_audio=False)
        for a in assets:
            check_image(a)
        for sl in slide_files:
            check_slide(sl)
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
            cut_style=None if cut_style == _style.AUTO else cut_style,
            lang=lang,
            target=None if target is None else str(target),
        )
    except pipeline.RunError as e:
        raise HTTPException(400, str(e)) from None
    try:
        _new_job_settings(job, brand_ref, words, f, slide_files, slides_mode, slides_wish)
    except HTTPException:
        shutil.rmtree(job, ignore_errors=True)  # no half-made job is left behind
        raise
    except BaseException as e:
        shutil.rmtree(job, ignore_errors=True)
        if isinstance(e, OSError):
            raise HTTPException(500, f"could not finish the new job: {e}") from None
        raise
    return {"id": job.name}


def _new_job_settings(job, brand_ref, words, f, slide_files, slides_mode, slides_wish) -> None:
    """Everything set on a new job after `job new`; the caller removes the job on failure."""
    fields = {}
    if brand_ref:
        fields["brand"] = brand_ref
    if words:
        fields["vocabulary"] = words
    if fields:
        update_job(job, **fields)
    _new_job_redact_style(job, f.get("redact_style"))
    try:
        if slide_files:  # rasterised on this machine into input/slides/ (untrusted files)
            _job_slides.import_slides(job, slide_files, say=lambda _: None)
        if slides_mode != "none" or slides_wish:
            _job_slides.set_slides(job, slides_mode, slides_wish, say=lambda _: None)
    except ToolError as e:  # a broken PDF (the caller removes the job)
        raise HTTPException(415 if slide_files else 400, f"slides: {e}") from None


def _new_job_redact_style(job, value) -> None:
    """The redaction style picked in the new-job form; "auto" or none: the brand's default
    (not stored). An unknown value was refused before the job was made."""
    value = (value or "").strip().lower()
    if value and value != _redact_style.AUTO:
        set_redact_style(job, value, say=lambda _: None)
