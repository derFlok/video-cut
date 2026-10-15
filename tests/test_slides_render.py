"""Slides through the renderer: a final-state still of every template in every format, and
end to end a generated face clip with three slides (bubble across a cut, split, full) that
renders, verifies, notices a changed clip and keeps unchanged clips."""

import json
import subprocess
from pathlib import Path

import pytest
from clips import ffmpeg, ffprobe
from helpers import run_tool

from videocut.paths import BIN, BROWSERS, RENDERER, tool_env
from videocut.tools.job import approve_job
from videocut.tools.slides import MIN_SCALE, STILL_MAX

pytestmark = pytest.mark.media


def _ready() -> bool:
    return (
        (BIN / "node").is_file()
        and (RENDERER / "node_modules" / "playwright-core").is_dir()
        and any(BROWSERS.glob("chromium*"))
        and ffmpeg() is not None
        and ffprobe() is not None
    )


needs_renderer = pytest.mark.skipif(
    not _ready(), reason="needs node, playwright-core, Chromium, ffmpeg"
)

# The longest content the limits allow where it matters, so a still shows the worst case.
DOC = {
    "slides": [
        {"id": "title", "template": "title", "content": {
            "title": "Drei Dinge, die jede Woche zwei Stunden Arbeit sparen helfen",
            "subtitle": "Was wir im ersten Jahr mit Zentrovia gelernt haben, "
                        "kurz und ehrlich erzählt"}},
        {"id": "bullets", "template": "bullets", "content": {
            "title": "Was im Alltag wirklich hilft",
            "items": ["Ein Eingang für alle Anfragen, statt fünf verschiedener Postfächer",
                      "Kurze Wege zwischen Empfang und Behandlungsraum",
                      "Messen, was zählt, und den Rest bewusst weglassen",
                      "Einmal pro Woche gemeinsam auf die Zahlen schauen"]}},
        {"id": "big_number", "template": "big_number", "content": {
            "value": "1.250", "label": "Anfragen im ersten Monat, ganz ohne Werbung",
            "count": True}},
        {"id": "compare", "template": "compare", "content": {
            "left": {"title": "Vorher", "items": ["Zettel am Empfang", "Rückrufe am Abend",
                                                  "Doppelte Termine"]},
            "right": {"title": "Nachher", "items": ["Ein Ort für alles", "Antwort in Minuten",
                                                    "Klare Zuständigkeit"]}}},
        {"id": "steps", "template": "steps", "content": {
            "title": "So läuft es ab",
            "steps": ["Anfrage kommt an", "Termin wird vorgeschlagen", "Bestätigung per Nachricht",
                      "Erinnerung am Vortag"]}},
        {"id": "timeline", "template": "timeline", "content": {
            "title": "Unser erstes Jahr",
            "points": [{"label": "Januar", "text": "Start mit einem Standort"},
                       {"label": "April", "text": "Zweiter Standort kommt dazu"},
                       {"label": "September", "text": "Das ganze Team arbeitet damit"},
                       {"label": "Dezember", "text": "Halb so viele Rückfragen"}]}},
        {"id": "bar_chart", "template": "bar_chart", "content": {
            "title": "Antwortzeit in Minuten", "unit": "min",
            "bars": [{"label": "Telefon", "value": 42}, {"label": "Mail", "value": 95},
                     {"label": "Formular", "value": 12.5}, {"label": "Chat", "value": 4}]}},
        {"id": "quote", "template": "quote", "content": {
            "text": "Seit wir alles an einem Ort haben, klingelt das Telefon weniger und wir "
                    "haben endlich wieder Zeit für die Menschen, die vor uns sitzen.",
            "author": "Leitung Empfang, Praxis Zentrovia"}},
        {"id": "cta", "template": "cta", "content": {
            "title": "Jetzt zwei Wochen kostenlos testen",
            "subtitle": "Ohne Vertrag, ohne Kreditkarte, mit persönlicher Einrichtung",
            "url": "zentrovia.example/test"}},
        {"id": "image", "template": "image", "content": {"asset": "input/slides/page_01.png"},
         "source": "upload"},
    ]
}  # fmt: skip


def _ff(*args: str) -> None:
    subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
        check=True,
        env=tool_env(),
    )


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
         "stream=width,height,codec_name,pix_fmt,nb_read_packets,r_frame_rate", "-of", "json",
         str(path)],
        capture_output=True, text=True, check=True, env=tool_env(),
    )  # fmt: skip
    return json.loads(out.stdout)["streams"][0]


