"""Face track (step 4): blink intervals, the follow rule, blink warnings in `edl check` and
`blink_at_cut` in verify on synthetic face.json, and the real tool on a generated clip
(a test pattern: no face, so everything falls back to the centre)."""

import json
import os
from pathlib import Path

import clips
import numpy as np
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
    pts += [(10 + t / 4, 0.50, 0.40) for t in range(8)]
    pts += [(12 + t / 4, 0.65, 0.40) for t in range(8)]
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
    assert code == 0 and "blink_at_cut" in s["flagged"][0]["flags"], s


@pytest.mark.parametrize(
    "bad",
    [
        {"blinks": [1]},
        {"blinks": None},
        {"blinks": [{"t0": 1.0}]},
        {"median": 5},
        {"median": {"cx": "a", "cy": 0.5, "box": [0, 0, 1, 1]}},
        {"median": {"cx": float("nan"), "cy": 0.5, "box": [0, 0, 1, 1]}},
        {"frames": [None]},
        {"frames": [{"t": 0.0, "found": True, "cx": 0.5, "cy": 0.5, "box": None}]},
    ],
)
def test_malformed_face_json_counts_as_missing(tmp_path, bad):
    job = tmp_path / "job"
    (job / "work").mkdir(parents=True)
    (job / "input").mkdir()
    (job / "input" / "a.mp4").write_bytes(b"x")
    os.utime(job / "input" / "a.mp4", (1, 1))
    good = {"version": face.FACE_VERSION, "source": "input/a.mp4", "fps_sampled": 30,
            "frames": [], "median": None, "blinks": []}  # fmt: skip
    (job / "work" / "face.json").write_text(json.dumps(good))
    assert face.load_face(job, "input/a.mp4") == good
    (job / "work" / "face.json").write_text(json.dumps({**good, **bad}))
    assert face.load_face(job, "input/a.mp4") is None


def test_a_short_segment_never_follows_a_single_frame():
    doc = _face_doc([(t / 2, 0.45, 0.40) for t in range(20)] + [(10.0, 0.9, 0.4)])
    assert face.segment_centre(doc, 9.99, 10.01) == (0.45, 0.40)


# ---------------------------------------------------------------- small faces (video calls)

# A generated video-call frame (1920x1080): a screen share on the left, the speaker as a
# small tile on the right. The "face" is a flat skin-coloured target the fake detector
# below finds only when it is at least SEEN of its input's height high (like a real
# detector, which misses faces this small in the whole frame).
CALL = (1920, 1080)
TILE = (1400, 380, 1880, 704)  # x0, y0, x1, y1 of the speaker tile, px
TARGET = (1610, 470, 1670, 550)  # the face target inside it, px
SKIN = (224, 172, 140)
SEEN = 0.15


def _call_frame(dx: int = 0) -> np.ndarray:
    img = np.full((CALL[1], CALL[0], 3), 40, np.uint8)
    img[60:1020, 60:1340] = 200  # the shared screen
    for i in range(10):
        img[120 + i * 80 : 150 + i * 80, 120:1200] = 120  # rows of "UI"
    img[TILE[1] : TILE[3], TILE[0] : TILE[2]] = (70, 90, 110)  # the camera tile
    x0, y0, x1, y1 = TARGET
    img[y0:y1, x0 + dx : x1 + dx] = SKIN
    return img


def _fake_find(calls: list):
    def find(img):
        calls.append(img.shape)
        ys, xs = np.nonzero(np.all(np.abs(img.astype(int) - SKIN) <= 12, axis=2))
        if len(ys) == 0 or (ys.max() - ys.min() + 1) / img.shape[0] < SEEN:
            return None
        h, w = img.shape[:2]
        box = [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]
        return {"box": box, "blink": 0.1, "gaze_off": 0.2}

    return find


def test_fake_detector_misses_the_small_face_in_the_whole_frame():
    calls = []
    find = _fake_find(calls)
    assert find(face._resize(_call_frame(), 854, 480)) is None  # the full-frame pass size


def test_small_pass_finds_the_tile_face_by_scan_then_region():
    fps = 10.0
    n = 30
    frames = [{"t": i / fps, "found": False, "box": None, "cx": None, "cy": None,
               "blink": None, "gaze_off": None} for i in range(n)]  # fmt: skip
    images = [_call_frame(dx=i) for i in range(n)]  # the speaker moves a little
    calls = []
    stats = face.small_pass(frames, iter(images), _fake_find(calls), fps)
    assert stats == {"checked": n, "found": n}
    for i, f in enumerate(frames):
        assert f["found"] and f["small"] is True
        want = [(TARGET[0] + i) / 1920, TARGET[1] / 1080, (TARGET[2] + i) / 1920, TARGET[3] / 1080]
        assert f["box"] == pytest.approx(want, abs=0.004), i
        assert f["cx"] == pytest.approx((want[0] + want[2]) / 2, abs=0.004)
    # One tile scan on the first frame, then one region-of-interest look per frame.
    assert len(calls) <= len(face.tiles()) + n
    med = face.median_of(frames)
    assert (
        TILE[0] / 1920 < med["cx"] < TILE[2] / 1920 and TILE[1] / 1080 < med["cy"] < TILE[3] / 1080
    )


