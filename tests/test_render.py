import hashlib
import json
import subprocess
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool

from videocut.tools import _snap, verify
from videocut.tools.job import approve_job

pytestmark = pytest.mark.media

FRAME = 1 / clips.FPS
RATE = 48000
# Cuts inside speech of the generated clip, so an unfaded cut would click. Long enough
# (~5 s) for loudnorm to reach its target.
SEGMENTS = [
    {"in": 0.3, "out": 1.0},
    {"in": 1.9, "out": 2.6, "crop": {"cx": 0.4, "cy": 0.5, "zoom": 1.0}, "zoom_keys": [[2.2, 1.3]]},
    {"in": 5.0, "out": 8.5},
]


def _job(capsys, clip, job_id, segments=SEGMENTS, **extra) -> Path:
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    edl = {"fps": 30, "size": [1080, 1920], "source": s["source"], "segments": segments, **extra}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: render test\n")
    approve_job(job)  # render refuses before the human gate
    return job


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def _ff_out(*args) -> bytes:
    return subprocess.run(
        [clips.ffmpeg(), "-v", "error", *args], capture_output=True, check=True
    ).stdout


def _audio(path: Path) -> np.ndarray:
    raw = _ff_out("-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-")
    return np.frombuffer(raw, np.float32)


def _frame(path: Path, t: float, size=(54, 96)) -> np.ndarray:
    raw = _ff_out(
        "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
        "-vf", f"scale={size[0]}:{size[1]}", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    )  # fmt: skip
    return np.frombuffer(raw, np.uint8).astype(float).reshape(size[1], size[0])


def _output_duration(segments) -> float:
    return sum(s["out"] - s["in"] for s in segments)


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_render_reel(capsys, request, orientation):
    clip = request.getfixturevalue(f"{orientation}_clip")
    job = _job(capsys, clip, f"r-{orientation}")
    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, s
    reel = job / "out" / "reel.mp4"
    assert s["output"] == "out/reel.mp4" and s["preview"] is False

    info = _probe(reel)
    v = next(x for x in info["streams"] if x["codec_type"] == "video")
    a = next(x for x in info["streams"] if x["codec_type"] == "audio")
    assert (v["codec_name"], v["profile"], v["pix_fmt"]) == ("h264", "High", "yuv420p")
    assert (v["width"], v["height"], v["avg_frame_rate"]) == (1080, 1920, "30/1")
    assert (a["codec_name"], a["sample_rate"]) == ("aac", "48000")
    want = _output_duration(SEGMENTS)
    assert abs(float(info["format"]["duration"]) - want) <= FRAME + 1e-3
    assert abs(int(v["nb_frames"]) - want * 30) <= 1

    report = json.loads((job / "out" / "render.json").read_text())
    assert set(report) == {
        "edl_sha256", "output_duration", "cuts", "loudness", "preview", "snap", "xfade_ms",
        "c2pa", "provenance", "disclosure",
    }  # fmt: skip
    assert report["c2pa"] in ("signed", "skipped") and report["provenance"]["comment"] is None
    style = _snap.cut_style(json.loads((job / "job.json").read_text()))
    assert report["xfade_ms"] == style["xfade_ms"] and report["snap"]["skipped"]  # no wav
    edl_bytes = (job / "work" / "edl.json").read_bytes()
    assert report["edl_sha256"] == hashlib.sha256(edl_bytes).hexdigest()
    assert report["output_duration"] == pytest.approx(want)
    assert report["cuts"] == pytest.approx([0.7, 1.4])
    assert report["preview"] is False
    loud = report["loudness"]
    assert abs(loud["integrated_lufs"] + 14) <= 1
    assert loud["true_peak_dbtp"] <= -1
    assert loud["lra"] >= 0

    (c,) = _probe(job / "out" / "cover.jpg")["streams"]
    assert (c["width"], c["height"]) == (1080, 1920)
    assert "loudnorm=" in (job / "work" / "render_filter.txt").read_text()
    # A 1080p landscape source zoomed 1.3x is below the sharpness limit; portrait is not.
    assert bool(s["warnings"]) == (orientation == "landscape")
    assert ("warning: crop is only" in out) == (orientation == "landscape")


def test_no_click_at_cuts(capsys, landscape_clip):
    """The cuts lie inside speech: crossfaded (the default) they do not click, hard cut
    ("xfade_ms": 0) the same cuts jump. The duration stays the same either way."""
    peaks, durations = {}, {}
    for name, extra in (("xfade", {}), ("hard", {"xfade_ms": 0})):
        segs = [{**s, **extra} for s in SEGMENTS]
        job = _job(capsys, landscape_clip, f"click-{name}", segs)
        code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
        assert code == 0, s
        x = _audio(job / "out" / "preview.mp4")
        peaks[name] = [verify.audio_numbers(x, c)["click_peak"] for c in s["cuts"]]
        durations[name] = len(x) / RATE
    assert max(peaks["xfade"]) < verify.CLICK_PEAK_MAX, peaks
    assert max(peaks["hard"]) > max(peaks["xfade"]) * 2, peaks  # the check would catch it
    assert abs(durations["xfade"] - durations["hard"]) < 0.005, durations
    assert abs(durations["xfade"] - _output_duration(SEGMENTS)) <= FRAME + 0.03, durations


