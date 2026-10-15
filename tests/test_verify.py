import json
import shutil
import subprocess
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool

from videocut.tools import _image as im
from videocut.tools import _verify_screen, verify
from videocut.tools import overlays as overlays_mod

pytestmark = pytest.mark.skipif(clips.ffmpeg() is None, reason="needs ffmpeg")

AMP = 0.3056  # a sine of this amplitude measures about -14 LUFS


def _build_reel(path: Path) -> None:
    """3 s, 1080x1920, 30 fps. Cuts at 1.0 s (hard audio step + inverted picture in the picture)
    and 2.0 s (same step, but 30 ms fades on both sides, picture continuous)."""
    a1 = f"{AMP}*sin(2*PI*300*t+PI/2)"  # ends and starts at the peak
    a2 = f"{AMP}*sin(2*PI*440*t)"  # starts and ends at zero
    a3 = f"{AMP}*sin(2*PI*300*t+PI/2)*min(1,t/0.03)*min(1,(1-t)/0.03)"
    audio = ";".join(
        [f"aevalsrc='{e}':s=48000:d=1[a{i}]" for i, e in enumerate((a1, a2, a3), 1)]
        # the fade-out of segment 2 is applied here, its start stays hard
        + ["[a2]afade=t=out:st=0.97:d=0.03[a2f]", "[a1][a2f][a3]concat=n=3:v=0:a=1[aout]"]
    )
    video = (
        "testsrc2=size=1080x1920:rate=30:duration=3,split[v1][v2];"
        "[v1]trim=0:1,setpts=PTS-STARTPTS[x1];"
        "[v2]trim=1:3,setpts=PTS-STARTPTS,negate[x2];[x1][x2]concat=n=2:v=1:a=0[vout]"
    )
    subprocess.run(
        [
            clips.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-filter_complex", f"{video};{audio}", "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", str(path),
        ],
        check=True,
    )  # fmt: skip


@pytest.fixture(scope="module")
def reel(tmp_path_factory):
    path = tmp_path_factory.mktemp("reel") / "reel.mp4"
    _build_reel(path)
    return path


def _job(capsys, reel, cuts, duration=3.0):
    src = reel.parent / "src.mp4"
    shutil.copy(reel, src)
    code, s, _ = run_tool(capsys, "job", "new", str(src), "--id", "v1")
    assert code == 0, s
    job = Path(s["job"])
    (job / "out").mkdir(exist_ok=True)
    shutil.copy(reel, job / "out" / "reel.mp4")
    (job / "out" / "render.json").write_text(
        json.dumps(
            {
                "edl_sha256": "0" * 64,
                "output_duration": duration,
                "cuts": cuts,
                "loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5, "lra": 1.0},
                "preview": False,
            }
        )
    )
    return job


def test_hard_cut_is_flagged_and_faded_cut_passes(capsys, reel):
    job = _job(capsys, reel, [1.0, 2.0])
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, s
    doc = json.loads((job / "out" / "verify.json").read_text())
    hard, soft = doc["cuts"]
    assert "click" in hard["flags"] and hard["audio"]["click_peak"] >= 12
    # a tone runs on across both cuts: no click when faded, but speech is cut there
    assert soft["flags"] == ["speech_clipped"], soft
    assert "speech_clipped" in hard["flags"] and "speech_clipped" in s["failed"]
    # the inverted picture is far above the other cut's jump: info and flag
    assert hard["info"] == ["jump_cut"] and hard["visual"]["jump_score"] >= 4
    assert "visual_jump" in hard["flags"]
    assert "visual_jump" not in soft["flags"] and soft["info"] == []
    assert [i["cut"] for i in s["info"]] == [1]
    assert s["ok"] is False
    assert [f["cut"] for f in s["flagged"]] == [1, 2]
    assert s["images"] == ["out/verify/cut_01.jpg", "out/verify/cut_02.jpg"]
    for name in s["images"]:
        assert (job / name).stat().st_size > 1000


def test_global_checks_pass_on_a_good_reel(capsys, reel):
    job = _job(capsys, reel, [])
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, s
    assert s["failed"] == [] and s["ok"] is True and s["cuts"] == 0, s


