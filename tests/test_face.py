"""Face track (step 4): blink intervals, the follow rule, blink warnings in `edl check` and
`blink_at_cut` in verify on synthetic face.json, and the real tool on a generated clip
(a test pattern: no face, so everything falls back to the centre)."""

import json
import os
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut.edl import blink_warnings, from_dict
from videocut.tools import face, verify
from videocut.tools.job import approve_job

# Captured at import, before the autouse fixture points VIDEOCUT_HOME at a temp folder.
REAL_MODEL = Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut")) / "models" / face.MODEL
FPS = 30


def _frames(blinks: list[float | None], fps: float = FPS) -> list[dict]:
    return [
        {"t": round(i / fps, 3), "found": b is not None, "blink": b} for i, b in enumerate(blinks)
    ]


def _edl(segments, fps=FPS):
    return from_dict({"fps": fps, "size": [1080, 1920], "source": "input/a.mp4",
                      "segments": segments})  # fmt: skip


# ---------------------------------------------------------------- blink intervals


def test_blink_intervals_use_hysteresis():
    # opens at >= 0.5, stays closed while >= 0.35: one blink despite the dip to 0.4
    scores = [0.1, 0.2, 0.6, 0.4, 0.7, 0.36, 0.2, 0.1]
    assert face.blink_intervals(_frames(scores), FPS) == [{"t0": 0.067, "t1": 0.2}]
    # a score that never reaches BLINK_ON is no blink
    assert face.blink_intervals(_frames([0.1, 0.45, 0.48, 0.1]), FPS) == []


def test_blink_intervals_split_end_and_drop_long_closures():
    # two blinks; the second runs to the end of the clip
    scores = [0.8, 0.8, 0.1, 0.1, 0.9, 0.9]
    assert face.blink_intervals(_frames(scores), FPS) == [
        {"t0": 0.0, "t1": 0.067},
        {"t0": 0.133, "t1": 0.2},
    ]
    # a lost face ends a blink
    assert face.blink_intervals(_frames([0.9, None, 0.1]), FPS) == [{"t0": 0.0, "t1": 0.033}]
    # eyes closed for a second (looking down): not a blink
    assert face.blink_intervals(_frames([0.1] + [0.9] * 30 + [0.1]), FPS) == []


def test_gaze_off():
    assert face.gaze_off({}) == 0.0
    assert face.gaze_off({"eyeLookOutLeft": 0.6, "eyeLookInRight": 0.6}) == pytest.approx(0.6)
    assert face.gaze_off({"eyeLookDownLeft": 1.0, "eyeLookDownRight": 1.0}) == 1.0


# ---------------------------------------------------------------- follow rule


def _face_doc(centres: list[tuple[float, float, float]]) -> dict:
    """face.json with one found frame per (t, cx, cy)."""
    frames = [
        {"t": t, "found": True, "box": [cx - 0.1, cy - 0.1, cx + 0.1, cy + 0.1], "cx": cx,
         "cy": cy, "blink": 0.1, "gaze_off": 0.1}
        for t, cx, cy in centres
    ]  # fmt: skip
    return {"frames": frames, "median": face.median_of(frames), "blinks": []}


def test_crop_follows_only_large_moves():
    # 0..10 s at (0.45, 0.40), 10..12 s slightly off (+0.05), 12..14 s far off (+0.2)
    pts = [(t / 2, 0.45, 0.40) for t in range(20)]
    pts += [(10 + t / 2, 0.50, 0.40) for t in range(4)]
    pts += [(12 + t / 2, 0.65, 0.40) for t in range(4)]
    doc = _face_doc(pts)
    assert (doc["median"]["cx"], doc["median"]["cy"]) == (0.45, 0.40)
    assert face.segment_centre(doc, 0.0, 9.5) == (0.45, 0.40)
    assert face.segment_centre(doc, 10.0, 11.5) == (0.45, 0.40)  # 5 % < 8 %: fixed crop
    assert face.segment_centre(doc, 12.0, 13.5) == (0.65, 0.40)  # 20 %: follow
    assert face.segment_centre(doc, 20.0, 21.0) == (0.45, 0.40)  # no face here: global

    edl = _edl([
        {"in": 1.0, "out": 3.0},
        {"in": 12.0, "out": 13.5},
        {"in": 4.0, "out": 5.0, "crop": {"cx": 0.3, "cy": 0.6, "zoom": 1.2}},
    ])  # fmt: skip
    framed, followed = face.face_crops(edl, doc)
    assert followed == 1
    assert [(s.crop.cx, s.crop.cy, s.crop.zoom) for s in framed.segments] == [
        (0.45, 0.40, 1.0),
        (0.65, 0.40, 1.0),
        (0.3, 0.6, 1.2),  # an explicit crop always wins
    ]
    assert edl.segments[0].crop is None  # the input EDL is not changed
    # verify sees the follow as a reframe
    assert verify.reframes(framed) == [True, True]


