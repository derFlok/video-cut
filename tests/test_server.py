"""The local app's API (src/videocut/server): auth, uploads, plan, approval, events, media,
path safety and the one-run-at-a-time queue. Agent runs are replaced by a fake pipeline."""

import io
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import clips
import pytest
from fastapi.testclient import TestClient

from videocut import install
from videocut import redaction as red
from videocut import run as pipeline
from videocut.server import app as server_app
from videocut.server import create_app
from videocut.server.runs import Channel
from videocut.tools.common import jobs_root
from videocut.tools.job import is_approved, project_sha, read_approval

TOKEN = "test-token"
BASE = "http://127.0.0.1:4300"
ORIGIN = {"Origin": BASE}
SCREEN = {"id": 1, "kind": "email", "t0": 1.0, "t1": 2.0, "box": [0.1, 0.1, 0.4, 0.2]}
SHOT = {"id": 2, "kind": "name", "asset": "input/shot.png", "box": [0.2, 0.2, 0.5, 0.3]}


class FakePipeline(server_app.Pipeline):
    """Stands in for run.run: start plans (exit 3 = plan gate), cont renders (exit 0)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.gate = threading.Event()
        self.gate.set()

    def start(self, job, say, ask):
        self.calls.append(("start", job.name))
        self.gate.wait(5)
        say("prep:")
        say("  ingest --denoise    1.0s")
        say("plan: Claude is working")
        say("  · Read work/takes_packed.md")
        (job / "work" / "project.md").write_text("# Plan\n\nKeep the hook.\n")
        assert ask("Approve this plan and start cutting? ") == ""
        say("Not approved. Later: videocut tool job approve x")
        return 3

    def cont(self, job, say, ask):
        self.calls.append(("cont", job.name, is_approved(job)))
        say("cut: Claude is working")
        (job / "out" / "reel.mp4").write_bytes(b"\x00" * 4096)
        return 0

    def plan_feedback(self, job, text, say, ask):
        self.calls.append(("plan_feedback", job.name, text))
        return 3

    def feedback(self, job, text, at_s, say, ask):
        self.calls.append(("feedback", job.name, text, at_s))
        return 0


@pytest.fixture
def fake():
    return FakePipeline()


@pytest.fixture
def app(fake):
    return create_app(token=TOKEN, port=4300, runner=fake)


@pytest.fixture
def anon(app):
    return TestClient(app, base_url=BASE)


@pytest.fixture
def client(app):
    c = TestClient(app, base_url=BASE)
    r = c.get(f"/?t={TOKEN}", follow_redirects=False)
    assert r.status_code == 303
    c.headers.update(ORIGIN)
    return c


def make_job(job_id="j1", **extra) -> Path:
    job = jobs_root() / job_id
    for d in ("input", "work", "out"):
        (job / d).mkdir(parents=True)
    (job / "input" / "take.mov").write_bytes(b"x")
    data = {
        "id": job_id,
        "created": "2026-10-13T10:00:00+02:00",
        "source": "input/take.mov",
        "type": "thought",
        "brief": "One thesis",
        "status": "new",
        **extra,
    }
    (job / "job.json").write_text(json.dumps(data))
    return job


def planned_job(job_id="j1", candidates=None) -> Path:
    job = make_job(job_id)
    (job / "work" / "project.md").write_text("# Plan\n")
    if candidates is not None:
        (job / "work" / "redaction.json").write_text(json.dumps({"candidates": candidates}))
    return job


def events(client, job_id) -> list[dict]:
    r = client.get(f"/api/jobs/{job_id}/events?follow=0")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


# ---------------------------------------------------------------- auth


def test_no_session_is_forbidden(anon):
    assert anon.get("/api/session").status_code == 403
    assert anon.get("/").status_code == 403
    assert anon.get("/?t=wrong").status_code == 403
    assert anon.get("/api/jobs", headers={"Cookie": f"videocut_session={TOKEN}"}).status_code == 403


def test_token_is_exchanged_for_a_strict_httponly_cookie(anon):
    r = anon.get(f"/?t={TOKEN}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert TOKEN not in r.headers["set-cookie"]  # the cookie is its own secret
    s = anon.get("/api/session")
    assert s.status_code == 200
    assert set(s.json()) >= {"version", "lang", "claude"}
    assert s.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in s.headers["content-security-policy"]


def test_wrong_host_is_forbidden(client):
    assert client.get("/api/session").status_code == 200
    r = client.get("/api/session", headers={"Host": "evil.example:4300"})
    assert r.status_code == 403
    r = client.get(f"/?t={TOKEN}", headers={"Host": "evil.example"})
    assert r.status_code == 403


def test_cross_origin_writes_are_forbidden(client):
    make_job()
    for origin in ("http://evil.example", "null", "http://127.0.0.1:9999"):
        r = client.post("/api/jobs/j1/start", headers={"Origin": origin})
        assert r.status_code == 403
    no_origin = TestClient(client.app, base_url=BASE, cookies=dict(client.cookies))
    assert no_origin.post("/api/jobs/j1/start").status_code == 403
    assert no_origin.get("/api/jobs").status_code == 200  # reads need no Origin


def test_non_ascii_credentials_are_refused_not_crashed(anon):
    assert anon.get("/api/session", headers={"X-Token": b"\xe9"}).status_code == 403
    assert anon.get("/api/session", headers={"Cookie": b"videocut_session=\xe9"}).status_code == 403
    assert anon.get("/?t=%C3%A9").status_code == 403


def test_token_header_works_without_cookie(anon):
    r = anon.get("/api/session", headers={"X-Token": TOKEN})
    assert r.status_code == 200
    assert anon.get("/api/session", headers={"X-Token": "nope"}).status_code == 403


def test_ui_fallback_page_when_not_built(client, monkeypatch):
    r = client.get("/")
    if r.status_code == 503:
        assert "./start.sh" in r.text
    else:
        assert r.status_code == 200  # a built UI in this checkout


# ---------------------------------------------------------------- types, brands


def test_types_and_brands(client):
    types = client.get("/api/types").json()
    assert {"id", "name_de", "name_en", "desc_de", "desc_en", "needs", "optional"} <= set(types[0])
    assert all("camera" in t["needs"] for t in types)
    brands = client.get("/api/brands").json()
    assert brands[0]["id"] == "_example"
    one = client.get("/api/brands/_example").json()
    assert one["colors"]["bg"] and one["files"] == [] and one["editable"] is False
    assert client.get("/api/brands/..%2Fetc").status_code == 404
    assert client.put("/api/brands/_example", json=one).status_code == 409


def test_brand_create_edit_and_validate(client):
    r = client.post("/api/brands", json={"name": "My Test"})
    assert r.status_code == 200 and r.json() == {"id": "my-test"}
    assert client.post("/api/brands", json={"name": "My Test"}).status_code == 409
    data = client.get("/api/brands/my-test").json()
    assert data["name"] == "My Test" and data["editable"]
    data["colors"]["accent"] = "#3366FF"
    saved = client.put("/api/brands/my-test", json=data)
    assert saved.status_code == 200 and saved.json()["colors"]["accent"] == "#3366FF"
    assert saved.json()["name"] == "My Test" and "files" in saved.json()  # the brand, as GET
    assert client.get("/api/brands/my-test").json()["colors"]["accent"] == "#3366FF"
    data["colors"]["bg"] = "not a colour"
    bad = client.put("/api/brands/my-test", json=data)
    assert bad.status_code == 422 and bad.json()["detail"]["errors"]
    assert bad.json()["detail"]["message"]
    dup = client.post("/api/brands", json={"name": "My Test"}).json()["detail"]
    assert dup["code"] == "exists" and "already exists" in dup["message"]


def test_brand_file_upload_checks_content(client):
    client.post("/api/brands", json={"name": "b"})
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = client.post("/api/brands/b/files", files={"logo": ("logo.png", png, "image/png")})
    assert r.status_code == 200 and r.json()["path"] == "logo/logo.png"
    fake = client.post("/api/brands/b/files", files={"logo": ("x.png", b"<script>", "image/png")})
    assert fake.status_code == 415
    exe = client.post("/api/brands/b/files", files={"font": ("x.sh", b"#!/bin/sh", "text/plain")})
    assert exe.status_code == 415
    dark = client.post(
        "/api/brands/b/files",
        files={"logo": ("dark.png", png, "image/png")},
        data={"role": "dark"},
    )
    assert dark.status_code == 200, dark.text
    assert dark.json()["logo"]["dark"] == "logo/dark.png"  # the updated brand
    assert "logo/dark.png" in dark.json()["files"]
    assert client.get("/api/brands/b").json()["logo"]["dark"] == "logo/dark.png"
    bad_role = client.post(
        "/api/brands/b/files", files={"logo": ("y.png", png, "image/png")}, data={"role": "x"}
    )
    assert bad_role.status_code == 400
    sneaky = client.post(
        "/api/brands/b/files", files={"logo": ("../../evil.svg", b"<svg></svg>", "image/svg+xml")}
    )
    assert sneaky.status_code == 200 and sneaky.json()["path"] == "logo/evil.svg"


# ---------------------------------------------------------------- jobs


@pytest.mark.media
def test_job_create_by_upload(client, portrait_clip, tmp_path):
    shot = tmp_path / "shot.png"
    subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64",
         "-frames:v", "1", str(shot)],
        check=True,
    )  # fmt: skip
    with portrait_clip.open("rb") as cam, shot.open("rb") as img:
        r = client.post(
            "/api/jobs",
            files=[
                ("camera", (f"../{portrait_clip.name}", cam, "video/quicktime")),
                ("assets[]", ("shot.png", img, "image/png")),
            ],
            data={"type": "thought", "brand": "_example", "format": "4:5", "brief": "Short"},
        )
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    job = jobs_root() / job_id
    data = json.loads((job / "job.json").read_text())
    assert data["source"] == f"input/{portrait_clip.name}"
    assert data["assets"] == ["input/shot.png"]
    assert (data["type"], data["brand"], data["format"], data["brief"]) == (
        "thought", "_example", "4:5", "Short",
    )  # fmt: skip
    assert (job / data["source"]).stat().st_size == portrait_clip.stat().st_size
    assert not any((jobs_root().parent / "uploads").iterdir())  # staging cleaned up

    listed = client.get("/api/jobs").json()
    assert [j["id"] for j in listed] == [job_id]
    assert listed[0]["status"] == "new" and listed[0]["format"] == "4:5"
    shown = client.get(f"/api/jobs/{job_id}").json()
    assert shown["format_resolved"] == "4:5" and shown["outputs"] is None
    assert shown["approved"] is False and shown["plan_sha"] is None


@pytest.mark.media
def test_job_upload_rejects_non_media(client, tmp_path):
    r = client.post("/api/jobs", files={"camera": ("take.mov", b"not a video", "video/mp4")})
    assert r.status_code == 415
    r = client.post("/api/jobs", files={"camera": ("take.exe", b"MZ", "application/x")})
    assert r.status_code == 415
    r = client.post("/api/jobs", data={"type": "thought"})
    assert r.status_code in (400, 415)
    r = client.post("/api/jobs", files={"evil": ("a.mov", b"x", "video/mp4")})
    assert r.status_code == 400
    assert not jobs_root().exists() or not any(jobs_root().iterdir())
    uploads = jobs_root().parent / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())


def test_upload_is_streamed_with_a_size_cap(client, monkeypatch):
    from videocut.server import uploads

    seen = []
    real_open = Path.open

    def spy_open(self, mode="r", *a, **k):
        f = real_open(self, mode, *a, **k)
        if "w" in mode:
            seen.append(self)
        return f

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(uploads, "MAX_FILE_BYTES", 1000)
    big = io.BytesIO(b"\x00" * 5000)
    r = client.post("/api/jobs", files={"camera": ("take.mov", big, "video/quicktime")})
    assert r.status_code == 413
    assert seen and all(not p.exists() for p in seen)  # written to disk, then removed


def test_truncated_upload_is_refused_and_cleaned_up(client):
    body = (
        b'--XyZ\r\nContent-Disposition: form-data; name="camera"; filename="t.mov"\r\n'
        b"Content-Type: video/quicktime\r\n\r\n" + b"\x00" * 3000
    )  # no closing boundary: the connection ended early
    r = client.post(
        "/api/jobs", content=body, headers={"Content-Type": "multipart/form-data; boundary=XyZ"}
    )
    assert r.status_code == 400 and "incomplete" in r.text
    uploads = jobs_root().parent / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())


def test_show_unknown_and_invalid_job_ids(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/..").status_code == 404
    assert client.get("/api/jobs/%2e%2e%2fetc").status_code == 404


def test_job_detail_reports_runs_redaction_and_outputs(client):
    job = planned_job(candidates=[SCREEN])
    (job / "work" / "runs.jsonl").write_text(
        json.dumps({"phase": "plan", "wall_s": 12.5, "turns": 7, "cost_usd": 0.4}) + "\n"
    )
    (job / "out" / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    d = client.get("/api/jobs/j1").json()
    assert d["status"] == "waiting" and d["gate"] == "plan" and d["stage"] == "approval"
    assert d["runs"] == [
        {"phase": "plan", "wall_s": 12.5, "turns": 7, "cost_usd": 0.4, "ok": None, "at": None}
    ]
    assert d["redaction"]["candidates"] == [SCREEN] and d["redaction"]["decision"] is None
    assert d["outputs"] == {"reel": None, "preview": None, "cover": "/api/jobs/j1/media/cover"}
    assert d["plan_sha"] == project_sha(job)


def test_invalid_body_reports_message_and_errors(client):
    planned_job()
    r = client.post("/api/jobs/j1/feedback", json={"text": ""})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["message"].startswith("invalid request") and d["errors"][0].startswith("text:")


def test_delete_moves_the_job_to_trash(client):
    make_job()
    r = client.delete("/api/jobs/j1")
    assert r.status_code == 200
    assert not (jobs_root() / "j1").exists()
    assert (Path(r.json()["trash"]) / "job.json").is_file()


# ---------------------------------------------------------------- plan


def test_plan_get_and_put_with_sha(client):
    job = planned_job()
    p = client.get("/api/jobs/j1/plan").json()
    assert p["markdown"] == "# Plan\n" and p["plan"] is None and p["sha"] == project_sha(job)
    (job / "work" / "plan.json").write_text(json.dumps({"title": "A title"}))
    assert client.get("/api/jobs/j1/plan").json()["plan"] == {"title": "A title"}
    stale = client.put("/api/jobs/j1/plan", json={"markdown": "# Mine\n", "sha": "0" * 64})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"
    ok = client.put("/api/jobs/j1/plan", json={"markdown": "# Mine\n", "sha": p["sha"]})
    assert ok.status_code == 200 and ok.json()["sha"] == project_sha(job)
    assert ok.json()["markdown"] == "# Mine\n" and ok.json()["plan"] == {"title": "A title"}
    assert (job / "work" / "project.md").read_text() == "# Mine\n"
    again = client.put("/api/jobs/j1/plan", json={"markdown": "# Again\n", "sha": p["sha"]})
    assert again.status_code == 409
    assert client.get("/api/jobs/j1").json()["title"] == "A title"


def test_plan_edit_invalidates_an_approval(client):
    job = planned_job()
    sha = project_sha(job)
    assert client.post("/api/jobs/j1/approve", json={"plan_sha": sha}).status_code == 200
    client.app.state.runs.wait_idle()
    assert is_approved(job)
    r = client.put("/api/jobs/j1/plan", json={"markdown": "# New\n", "sha": sha})
    assert r.status_code == 200 and r.json()["approved"] is False
    assert not is_approved(job)


def test_plan_feedback_starts_a_replan_and_revokes_the_approval(client, fake):
    job = planned_job()
    client.post("/api/jobs/j1/approve", json={"plan_sha": project_sha(job)})
    client.app.state.runs.wait_idle()
    r = client.post("/api/jobs/j1/plan/feedback", json={"text": "  shorter hook  "})
    assert r.status_code == 200
    client.app.state.runs.wait_idle()
    assert ("plan_feedback", "j1", "shorter hook") in fake.calls
    assert read_approval(job) is None
    assert client.post("/api/jobs/j1/plan/feedback", json={"text": ""}).status_code == 422


# ---------------------------------------------------------------- approve


def test_approve_records_the_same_approval_and_decision_as_the_cli(client, fake):
    job = planned_job(candidates=[SCREEN, SHOT])
    sha = project_sha(job)
    assert client.post("/api/jobs/j1/approve", json={"plan_sha": sha}).status_code == 400
    assert not is_approved(job)  # no decision, no approval
    bad = {"plan_sha": sha, "redaction": {"mode": "redact", "ids": [9]}}
    assert client.post("/api/jobs/j1/approve", json=bad).status_code == 400
    stale = {"plan_sha": "0" * 64, "redaction": {"mode": "demo"}}
    assert client.post("/api/jobs/j1/approve", json=stale).status_code == 409

    body = {"plan_sha": sha, "redaction": {"mode": "redact", "ids": [2]}}
    r = client.post("/api/jobs/j1/approve", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["redaction"]["ids"] == [2]
    client.app.state.runs.wait_idle()
    # The stores `videocut tool job approve` / `job redaction` write, outside the job folder.
    rec = read_approval(job)
    assert rec["project_sha256"] == sha and rec["redaction_candidates"] == [SCREEN, SHOT]
    assert is_approved(job)
    decision = red.valid_decision(job)
    assert decision["redact"] == [2] and decision["demo"] == [1]
    assert decision["method"] == "app"
    assert not str(red.decision_path(job)).startswith(str(job))
    assert json.loads((job / "job.json").read_text())["status"] == "approved"
    assert ("cont", "j1", True) in fake.calls  # the cut starts after the approval

    d = client.get("/api/jobs/j1").json()
    assert d["redaction"]["decision"]["mode"] == "redact"
    assert d["status"] == "new" and d["stage"] == "cut"  # the fake reel is not a real cut
    assert d["outputs"]["reel"]


@pytest.mark.parametrize(
    ("mode", "redact", "demo"), [("demo", [], [1, 2]), ("redact_all", [1, 2], [])]
)
def test_approve_redaction_modes(client, mode, redact, demo):
    job = planned_job(candidates=[SCREEN, SHOT])
    body = {"plan_sha": project_sha(job), "redaction": {"mode": mode}}
    assert client.post("/api/jobs/j1/approve", json=body).status_code == 200
    client.app.state.runs.wait_idle()
    rec = red.valid_decision(job)
    assert (rec["redact"], rec["demo"]) == (redact, demo)


def test_feedback_needs_a_finished_reel(client, fake):
    job = planned_job()
    assert client.post("/api/jobs/j1/feedback", json={"text": "less zoom"}).status_code == 409
    client.post("/api/jobs/j1/approve", json={"plan_sha": project_sha(job)})
    client.app.state.runs.wait_idle()
    r = client.post("/api/jobs/j1/feedback", json={"text": "at 0:12 zoom out less", "at_s": 12})
    assert r.status_code == 200
    client.app.state.runs.wait_idle()
    assert ("feedback", "j1", "at 0:12 zoom out less", 12.0) in fake.calls
    bad = client.post("/api/jobs/j1/feedback", json={"text": "x", "at_s": -1})
    assert bad.status_code == 422


# ---------------------------------------------------------------- runs and events


def test_start_streams_stages_and_ends_at_the_plan_gate(client, fake):
    make_job()
    assert events(client, "j1") == [{"t": "stage", "stage": "prep"}]
    assert client.post("/api/jobs/j1/start").status_code == 200
    client.app.state.runs.wait_idle()
    evs = events(client, "j1")  # replayed after the run
    kinds = [(e["t"], e.get("stage") or e.get("gate")) for e in evs]
    assert kinds[0] == ("stage", "prep")
    assert ("stage", "plan") in kinds
    assert {"t": "tool", "text": "Read work/takes_packed.md"} in evs
    assert kinds[-3:-1] == [("stage", "approval"), ("gate", "plan")]
    assert evs[-1]["t"] == "done" and evs[-1]["ok"] is True
    assert not any("Not approved" in e.get("text", "") for e in evs)
    d = client.get("/api/jobs/j1").json()
    assert d["status"] == "waiting" and d["gate"] == "plan"


def test_idle_job_replays_its_gate_from_disk(client):
    planned_job()
    assert events(client, "j1") == [
        {"t": "stage", "stage": "approval"},
        {"t": "gate", "gate": "plan"},
    ]


def test_one_run_at_a_time(client, fake):
    make_job("j1")
    make_job("j2")
    fake.gate.clear()
    assert client.post("/api/jobs/j1/start").status_code == 200
    busy = client.post("/api/jobs/j1/start")
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "busy"
    assert client.post("/api/jobs/j2/start").json()["state"] == "queued"
    assert client.get("/api/jobs/j1").json()["status"] == "running"
    j2 = client.get("/api/jobs/j2").json()
    assert j2["status"] == "running" and j2["queued"] is True
    assert client.delete("/api/jobs/j1").status_code == 409
    fake.gate.set()
    client.app.state.runs.wait_idle()
    assert [c for c in fake.calls if c[0] == "start"] == [("start", "j1"), ("start", "j2")]
    assert client.post("/api/jobs/j1/start").status_code == 200
    client.app.state.runs.wait_idle()


def test_a_failing_run_reports_the_error(client, fake, monkeypatch):
    make_job()

    def boom(job, say, ask):
        raise pipeline.RunError("the plan run failed: no tokens")

    monkeypatch.setattr(fake, "start", boom)
    client.post("/api/jobs/j1/start")
    client.app.state.runs.wait_idle()
    evs = events(client, "j1")
    assert {"t": "error", "text": "the plan run failed: no tokens"} in evs
    assert evs[-1]["t"] == "done" and evs[-1]["ok"] is False
    d = client.get("/api/jobs/j1").json()
    assert d["status"] == "error" and d["stage"] == "error" and "no tokens" in d["error"]
    assert {"t": "stage", "stage": "error"} in evs


def test_the_worker_survives_a_system_exit(client, fake, monkeypatch):
    make_job()

    def exits(job, say, ask):
        raise SystemExit(2)

    real = fake.start
    monkeypatch.setattr(fake, "start", exits)
    client.post("/api/jobs/j1/start")
    client.app.state.runs.wait_idle()
    assert events(client, "j1")[-1] == {"t": "done", "ok": False, "wall_s": 0.0}
    monkeypatch.setattr(fake, "start", real)
    assert client.post("/api/jobs/j1/start").status_code == 200
    client.app.state.runs.wait_idle()
    assert events(client, "j1")[-1]["ok"] is True


def test_channel_turns_say_lines_into_events():
    ch = Channel("j")
    for line in (
        "job: /x",
        "prep:",
        "  face              2.0s",
        "plan: Claude is working",
        "  · Bash videocut tool job show",
        "\nPlan: /x/work/project.md",
        "plan: approved",
        "",
    ):
        ch.say(line)
    assert [e["t"] for e in ch.events] == [
        "log", "stage", "log", "log", "stage", "log", "tool", "log", "log",
    ]  # fmt: skip
    assert ch.stage == "plan"


# ---------------------------------------------------------------- files


def test_media_supports_range_requests(client):
    job = make_job()
    (job / "out" / "reel.mp4").write_bytes(bytes(range(256)) * 4)
    r = client.get("/api/jobs/j1/media/reel", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == bytes(range(10, 20))
    assert r.headers["content-range"] == "bytes 10-19/1024"
    assert r.headers["content-type"] == "video/mp4"
    full = client.get("/api/jobs/j1/media/reel")
    assert full.status_code == 200 and len(full.content) == 1024
    assert client.get("/api/jobs/j1/media/preview").status_code == 404
    assert client.get("/api/jobs/j1/media/job").status_code == 404
    dl = client.get("/api/jobs/j1/download")
    assert dl.status_code == 200
    assert 'attachment; filename="One-thesis-9x16.mp4"' in dl.headers["content-disposition"]


def test_stills_are_confined_to_the_allowed_folders(client, tmp_path):
    job = make_job()
    for d in ("work/plan_stills", "work/sheets", "out/verify"):
        (job / d).mkdir()
        (job / d / "a.jpg").write_bytes(b"\xff\xd8\xff")
    (job / "out" / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    (job / "input" / "secret.jpg").write_bytes(b"\xff\xd8\xff")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8\xff")
    (job / "work" / "plan_stills" / "link.jpg").symlink_to(outside)
    for good in ("work/plan_stills/a.jpg", "work/sheets/a.jpg", "out/verify/a.jpg"):
        r = client.get(f"/api/jobs/j1/stills/{good}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg", good
    for bad in (
        "out/cover.jpg",
        "input/secret.jpg",
        "work/plan_stills/../../input/secret.jpg",
        "work/plan_stills/..%2F..%2Finput%2Fsecret.jpg",
        "work/plan_stills/%2e%2e/%2e%2e/input/secret.jpg",
        "work/plan_stills/link.jpg",
        "work/plan_stills/../../job.json",
        "/etc/passwd",
        "%2Fetc%2Fpasswd",
        "work/project.md",
        "work/plan_stills",
    ):
        assert client.get(f"/api/jobs/j1/stills/{bad}").status_code == 404, bad


def test_a_reel_with_flagged_overlays_is_done_not_restartable(client):
    """The brand's overlays are composited onto the current EDL but their verify flagged
    something (or was re-run by hand later): the result gate, not "new" with a Start."""
    from videocut.tools.job import approve_job

    job = make_job(brand="_example", format="16:9")
    (job / "work" / "project.md").write_text("# Plan\n")
    approve_job(job)
    edl = {"fps": 30, "size": [1920, 1080], "source": "input/take.mov",
           "segments": [{"in": 0.0, "out": 2.0}]}  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    edl_sha = pipeline._file_sha(job / "work" / "edl.json")
    (job / "out" / "reel.mp4").write_bytes(b"reel")
    (job / "out" / "verify.json").write_text(json.dumps({"ok": True, "reel": "out/reel.mp4"}))
    (job / "out" / "render.json").write_text(json.dumps({"edl_sha256": edl_sha, "overlay": True}))
    pipeline.record_cut(job, project_sha(job))
    data = json.loads((job / "job.json").read_text())
    data["overlays"] = {"brand": "_example", "edl_sha256": edl_sha, "verify_ok": False}
    (job / "job.json").write_text(json.dumps(data))
    d = client.get("/api/jobs/j1").json()
    assert (d["status"], d["stage"], d["gate"]) == ("done", "done", "result")
    (job / "out" / "render.json").write_text(json.dumps({"edl_sha256": edl_sha}))  # not composited
    d = client.get("/api/jobs/j1").json()
    assert (d["status"], d["gate"]) == ("new", None)


def test_versions_map_the_kept_reels(client):
    """previous_reel is the reel BEFORE a revision: version 1 = the first revision's
    previous_reel, the latest version = out/reel.mp4; a failed revision makes no version."""
    job = make_job()
    hist = job / "out" / "history"
    hist.mkdir()
    (job / "out" / "reel.mp4").write_bytes(b"\x03" * 100)
    for k in (1, 2):
        (hist / f"reel-{k}.mp4").write_bytes(bytes([k]) * 100)
    kept = "out/history/reel-{}.mp4"
    lines = [
        {"n": 1, "text": "less zoom", "at_s": 12, "ok": True, "previous_reel": kept.format(1)},
        {"n": 2, "text": "broken", "ok": False, "error": "x"},
        {"n": 3, "text": "drop the question", "ok": True, "previous_reel": kept.format(2)},
    ]
    (job / "work" / "revisions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    d = client.get("/api/jobs/j1").json()
    revs, versions = d["revisions"], d["versions"]
    assert [r.get("version") for r in revs] == [2, None, 3]
    assert revs[0]["video_url"] == "/api/jobs/j1/media/history/reel-2.mp4"
    assert "video_url" not in revs[1]
    assert revs[2]["video_url"] == "/api/jobs/j1/media/reel"
    assert [(v["version"], v["video_url"], v["current"]) for v in versions] == [
        (3, "/api/jobs/j1/media/reel", True),
        (2, "/api/jobs/j1/media/history/reel-2.mp4", False),
        (1, "/api/jobs/j1/media/history/reel-1.mp4", False),
    ]
    r = client.get(versions[2]["video_url"], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == b"\x01" * 10
    (hist / "reel-1.mp4").unlink()  # dropped from the history: listed, not playable
    assert client.get("/api/jobs/j1").json()["versions"][2]["video_url"] is None
    for bad in ("history/reel-x.mp4", "history/../job.json", "history/reel-9.mp4"):
        assert client.get(f"/api/jobs/j1/media/{bad}").status_code == 404, bad


def test_no_revisions_one_version(client):
    job = make_job()
    (job / "out" / "reel.mp4").write_bytes(b"\x00" * 10)
    d = client.get("/api/jobs/j1").json()
    assert d["revisions"] == []
    assert d["versions"] == [
        {"version": 1, "revision": None, "text": None, "video_url": "/api/jobs/j1/media/reel",
         "current": True}
    ]  # fmt: skip


# ---------------------------------------------------------------- adapters, internals


def test_revision_adapters_drive_run(monkeypatch, tmp_path):
    p = server_app.Pipeline()
    seen = {}

    def fake_run(ref, plan_feedback=None, feedback=None, at_s=None, replan=False, **kw):
        seen.update(ref=ref, plan_feedback=plan_feedback, feedback=feedback, at_s=at_s,
                    replan=replan)  # fmt: skip
        return 0

    monkeypatch.setattr(pipeline, "run", fake_run)
    p.plan_feedback(tmp_path, "shorter", print, input)
    assert seen["plan_feedback"] == "shorter" and seen["replan"] is True
    p.feedback(tmp_path, "less zoom", 3.5, print, input)
    assert (seen["feedback"], seen["at_s"]) == ("less zoom", 3.5)


def test_stdout_capture_is_per_thread(monkeypatch):
    router = pipeline._StdoutRouter(io.StringIO())
    monkeypatch.setattr(sys, "stdout", router)
    results = {}
    barrier = threading.Barrier(2)

    def work(name):
        with pipeline.capture_stdout() as buf:
            barrier.wait()
            for _ in range(200):
                print(name)
        results[name] = set(buf.getvalue().split())

    threads = [threading.Thread(target=work, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"a": {"a"}, "b": {"b"}}
    print("after")
    assert router.out.getvalue() == "after\n"


def test_setup_builds_the_app_only_when_its_source_exists(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(install, "APP", tmp_path / "app")
    install.build_app()
    assert calls == []
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "package.json").write_text("{}")
    monkeypatch.setattr(install, "APP_DIST", tmp_path / "dist")
    monkeypatch.setattr(install, "STATE", tmp_path / "installed.json")
    with pytest.raises(RuntimeError, match="package-lock.json is missing"):
        install.build_app()  # never an unpinned npm install
    (tmp_path / "app" / "package-lock.json").write_text("{}")
    with pytest.raises(RuntimeError, match="wrote no"):
        install.build_app()
    assert [c[0][1:3] for c in calls] == [["ci", "--no-audit"], ["run", "build"]]


def test_cli_ui_command_uses_the_server(monkeypatch):
    from videocut import cli, server

    got = {}
    monkeypatch.setattr(
        server, "serve", lambda port, open_browser: got.update(p=port, o=open_browser)
    )
    assert cli.main(["ui", "--port", "4411", "--no-open"]) == 0
    assert got == {"p": 4411, "o": False}


@pytest.fixture(autouse=True)
def _no_leftover_uploads():
    yield
    shutil.rmtree(jobs_root().parent / "uploads", ignore_errors=True)
