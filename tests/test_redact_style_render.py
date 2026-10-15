"""Redaction styles rendered end to end on the generated layout material
(test_layouts_render): a full-width band at the top of the screen is cut off ("auto"),
verify passes on it and catches a reel that does not match the style."""

import json

import numpy as np
import pytest
from helpers import run_tool
from test_layouts_render import (  # noqa: F401 (the module fixture)
    BIT,
    BIT_Y,
    SH,
    SW,
    _frames,
    _job,
    _starts,
    material,
)

from videocut.edl import from_dict
from videocut.tools import _layouts as lay
from videocut.tools import _verify_screen

pytestmark = pytest.mark.media

BAND = [0.0, 0.0, 1.0, 0.15]  # the frame counter strip at the top (y 40-200 of 1800)
SEGMENTS = [
    {"in": 0.3, "out": 1.0},
    {"in": 1.0, "out": 2.5, "layout": "screen",
     "redact": [{"t0": 1.0, "t1": 2.5, "box": BAND}]},
    {"in": 2.5, "out": 4.0, "layout": "pip",
     "screen_zoom": [{"t": 3.3, "box": [0.5, 0.45, 0.9, 0.85]}],
     "redact": [{"t0": 2.6, "t1": 3.0, "box": BAND}]},
]  # fmt: skip


def _crop_job(capsys, material, job_id, style):  # noqa: F811
    job = _job(capsys, material, job_id, redact_style=style)
    edl = json.loads((job / "work" / "edl.json").read_text())
    edl["segments"] = SEGMENTS
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    from videocut.tools.job import approve_job

    approve_job(job)
    return job


def _drawn(i: int):
    edl = from_dict({"fps": 30, "size": [1080, 1920], "source": "input/a.mp4",
                     "segments": [SEGMENTS[i]]})  # fmt: skip
    n = round((SEGMENTS[i]["out"] - SEGMENTS[i]["in"]) * 30)
    from fractions import Fraction

    from test_layouts_render import OFFSET

    return lay.plan_segment(
        edl.segments[0], n, Fraction(30), offset=OFFSET, screen=(SW, SH), camera=(1920, 1080),
        out=(1080, 1920), full_out=(1080, 1920), face=None, redact_style="auto",
    )[0]  # fmt: skip


def test_auto_cuts_off_a_top_band_and_verify_passes(capsys, material):  # noqa: F811
    job = _crop_job(capsys, material, "crop-auto", "auto")
    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, (s, out)
    blocks = s["redaction"]["blocks"]
    assert s["redaction"]["style"] == "auto"
    assert [(b["segment"], b["style"], b.get("edge")) for b in blocks] == [
        (1, "crop", "top"), (2, "crop", "top")]  # fmt: skip
    reel = job / "out" / "reel.mp4"
    starts = _starts()[:4]
    for i in (1, 2):
        seg = _drawn(i)
        assert seg.origin == (0, 270) and seg.screen == (SW, SH - 270)
        v = seg.view
        # the counter (white blocks at the top of the recording) is nowhere in the view,
        # in any frame of the segment: the band is cut off for the whole segment
        assert BIT_Y + BIT <= seg.origin[1]
        imgs = _frames(reel, starts[i], starts[i + 1], (v.w, v.h, v.x, v.y))
        # the view starts below the band: the dark UI background (grey ~50), not the
        # counter strip (black with white bits)
        top = imgs[: 5 if seg.moves else None, :20]  # (before the zoom moves in)
        assert top.min() > 30 and top.max() < 80, (i, top.min(), top.max())
    # 9:16 screen layout: refitted to the cropped aspect, centred, no bars inside the view
    seg = _drawn(1)
    assert abs(seg.view.w / seg.view.h - SW / (SH - 270)) < 0.01

    code, s, out = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, out
    assert s["screen_flagged"] == [], out
    doc = json.loads((job / "out" / "verify.json").read_text())
    crops = [d for d in doc["screen"]["redaction"] if d.get("style") == "crop"]
    assert [d["segment"] for d in crops] == [1, 2] and all(
        d["diff"] + 3 <= d["diff_uncropped"] or d["diff"] <= 4 for d in crops
    )


def test_verify_catches_a_reel_drawn_in_another_style(capsys, material):  # noqa: F811
    job = _crop_job(capsys, material, "crop-mismatch", "blur")
    code, s, out = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, (s, out)
    assert {b["style"] for b in s["redaction"]["blocks"]} == {"blur"}
    # the reel blurred the band; the job now says auto (crop): verify sees the band
    meta = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**meta, "redact_style": "auto"}))
    code, s, out = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and not s["ok"]
    got = {(d["check"], d["segment"]) for d in s["screen_flagged"] if
           "redaction_missing" in d["flags"]}  # fmt: skip
    assert ("redaction", 1) in got, out
    ctx = _verify_screen.context(job, json.loads((job / "work" / "edl.json").read_text()),
                                 (540, 960))  # fmt: skip
    assert all(np.isfinite(s.view.w) for s in ctx["segs"].values())
