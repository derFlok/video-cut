import json
import shutil
import subprocess
from pathlib import Path

import clips
import pytest
from helpers import run_tool

pytestmark = pytest.mark.media


def _job(capsys, clip, job_id):
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", job_id)
    assert code == 0
    return Path(s["job"])


def _stream(path, entries):
    out = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)["streams"]


@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
def test_ingest_outputs(capsys, request, orientation):
    clip = request.getfixturevalue(f"{orientation}_clip")
    job = _job(capsys, clip, orientation)
    code, s, _ = run_tool(capsys, "ingest", "--job", str(job))
    assert code == 0, s
    assert s["done"] == ["probe", "audio_raw", "audio", "proxy"]
    w, h = clips.SIZES[orientation]
    probe = json.loads((job / "work" / "probe.json").read_text())
    assert probe["size"] == [w, h] and probe["fps"] == 30 and probe["rotation"] == 0
    assert probe["audio_rate"] == 48000
    assert probe["duration"] == pytest.approx(clips.duration(clip), abs=0.05)
    for name in ("audio.wav", "audio_raw.wav"):
        (a,) = _stream(job / "work" / name, "stream=sample_rate,channels,codec_name")
        assert a == {"codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 1}
    v = _stream(job / "work" / "proxy.mp4", "stream=width,height,avg_frame_rate,codec_type")
    video = next(x for x in v if x["codec_type"] == "video")
    assert min(video["width"], video["height"]) == 360
    assert (video["width"] > video["height"]) == (w > h)
    assert video["avg_frame_rate"] == "30/1"
    assert json.loads((job / "job.json").read_text())["status"] == "ingested"


def test_ingest_is_idempotent_and_runs_from_cwd(capsys, monkeypatch, portrait_clip):
    job = _job(capsys, portrait_clip, "idem")
    monkeypatch.chdir(job)
    assert run_tool(capsys, "ingest")[0] == 0
    code, s, _ = run_tool(capsys, "ingest")
    assert code == 0 and s["done"] == [] and len(s["skipped"]) == 4


def test_rotated_source_reports_display_size(capsys, rotated_clip):
    job = _job(capsys, rotated_clip, "rot")
    code, s, _ = run_tool(capsys, "ingest", "--job", "rot")
    assert code == 0, s
    assert s["rotation"] in (90, 270) and s["size"] == [1080, 1920]
    v = _stream(job / "work" / "proxy.mp4", "stream=width,height,codec_type")
    video = next(x for x in v if x["codec_type"] == "video")
    assert (video["width"], video["height"]) == (360, 640)


@pytest.mark.skipif(
    not shutil.which("deep-filter") and not clips._bin("deep-filter"),
    reason="deep-filter not installed",
)
def test_denoise_keeps_raw_and_switches_back(capsys, portrait_clip):
    job = _job(capsys, portrait_clip, "dn")
    code, s, _ = run_tool(capsys, "ingest", "--job", "dn", "--denoise")
    assert code == 0 and s["denoised"] is True, s
    raw, clean = job / "work" / "audio_raw.wav", job / "work" / "audio.wav"
    assert raw.is_file() and clean.is_file()
    assert raw.read_bytes() != clean.read_bytes()
    assert clips.duration(clean) == pytest.approx(clips.duration(raw), abs=0.05)
    code, s, _ = run_tool(capsys, "ingest", "--job", "dn")  # without --denoise: redo audio only
    assert s["done"] == ["audio"] and raw.read_bytes() == clean.read_bytes()


def test_source_without_audio_fails(capsys, tmp_path):
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            clips.ffmpeg(),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ],
        check=True,
    )
    _job(capsys, silent, "noaudio")
    code, s, _ = run_tool(capsys, "ingest", "--job", "noaudio")
    assert code == 1 and "no audio" in s["error"]
