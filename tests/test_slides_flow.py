"""Slides in the job flow: job.json "slides" (job slides), the human's own pages (job
slides-import, local rasterisation), plan.json "slides" validation, the plan-made-for
record, the run guards and the API (create with slides, plan feedback on a slide, marks)."""

import json
import subprocess
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut import run as pipeline
from videocut.edl import SLIDE_LAYOUTS
from videocut.tools import _job_slides, _pdf
from videocut.tools import plan as plan_tool
from videocut.tools import slides as slides_tool
from videocut.tools.common import jobs_root
from videocut.tools.job import approve_job, is_approved, read_approval

needs_pdf = pytest.mark.skipif(not _pdf.available(), reason="PDF pages need macOS CoreGraphics")


def make_job(job_id="j1", **extra) -> Path:
    job = jobs_root() / job_id
    for d in ("input", "work", "out"):
        (job / d).mkdir(parents=True)
    (job / "input" / "take.mov").write_bytes(b"x")
    data = {"id": job_id, "source": "input/take.mov", "type": "thought", "status": "new",
            **extra}  # fmt: skip
    (job / "job.json").write_text(json.dumps(data))
    return job


def make_pdf(path: Path, pages: list[tuple[int, int, int]]) -> Path:
    """A small valid PDF: one filled rectangle and a label per page (w, h, rotation)."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for i, (w, h, rot) in enumerate(pages):
        content = f"0 0.4 0.8 rg 20 20 {w // 2} {h // 2} re f BT /F1 48 Tf 40 {h - 80} Td "
        content = (content + f"(Seite {i + 1}) Tj ET").encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] /Rotate {rot} "
            f"/Contents {len(objs)} 0 R /Resources << /Font << /F1 << /Type /Font "
            "/Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode()
        )
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def make_png(path: Path, size="800x600") -> Path:
    subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", f"color=c=gray:s={size}",
         "-frames:v", "1", "-y", str(path)],
        check=True,
    )  # fmt: skip
    return path


def size_of(p: Path) -> tuple[int, int]:
    r = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_entries", "stream=width,height", "-of", "json",
         str(p)], capture_output=True, text=True, check=True,
    )  # fmt: skip
    st = json.loads(r.stdout)["streams"][0]
    return st["width"], st["height"]


# ---------------------------------------------------------------- settings


def test_slides_settings_default_set_and_show(capsys, monkeypatch):
    job = make_job()
    assert _job_slides.view(job)["mode"] == "none"
    code, s, _ = run_tool(
        capsys, "job", "slides", "j1", "--mode", "auto", "--wish", " drei  Schritte "
    )
    assert code == 0 and (s["mode"], s["wish"]) == ("auto", "drei Schritte")
    assert json.loads((job / "job.json").read_text())["slides"] == {
        "mode": "auto", "wish": "drei Schritte",
    }  # fmt: skip
    code, s, out = run_tool(capsys, "job", "show", "j1")
    assert s["slides"]["mode"] == "auto" and "slides: auto" in out
    code, s, _ = run_tool(capsys, "job", "slides", "j1", "--mode", "pictures")
    assert code == 1 and "unknown slides mode" in s["error"]
    code, s, _ = run_tool(capsys, "job", "slides", "j1", "--wish", "x" * 2001)
    assert code == 1 and "at most 2000" in s["error"]
    code, s, _ = run_tool(capsys, "job", "slides", "j1", "--mode", "none", "--wish", "")
    assert code == 0 and "slides" not in json.loads((job / "job.json").read_text())
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "job", "slides", "j1", "--mode", "auto")
    assert code == 1 and "only a human" in s["error"]
    code, s, _ = run_tool(capsys, "job", "slides", "j1")  # reading is fine
    assert code == 0 and s["mode"] == "none"


def test_changed_slides_make_the_plan_stale_and_revoke_the_approval(capsys):
    job = make_job()
    (job / "work" / "project.md").write_text("# Plan\n")
    approve_job(job)
    assert is_approved(job)
    _job_slides.set_slides(job, "auto", "zeig die Zahlen", say=lambda _: None)
    assert _job_slides.plan_stale(job) and not is_approved(job) and read_approval(job) is None
    with pytest.raises(Exception, match="other slides"):
        approve_job(job)
    _job_slides.stamp_plan(job)  # a plan run made for these settings
    assert not _job_slides.plan_stale(job)
    approve_job(job)
    assert is_approved(job)


# ---------------------------------------------------------------- import


@pytest.mark.media
@needs_pdf
def test_import_pdf_and_images_in_order(tmp_path, capsys):
    job = make_job()
    pdf = make_pdf(tmp_path / "deck.pdf", [(960, 540, 0), (612, 792, 0), (960, 540, 90)])
    png = make_png(tmp_path / "extra.png", "800x600")
    code, s, _ = run_tool(capsys, "job", "slides-import", "j1", str(pdf), str(png))
    assert code == 0, s
    assert s["pages"] == [f"input/slides/page_{k:02d}.png" for k in range(1, 5)]
    assert [a["from"] for a in s["added"]] == ["deck.pdf"] * 3 + ["extra.png"]
    assert s["mode"] == "upload"  # importing pages switches none -> upload
    sizes = [size_of(job / p) for p in s["pages"]]
    assert sizes[0] == (1920, 1080) and max(sizes[1]) == 1920 and sizes[2] == (1080, 1920)
    assert sizes[3] == (1920, 1440)
    # appended after the pages already there; --replace starts again
    code, s, _ = run_tool(capsys, "job", "slides-import", "j1", str(png))
    assert s["pages"][-1] == "input/slides/page_05.png"
    code, s, _ = run_tool(capsys, "job", "slides-import", "j1", str(png), "--replace")
    assert s["pages"] == ["input/slides/page_01.png"]


@pytest.mark.media
def test_import_refuses_what_is_not_a_slide_and_changes_nothing(tmp_path, capsys, monkeypatch):
    job = make_job(slides={"mode": "auto", "wish": ""})
    good = make_png(tmp_path / "a.png")
    fake = tmp_path / "b.png"
    fake.write_text("<script>alert(1)</script>")
    code, s, _ = run_tool(capsys, "job", "slides-import", "j1", str(good), str(fake))
    assert code == 1 and "not a PNG" in s["error"]
    assert _job_slides.pages(job) == []  # all or nothing
    exe = tmp_path / "c.exe"
    exe.write_bytes(b"MZ")
    code, s, _ = run_tool(capsys, "job", "slides-import", "j1", str(exe))
    assert code == 1 and "PDF or an image" in s["error"]
    code, s, _ = run_tool(capsys, "job", "slides-import", "j1", str(good))
    assert code == 0 and s["mode"] == "both"  # auto + own pages
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "job", "slides-import", "j1", str(good))
    assert code == 1 and "only a human" in s["error"]


@needs_pdf
def test_broken_or_locked_pdf_is_refused(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\n garbage without objects")
    with pytest.raises(_pdf.PdfError, match="not a readable PDF"):
        _pdf.rasterise(bad, tmp_path)


# ---------------------------------------------------------------- plan.json


def plan(**extra) -> dict:
    return {
        "title": "T",
        "format": "9:16",
        "hook": {"text": "h", "src": [0.0, 1.0]},
        "keep_list": [{"n": 1, "src": [0.0, 10.0], "text": "a", "why": "b"}],
        **extra,
    }


STEPS = {
    "id": "s1", "template": "steps",
    "content": {"title": "So geht's", "steps": ["Anmelden", "Auswählen", "Fertig"]},
    "t0": 2.0, "t1": 5.0, "layout": "slide", "why": "the three steps", "source": "auto",
}  # fmt: skip
IMAGE = {"id": "s2", "template": "image", "content": {"asset": "input/slides/page_01.png"},
         "t0": 6.0, "t1": 8.0, "layout": "slide_full", "why": "their own chart",
         "source": "upload"}  # fmt: skip


def ctx(mode, pages=(), slides=None) -> dict:
    return {"mode": mode, "pages": list(pages), "file": {"slides": slides or []}}


def test_plan_slides_valid_in_both_modes():
    slides = [STEPS, IMAGE]
    pages = ["input/slides/page_01.png"]
    assert plan_tool.validate(plan(slides=slides), "9:16", 30.0, False, None,
                              ctx("both", pages, slides)) == []  # fmt: skip
    assert plan_tool.validate(plan(slides=[STEPS]), "9:16", 30.0, False, None,
                              ctx("auto", (), [STEPS])) == []  # fmt: skip


@pytest.mark.parametrize(
    ("slides", "context", "msg"),
    [
        ([STEPS], ctx("none"), "has no slides"),
        ([STEPS], ctx("upload", ["input/slides/page_01.png"], [STEPS]), 'only "image"'),
        ([IMAGE], ctx("auto", (), [IMAGE]), "the mode is auto"),
        ([IMAGE], ctx("upload", [], [IMAGE]), "not an uploaded page"),
        ([{**STEPS, "layout": "pip"}], ctx("auto", (), [STEPS]), "layout"),
        ([{**STEPS, "t0": 12.0, "t1": 14.0}], ctx("auto", (), [STEPS]), "not in the keep list"),
        ([STEPS, {**STEPS, "id": "s3", "t0": 4.0, "t1": 6.0}], None, "one slide at a time"),
        ([STEPS, STEPS], None, "ids must be unique"),
        ([{**STEPS, "content": {"steps": ["nur einer"]}}], None, ".content.steps"),
        ([{**STEPS, "template": "hologram"}], None, ".template"),
        ([{**STEPS, "content": {**STEPS["content"], "extra": 1}}], None, "unknown field"),
        ([{**STEPS, "why": ""}], None, ".why"),
        ([STEPS], ctx("auto", (), []), "is not in work/slides.json"),
        ([STEPS], ctx("auto", (), [{**STEPS, "content": {"steps": ["a", "b"]}}]), "differ"),
        ([STEPS], {"mode": "auto", "pages": [], "file": None}, "missing"),
    ],
)
def test_plan_slides_problems(slides, context, msg):
    errs = plan_tool.validate(plan(slides=slides), "9:16", 30.0, False, None, context)
    assert any(msg in e for e in errs), errs


@pytest.mark.parametrize(
    ("template", "content", "ok"),
    [
        ("title", {"title": "x" * 60}, True),
        ("title", {"title": "x" * 61}, False),
        ("bullets", {"items": ["a", "b", "c", "d", "e"]}, True),
        ("bullets", {"items": ["a", "b", "c", "d", "e", "f"]}, False),
        ("big_number", {"value": "40 %", "label": "weniger Aufwand", "count": True}, True),
        ("big_number", {"value": "1234567890123", "label": "x"}, False),
        (
            "compare",
            {
                "left": {"title": "Vorher", "items": ["a"]},
                "right": {"title": "Nachher", "items": ["b"]},
            },
            True,
        ),  # fmt: skip
        (
            "timeline",
            {"points": [{"label": "2024", "text": "a"}, {"label": "2025", "text": "b"}]},
            True,
        ),  # noqa: E501
        ("bar_chart", {"bars": [{"label": "A", "value": 3}, {"label": "B", "value": "4"}]}, False),
        ("quote", {"text": "x" * 160, "author": "Zentrovia"}, True),
        ("cta", {"title": "Jetzt testen", "url": "example.com"}, True),
        ("image", {"asset": "../../etc/passwd"}, False),
        ("bar_chart", {"bars": [{"label": "A", "value": 3}, {"label": "B", "value": 4}]}, True),
    ],
)
def test_slide_content_limits(template, content, ok):
    assert (slides_tool.content_errors(template, content, "$") == []) is ok


# ---------------------------------------------------------------- run guards


def approved_with_slides(slides_json: list[dict], job_id="j1") -> Path:
    job = make_job(job_id, slides={"mode": "auto", "wish": ""})
    (job / "work" / "project.md").write_text("# Plan\n")
    (job / "work" / "plan.json").write_text(json.dumps(plan(slides=[STEPS])))
    (job / "work" / "slides.json").write_text(json.dumps({"slides": slides_json}))
    approve_job(job)
    edl = {"segments": [{"in": 0.0, "out": 2.0}, {"in": 2.0, "out": 5.0, "slide": "s1",
                                                   "layout": "slide"}]}  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    return job


def test_cut_run_must_keep_the_approved_slides():
    assert pipeline.slides_kept(approved_with_slides([STEPS])) is None
    changed = {**STEPS, "content": {"steps": ["Mehr", "Umsatz", "garantiert"]}}
    job = approved_with_slides([changed], "j2")
    assert "differs from the approved plan" in pipeline.slides_kept(job)
    edl = json.loads((job / "work" / "edl.json").read_text())
    edl["segments"][1]["slide"] = "s9"
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    assert "not in the approved plan" in pipeline.slides_kept(job)


def test_prompt_names_the_slides():
    job = make_job(slides={"mode": "both", "wish": "zeig den Ablauf"})
    note = pipeline.format_note(job)
    assert "mode both, 0 uploaded page(s) and a wish from the human" in note
    assert "zeig den Ablauf" not in note  # the wish is read from job show, not the prompt
    assert "Slides" not in pipeline.format_note(make_job("j2"))


# ---------------------------------------------------------------- sheet slides


@pytest.mark.media
def test_sheet_slides_numbers_the_pages(tmp_path, capsys):
    job = make_job()
    png = make_png(tmp_path / "a.png", "640x360")
    run_tool(capsys, "job", "slides-import", "j1", str(png), str(png))
    code, s, _ = run_tool(capsys, "sheet", "slides", "--job", str(job))
    assert code == 0 and s["sheets"] == ["work/sheets/slides_01.jpg"]
    assert [p["page"] for p in s["pages"]] == _job_slides.pages(job)
    assert (job / "work" / "sheets" / "slides_01.jpg").is_file()


def test_skills_cover_slides():
    from videocut import agent

    skill = {n: (agent.PLUGIN / "skills" / n / "SKILL.md").read_text()
             for n in ("plan", "cut", "overlays", "revise")}  # fmt: skip
    plan = skill["plan"]
    assert "## Slides" in plan and "never invent" in plan.lower()
    for cmd in ("slides check", "slides stills", "sheet slides"):
        assert f"videocut tool {cmd}" in plan
    for tpl in slides_tool.TEMPLATES:
        assert f"`{tpl}`" in plan
    for layout in SLIDE_LAYOUTS:
        assert f"`{layout}`" in plan and f"`{layout}`" in skill["cut"]
    assert "videocut tool slides render" in skill["cut"] and '"reveal"' in skill["cut"]
    assert "work/slides.json" in skill["overlays"] and "slides render" in skill["revise"]


def test_views_never_take_a_slide_layout():
    view = {"n": 1, "src": [0.0, 2.0], "layout": "slide", "why": "x"}
    errs = plan_tool.validate(plan(views=[view]), "9:16", 30.0, False)
    assert any("slide layout; slides go in $.slides" in e for e in errs), errs
    face = {**view, "layout": "face"}
    assert plan_tool.validate(plan(views=[face]), "9:16", 30.0, False) == []
