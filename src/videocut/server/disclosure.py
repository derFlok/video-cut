"""Publishing: what to disclose for a finished video, and the AI label setting.

GET /api/jobs/{id}/disclosure   work/disclosure.json written by the last full render
    (tools/_disclosure.py) plus {"ready": true}; {"ready": false} before one.
GET/PUT /api/settings/labels    {"ai_label_illustrations": bool}: whether AI-generated
    illustrations get the visible label too. Realistic AI material is always labelled
    (EU AI Act Art. 50); there is no setting for it."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import settings
from ..tools import _disclosure
from . import state

router = APIRouter(prefix="/api")


class LabelSettings(BaseModel):
    ai_label_illustrations: bool


@router.get("/jobs/{job_id}/disclosure")
def get_disclosure(job_id: str) -> dict:
    try:
        job = state.job_dir(job_id)
    except state.NotFound:
        raise HTTPException(404, f"no job {job_id!r}") from None
    doc = _disclosure.read(job)
    return {"ready": False} if doc is None else {**doc, "ready": True}


def _labels() -> dict:
    return {"ai_label_illustrations": settings.load().get("ai_label_illustrations") is not False}


@router.get("/settings/labels")
def get_labels() -> dict:
    return _labels()


@router.put("/settings/labels")
def put_labels(body: LabelSettings) -> dict:
    settings.save(ai_label_illustrations=body.ai_label_illustrations)
    return _labels()
