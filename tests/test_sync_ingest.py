import json
from pathlib import Path

import clips
import pytest
from helpers import run_tool

pytestmark = pytest.mark.media


def _stream(path, entries):
    import subprocess

    out = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)["streams"]


def test_screen_ingest_outputs(capsys, demo_pair):
    cam, scr, meta = demo_pair
    _, s, _ = run_tool(capsys, "job", "new", str(cam), "--screen", str(scr), "--id", "si")
    job = Path(s["job"])
    assert s["screen"] == f"input/{scr.name}" and (job / s["screen"]).is_file()
    code, s, _ = run_tool(capsys, "ingest", "--job", str(job))
    assert code == 0, s
    assert s["screen"]["size"] == [2880, 1800] and s["screen"]["vfr"] is True
    assert {"screen_probe", "screen_audio", "screen_proxy"} <= set(s["done"])
    work = job / "work"
    probe = json.loads((work / "screen_probe.json").read_text())
    assert probe["file"] == f"input/{scr.name}" and probe["audio_index"] is not None
    assert probe["duration"] == pytest.approx(meta["screen_duration"], abs=0.1)
    (a,) = _stream(work / "screen_audio.wav", "stream=sample_rate,channels,codec_name")
    assert a == {"codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 1}
    v = _stream(work / "screen_proxy.mp4", "stream=width,height,avg_frame_rate,codec_type")
    assert [x["codec_type"] for x in v] == ["video"]  # no audio
    assert (v[0]["width"], v[0]["height"]) == (960, 600) and v[0]["avg_frame_rate"] == "30/1"
    # CFR: frame count = duration * 30
    frames = _stream(work / "screen_proxy.mp4", "stream=nb_frames")[0]["nb_frames"]
    assert int(frames) == pytest.approx(probe["duration"] * 30, abs=3)
    again = run_tool(capsys, "ingest", "--job", str(job))[1]
    assert again["done"] == []  # idempotent


def test_screen_without_audio_has_no_wav(capsys, clip_dir):
    cam, scr, _ = clips.screen_pair(clip_dir, screen_audio=False, size=(1440, 900))
    run_tool(capsys, "job", "new", str(cam), "--screen", str(scr), "--id", "sn")
    code, s, _ = run_tool(capsys, "ingest", "--job", "sn")
    assert code == 0 and s["screen"]["audio"] is False
    job = Path(s["job"])
    assert (job / "work" / "screen_proxy.mp4").is_file()
    assert not (job / "work" / "screen_audio.wav").exists()
    # without audio only a human offset works, and later runs (videocut run prep) keep it
    code, s, _ = run_tool(capsys, "sync", "--job", "sn")
    assert code == 1 and "--offset" in s["error"]
    code, s, _ = run_tool(capsys, "sync", "--job", "sn", "--offset", "2.5")
    assert code == 0 and s["method"] == "manual"
    code, s, _ = run_tool(capsys, "sync", "--job", "sn")
    assert code == 0 and s["kept"] is True and s["offset_s"] == 2.5


def test_job_screen_replaces_and_clears_outputs(capsys, demo_pair, clip_dir):
    cam, scr, _ = demo_pair
    run_tool(capsys, "job", "new", str(cam), "--id", "js")
    job = Path(run_tool(capsys, "job", "show", "--job", "js")[1]["job"])
    code, s, _ = run_tool(capsys, "job", "screen", "--job", "js", str(scr))
    assert code == 0 and s["screen"] == f"input/{scr.name}" and s["replaced"] is None
    stale = job / "work" / "sync.json"
    stale.write_text("{}")
    other = clips.screen_pair(clip_dir, screen_audio=False, size=(1440, 900))[1]
    code, s, _ = run_tool(capsys, "job", "screen", "--job", "js", str(other))
    assert code == 0 and s["replaced"] == f"input/{scr.name}"
    assert not stale.exists() and not (job / "input" / scr.name).exists()
    assert json.loads((job / "job.json").read_text())["screen"] == f"input/{other.name}"
