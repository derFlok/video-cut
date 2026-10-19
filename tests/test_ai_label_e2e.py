"""AI label end to end with the real renderer: a hand-written work/material.json (one
generated still, chosen) and an EDL segment that shows it -> overlays adds the badge ->
render writes the container comment, render.json "c2pa" and work/disclosure.json ->
verify finds the drawn badge (and flags a missing one)."""

import json
import subprocess
from pathlib import Path

import clips
import pytest
from helpers import run_tool
from test_ai_label import BROLL, candidate, material, wish
from test_renderer import needs_renderer

from videocut import settings
from videocut.tools import _c2pa
from videocut.tools.job import approve_job

pytestmark = [pytest.mark.media, needs_renderer]

SEGMENTS = [{"in": 0.5, "out": 2.0}, {"in": 2.5, "out": 4.0, "layout": BROLL, "material": 1}]


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("src") / "take.mp4"
    subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=5",
         "-f", "lavfi", "-i", "sine=f=300:d=5",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _job(capsys, source: Path, style: str = "photo") -> Path:
    code, s, _ = run_tool(capsys, "job", "new", str(source), "--id", "ai", "--brand", "_example")
    assert code == 0, s
    job = Path(s["job"])
    edl = {"fps": 30, "size": [1080, 1920], "source": s["source"], "segments": SEGMENTS}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "material.json").write_text(
        json.dumps(material(wish(1, "c1", [candidate("c1")], style=style)))
    )
    (job / "work" / "project.md").write_text("# Plan: ai label\n")
    approve_job(job)
    return job


def _comment(path: Path) -> str | None:
    out = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return json.loads(out.stdout)["format"].get("tags", {}).get("comment")


def test_badge_comment_disclosure_and_verify(capsys, source, monkeypatch):
    monkeypatch.setattr(_c2pa, "binary", lambda: None)  # the real tool: test_ai_label
    job = _job(capsys, source)
    code, s, _ = run_tool(capsys, "overlays", "--job", str(job))
    assert code == 0 and s["renderer"]["undrawn_items"] == 0, s
    doc = json.loads((job / "work" / "overlays.json").read_text())
    assert doc["ai_label"] == [{"t0": 1.5, "t1": 3.0, "text": "KI-generiert", "material": [1]}]
    boxes = json.loads((job / "work" / "overlay_boxes.json").read_text())
    badge = [b for b in boxes if b["scene"] == "ai_badge"]
    assert len(badge) == 1 and badge[0]["box"][1] >= 250, badge  # top safe zone kept
    assert badge[0]["drawn"][0] == 1.5 and badge[0]["drawn"][1] >= 2.96

    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0 and s["overlay"] and s["c2pa"] == "skipped", s
    reel = job / "out" / "reel.mp4"
    assert _comment(reel) == "Contains AI-generated material: zentro-image-1 rev abc123"
    render = json.loads((job / "out" / "render.json").read_text())
    assert render["c2pa"] == "skipped"
    assert render["provenance"]["ai_models"] == ["zentro-image-1 rev abc123"]
    disc = json.loads((job / "work" / "disclosure.json").read_text())
    assert [(a["t0"], a["t1"]) for a in disc["ai"]] == [(1.5, 3.0)]
    assert disc["platforms"][0]["status"] == "required"

    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, s
    assert not [o for o in s["overlay_flagged"] if "ai_label_missing" in o["flags"]], s
    v = json.loads((job / "out" / "verify.json").read_text())
    assert v["ai_material"][0]["drawn"] is True
    assert [t for t in v["overlay_times"] if 1.5 < t < 3.0]  # the badge's tile in the sheet
    assert s["overlay_sheets"]


def test_verify_flags_a_label_that_was_not_drawn(capsys, source, monkeypatch):
    monkeypatch.setattr(_c2pa, "binary", lambda: None)
    settings.save(ai_label_illustrations=False)
    job = _job(capsys, source, style="illustration")
    code, s, _ = run_tool(capsys, "overlays", "--job", str(job))
    assert code == 0 and "ai_label" not in json.loads((job / "work" / "overlays.json").read_text())
    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, s
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert not [o for o in s["overlay_flagged"] if "ai_label_missing" in o["flags"]]
    settings.save(ai_label_illustrations=True)  # now it must show, but overlays ran before
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    flagged = [o for o in s["overlay_flagged"] if "ai_label_missing" in o["flags"]]
    assert flagged == [{"scene": "ai_badge", "t0": 1.5, "t1": 3.0,
                        "flags": ["ai_label_missing"], "material": 1}], s  # fmt: skip
    assert s["ok"] is False
