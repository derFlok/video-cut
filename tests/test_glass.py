"""Glass material, pipeline side: brand enum, frost measurement, the composite graphs and
the overlay record of the optional glass files."""

import json
import subprocess
from pathlib import Path

import clips
from test_brand import make_brand

from videocut import brand
from videocut.tools import _glass, motion, overlays, render
from videocut.tools.ingest import probe


def test_glass_is_a_depth_and_stays_opt_in(videocut_home):
    make_brand(videocut_home, "g", motion={"depth": "glass"})
    assert brand.load("g")["motion"]["depth"] == "glass"
    make_brand(videocut_home, "n", motion={})
    assert brand.load("n")["motion"]["depth"] in ("glow", "soft")  # today's defaults
    assert brand.load("_example")["motion"]["depth"] == "soft"
    assert _glass.is_glass(brand.load("g")) and not _glass.is_glass(brand.load("n"))


def test_backdrop_range_measures_the_frost_and_caches_it(tmp_path):
    src = tmp_path / "v.mp4"
    subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=0x202020:s=320x568:d=1,format=yuv420p", "-g", "5", "-c:v", "libx264",
         "-preset", "ultrafast", str(src)],
        check=True,
    )  # fmt: skip
    job = tmp_path / "job"
    (job / "work").mkdir(parents=True)
    rng = _glass.backdrop_range(job, src, probe(src, need_audio=False))
    assert rng is not None and 0 <= rng[0] <= rng[1] < 0.05  # a dark grey picture
    doc = json.loads((job / _glass.CACHE).read_text())
    assert doc["range"] == rng and doc["frames"] >= 1
    assert _glass.backdrop_range(job, src, probe(src, need_audio=False)) == rng  # from the cache


PARAMS = {"sigma": 34.6, "saturation": 1.5, "brightness": -0.04}


def test_overlay_composite_frosts_through_the_mask():
    plain = render.composite_graph(4, 30, (540, 960))
    assert "alphamerge" not in plain
    glass = {"index": 5, "params": PARAMS, "scale": 0.5}
    text = render.composite_graph(4, 30, (540, 960), glass=glass)
    assert "[5:v]setpts=PTS-STARTPTS,fps=30,settb=1/30,setpts=N+0," in text
    assert "scale=135:240:flags=area,gblur=sigma=4.33," in text  # quarter size, sigma * 0.5 / 4
    assert "eq=saturation=1.5:brightness=-0.04" in text
    assert "[gfz][gmk]alphamerge[gfg]" in text and text.rstrip().endswith("[vo]")
    # the frost goes under the overlay, the overlay on top
    assert text.index("[gbase][gfg]overlay") < text.index("[base][ov]overlay")


def test_motion_composite_frosts_only_the_clip_frames(tmp_path):
    mask = tmp_path / "glass.mkv"
    mask.write_bytes(b"x")
    Path(f"{mask}.json").write_text(json.dumps(PARAMS))
    clips_ = [
        {"t0": 1.0, "size": [1080, 500], "place_at": [0, 184], "frames": 60, "clip": "a.mov",
         "mask": mask},
        {"t0": 4.5, "size": [1080, 520], "place_at": [0, 184], "frames": 30, "clip": "b.mov",
         "mask": None},
    ]  # fmt: skip
    text, out = motion.composite_graph(clips_, 3, 30, 1.0, "[vo]")
    assert out == "[mo1]"
    # inputs: clip a (3), its mask (4), clip b (5)
    assert motion.inputs(clips_) == ["-i", "a.mov", "-i", str(mask), "-i", "b.mov"]
    assert "[4:v]setpts=PTS-STARTPTS,fps=30,settb=1/30,setpts=N+30," in text
    assert "trim=start_frame=30:end_frame=90,crop=1080:500:0:184," in text
    assert "[5:v]setpts=PTS-STARTPTS" in text and "[mb1][mc1]overlay=0:184" in text


def test_overlay_record_covers_the_glass_files_when_there_are_some(tmp_path, monkeypatch):
    job = tmp_path / "j"
    (job / "work").mkdir(parents=True)
    for rel in overlays.RECORDED:
        (job / rel).write_text(rel)
    assert set(overlays.file_hashes(job)) == set(overlays.RECORDED)  # none: not listed
    for rel in overlays.GLASS:
        (job / rel).write_text(rel)
    monkeypatch.setattr(overlays, "record_path", lambda j: tmp_path / "rec.json")
    overlays.write_record(job)
    assert overlays.check_record(job) is None
    (job / overlays.GLASS[0]).write_text("tampered")
    assert overlays.GLASS[0] in overlays.check_record(job)
    (job / overlays.GLASS[0]).unlink()
    assert overlays.GLASS[0] in overlays.check_record(job)  # a removed mask counts too