def test_global_checks_fail_on_wrong_duration(capsys, reel):
    job = _job(capsys, reel, [], duration=5.0)
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0
    assert s["ok"] is False and s["failed"] == ["duration"]


def test_cut_image_within_budget_and_stale_images_removed(capsys, reel):
    job = _job(capsys, reel, [1.0])
    stale = job / "out" / "verify" / "cut_07.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, s
    assert not stale.exists()
    out = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0"]
        + [str(job / s["images"][0])],
        capture_output=True,
        text=True,
        check=True,
    )
    w, h = (int(x) for x in out.stdout.strip().split(","))
    assert w == 1080 and max(w, h) <= 2000


def test_missing_render_json_is_an_error(capsys, reel):
    job = _job(capsys, reel, [])
    (job / "out" / "render.json").unlink()
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 1 and s["ok"] is False and "render.json" in s["error"]


def test_cut_outside_the_reel_is_a_failed_check(capsys, reel):
    job = _job(capsys, reel, [1.0, 9.0])
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and "cuts_in_range" in s["failed"] and s["cuts"] == 1


# ---------------------------------------------------------------- click calibration
# Shapes of the false flags measured on two real reels (a denoised track is digital silence
# between words): a soft mouth tick in a short stretch of room tone, a word fragment that
# starts just before the cut. A real cut click is an audible sample step at the cut.

R = verify.RATE
CUT = R // 2


def _db(v):
    return 10 ** (v / 20)


def _sines(level_db, silent_around=False):
    """300 Hz up to the cut, then 440 Hz starting at its peak: a sample step at the cut."""
    t = np.arange(R) / R
    x = _db(level_db) * np.sin(2 * np.pi * 300 * t)
    x[CUT:] = _db(level_db) * np.sin(2 * np.pi * 440 * (t[CUT:] - t[CUT]) + np.pi / 2)
    if silent_around:  # only +-20 ms of sound, digital silence around it
        x[: CUT - 960], x[CUT + 960 :] = 0.0, 0.0
    return x.astype(np.float32)


def _tick():
    rng = np.random.default_rng(1)
    x = np.zeros(R)
    x[CUT - 480 : CUT + 1440] = rng.normal(0, _db(-62), 1920)  # the gate opens briefly
    m = np.arange(240)
    x[CUT + 100 : CUT + 340] += _db(-45) * np.exp(-m / 40) * np.sin(2 * np.pi * 3000 * m / R)
    return x.astype(np.float32)


def _word_onset():
    rng = np.random.default_rng(2)
    n = np.arange(1392)
    v = sum(
        np.sin(2 * np.pi * f * n / R + rng.uniform(0, 6)) / (i + 1)
        for i, f in enumerate((140, 280, 420, 560, 700, 1200, 2400))
    )
    v = v / np.abs(v).max() * _db(-12) * np.minimum(1, n / 96) * np.minimum(1, (1391 - n) / 96)
    x = np.zeros(R)
    x[CUT - 432 : CUT + 960] = v  # starts 9 ms before the cut
    return x.astype(np.float32)


NO_VIS = {"diff_cut": None, "diff_around": None, "jump_score": None}


def _clicks(x):
    a = verify.audio_numbers(x, CUT / R)
    return "click" in verify.cut_notes(NO_VIS, a, None)[0], a


@pytest.mark.parametrize("make", [lambda: _sines(-10), lambda: _sines(-20, silent_around=True)])
def test_a_real_sample_step_clicks(make):
    flagged, a = _clicks(make())
    assert flagged, a
    assert a["click_peak"] >= 20 and a["step_sharpness"] >= 20 and a["step_db"] >= -30


@pytest.mark.parametrize(
    "make", [_tick, _word_onset, lambda: _sines(-55)], ids=["tick", "word_onset", "inaudible"]
)
def test_quiet_or_natural_sounds_next_to_silence_do_not_click(make):
    flagged, a = _clicks(make())
    assert not flagged, a


def test_digital_silence_has_no_click():
    flagged, a = _clicks(np.zeros(R, np.float32))
    assert not flagged and a["click_peak"] == 0.0, a


def _vis(score):
    return {"diff_cut": 30.0, "diff_around": 30.0 / score, "jump_score": score}


QUIET = {"click_peak": 1.0, "hf_spike_db": 0.0}


