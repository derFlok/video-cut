"""Material in the app: the settings (default mode, image generator, stock sources, cost
limit), the providers' API keys (write-only, macOS keychain), and per job the Material
screen (tools/material.py): the wishes with their candidates, the human's choice, and a
host-side re-fetch of one wish.

    GET/PUT /api/settings/material      the settings view (+ which keys are stored)
    GET/PUT /api/settings/secrets       {account, value}: store ("" removes); never read back
    GET  /api/jobs/{id}/material        wishes, candidates (thumb_url, file_url), choices
    POST /api/jobs/{id}/material/fetch  {wish?, query?, prompt?, generate?}: a queued run
    POST /api/jobs/{id}/material/choose {n, candidate | null}: the human's pick
    GET  /api/jobs/{id}/material/files/<n>/<name>   a candidate file or thumb (Range)

Keys never appear in a response, a log line or a job folder."""

import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import settings
from ..tools import _job_material
from ..tools import material as mat
from ..tools._material_providers import providers
from ..tools.common import ToolError, read_job

ACCOUNTS = tuple(a for a in settings.SECRET_ACCOUNTS if a != settings.KEYCHAIN_ACCOUNT)
IMAGE_PROVIDERS = ("none", "gemini", "openai", "local")
STOCK = mat.STOCK_PROVIDERS
BUDGET_MAX = 50.0
FILE_RE = re.compile(r"c\d{1,3}(_sm)?\.(mp4|jpg|jpeg|png|webp)")
MEDIA = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp"}  # fmt: skip


class MaterialSettings(BaseModel):
    material_default_mode: str | None = None
    image_provider: str | None = None
    stock_providers: list[str] | None = None
    material_budget_usd: float | None = None


class SecretBody(BaseModel):
    account: str = Field(max_length=40)
    value: str = Field(default="", max_length=400)


class FetchBody(BaseModel):
    wish: int | None = Field(default=None, ge=1, le=999)
    query: str | None = Field(default=None, max_length=200)
    prompt: str | None = Field(default=None, max_length=mat.PROMPT_MAX)
    generate: bool = False


class ChooseBody(BaseModel):
    n: int = Field(ge=1, le=999)
    candidate: str | None = Field(default=None, max_length=8)


def secrets_view() -> dict:
    out = {}
    for a in ACCOUNTS:
        try:
            out[a] = settings.has_secret(a)
        except (OSError, ValueError):
            out[a] = False
    return out


def image_providers() -> list[dict]:
    have = providers("image")
    out = []
    for name in IMAGE_PROVIDERS[1:]:
        cls = have.get(name)
        if cls is None:
            out.append({"name": name, "registered": False, "available": False,
                        "reason": "not installed"})  # fmt: skip
            continue
        try:
            ok, why = cls().available()
        except Exception as e:  # noqa: BLE001 — one broken provider must not hide the rest
            ok, why = False, str(e)[:200]
        out.append({"name": name, "registered": True, "available": ok, "reason": why})
    return out


def settings_view() -> dict:
    conf = settings.load()
    return {
        "material_default_mode": conf.get("material_default_mode")
        if conf.get("material_default_mode") in _job_material.MODES else "none",
        "image_provider": conf.get("image_provider")
        if conf.get("image_provider") in IMAGE_PROVIDERS else "none",
        "stock_providers": [s for s in conf.get("stock_providers") or [] if s in STOCK],
        "material_budget_usd": mat.budget(),
        "image_providers": image_providers(),
        "secrets": secrets_view(),
    }  # fmt: skip


def put_settings(body: MaterialSettings) -> dict:
    ch = body.model_dump(exclude_unset=True, exclude_none=True)
    if "material_default_mode" in ch and ch["material_default_mode"] not in _job_material.MODES:
        raise HTTPException(400, f"material_default_mode: one of {', '.join(_job_material.MODES)}")
    if "image_provider" in ch and ch["image_provider"] not in IMAGE_PROVIDERS:
        raise HTTPException(400, f"image_provider: one of {', '.join(IMAGE_PROVIDERS)}")
    if "stock_providers" in ch:
        bad = [s for s in ch["stock_providers"] if s not in STOCK]
        if bad:
            raise HTTPException(400, f"stock_providers: only {', '.join(STOCK)}")
        ch["stock_providers"] = [s for s in STOCK if s in ch["stock_providers"]]
    if "material_budget_usd" in ch and not 0 <= ch["material_budget_usd"] <= BUDGET_MAX:
        raise HTTPException(400, f"material_budget_usd: 0 to {BUDGET_MAX:g}")
    settings.save(**ch)
    return settings_view()