def test_no_face_keeps_the_centre():
    edl = _edl([{"in": 1.0, "out": 3.0}])
    doc = {"frames": _frames([None, None]), "median": None, "blinks": []}
    assert face.face_crops(edl, doc) == (edl, 0)
    assert face.face_crops(edl, None) == (edl, 0)


# ---------------------------------------------------------------- blink warnings


BLINKS = [{"t0": 1.0, "t1": 1.1}]
WORDS = [{"w": "eins", "t0": 0.5, "t1": 0.9}, {"w": "zwei", "t0": 1.12, "t1": 1.6}]


def test_blink_warning_suggests_the_nearest_free_time_outside_words():
    edl = _edl([{"in": 1.08, "out": 3.0}, {"in": 4.0, "out": 5.0}])
    w = blink_warnings(edl, BLINKS, WORDS)
    assert len(w) == 1
    # blocked: the blink +-1 frame (0.967..1.133) and the words (0.5..0.9, 1.12..1.6)
    assert w[0] == {"path": "$.segments[0].in", "t": 1.08, "blink": [1.0, 1.1], "suggest": 0.965}
    # without words the nearer time right after the blink wins
    assert blink_warnings(edl, BLINKS)[0]["suggest"] == 1.135
    # +-1 frame: a cut one frame after the blink still warns, two frames after does not
    assert blink_warnings(_edl([{"in": 1.13, "out": 3.0}]), BLINKS)
    assert not blink_warnings(_edl([{"in": 1.17, "out": 3.0}]), BLINKS)


def test_blink_warning_on_out_and_without_a_suggestion():
    edl = _edl([{"in": 0.0, "out": 1.08}])
    w = blink_warnings(edl, BLINKS, WORDS)
    assert w[0]["path"] == "$.segments[0].out" and w[0]["suggest"] == 0.965
    # words everywhere around the blink: no suggestion, still a warning
    busy = [{"w": "lang", "t0": 0.0, "t1": 3.0}]
    assert blink_warnings(edl, BLINKS, busy)[0]["suggest"] is None


def test_edl_check_reports_blinks_as_warnings(capsys, tmp_path):
    v = tmp_path / "take.mov"
    v.write_bytes(b"x")
    _, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "f1")
    job = Path(s["job"])
    src = s["source"]
    os.utime(job / src, (1, 1))  # face.json below must be newer than the source
    edl = {"fps": 30, "size": [1080, 1920], "source": src,
           "segments": [{"in": 1.05, "out": 3.0}, {"in": 4.0, "out": 5.0}]}  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "words.json").write_text(json.dumps({"words": WORDS}))
    doc = {"version": face.FACE_VERSION, "source": src, "frames": [], "median": None,
           "blinks": BLINKS}  # fmt: skip
    (job / "work" / "face.json").write_text(json.dumps(doc))
    code, s, out = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0 and s["ok"]
    assert s["warnings"] == ["$.segments[0].in: 1.05 falls in a blink 1.0-1.1; try 0.965"]
    assert "warning: $.segments[0].in" in out
    # a face.json of another source is ignored
    (job / "work" / "face.json").write_text(json.dumps({**doc, "source": "input/other.mov"}))
    assert run_tool(capsys, "edl", "check", "--job", str(job))[1]["warnings"] == []


# ---------------------------------------------------------------- verify