def test_talking_head_jump_cuts_are_info_not_flags():
    # similar jumps everywhere (normal jump cuts): reported, not flagged
    assert verify.cut_notes(_vis(9.0), QUIET, 8.0) == ([], ["jump_cut"])
    # a lone jump cut has no baseline and is never flagged
    assert verify.cut_notes(_vis(50.0), QUIET, None) == ([], ["jump_cut"])
    # far above the other cuts' median: flagged
    assert verify.cut_notes(_vis(30.0), QUIET, 8.0) == (["visual_jump"], ["jump_cut"])
    # still pose: smooth cuts around 1, a normal jump cut at 4.5 is not a glitch
    assert verify.cut_notes(_vis(4.5), QUIET, 1.0) == ([], ["jump_cut"])
    assert verify.cut_notes(_vis(30.0), QUIET, 0.0) == (["visual_jump"], ["jump_cut"])
    # no visible jump: neither
    assert verify.cut_notes(_vis(1.2), QUIET, 8.0) == ([], [])
    assert verify.cut_notes(_vis(1.2), {"click_peak": 20.0, "hf_spike_db": 0.0}, 8.0) == (
        ["click"],
        [],
    )


def test_reframe_at_a_cut_is_info_not_a_flag():
    from videocut.edl import from_dict

    segs = [
        {"in": 0.0, "out": 2.0, "zoom_keys": [[1.0, 1.25]]},  # ends zoomed in
        {"in": 3.0, "out": 5.0},  # starts at 1.0: reframe
        {"in": 6.0, "out": 8.0},  # same framing: plain jump cut
        {"in": 9.0, "out": 10.0, "crop": {"cx": 0.4, "cy": 0.5, "zoom": 1.0}},  # moved
    ]
    edl = from_dict({"fps": 30, "size": [1080, 1920], "source": "input/a.mp4", "segments": segs})
    assert verify.reframes(edl) == [True, False, True]
    assert verify.cut_notes(_vis(30.0), QUIET, 8.0, reframe=True) == ([], ["jump_cut", "reframe"])


FACE = [{"t": t / 10, "box": [400, 600, 680, 900]} for t in range(30)]  # output px


def test_overlay_boxes_flag_safe_zone_and_face():
    boxes = [
        {"scene": "caption", "t0": 0.0, "t1": 1.0, "box": [100, 1000, 980, 1100]},  # fine
        {"scene": "keyword", "t0": 0.0, "t1": 1.0, "box": [100, 200, 980, 300]},  # top zone
        {"scene": "caption", "t0": 1.0, "t1": 2.0, "box": [30, 1000, 500, 1100]},  # side zone
        {"scene": "number_card", "t0": 1.0, "t1": 1.5, "box": [600, 850, 900, 1000]},  # face
        {"scene": "lower_third", "t0": 1.0, "t1": 2.0, "box": [100, 1500, 980, 1600]},  # bottom
        # overlaps the face box but only while no face sample is visible
        {"scene": "cta_end", "t0": 3.5, "t1": 4.0, "box": [400, 600, 680, 900]},
        # touches the face box by under 2 % of its area
        {"scene": "keyword", "t0": 0.0, "t1": 1.0, "box": [100, 895, 980, 1000]},
    ]
    out = verify.overlay_checks(boxes, FACE, (1080, 1920))
    assert [o["flags"] for o in out] == [
        [], ["overlay_in_safe_zone"], ["overlay_in_safe_zone"], ["overlay_on_face"],
        ["overlay_in_safe_zone"], [], [],
    ]  # fmt: skip
    assert out[3]["face_overlap"] == round(80 * 50 / (280 * 300), 3) and out[3]["face_t"] == 1.0
    assert 0 < out[6]["face_overlap"] <= verify.FACE_OVERLAP_MAX


def test_caption_times_one_per_change_snapped_and_unique():
    caps = [{"t0": 0.0, "t1": 1.0}, {"t0": 1.0, "t1": 1.1}, {"t0": 1.0, "t1": 1.1}]
    caps.append({"t0": 2.95, "t1": 3.5})  # its frame lies beyond the reel
    # settle time after the change, at most the middle of a short group, on the frame grid
    assert verify.caption_times(caps, 30, 3.0) == [4 / 30, 32 / 30]