def test_zoom_key_changes_crop(capsys, landscape_clip):
    # Off centre: the pattern has a bar edge in the middle that a centred zoom keeps in place.
    seg = {"in": 1.0, "out": 3.0, "crop": {"cx": 0.3, "cy": 0.5, "zoom": 1.0}}
    frames = {}
    for name, keys in (("flat", []), ("zoom", [[2.0, 1.5]])):
        job = _job(capsys, landscape_clip, f"zoom-{name}", [{**seg, "zoom_keys": keys}])
        code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
        assert code == 0, s
        reel = job / "out" / "preview.mp4"
        frames[name] = (_frame(reel, 0.5), _frame(reel, 1.5))
    before = np.abs(frames["flat"][0] - frames["zoom"][0]).mean()
    after = np.abs(frames["flat"][1] - frames["zoom"][1]).mean()
    assert before < 3 and after > 10, (before, after)


def test_preview(capsys, portrait_clip):
    job = _job(capsys, portrait_clip, "prev")
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    assert s["output"] == "out/preview.mp4" and s["cover"] is None
    v = next(
        x for x in _probe(job / "out" / "preview.mp4")["streams"] if x["codec_type"] == "video"
    )
    assert (v["width"], v["height"], v["avg_frame_rate"]) == (540, 960, "30/1")
    assert json.loads((job / "out" / "render.json").read_text())["preview"] is True
    assert not (job / "out" / "reel.mp4").exists()


def test_invalid_edl_aborts_with_its_errors(capsys, portrait_clip):
    job = _job(capsys, portrait_clip, "bad", [{"in": 2.0, "out": 1.0}, {"in": 0, "out": 999}])
    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 1 and "2 problem(s)" in s["error"]
    assert "$.segments[0]: in (2.0) must be < out (1.0)" in out
    assert "beyond the source duration" in out
    assert not any((job / "out").iterdir())


def test_rotated_source_renders_upright(capsys, rotated_clip):
    job = _job(capsys, rotated_clip, "rot", [{"in": 0.5, "out": 2.0}])
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    assert s["min_crop_width"] == 1080  # display size 1080x1920: full width, no crop needed
    assert not [w for w in s["warnings"] if w.startswith("crop")]


def _hlg_clip(folder: Path) -> tuple[Path, Path]:
    """The same pattern as SDR and as HLG/BT.2020 10-bit HEVC (like a phone records)."""
    sdr, hlg = folder / "pattern-sdr.mp4", folder / "pattern-hlg.mov"
    src = ["-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=3",
           "-f", "lavfi", "-i", "sine=f=440:d=3:sample_rate=48000"]  # fmt: skip
    if not sdr.is_file():
        _ff_out(
            *src, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(sdr)
        )
    if not hlg.is_file():
        _ff_out(
            *src,
            "-vf", "zscale=tin=bt709:min=bt709:pin=bt709:t=arib-std-b67:p=bt2020:m=bt2020nc,"
            "format=yuv420p10le",
            "-c:v", "libx265", "-x265-params", "log-level=none", "-color_primaries", "bt2020",
            "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
            "-c:a", "aac", "-shortest", str(hlg),
        )  # fmt: skip
    return sdr, hlg


def test_hdr_source_is_tonemapped_to_bt709(capsys, clip_dir):
    sdr, hlg = _hlg_clip(clip_dir)
    seg = [{"in": 0.5, "out": 2.5}]
    means = {}
    for name, clip in (("sdr", sdr), ("hlg", hlg)):
        job = _job(capsys, clip, f"color-{name}", seg)
        code, s, _ = run_tool(capsys, "ingest", "--job", str(job))
        assert code == 0 and s["hdr"] == (name == "hlg"), s
        code, s, _ = run_tool(capsys, "render", "--job", str(job))
        assert code == 0 and s["hdr_tonemapped"] == (name == "hlg"), s
        means[name] = _frame(job / "out" / "reel.mp4", 1.0).mean()
        if name == "hlg":
            for f in (job / "out" / "reel.mp4", job / "work" / "proxy.mp4"):
                v = next(x for x in _probe(f)["streams"] if x["codec_type"] == "video")
                assert v["pix_fmt"] == "yuv420p"
                tags = (v["color_primaries"], v["color_transfer"], v["color_space"])
                assert tags == ("bt709", "bt709", "bt709"), f.name
    # Tonemapped HLG stays in the SDR range (not washed out, not crushed). The pattern is SDR
    # content wrapped as HLG, i.e. mapped to 100 nits; with 203 nit reference white it comes out
    # darker than the SDR original, so the tolerance is wider than for real footage.
    assert abs(means["hlg"] - means["sdr"]) < 45, means