def _clip(path: Path, size: tuple[int, int], seconds: int) -> Path:
    _ff(
        "-f", "lavfi", "-i", f"testsrc2=size={size[0]}x{size[1]}:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=f=300:d={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="module")
def portrait(tmp_path_factory):
    return _clip(tmp_path_factory.mktemp("src") / "take.mp4", (1080, 1920), 12)


def _job(capsys, source: Path, job_id: str, fmt: str, doc: dict) -> Path:
    code, s, _ = run_tool(capsys, "job", "new", str(source), "--id", job_id, "--format", fmt)
    assert code == 0, s
    job = Path(s["job"])
    (job / "work").mkdir(exist_ok=True)
    (job / "work" / "slides.json").write_text(json.dumps(doc, ensure_ascii=False))
    (job / "input" / "slides").mkdir(parents=True, exist_ok=True)
    # an uploaded page: a landscape picture with a pattern (stands in for a rasterised PDF)
    _ff("-f", "lavfi", "-i", "testsrc2=size=1600x900:duration=1", "-frames:v", "1",
        str(job / "input" / "slides" / "page_01.png"))  # fmt: skip
    return job


@needs_renderer
@pytest.mark.parametrize("fmt", ["9:16", "16:9", "1:1", "4:5"])
def test_every_template_has_a_fitting_still_in_every_format(capsys, portrait, fmt):
    job = _job(capsys, portrait, "st" + fmt.replace(":", "x"), fmt, DOC)
    code, s, _ = run_tool(capsys, "slides", "check", str(job))
    assert code == 0 and s["slides"] == 10, s
    code, s, out = run_tool(capsys, "slides", "stills", str(job))
    assert code == 0, out
    assert [d["id"] for d in s["stills"]] == [d["id"] for d in DOC["slides"]]
    for d in s["stills"]:
        assert d["layout"] == "slide" and d["fits"], d  # the bubble layout leaves the least room
        assert d["scale"] >= MIN_SCALE, d
        big, small = job / d["still"], job / d["small"]
        assert big.stat().st_size > 10_000
        p = _probe(small)
        assert max(p["width"], p["height"]) <= STILL_MAX
    assert not list((job / "work" / "slides" / "stills").glob("*.png"))


@needs_renderer
def test_three_slides_render_and_verify(capsys, portrait):
    doc = {"slides": [
        {"id": "bullets", "template": "bullets", "content": {
            "title": "Was hilft", "items": ["Ein Eingang", "Kurze Wege", "Messen"]}},
        {"id": "big_number", "template": "big_number", "content": {
            "value": "40 %", "label": "weniger Rückfragen", "count": True}},
        DOC["slides"][8],
    ]}  # fmt: skip
    job = _job(capsys, portrait, "e2e", "9:16", doc)
    src = json.loads((job / "job.json").read_text())["source"]
    edl = {"fps": 30, "size": [1080, 1920], "source": src, "segments": [
        {"in": 0.0, "out": 1.5},
        {"in": 2.0, "out": 5.0, "layout": "slide", "slide": "bullets", "reveal": [0.3, 1.0]},
        {"in": 5.5, "out": 9.0, "layout": "slide", "slide": "bullets", "reveal": [0.2]},
        {"in": 9.0, "out": 11.5, "layout": "slide_split", "slide": "big_number"},
        {"in": 11.5, "out": 12.0, "layout": "slide_full", "slide": "cta"},
    ]}  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan\n")
    approve_job(job)

    code, s, out = run_tool(capsys, "edl", "check", str(job / "work" / "edl.json"))
    assert code == 0, out
    code, s, out = run_tool(capsys, "slides", "render", str(job))
    assert code == 0, out
    assert sorted(s["rendered"]) == ["big_number", "bullets", "cta"] and s["kept"] == []
    clips = {d["id"]: d for d in s["slides"]}
    # the bullets clip runs through both segments: 3.0 s + 3.5 s, one clock
    p = _probe(job / "work" / "slides" / "bullets.mov")
    assert (p["width"], p["height"], p["codec_name"]) == (1080, 1920, "prores")
    assert int(p["nb_read_packets"]) == 195 and p["r_frame_rate"] == "30/1"
    rep = json.loads((job / "work" / "slides" / "bullets.json").read_text())
    assert rep["times"] == pytest.approx([0.3, 1.0, 3.2])  # last: 0.2 s into the 2nd part
    assert all(d["fits"] for d in clips.values())

    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, out
    rj = json.loads((job / "out" / "render.json").read_text())
    assert [d["id"] for d in rj["slides"]] == ["bullets", "big_number", "cta"]

    code, s, out = run_tool(capsys, "verify", "--job", str(job))
    by = {d["id"]: d for d in json.loads((job / "out" / "verify.json").read_text())["slides"]}
    assert by["bullets"]["flags"] == [] and by["big_number"]["flags"] == []
    assert by["cta"]["flags"] == [
        "slide_reading_time"
    ]  # on screen 0.5 s, far below its reading time
    assert [d["id"] for d in s["slide_flagged"]] == ["cta"]
    assert s["slide_sheets"] and (job / s["slide_sheets"][0]).is_file()

    # a clip changed after render is not believed; render again keeps the untouched ones
    mov = job / "work" / "slides" / "cta.mov"
    mov.write_bytes(mov.read_bytes()[:-16] + b"\0" * 16)
    code, s, out = run_tool(capsys, "verify", "--job", str(job))
    assert "slide_untrusted" in {d["id"]: d for d in s["slide_flagged"]}["cta"]["flags"]
    code, s, out = run_tool(capsys, "slides", "render", str(job))
    assert code == 0, out
    assert s["rendered"] == ["cta"] and sorted(s["kept"]) == ["big_number", "bullets"]
    # an edited layout report is not blessed by the next render: that slide renders again
    rep_path = job / "work" / "slides" / "bullets.json"
    rep = json.loads(rep_path.read_text())
    rep_path.write_text(json.dumps({**rep, "texts": []}))
    code, s, out = run_tool(capsys, "slides", "render", str(job))
    assert code == 0, out
    assert s["rendered"] == ["bullets"] and json.loads(rep_path.read_text())["texts"]
