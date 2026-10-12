import json
import subprocess
import sys
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut.paths import ROOT
from videocut.tools import face as face_mod
from videocut.tools import overlays

pytestmark = pytest.mark.skipif(clips.ffmpeg() is None, reason="needs ffmpeg")

# Stand-in for renderer/render.js that honours its CLI contract:
#   <overlays.json> --out <mov> [--boxes <json>]; one JSON summary line last on stdout.
FAKE_RENDERER = """
import json, sys
args = sys.argv[1:]
doc = json.load(open(args[0]))
out = args[args.index("--out") + 1]
open(out, "wb").write(b"fake")
boxes = [{"scene": "caption", "t0": c["t0"], "t1": c["t1"], "box": [100, 900, 980, 1000]}
         for c in doc["captions"]]
if "--boxes" in args:
    json.dump(boxes, open(args[args.index("--boxes") + 1], "w"))
json.dump({"seen": sorted(doc), "brand": doc["tokens"]["name"]}, open(out + ".doc.json", "w"))
print(json.dumps({"ok": True, "frames": round(doc["duration"] * doc["fps"])}))
"""


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("src") / "take.mp4"
    subprocess.run(
        [
            clips.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=f=300:d=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture
def fake_renderer(tmp_path, monkeypatch):
    script = tmp_path / "fake_render.py"
    script.write_text(FAKE_RENDERER)
    monkeypatch.setattr(overlays, "renderer_cmd", lambda: [sys.executable, str(script)])
    return script


def _job(capsys, source, **edl_extra) -> Path:
    code, s, _ = run_tool(capsys, "job", "new", str(source), "--id", "ov1")
    assert code == 0, s
    job = Path(s["job"])
    edl = {
        "fps": 30, "size": [1080, 1920], "source": s["source"],
        "segments": [{"in": 0.0, "out": 1.0}, {"in": 2.0, "out": 3.0, "zoom_keys": [[2.5, 1.5]]}],
        "captions": [{"t0": 0.1, "t1": 0.9, "words": [{"w": "hi", "t0": 0.1, "t1": 0.5}]}],
        "overlays": [{"scene": "keyword", "t0": 1.2, "t1": 1.8, "props": {"text": "x"}}],
        **edl_extra,
    }  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    return job


def test_overlays_json_contract_and_renderer_call(capsys, source, fake_renderer):
    job = _job(capsys, source)
    src = json.loads((job / "job.json").read_text())["source"]
    frames = [
        {"t": t / 10, "found": True, "box": [0.45, 0.3, 0.55, 0.5], "cx": 0.5, "cy": 0.4,
         "blink": 0.1, "gaze_off": 0.1}
        for t in range(40)
    ]  # fmt: skip
    face = {
        "version": face_mod.FACE_VERSION, "source": src, "fps_sampled": 10, "size": [480, 270],
        "frames": frames, "median": face_mod.median_of(frames), "blinks": [],
    }  # fmt: skip
    (job / "work" / "face.json").write_text(json.dumps(face))

    code, s, _ = run_tool(capsys, "overlays", "--job", str(job))
    assert code == 0, s
    assert s["rendered"] and s["box_count"] == 1 and s["renderer"]["frames"] == 60
    doc = json.loads((job / "work" / "overlays.json").read_text())
    keys = {"size", "fps", "duration", "tokens", "safe", "face", "captions", "overlays"}
    assert set(doc) == keys
    assert doc["size"] == [1080, 1920] and doc["fps"] == 30 and doc["duration"] == 2.0
    assert doc["safe"] == {"top": 250, "bottom": 350, "side": 65}
    assert doc["tokens"]["name"] and "colors" in doc["tokens"]  # the neutral bundled brand
    assert doc["captions"][0]["words"][0]["w"] == "hi" and doc["overlays"][0]["scene"] == "keyword"
    # face: samples in kept ranges only (0..1 s and 2..3 s source), in output time
    ts = [f["t"] for f in doc["face"]]
    assert ts[0] == 0.0 and max(ts) < 2.0
    assert len(ts) == 20
    before, after = doc["face"][12]["box"], doc["face"][18]["box"]  # 1.2 s vs. 1.8 s (zoomed)
    assert (after[2] - after[0]) > 1.4 * (before[2] - before[0])
    seen = json.loads((job / "work" / "overlay.mov.doc.json").read_text())
    assert seen["seen"] == sorted(keys)


def test_no_render_only_writes_the_json(capsys, source, fake_renderer):
    job = _job(capsys, source)
    code, s, _ = run_tool(capsys, "overlays", "--no-render", "--job", str(job))
    assert code == 0 and s["rendered"] is False and s["face_samples"] == 0
    assert (job / "work" / "overlays.json").is_file()
    assert not (job / "work" / "overlay.mov").exists()


def test_renderer_failure_is_reported(capsys, source, tmp_path, monkeypatch):
    bad = tmp_path / "bad.py"
    bad.write_text('import json; print(json.dumps({"ok": False, "error": "no scene x"})); exit(1)')
    monkeypatch.setattr(overlays, "renderer_cmd", lambda: [sys.executable, str(bad)])
    job = _job(capsys, source)
    code, s, _ = run_tool(capsys, "overlays", "--job", str(job))
    assert code == 1 and "no scene x" in s["error"]


def test_brand_from_job_json(capsys, source, fake_renderer, videocut_home):
    job = _job(capsys, source)
    brand_dir = videocut_home / "brands" / "brand-a"
    brand_dir.mkdir(parents=True)
    tokens = json.loads((ROOT / "brands" / "_example" / "brand.json").read_text())
    (brand_dir / "brand.json").write_text(json.dumps({**tokens, "name": "brand-a"}))
    data = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**data, "brand": "brand-a"}))
    code, s, _ = run_tool(capsys, "overlays", "--no-render", "--job", str(job))
    assert code == 0, s
    doc = json.loads((job / "work" / "overlays.json").read_text())
    assert doc["tokens"]["name"] == "brand-a" and doc["tokens"]["caption_colors"]


def test_bad_brand_and_edl_beyond_source_are_errors(capsys, source, fake_renderer):
    job = _job(capsys, source)
    data = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**data, "brand": ["x"]}))
    code, s, _ = run_tool(capsys, "overlays", "--no-render", "--job", str(job))
    assert code == 1 and "brand" in s["error"]
    (job / "job.json").write_text(json.dumps(data))
    edl = json.loads((job / "work" / "edl.json").read_text())
    edl["segments"][-1]["out"] = 9.0  # the source is 4 s long
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    code, s, _ = run_tool(capsys, "overlays", "--no-render", "--job", str(job))
    assert code == 1 and "beyond the source duration" in s["error"]
