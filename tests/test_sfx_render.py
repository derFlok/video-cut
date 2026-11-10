"""Render mixes work/sfx.wav from synthetic motion events under the voice; verify checks it."""

import json
from pathlib import Path

import pytest
from helpers import run_tool

from videocut.tools.job import approve_job

pytestmark = pytest.mark.media

SEGMENTS = [{"in": 0.3, "out": 4.0}, {"in": 5.0, "out": 8.5}]
EVENTS = [
    {"t": 0.4, "kind": "whoosh", "component": "headline", "strength": 0.6},
    {"t": 0.9, "kind": "pop", "component": "headline", "strength": 0.5},
    {"t": 1.6, "kind": "check", "component": "checklist", "strength": 0.6},
    {"t": 2.4, "kind": "count", "component": "counter", "strength": 0.5},
    {"t": 3.3, "kind": "strike", "component": "strikeCard", "strength": 0.7},
    {"t": 4.6, "kind": "impact", "component": "counter", "strength": 0.6},
    {"t": 6.2, "kind": "swoosh_out", "component": "headline", "strength": 0.5},
]


def _job(capsys, clip, job_id) -> Path:
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    edl = {"fps": 30, "size": [1080, 1920], "source": s["source"], "segments": SEGMENTS}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: sfx test\n")
    d = job / "work" / "motion" / "m1"
    d.mkdir(parents=True)
    (d / "events.json").write_text(json.dumps(EVENTS))
    (job / "work" / "motion" / "spans.json").write_text(
        json.dumps([{"id": "m1", "t0": 0.0, "t1": 7.2, "region": [0, 0, 1080, 400]}])
    )
    approve_job(job)
    return job


@pytest.fixture(autouse=True)
def composited(request, monkeypatch):
    """These tests are about the mix: span m1 counts as composited (its clip is not)."""
    from videocut.tools import render

    if "orphan" not in request.node.name:
        monkeypatch.setattr(render, "_sounding", lambda mres: {"m1"})


def test_orphan_events_without_a_composited_clip_play_nothing(capsys, portrait_clip):
    job = _job(capsys, portrait_clip, "sfx-orphan")
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    assert s["sfx"] is None and not (job / "work" / "sfx.wav").exists()


def test_render_mixes_sound_effects_and_verify_checks_them(capsys, portrait_clip):
    job = _job(capsys, portrait_clip, "sfx-render")
    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, s
    assert s["sfx"]["mixed"] and s["sfx"]["level"] == "subtle" and s["sfx"]["hits"] == 7
    assert (job / "work" / "sfx.wav").is_file()  # built by render: the events were new
    rep = json.loads((job / "out" / "render.json").read_text())["sfx"]
    a = rep["analysis"]
    assert a["voice_lufs"] == pytest.approx(-14, abs=1.5)
    assert a["sfx_lufs"] < a["voice_lufs"] - 5  # gated LUFS of a sparse bus: while it sounds
    assert not a["masked"] and not a["clipped"] and not a["bus_clips"]
    assert s["loudness"]["true_peak_dbtp"] <= -1.0
    graph = (job / "work" / "render_filter.txt").read_text()
    assert "sidechaincompress" in graph and "alimiter" in graph
    disc = json.loads((job / "work" / "disclosure.json").read_text())
    assert {x["origin"] for x in disc["sounds"]} == {"procedural"}

    code, v, _ = run_tool(capsys, "verify", "--job", str(job))
    doc = json.loads((job / "out" / "verify.json").read_text())
    assert doc["checks"]["sfx"]["ok"], json.dumps(doc["checks"]["sfx"])
    assert doc["checks"]["sfx"]["flags"] == []

    # switched off: the same reel without the track, and the cut record notices the change
    code, s2, _ = run_tool(capsys, "job", "sfx", str(job), "off")
    assert code == 0 and s2["sfx_resolved"] == "off"
    code, s3, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s3
    assert s3["sfx"] == {"level": "off", "mixed": False, "flags": []}
    assert "sidechaincompress" not in (job / "work" / "render_filter.txt").read_text()


def test_render_without_motion_events_is_unchanged(capsys, portrait_clip):
    job = _job(capsys, portrait_clip, "sfx-none")
    for p in (job / "work" / "motion").rglob("*.json"):
        p.unlink()
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    assert s["sfx"] is None and not (job / "work" / "sfx.wav").exists()
    assert "sfx" not in json.loads((job / "out" / "render.json").read_text())


def test_an_invalid_level_refuses_the_render(capsys, portrait_clip):
    job = _job(capsys, portrait_clip, "sfx-bad")
    data = json.loads((job / "job.json").read_text())
    data["sfx"] = "loud"
    (job / "job.json").write_text(json.dumps(data))
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 1 and 'job.json "sfx"' in s["error"]
