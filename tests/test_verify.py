import json
import shutil
import subprocess
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut.tools import verify

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
    assert soft["flags"] == [], soft
    # the inverted picture is far above the other cut's jump: info and flag
    assert hard["info"] == ["jump_cut"] and hard["visual"]["jump_score"] >= 4
    assert "visual_jump" in hard["flags"]
    assert "visual_jump" not in soft["flags"] and soft["info"] == []
    assert [i["cut"] for i in s["info"]] == [1]
    assert s["ok"] is False
    assert [f["cut"] for f in s["flagged"]] == [1]
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