def test_blink_at_cut_maps_output_frames_to_source():
    edl = _edl([
        {"in": 0.0, "out": 1.05},  # its last two frames (0.983, 1.017): the second blinks
        {"in": 2.0, "out": 3.0},  # clean on both sides
        {"in": 4.0, "out": 5.0},
    ])  # fmt: skip
    assert verify.blinks_at_cuts(edl, BLINKS) == [True, False]
    quiet = {"click_peak": 1.0, "hf_spike_db": 0.0}
    vis = {"diff_cut": 1.0, "diff_around": 1.0, "jump_score": 1.0}
    assert verify.cut_notes(vis, quiet, None, blink=True) == (["blink_at_cut"], [])


# ---------------------------------------------------------------- the tool (generated clip)


@pytest.mark.media
@pytest.mark.skipif(not REAL_MODEL.is_file(), reason="face model not installed")
def test_face_tool_on_a_clip_without_a_face(capsys, portrait_clip, videocut_home):
    (videocut_home / "models").mkdir(parents=True)
    (videocut_home / "models" / face.MODEL).symlink_to(REAL_MODEL)
    code, s, _ = run_tool(capsys, "job", "new", str(portrait_clip), "--id", "nf")
    job = Path(s["job"])
    code, s, out = run_tool(capsys, "face", "--job", str(job))
    assert code == 0, s
    assert s["found_pct"] == 0.0 and s["blinks"] == 0 and s["median"] is None
    assert s["frames"] >= clips.FPS * 5 and s["fps_sampled"] == clips.FPS
    doc = json.loads((job / "work" / "face.json").read_text())
    assert doc["size"] == [480, 854]
    assert all(f["found"] is False and f["box"] is None for f in doc["frames"])
    assert doc["blinks"] == [] and doc["median"] is None
    assert "face in 0.0 %" in out
    # a second run keeps the fresh file
    assert run_tool(capsys, "face", "--job", str(job))[1]["skipped"] is True

    edl = {"fps": 30, "size": [1080, 1920], "source": json.loads(
        (job / "job.json").read_text())["source"],
        "segments": [{"in": 0.3, "out": 1.0}, {"in": 1.9, "out": 2.6}]}  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0 and s["warnings"] == []
    (job / "work" / "project.md").write_text("# Plan: face test\n")
    approve_job(job)
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 0, s
    assert s["default_crop"] == "centre" and s["face_follow_segments"] == 0
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and not any("blink_at_cut" in c["flags"] for c in s["flagged"])


def test_face_tool_needs_the_model(capsys, tmp_path):
    v = tmp_path / "take.mov"
    v.write_bytes(b"x")
    _, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "nm")
    code, s, _ = run_tool(capsys, "face", "--job", s["job"])
    assert code == 1 and "face model missing" in s["error"]


@pytest.mark.media
def test_render_and_verify_use_face_json(capsys, landscape_clip):
    from videocut.tools._render_plan import crop_window

    code, s, _ = run_tool(capsys, "job", "new", str(landscape_clip), "--id", "fj")
    job, src = Path(s["job"]), s["source"]
    segs = [{"in": 0.3, "out": 1.0}, {"in": 1.9, "out": 2.6}]
    edl = {"fps": 30, "size": [1080, 1920], "source": src, "segments": segs}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    doc = _face_doc([(t / 10, 0.3, 0.5) for t in range(40)])
    doc |= {"version": face.FACE_VERSION, "source": src, "blinks": [{"t0": 0.95, "t1": 1.05}]}
    (job / "work" / "face.json").write_text(json.dumps(doc))
    (job / "work" / "project.md").write_text("# Plan: face test\n")
    approve_job(job)
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0 and s["warnings"][0].startswith("$.segments[0].out: 1.0 falls in a blink")
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 0 and s["default_crop"] == "face", s
    w = crop_window((1920, 1080), tuple(s["size"]), 0.3, 0.5, 1.0)
    assert f"crop={w.w}:{w.h}:{w.x}:{w.y}" in (job / "work" / "render_filter.txt").read_text()
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and s["flagged"][0]["flags"][-1] == "blink_at_cut", s