def put_secret(body: SecretBody) -> dict:
    if body.account not in ACCOUNTS:
        raise HTTPException(400, f"account: one of {', '.join(ACCOUNTS)}")
    try:
        settings.set_secret(body.account, body.value.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from None
    return {"secrets": secrets_view()}


def _url(job: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    parts = rel.split("/")
    if len(parts) != 4 or "/".join(parts[:2]) != mat.DIR:
        return None
    return f"/api/jobs/{job.name}/material/files/{parts[2]}/{parts[3]}"


CAND_KEYS = ("id", "kind", "width", "height", "duration_s", "source", "source_url", "author",
             "licence", "licence_url", "attribution", "ai_generated", "model",
             "watermark")  # fmt: skip
WISH_KEYS = ("n", "kind", "src_from", "src_to", "query", "prompt", "style", "why", "decided",
             "chosen")  # fmt: skip


def job_view(job: Path) -> dict:
    data = read_job(job)
    m = _job_material.stored(data)
    wishes = mat.synced(job, write=False)["wishes"] if mat.approved_wishes(job) else []
    out = []
    for w in wishes:
        cands = [
            {**{k: c.get(k) for k in CAND_KEYS}, "thumb_url": _url(job, c.get("thumb")),
             "file_url": _url(job, c.get("file"))}
            for c in w.get("candidates", [])
        ]  # fmt: skip
        out.append({**{k: w.get(k) for k in WISH_KEYS}, "candidates": cands})
    return {
        "mode": m["mode"],
        "wish": m["wish"],
        "image_provider": mat.provider_name(),
        "budget_usd": mat.budget(),
        "cost_usd": mat.load(job).get("cost_usd", 0),
        "wishes": out,
        "pending": mat.pending(job),
        "fetched": any(w.get("candidates") for w in wishes),
    }


def summary(job: Path, data: dict) -> dict:
    """For the job detail: the settings and where the choice stands."""
    m = _job_material.view(job, data)
    try:
        wishes = mat.synced(job, write=False)["wishes"] if mat.approved_wishes(job) else []
        left = mat.pending(job)
    except (ToolError, OSError, ValueError):
        wishes, left = [], []
    return {**m, "wishes": len(wishes), "pending": left,
            "fetched": any(w.get("candidates") for w in wishes)}  # fmt: skip


def waiting(job: Path) -> bool:
    """An approved plan with material wishes the human has not all decided."""
    try:
        return bool(mat.approved_wishes(job)) and bool(mat.pending(job))
    except (ToolError, OSError, ValueError):
        return False


def file_path(job: Path, n: str, name: str) -> Path:
    if not (n.isdigit() and FILE_RE.fullmatch(name)):
        raise HTTPException(404, "no such file")
    root = (job / mat.DIR).resolve()
    p = (root / n / name).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise HTTPException(404, "no such file")
    return p


def fetch_run(job: Path, body: FetchBody):
    """The run function of POST material/fetch (host side, never an agent run)."""

    def go(say, ask) -> int:
        stock = not body.generate
        mat.fetch(job, body.wish, None if body.generate else body.query, body.prompt,
                  stock=stock, gen=body.generate or body.wish is None, say=say)  # fmt: skip
        return 5  # the material gate: the human chooses next

    return go


def register(app, job_or_404, submit, not_running, decision) -> None:
    app.get("/api/settings/material")(settings_view)
    app.put("/api/settings/material")(put_settings)
    app.get("/api/settings/secrets")(lambda: {"secrets": secrets_view()})
    app.put("/api/settings/secrets")(put_secret)

    @app.get("/api/jobs/{job_id}/material")
    def get_material(job_id: str) -> dict:
        return job_view(job_or_404(job_id))

    @app.post("/api/jobs/{job_id}/material/fetch")
    @decision
    def post_fetch(job_id: str, body: FetchBody) -> dict:
        job = job_or_404(job_id)
        not_running(job)
        if not mat.approved_wishes(job):
            raise HTTPException(409, {"message": "the approved plan has no material wishes",
                                      "code": "state"})  # fmt: skip
        if body.wish is not None and body.wish not in {w["n"] for w in mat.approved_wishes(job)}:
            raise HTTPException(400, f"no material wish {body.wish}")
        return submit(job, "material", fetch_run(job, body))

    @app.post("/api/jobs/{job_id}/material/choose")
    @decision
    def post_choose(job_id: str, body: ChooseBody) -> dict:
        job = job_or_404(job_id)
        not_running(job)
        try:
            mat.choose(job, body.n, body.candidate, say=lambda _: None)
        except ToolError as e:
            raise HTTPException(400, str(e)) from None
        return job_view(job)

    @app.get("/api/jobs/{job_id}/material/files/{n}/{name}")
    def material_file(job_id: str, n: str, name: str) -> FileResponse:
        p = file_path(job_or_404(job_id), n, name)
        return FileResponse(p, media_type=MEDIA[p.suffix.lower()],
                            headers={"Cache-Control": "no-cache"})  # fmt: skip