def test_small_pass_scans_at_most_every_half_second_and_keeps_found_frames():
    fps = 10.0
    frames = [{"t": i / fps, "found": False, "box": None} for i in range(20)]
    frames[0] = {"t": 0.0, "found": True, "box": [0.3, 0.2, 0.7, 0.8], "cx": 0.5, "cy": 0.5,
                 "blink": 0.0, "gaze_off": 0.0}  # fmt: skip
    blank = np.full((1080, 1920, 3), 40, np.uint8)
    calls = []
    stats = face.small_pass(frames, iter([blank] * 20), _fake_find(calls), fps)
    assert stats["found"] == 0 and frames[0]["found"] and "small" not in frames[0]
    scans = 4  # frames 1, 6, 11, 16 (every 0.5 s)
    assert len(calls) == 1 + scans * len(face.tiles())  # + one region look after frame 0


def test_tiles_cover_the_frame_and_roi_stays_inside():
    ts = face.tiles()
    assert len(ts) == 16 and min(t[0] for t in ts) == 0 and max(t[2] for t in ts) == 1.0
    r = face.roi([0.95, 0.9, 0.99, 0.98], 16 / 9)
    assert 0 <= r[0] < r[2] <= 1 and 0 <= r[1] < r[3] <= 1
    assert (r[2] - r[0]) * 1920 == pytest.approx((r[3] - r[1]) * 1080)  # square in px


def test_old_face_json_is_read_but_analysed_again(tmp_path):
    job = tmp_path / "job"
    (job / "work").mkdir(parents=True)
    (job / "input").mkdir()
    (job / "input" / "a.mp4").write_bytes(b"x")
    doc = _face_doc([(0.0, 0.5, 0.5)]) | {"version": 1, "source": "input/a.mp4", "blinks": []}
    (job / "work" / "face.json").write_text(json.dumps(doc))
    assert face.load_face(job, "input/a.mp4") is not None  # readers accept version 1
    assert face.FACE_VERSION == 2 and 1 in face.READ_VERSIONS


# A real face for the detector: set VIDEOCUT_TEST_FACE_CLIP to a clip of a person talking
# (not part of the repository). It is shrunk into the tile of a generated call frame.
FACE_CLIP = os.environ.get("VIDEOCUT_TEST_FACE_CLIP")


@pytest.mark.media
@pytest.mark.skipif(not REAL_MODEL.is_file(), reason="face model not installed")
@pytest.mark.skipif(not FACE_CLIP or not Path(FACE_CLIP).is_file(), reason="no face clip")
def test_real_detector_finds_the_face_in_a_small_call_tile(capsys, tmp_path, videocut_home):
    (videocut_home / "models").mkdir(parents=True)
    (videocut_home / "models" / face.MODEL).symlink_to(REAL_MODEL)
    call = tmp_path / "call.mp4"
    x0, y0, x1, y1 = TILE
    clips._ff(
        "-f", "lavfi", "-i", f"color=c=0x282828:s={CALL[0]}x{CALL[1]}:r=30:d=4",
        "-i", FACE_CLIP, "-filter_complex",
        f"[1:v]scale={x1 - x0}:{y1 - y0}:force_original_aspect_ratio=increase,"
        f"crop={x1 - x0}:{y1 - y0}[t];[0:v]drawbox=x=60:y=60:w=1280:h=960:c=0xC8C8C8:t=fill[b];"
        f"[b][t]overlay={x0}:{y0}:shortest=1[v]",
        "-map", "[v]", "-map", "1:a?", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(call),
    )  # fmt: skip
    code, s, _ = run_tool(capsys, "job", "new", str(call), "--id", "call")
    code, s, out = run_tool(capsys, "face", "--job", s["job"])
    assert code == 0, s
    assert s["small_pass"] and s["found_pct"] >= 50, out
    m = s["median"]
    assert x0 / CALL[0] < m["cx"] < x1 / CALL[0] and y0 / CALL[1] < m["cy"] < y1 / CALL[1]


def test_small_face_is_framed_closer_only_in_landscape_and_square():
    doc = {"median": {"cx": 0.8, "cy": 0.5, "box": [0.77, 0.44, 0.83, 0.56]}}  # 12 % high
    assert face.small_face_zoom(doc, (1080, 1920)) == 1.0  # portrait: as before
    assert face.small_face_zoom(doc, (1080, 1350)) == 1.0
    assert face.small_face_zoom(doc, (1920, 1080)) == face.SMALL_FACE_ZOOM_MAX
    assert face.small_face_zoom(doc, (1080, 1080)) == face.SMALL_FACE_ZOOM_MAX
    big = {"median": {"cx": 0.5, "cy": 0.4, "box": [0.4, 0.2, 0.6, 0.6]}}
    assert face.small_face_zoom(big, (1920, 1080)) == 1.0
    mid = {"median": {"cx": 0.5, "cy": 0.4, "box": [0.4, 0.3, 0.5, 0.45]}}  # 15 % high
    assert face.small_face_zoom(mid, (1920, 1080)) == round(face.SMALL_FACE / 0.15, 2)
