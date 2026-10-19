"""B-roll (EDL layout broll): the EDL shape, the job-aware `edl check` rules (only the human's
choice, inside the wish span, not over the hook, at most 40 %), the geometry and timing
helpers, verify's broll flags, `sheet material`, and a real render of a generated portrait
clip with a chosen image (push-in) and a chosen clip (looped)."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from clips import ffmpeg
from helpers import run_tool

from videocut.edl import from_dict, load, validate
from videocut.paths import tool_env
from videocut.tools import _layouts as lay
from videocut.tools import _verify_broll
from videocut.tools import material as material_mod
from videocut.tools import verify as verify_mod
from videocut.tools.job import approve_job

SRC = "input/clip-portrait.mp4"


def _seg(a, b, **kw):
    return {"in": a, "out": b, **kw}


def _edl(segs, size=(1080, 1920)):
    return {"fps": 30, "size": list(size), "source": SRC, "segments": segs}


# ------------------------------------------------------------------ EDL shape


def test_broll_needs_a_wish_number():
    assert validate(_edl([_seg(0, 2), _seg(2, 4, layout="broll", material=1)])) == []
    errs = validate(_edl([_seg(0, 2, layout="broll")]))
    assert any("material" in e and "wish" in e for e in errs), errs
    errs = validate(_edl([_seg(0, 2, layout="broll", material=0)]))
    assert errs
    errs = validate(_edl([_seg(0, 2, material=1)]))
    assert any("needs layout broll" in e for e in errs), errs


def test_broll_round_trip():
    edl = from_dict(_edl([_seg(0, 2), _seg(2, 4, layout="broll", material=3)]))
    assert edl.segments[1].material == 3
    assert edl.to_dict()["segments"][1] == {"in": 2, "out": 4, "fade_ms": 30,
                                            "layout": "broll", "material": 3}  # fmt: skip


# ------------------------------------------------------------------ geometry and timing


def test_broll_crop_covers_the_frame():
    w = lay.broll_crop((1920, 1080), (1080, 1920))
    assert (w.w, w.h) == (608, 1080) and w.x == (1920 - 608) // 2 and w.y == 0
    w = lay.broll_crop((1080, 1920), (1920, 1080))
    assert w.w == 1080 and w.h == 608 and w.y == (1920 - 608) // 2
    w = lay.broll_crop((3000, 3000), (1080, 1920))
    assert abs(w.w / w.h - 1080 / 1920) < 0.01 and w.h == 3000


def test_broll_timing():
    fps = Fraction(30)
    assert lay.broll_timing(60, "image", None, fps) == (60, False)
    assert lay.broll_timing(60, "video", 5.0, fps) == (60, False)  # trimmed
    assert lay.broll_timing(60, "video", 1.6, fps) == (60, True)  # 0.4 s short: looped
    assert lay.broll_timing(90, "video", 1.0, fps) == (30, False)  # 2 s short: ends


def test_compose_broll_filters():
    seg = lay.BrollSeg(frames=60, wish=1, candidate="c1", kind="image", file=Path("x.png"),
                       size=(1600, 1200), shown=60)  # fmt: skip
    lines = lay.compose_broll(3, seg, Fraction(30), (1080, 1920), material_label="4:v:0",
                              camera=None, matrix="bt709")  # fmt: skip
    text = "".join(lines)
    assert "zoompan" in text and "crop=676:1200:462:0" in text and text.endswith("[vc3];")
    assert lay.broll_inputs(seg, Fraction(30))[:2] == ["-loop", "1"]
    vid = lay.BrollSeg(frames=90, wish=2, candidate="c2", kind="video", file=Path("x.mp4"),
                       size=(1920, 1080), shown=30)  # fmt: skip
    lines = lay.compose_broll(1, vid, Fraction(30), (1080, 1920), material_label="5:v:0",
                              camera="[c1]trim=start=1", matrix="bt709")  # fmt: skip
    assert any("eof_action=pass" in ln for ln in lines)
    assert lines[1].startswith("[c1]trim=start=1[cb1]")
    looped = lay.BrollSeg(frames=60, wish=2, candidate="c2", kind="video",
                          file=Path("x.mp4"), size=(1920, 1080), shown=60, loop=True)  # fmt: skip
    assert lay.broll_inputs(looped, Fraction(30))[:2] == ["-stream_loop", "-1"]


# ------------------------------------------------------------------ verify flags


def test_verify_flags():
    rng = np.random.default_rng(1)
    busy = rng.integers(40, 220, size=(480, 270)).astype(np.float32)
    assert _verify_broll.flags_of(busy) == []
    assert _verify_broll.flags_of(np.full((480, 270), 16, np.float32)) == ["broll_black"]
    bars = busy.copy()
    bars[:60] = 16
    bars[-60:] = 16
    assert _verify_broll.flags_of(bars) == ["broll_aspect"]


# ------------------------------------------------------------------ a job with material


def _ff(*args):
    subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
                   check=True, env=tool_env())  # fmt: skip


WISHES = [
    {"n": 1, "kind": "still", "src_from": 3.0, "src_to": 5.0, "query": "calm office desk morning",
     "prompt": "A calm desk by a window", "style": "photo", "why": "shows the place"},
    {"n": 2, "kind": "broll", "src_from": 5.0, "src_to": 7.0, "query": "city street walking people",
     "prompt": None, "style": "photo", "why": "movement"},
]  # fmt: skip


def _material_job(capsys, monkeypatch, clip: Path, job_id="broll", black=False,
                  image: str | None = None) -> Path:  # fmt: skip
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    meta = json.loads((job / "job.json").read_text())
    meta |= {"format": "9:16", "material": {"mode": "both", "wish": ""}}
    (job / "job.json").write_text(json.dumps(meta))
    plan = {"title": "t", "format": "9:16", "hook": {"text": "x", "src": [0, 2]},
            "keep_list": [{"n": 1, "src": [0, 9], "text": "all", "why": "all"}],
            "material_wishes": WISHES}  # fmt: skip
    (job / "work" / "plan.json").write_text(json.dumps(plan))
    (job / "work" / "project.md").write_text("# Plan: broll test\n")
    approve_job(job)
    folder = job / "work" / "material"
    (folder / "1").mkdir(parents=True)
    (folder / "2").mkdir(parents=True)
    src = image or ("color=c=black:s=1600x1200" if black else "mandelbrot=size=1600x1200")
    _ff("-f", "lavfi", "-i", src, "-frames:v", "1", str(folder / "1" / "c1.png"))
    _ff("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=1.4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder / "2" / "c1.mp4"))  # fmt: skip
    for n, f in ((1, "c1.png"), (2, "c1.mp4")):
        _ff("-i", str(folder / str(n) / f), "-frames:v", "1", "-vf", "scale=240:-2",
            str(folder / str(n) / "c1_sm.jpg"))  # fmt: skip

    def cand(n, file, kind, w, h, dur, ai):
        return {"id": "c1", "kind": kind, "file": f"work/material/{n}/{file}",
                "thumb": f"work/material/{n}/c1_sm.jpg", "width": w, "height": h,
                "duration_s": dur, "source": "fake" if ai else "pexels", "ai_generated": ai,
                "licence": "generated" if ai else "Pexels License", "attribution": "x",
                "hdr": False,
                "sha256": material_mod._sha(job / "work" / "material" / str(n) / file)}  # fmt: skip

    data = {"version": 1, "wishes": [
        {**WISHES[0], "candidates": [cand(1, "c1.png", "image", 1600, 1200, None, True)]},
        {**WISHES[1], "candidates": [cand(2, "c1.mp4", "video", 1280, 720, 1.4, False)]},
    ]}  # fmt: skip
    material_mod.save(job, data)  # what a fetch stores (trusted copy + mirror)
    material_mod.choose(job, 1, "c1", say=lambda _: None)
    material_mod.choose(job, 2, "c1", say=lambda _: None)
    return job


def _write_edl(job, segs):
    (job / "work" / "edl.json").write_text(json.dumps(_edl(segs)))


media = pytest.mark.media


@media
def test_edl_check_rules(capsys, monkeypatch, portrait_clip):
    job = _material_job(capsys, monkeypatch, portrait_clip)
    good = [_seg(0, 3), _seg(3, 5, layout="broll", material=1),
            _seg(5, 6.5, layout="broll", material=2), _seg(6.5, 9)]  # fmt: skip
    _write_edl(job, good)
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0, s
    # over the hook, outside its span, a wish nobody chose, more than 40 %
    _write_edl(job, [_seg(3, 5, layout="broll", material=1), _seg(0, 3)])
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "hook" in s["error"], s
    _write_edl(job, [_seg(0, 3), _seg(7, 8, layout="broll", material=1), _seg(3, 9)])
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "outside wish 1" in s["error"], s
    _write_edl(job, [_seg(0, 3), _seg(3, 5, layout="broll", material=7), _seg(5, 9)])
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "not chosen by the human" in s["error"], s
    _write_edl(job, [_seg(0, 2), _seg(3, 5, layout="broll", material=1),
                     _seg(5, 7, layout="broll", material=2), _seg(7, 8)])  # fmt: skip
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "at most 40 %" in s["error"], s
    # "none" for a wish: not usable either
    material_mod.choose(job, 2, None, say=lambda _: None)
    _write_edl(job, good)
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "wish 2 is not chosen" in s["error"], s


@media
def test_replaced_file_is_not_the_choice(capsys, monkeypatch, portrait_clip):
    job = _material_job(capsys, monkeypatch, portrait_clip, "broll-swap")
    _ff("-f", "lavfi", "-i", "color=c=red:s=1600x1200", "-frames:v", "1",
        str(job / "work" / "material" / "1" / "c1.png"))  # fmt: skip
    assert 1 not in material_mod.chosen(job)
    _write_edl(job, [_seg(0, 3), _seg(3, 5, layout="broll", material=1), _seg(5, 9)])
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 1 and "no candidate chosen by the human" in s["error"], s


@media
def test_sheet_material(capsys, monkeypatch, portrait_clip):
    job = _material_job(capsys, monkeypatch, portrait_clip, "broll-sheet")
    code, s, _ = run_tool(capsys, "sheet", "material", "--job", str(job))
    assert code == 0, s
    assert s["sheets"] == ["work/sheets/material_01.jpg"]
    assert [(c["wish"], c["id"], c["ai_generated"], c["chosen"]) for c in s["candidates"]] == [
        (1, "c1", True, True), (2, "c1", False, True)]  # fmt: skip
    assert (job / s["sheets"][0]).is_file()


def _gray(path: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", "scale=270:480,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True, env=tool_env(),
    ).stdout  # fmt: skip
    return np.frombuffer(raw, np.uint8).reshape(480, 270).astype(float)


@media
def test_render_and_verify_broll(capsys, monkeypatch, portrait_clip):
    job = _material_job(capsys, monkeypatch, portrait_clip, "broll-render")
    segs = [_seg(0, 3), _seg(3, 4.6, layout="broll", material=1),
            _seg(5, 6.7, layout="broll", material=2), _seg(7, 9.3)]  # fmt: skip
    _write_edl(job, segs)
    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, s
    reel = job / "out" / "reel.mp4"
    render = json.loads((job / "out" / "render.json").read_text())
    assert [(b["wish"], b["kind"], b["ai_generated"], b["out_t0"], b["out_t1"], b["looped"])
            for b in render["broll"]] == [(1, "image", True, 3.0, 4.6, False),
                                          (2, "video", False, 4.6, 6.3, True)]  # fmt: skip
    assert abs(render["output_duration"] - 8.6) < 0.05
    face, image0, image1, clip = (_gray(reel, t) for t in (2.5, 3.1, 4.5, 6.1))
    assert np.abs(face - image0).mean() > 10, "the b-roll image replaces the camera"
    assert np.abs(image0 - image1).mean() > 1, "the image is pushed in"
    assert np.abs(clip - face).mean() > 10
    for t in (3.8, 5.5):
        assert _verify_broll.flags_of(_gray(reel, t)) == [], t
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    doc = json.loads((job / "out" / "verify.json").read_text())
    assert [d["flags"] for d in doc["broll"]] == [[], []], doc["broll"]
    assert doc["checks"]["broll"]["ok"]
    assert doc["checks"]["black_or_frozen"]["ok"], doc["checks"]["black_or_frozen"]


@media
def test_a_smooth_broll_image_is_not_a_frozen_picture(capsys, monkeypatch, portrait_clip):
    # a soft gradient under the slow push-in barely changes: freezedetect calls it frozen
    smooth = "gradients=s=1600x1200:c0=0x2a6f97:c1=0x61a5c2:x0=0:y0=0:x1=1600:y1=1200"
    job = _material_job(capsys, monkeypatch, portrait_clip, "broll-smooth", image=smooth)
    _write_edl(job, [_seg(0, 3), _seg(3, 6, layout="broll", material=1), _seg(6, 9.3)])
    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, s
    run_tool(capsys, "verify", "--job", str(job))
    doc = json.loads((job / "out" / "verify.json").read_text())
    assert doc["checks"]["black_or_frozen"]["ok"], doc["checks"]["black_or_frozen"]
    # the exempt span comes from the human's trusted choice and the EDL, not render.json
    edl = load(job / "work" / "edl.json")
    assert verify_mod.broll_image_spans(job, edl) == [(3.0, 6.0)]
    material_mod.choose(job, 1, None, say=lambda _: None)
    assert verify_mod.broll_image_spans(job, edl) == []


@media
def test_verify_flags_a_black_broll(capsys, monkeypatch, portrait_clip):
    job = _material_job(capsys, monkeypatch, portrait_clip, "broll-black", black=True)
    _write_edl(job, [_seg(0, 3), _seg(3, 5, layout="broll", material=1), _seg(5, 9)])
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    doc = json.loads((job / "out" / "verify.json").read_text())
    assert doc["broll"][0]["flags"] == ["broll_black"]
    assert not doc["checks"]["broll"]["ok"] and not doc["ok"]


def test_overlay_views_put_captions_low_over_broll():
    from videocut.tools import _render_plan as plan_mod
    from videocut.tools.overlays import broll_views

    edl = from_dict(_edl([_seg(0, 3), _seg(3, 5, layout="broll", material=2), _seg(5, 6)]))
    plan = plan_mod.build(edl, (1080, 1920), (1080, 1920), [s.layout for s in edl.segments])
    assert broll_views(plan, edl) == [{"t0": 3.0, "t1": 5.0, "layout": "broll", "material": 2,
                                       "screen": None, "speaker": None,
                                       "band": "lower"}]  # fmt: skip


@media
def test_overlays_build_with_broll(capsys, monkeypatch, portrait_clip):
    from videocut.tools import overlays

    job = _material_job(capsys, monkeypatch, portrait_clip)
    _write_edl(job, [_seg(0, 3), _seg(3, 5, layout="broll", material=1), _seg(5, 9)])
    doc = overlays.build(job)
    broll = [v for v in doc["views"] if v.get("layout") == "broll"]
    assert broll and broll[0]["band"] == "lower" and broll[0]["speaker"] is None
