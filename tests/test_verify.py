import json
import shutil
import subprocess
from pathlib import Path

import clips
import pytest
from helpers import run_tool

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
    assert "visual_jump" in hard["flags"] and hard["visual"]["jump_score"] >= 4
    assert "visual_jump" not in soft["flags"]
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