def test_verify_checks_overlays_and_writes_caption_sheets(capsys, reel):
    job = _job(capsys, reel, [])
    src = json.loads((job / "job.json").read_text())["source"]
    caps = [{"t0": i * 0.6, "t1": i * 0.6 + 0.6, "words": []} for i in range(5)]
    edl = {"fps": 30, "size": [1080, 1920], "source": src, "segments": [{"in": 0, "out": 3}]}
    ovs = [{"scene": "keyword", "t0": 1.0, "t1": 2.0, "props": {"text": "x"}}]
    (job / "work" / "edl.json").write_text(json.dumps({**edl, "captions": caps, "overlays": ovs}))
    (job / "work" / "overlays.json").write_text(
        json.dumps({"size": [1080, 1920], "fps": 30, "duration": 3.0, "face": FACE})
    )
    boxes = [
        {"scene": "caption", "t0": 0.0, "t1": 3.0, "box": [100, 1000, 980, 1100]},
        {"scene": "keyword", "t0": 1.0, "t1": 2.0, "box": [450, 650, 650, 850]},
    ]
    (job / "work" / "overlay_boxes.json").write_text(json.dumps(boxes))
    (job / "work" / "overlay.mov").write_bytes(b"mov")
    overlays_mod.write_record(job)  # as `videocut tool overlays` does after its render
    stale = job / "out" / "verify" / "captions_09.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, s
    assert s["ok"] is False and s["failed"] == []
    assert s["overlay_flagged"] == [
        {"scene": "keyword", "t0": 1.0, "t1": 2.0, "flags": ["overlay_on_face"]}
    ]
    assert any("no overlay composited" in w for w in s["warnings"])
    assert not stale.exists() and "out/verify/captions_01.jpg" in s["images"]
    doc = json.loads((job / "out" / "verify.json").read_text())
    assert len(doc["caption_times"]) == 5
    assert doc["caption_sheets"] == ["out/verify/captions_01.jpg"]
    assert doc["overlay_times"] == [1.5] and s["overlay_sheets"] == ["out/verify/overlays_01.jpg"]
    out = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0"]
        + [str(job / "out" / "verify" / "captions_01.jpg")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "1080,1920"  # 3x3 tiles of 360x640

    # Boxes edited after the tool wrote them (the agent may write work/) are not trusted.
    (job / "work" / "overlay_boxes.json").write_text(json.dumps(boxes[:1]))
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and s["ok"] is False
    assert s["overlay_flagged"][0]["flags"] == ["overlay_boxes_untrusted"]
    assert any("work/overlay_boxes.json" in w for w in s["warnings"])
    # ... nor is an overlay.mov whose boxes file was removed
    (job / "work" / "overlay_boxes.json").unlink()
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and s["ok"] is False
    assert s["overlay_flagged"][0]["flags"] == ["overlay_boxes_untrusted"]


def test_full_frame_end_card_is_not_flagged():
    face = [{"t": 1.0, "box": [400, 600, 700, 1000]}]
    boxes = [
        {"scene": "cta_end", "t0": 0.0, "t1": 2.0, "box": [0, 0, 1080, 1920]},
        {"scene": "keyword", "t0": 0.0, "t1": 2.0, "box": [0, 0, 1080, 1920]},
    ]
    out = verify.overlay_checks(boxes, face, (1080, 1920))
    assert out[0]["flags"] == []
    assert out[1]["flags"] == ["overlay_in_safe_zone", "overlay_on_face"]


def test_overlay_covering_a_caption_is_flagged():
    boxes = [
        {"scene": "caption", "t0": 1.0, "t1": 2.0, "box": [300, 300, 780, 480]},
        {"scene": "keyword", "t0": 1.5, "t1": 4.0, "box": [350, 320, 730, 500]},
        {"scene": "keyword", "t0": 2.0, "t1": 4.0, "box": [350, 1300, 730, 1450]},  # later, apart
        {"scene": "cta_end", "t0": 2.5, "t1": 4.0, "box": [0, 0, 1080, 1920]},
    ]
    flags = [o["flags"] for o in verify.overlay_checks(boxes, [], (1080, 1920))]
    assert flags == [[], ["overlay_overlap"], [], []]
    assert verify.full_frame_spans([{"scene": "cta_end", "t0": 1, "t1": 2}, {"scene": "x"}]) == [
        (1, 2)
    ]


def test_caption_on_the_end_card_and_end_card_over_speech_are_flagged():
    plate = {"scene": "cta_end", "t0": 5.0, "t1": 7.0, "box": [0, 0, 1080, 1920]}
    cap = {"scene": "caption", "t0": 4.0, "t1": 5.5, "box": [100, 1000, 980, 1100]}
    # The renderer hides captions under the plate: drawn only until 4.97 s, not flagged.
    hidden = {**cap, "drawn": [4.0, 4.967]}
    out = verify.overlay_checks([plate, hidden], [], (1080, 1920), last_word=4.8)
    assert [o["flags"] for o in out] == [[], []]
    # A caption drawn over the plate, and a plate before the last word + 0.2 s.
    out = verify.overlay_checks([{**plate, "drawn": [5.03, 7.0]}, cap], [], (1080, 1920), 4.9)
    assert [o["flags"] for o in out] == [["plate_over_speech"], ["caption_on_plate"]]


def test_merge_spans_and_the_bubble_square():
    from videocut.tools.verify import inscribed, merge_spans

    assert merge_spans([(3.0, 4.0), (0.0, 1.0), (1.02, 2.0)], 1 / 30) == [(0.0, 2.0), (3.0, 4.0)]
    sq = inscribed([1563, 670, 1843, 950])  # a 280 px bubble
    assert sq[2] - sq[0] == sq[3] - sq[1] == 196 and sq[0] > 1563 and sq[3] < 950


# ---------------------------------------------------------------- long reels


def test_select_expressions_stay_within_ffmpeg_nesting():
    """ffmpeg refuses an expression nested deeper than 100 ("Cannot allocate memory"): a
    flat a+b+c+... of a long reel's caption groups failed verify. The trees stay shallow."""
    times = [i / 30 for i in range(0, 9000, 3)]  # 3000 frames of a 5 min reel
    for expr in (im.select_times(times, 30), im.select_frames(list(range(3000)))):
        assert im.expr_depth(expr) <= im.MAX_EXPR_DEPTH
    assert im.expr_depth("+".join(["eq(n\\,1)"] * 150)) > 100  # what the measure catches
    assert im.select_times([1.0], 30).startswith("between(") and im.any_of([]) == "0"


def _long_reel(path: Path, seconds: int) -> None:
    subprocess.run(
        [
            clips.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=360x640:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"aevalsrc='{AMP}*sin(2*PI*300*t)':s=48000:d={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", str(path),
        ],
        check=True,
    )  # fmt: skip


def test_verify_runs_on_a_reel_with_many_caption_groups(capsys, tmp_path):
    long = tmp_path / "long" / "reel.mp4"
    long.parent.mkdir()
    _long_reel(long, 12)
    job = _job(capsys, long, [], duration=12.0)
    src = json.loads((job / "job.json").read_text())["source"]
    caps = [{"t0": i * 0.075, "t1": i * 0.075 + 0.075, "words": []} for i in range(160)]
    edl = {"fps": 30, "size": [360, 640], "source": src, "segments": [{"in": 0, "out": 12}]}
    (job / "work" / "edl.json").write_text(json.dumps({**edl, "captions": caps}))
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, s
    doc = json.loads((job / "out" / "verify.json").read_text())
    assert len(doc["caption_times"]) == 160
    assert len(doc["caption_sheets"]) == -(-160 // verify.CAPTION_COLS**2)


def test_many_reel_frames_are_read_exactly(tmp_path):
    long = tmp_path / "reel.mp4"
    _long_reel(long, 8)
    want = list(range(0, 240, 2))  # 120 frames: more than one flat expression could hold
    got = _verify_screen._frames(long, want, (360, 640))
    assert sorted(got) == want
    for n in (0, 118, 238):
        assert np.array_equal(got[n], _verify_screen._frames(long, [n], (360, 640))[n])
    streamed = dict(_verify_screen._frame_stream(long, want, (360, 640)))
    assert all(streamed[n] is not None and np.array_equal(streamed[n], got[n]) for n in want)