def _vfr_sync_clip(folder: Path) -> Path:
    """Variable frame rate clip with a white flash and a beep together every 4 s."""
    out = folder / "sync-vfr.mp4"
    if out.is_file():
        return out
    _ff_out(
        "-f", "lavfi", "-i", "color=c=black:s=320x568:r=60:d=24",
        "-f", "lavfi", "-i", "aevalsrc='if(lt(mod(t,4),0.1),0.5*sin(2*PI*1000*t),0)':s=48000:d=24",
        # Drop frames irregularly: the result is VFR (avg ~51 fps, gaps of 1-2 frames).
        "-vf", "drawbox=c=white:t=fill:enable='lt(mod(t,4),0.1)',select='not(eq(mod(n,7),3))'",
        "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(out),
    )  # fmt: skip
    return out


def _onsets(signal: np.ndarray, rate: float, thresh: float) -> list[float]:
    on = signal > thresh
    starts = np.flatnonzero(on[1:] & ~on[:-1]) + 1
    return [float(i / rate) for i in starts]


def test_vfr_source_keeps_av_sync(capsys, clip_dir):
    clip = _vfr_sync_clip(clip_dir)
    segs = [{"in": 1.5, "out": 6.5}, {"in": 9.5, "out": 14.5}, {"in": 15.5, "out": 23.9}]
    job = _job(capsys, clip, "sync", segs)
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    reel = job / "out" / "preview.mp4"
    (v,) = [x for x in _probe(reel)["streams"] if x["codec_type"] == "video"]
    assert v["avg_frame_rate"] == "30/1"
    luma = (
        np.frombuffer(
            _ff_out("-i", str(reel), "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt", "gray", "-"),
            np.uint8,
        )
        .reshape(-1, 64)
        .mean(axis=1)
    )
    flashes = _onsets(luma, 30, 128)
    env = np.abs(_audio(reel)).reshape(-1, RATE // 1000).max(axis=1)  # 1 ms envelope
    beeps = _onsets(env, 1000, 0.05)
    # Flashes at source 4, 12, 16, 20 -> output 2.5, 7.5, 10.5, 14.5.
    assert flashes == pytest.approx([2.5, 7.5, 10.5, 14.5], abs=FRAME + 1e-3)
    assert len(beeps) == len(flashes)
    assert max(abs(a - b) for a, b in zip(flashes, beeps, strict=True)) <= FRAME + 0.005


def test_silent_source_renders_with_a_warning(capsys, clip_dir):
    clip = clip_dir / "silent.mp4"
    if not clip.is_file():
        _ff_out(
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=3",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip),
        )  # fmt: skip
    job = _job(capsys, clip, "silent", [{"in": 0.5, "out": 2.5}])
    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, s
    assert s["loudness"]["integrated_lufs"] is None and "the reel is silent" in s["warnings"]
    json.loads((job / "out" / "render.json").read_text())  # strict JSON, no -Infinity


@pytest.mark.parametrize(("fmt", "size"), [("16:9", (1920, 1080)), ("1:1", (1080, 1080))])
def test_render_follows_the_job_format(capsys, landscape_clip, fmt, size):
    """The job's format decides the output size (reel, cover, verify), also when the EDL
    was written with the 9:16 size; the crop window has the output aspect."""
    segs = [{"in": 0.3, "out": 1.0}, {"in": 1.9, "out": 2.6, "zoom_keys": [[2.2, 1.3]]}]
    job = _job(capsys, landscape_clip, f"r-fmt-{size[0]}x{size[1]}", segments=segs)
    code, s, _ = run_tool(capsys, "job", "format", str(job), fmt)
    assert code == 0 and s["format"] == fmt and s["size"] == list(size), s
    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, s
    assert s["size"] == list(size)
    assert any("is not the job's format" in w for w in s["warnings"])
    v = next(x for x in _probe(job / "out" / "reel.mp4")["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"]) == size
    (c,) = _probe(job / "out" / "cover.jpg")["streams"]
    assert (c["width"], c["height"]) == size
    crop = s["min_crop_width"]  # 1080p source: the zoomed window keeps the output aspect
    assert crop == round(1080 * size[0] / size[1] / 1.3 / 2) * 2
    code, v, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and "size" not in v["failed"], v
    doc = json.loads((job / "out" / "verify.json").read_text())
    assert doc["checks"]["size"] == {"ok": True, "size": list(size), "want": list(size)}
