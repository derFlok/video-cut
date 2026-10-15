"""The vocabulary routes of the local app: brand vocabulary through GET/PUT /api/brands/{id},
the job's own terms, applying them to an existing transcript and setting the captions again."""

import json
import time

import pytest
from test_server import BASE, ORIGIN, TOKEN, FakePipeline, cut_job, make_job
from test_vocab import LUMO, ZEN, make_brand, texts, words

from videocut.server import create_app
from videocut.tools.common import jobs_root
from videocut.tools.job import approve_job


class Pipeline(FakePipeline):
    def overlays(self, job, say, ask):
        self.calls.append(("overlays", job.name))
        data = json.loads((job / "job.json").read_text())
        data["overlays"] = {**data["overlays"], "stale": None}
        (job / "job.json").write_text(json.dumps(data))
        return 0


@pytest.fixture
def fake():
    return Pipeline()


@pytest.fixture
def client(fake):
    from fastapi.testclient import TestClient

    c = TestClient(create_app(token=TOKEN, port=4300, runner=fake), base_url=BASE)
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303
    c.headers.update(ORIGIN)
    return c


def transcribed(job_id="j1", **extra):
    job = make_job(job_id, **extra)
    doc = {"model": "m", "language": "de", "duration": 9.0,
           "words": words("bei Zentrowia und Zentrowya mit Lumocloud")}  # fmt: skip
    (job / "work" / "words.json").write_text(json.dumps(doc))
    (job / "work" / "transcribe.json").write_text(json.dumps({"model": "m"}))
    return job


def test_brand_vocabulary_is_read_validated_and_written(client):
    make_brand(vocabulary=[ZEN])
    b = client.get("/api/brands/acme").json()
    assert b["vocabulary"] == [ZEN] and b["errors"] == []
    b["vocabulary"] = [ZEN, {"term": "Kalda", "variants": ["Zentrowia", "Kal.*"]}]
    r = client.put("/api/brands/acme", json=b)
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e.startswith("$.vocabulary[1].variants[0]") and "already used" in e for e in errs)
    assert any(e.startswith("$.vocabulary[1].variants[1]") and "pattern" in e for e in errs)
    b["vocabulary"] = [ZEN, LUMO]
    r = client.put("/api/brands/acme", json=b)
    assert r.status_code == 200, r.text
    assert r.json()["vocabulary"] == [ZEN, LUMO]


def test_job_vocabulary_get_put_and_apply(client):
    make_brand(vocabulary=[ZEN])
    job = transcribed(brand="acme")
    v = client.get("/api/jobs/j1/vocabulary").json()
    assert (v["vocabulary"], v["brand"], v["brand_vocabulary"]) == ([], "acme", [ZEN])
    assert v["has_transcript"] and v["applied"] is None and v["overlays_stale"] is False
    assert [(s["heard"], s["term"]) for s in v["suggestions"]] == [("Zentrowya", "Zentrovia")]

    r = client.put("/api/jobs/j1/vocabulary", json={"vocabulary": [{"term": "", "variants": []}]})
    assert r.status_code == 422 and "$.vocabulary[0].term" in r.json()["detail"]["errors"][0]
    r = client.put("/api/jobs/j1/vocabulary", json={"vocabulary": [LUMO]})
    assert r.status_code == 200 and r.json()["effective"] == [ZEN, LUMO]
    assert json.loads((job / "job.json").read_text())["vocabulary"] == [LUMO]
    # stored, not applied yet
    assert texts(json.loads((job / "work" / "words.json").read_text())["words"])[1] == "Zentrowia"

    r = client.post("/api/jobs/j1/vocabulary/apply")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["result"]["applied"] == 2 and res["result"]["caption_words"] == 0
    assert res["applied"]["terms"] == {"Lumo Cloud": 1, "Zentrovia": 1}
    ws = json.loads((job / "work" / "words.json").read_text())["words"]
    assert texts(ws) == ["bei", "Zentrovia", "und", "Zentrowya", "mit", "Lumo Cloud"]


def test_apply_needs_a_transcript_and_no_run(client, fake):
    make_job()
    r = client.post("/api/jobs/j1/vocabulary/apply")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "state"
    assert client.get("/api/jobs/j1/vocabulary").json()["has_transcript"] is False
    transcribed("j2")
    fake.gate.clear()  # the start run blocks: the job is busy
    assert client.post("/api/jobs/j2/start").status_code == 200
    r = client.put("/api/jobs/j2/vocabulary", json={"vocabulary": [ZEN]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "busy"
    fake.gate.set()


def test_applied_captions_make_the_overlays_stale_and_redo_sets_them_again(client, fake):
    make_brand(vocabulary=[ZEN])
    job = transcribed(brand="acme")
    (job / "work" / "project.md").write_text("# Plan\n")
    approve_job(job)
    cut_job(job, overlay=True)
    edl = json.loads((job / "work" / "edl.json").read_text())
    edl["captions"] = [{"t0": 0.0, "t1": 1.0, "words": [{"w": "Zentrowia", "t0": 0.0, "t1": 1.0}]}]
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    data = json.loads((job / "job.json").read_text())
    data["overlays"] = {"brand": "acme", "verify_ok": True}
    (job / "job.json").write_text(json.dumps(data))
    assert client.get("/api/jobs/j1").json()["overlays_stale"] is False
    r = client.post("/api/jobs/j1/overlays")
    assert r.status_code == 409  # nothing stale yet

    res = client.post("/api/jobs/j1/vocabulary/apply").json()
    assert res["result"]["caption_words"] == 1 and res["overlays_stale"] is True
    assert client.get("/api/jobs/j1").json()["overlays_stale"] is True
    r = client.post("/api/jobs/j1/overlays")
    assert r.status_code == 200 and r.json()["run"] == "overlays"
    client.get("/api/jobs/j1/events?follow=0")
    for _ in range(100):
        done = ("overlays", "j1") in fake.calls
        if done and not client.get("/api/jobs/j1").json()["overlays_stale"]:
            break
        time.sleep(0.05)
    assert ("overlays", "j1") in fake.calls
    assert client.get("/api/jobs/j1").json()["overlays_stale"] is False


def test_job_create_refuses_an_invalid_vocabulary(client):
    for raw, code in (("not json", 400), (json.dumps([{"term": "Zen", "variants": ["Z*"]}]), 422)):
        r = client.post(
            "/api/jobs",
            files=[("camera", ("take.mov", b"x", "video/quicktime"))],
            data={"type": "thought", "vocabulary": raw},
        )
        assert r.status_code == code, r.text
    assert not jobs_root().exists() or not any(jobs_root().iterdir())


@pytest.mark.media
def test_job_create_stores_the_vocabulary(client, portrait_clip):
    with portrait_clip.open("rb") as cam:
        r = client.post(
            "/api/jobs",
            files=[("camera", (portrait_clip.name, cam, "video/quicktime"))],
            data={"type": "thought", "vocabulary": json.dumps([ZEN])},
        )
    assert r.status_code == 200, r.text
    data = json.loads((jobs_root() / r.json()["id"] / "job.json").read_text())
    assert data["vocabulary"] == [ZEN]
