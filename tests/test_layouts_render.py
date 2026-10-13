"""End-to-end render of the demo layouts on generated material: a landscape camera clip and
a 2880x1800 "screen recording" (fake UI, a sensitive red block, a binary frame counter
burnt in) that starts OFFSET seconds earlier than the camera (screen = camera + OFFSET)."""

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool

from videocut.edl import from_dict
from videocut.tools import _layouts as lay
from videocut.tools.job import approve_job

pytestmark = pytest.mark.media

OFFSET = 3.27
SW, SH = 2880, 1800
BITS, BIT, BIT_STEP, BIT_X, BIT_Y = 11, 160, 170, 40, 40  # frame counter blocks (source px)
SECRET = [1800, 1000, 2400, 1400]  # red block, source px
SECRET_BOX = [SECRET[0] / SW, SECRET[1] / SH, SECRET[2] / SW, SECRET[3] / SH]
SEGMENTS = [
    {"in": 0.3, "out": 1.0},
    {"in": 1.0, "out": 2.5, "layout": "screen",
     "redact": [{"t0": 1.5, "t1": 2.2, "box": SECRET_BOX}]},
    {"in": 2.5, "out": 4.0, "layout": "pip",
     "screen_zoom": [{"t": 3.3, "box": [0.5, 0.45, 0.9, 0.85]}],
     "redact": [{"t0": 2.5, "t1": 4.0, "box": SECRET_BOX}]},
    {"in": 4.0, "out": 5.5, "layout": "split"},
    {"in": 5.5, "out": 7.0, "layout": "screen",
     "screen_zoom": [{"t": 6.0, "box": [0.4, 0.4, 0.4 + 1 / 3.5, 0.4 + 1 / 3.5]}]},
]  # fmt: skip


def _screen(folder: Path, duration: float) -> Path:
    out = folder / "screen.mp4"
    if out.is_file():
        return out
    bits = ",".join(
        f"drawbox=x={BIT_X + k * BIT_STEP}:y={BIT_Y}:w={BIT}:h={BIT}:c=white:t=fill"
        f":enable='eq(mod(floor(round(t*30)/{2**k}),2),1)'"
        for k in range(BITS)
    )
    ui = ",".join(
        [
            f"color=c=0x2E3440:s={SW}x{SH}:r=30:d={duration:.3f}",
            f"drawbox=x=0:y=0:w={BIT_X * 2 + BITS * BIT_STEP}:h={BIT + 2 * BIT_Y}:c=black:t=fill",
            "drawbox=x=200:y=400:w=1300:h=1300:c=0xD8DEE9:t=fill",  # a window
            *(f"drawbox=x=260:y={480 + i * 90}:w=1180:h=60:c=0x4C566A:t=fill" for i in range(12)),
            "drawbox=x='mod(t*300,1200)+250':y=1500:w=40:h=40:c=0x5E81AC:t=fill",  # cursor
            f"drawbox=x={SECRET[0]}:y={SECRET[1]}:w={SECRET[2] - SECRET[0]}"
            f":h={SECRET[3] - SECRET[1]}:c=red:t=fill",
            bits,
        ]
    )
    clips._ff(
        "-f", "lavfi", "-i", ui, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-c:a", "aac", "-shortest", str(out),
    )  # fmt: skip
    return out


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    if not clips.available():
        pytest.skip("needs ffmpeg and the say voice")
    folder = tmp_path_factory.mktemp("layout-clips")
    camera = clips.build(folder, "landscape")
    return camera, _screen(folder, clips.duration(camera) + OFFSET + 1)


def _job(capsys, material, job_id) -> Path:
    camera, screen = material
    code, s, _ = run_tool(capsys, "job", "new", str(camera), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    shutil.copy(screen, job / "input" / "screen.mp4")
    meta = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**meta, "screen": "input/screen.mp4"}))
    sync = {"offset_s": OFFSET, "method": "manual", "confidence": 1.0, "drift_ppm": None,
            "overlap": [0, 8]}  # fmt: skip
    (job / "work" / "sync.json").write_text(json.dumps(sync))
    edl = {"fps": 30, "size": [1080, 1920], "source": s["source"], "segments": SEGMENTS}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: layout test\n")
    approve_job(job)
    return job


def _frames(path: Path, f0: int, f1: int, crop: tuple[int, int, int, int], fmt="gray"):
    """Output frames f0..f1-1, cropped to (w, h, x, y), as an array [n, h, w(, 3)]."""
    w, h, x, y = crop
    raw = subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-i", str(path), "-vf",
         f"trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS,crop={w}:{h}:{x}:{y}",
         "-f", "rawvideo", "-pix_fmt", fmt, "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    shape = (-1, h, w, 3) if fmt == "rgb24" else (-1, h, w)
    return np.frombuffer(raw, np.uint8).reshape(shape).astype(int)


def _starts() -> list[int]:
    out, t = [0], 0.0
    for s in SEGMENTS:
        t += s["out"] - s["in"]
        out.append(round(t * 30))
    return out


def _seg_plan(i: int, out=(1080, 1920)):
    seg = {"in": SEGMENTS[i]["in"], "out": SEGMENTS[i]["out"]}
    edl = from_dict({"fps": 30, "size": [1080, 1920], "source": "input/a.mp4", "segments": [seg]})
    specs, _ = lay.specs({"segments": [SEGMENTS[i]]})
    n = _starts()[i + 1] - _starts()[i]
    return lay.plan_segment(
        specs[0], edl.segments[0], n, Fraction(30), offset=OFFSET, screen=(SW, SH),
        camera=(1920, 1080), out=out, full_out=(1080, 1920), face=None,
    )[0]  # fmt: skip


def test_layouts_render(capsys, material):
    job = _job(capsys, material, "layouts")
    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, (s, out)
    reel = job / "out" / "reel.mp4"
    probe = json.loads(
        subprocess.run(
            [clips.ffprobe(), "-v", "error", "-show_streams", "-of", "json", reel],
            capture_output=True,
            text=True,
            check=True,
        ).stdout  # fmt: skip
    )
    v = next(x for x in probe["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert int(v["nb_frames"]) == _starts()[-1]
    assert any(x["codec_type"] == "audio" for x in probe["streams"])
    report = json.loads((job / "out" / "render.json").read_text())
    assert report["layouts"] == {"face": 1, "screen": 2, "pip": 1, "split": 1}
    assert [c["segment"] for c in report["screen_zoom_clamped"]] == [4]
    assert report["screen_zoom_clamped"][0]["max_zoom"] == round(SW / 1080, 2)
    assert any("blurry" in w for w in s["warnings"])
    starts = _starts()

    # Screen time: the burnt-in counter of segment 1 is camera time + OFFSET.
    seg = _seg_plan(1)
    view, k = seg.view, seg.view.w / SW
    strip = _frames(reel, starts[1], starts[2], (1080, 120, 0, view.y))
    centres = [round((BIT_X + j * BIT_STEP + BIT / 2) * k) for j in range(BITS)]
    cy = round((BIT_Y + BIT / 2) * k)
    for j, img in enumerate(strip):
        got = sum(1 << b for b, x in enumerate(centres) if img[cy, x] > 128)
        assert abs(got - round((SEGMENTS[1]["in"] + j / 30 + OFFSET) * 30)) <= 1, j

    # Redaction: no red inside the block in any redacted frame (also while zooming); the
    # red block is visible before the redaction starts.
    for i in (1, 2):
        seg = _seg_plan(i)
        v = seg.view
        imgs = _frames(reel, starts[i], starts[i + 1], (v.w, v.h, v.x, v.y), "rgb24")
        for n, img in enumerate(imgs):
            x, y, w = lay.window_at(seg, n)
            kk = v.w / w
            x0, x1 = (SECRET[0] - x) * kk + 4, (SECRET[2] - x) * kk - 4
            y0, y1 = (SECRET[1] - y) * kk + 4, (SECRET[3] - y) * kk - 4
            box = img[max(0, int(y0)) : max(0, int(y1)), max(0, int(x0)) : max(0, int(x1))]
            red = (box[..., 0] > 150) & (box[..., 1] < 100) & (box[..., 2] < 100)
            redacted = any(r[0] <= n <= r[1] for r in seg.redact)
            if redacted:
                assert box.size and not red.any(), (i, n)
            elif box.size:
                assert red.mean() > 0.9, (i, n)
    assert any(not any(r[0] <= n <= r[1] for r in _seg_plan(1).redact) for n in range(10))

    # Layout look: screen keeps the bottom on the brand background, split shows the
    # camera there, pip has the inset below the view.
    def bottom(i):
        mid = (starts[i] + starts[i + 1]) // 2
        return _frames(reel, mid, mid + 1, (1080, 300, 0, 1500))[0]

    assert np.abs(bottom(1) - 244).max() < 6  # colors.bg #F4F4F4 of the _example brand
    assert bottom(3).std() > 20
    inset = _seg_plan(2).inset
    mid = (starts[2] + starts[3]) // 2
    pip = _frames(reel, mid, mid + 1, (inset.w, inset.h, inset.x, inset.y))[0]
    assert pip.std() > 20 and abs(pip[2, 2] - 244) < 6  # camera inside, rounded corner


def test_layouts_preview_and_errors(capsys, material):
    job = _job(capsys, material, "layouts-prev")
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    assert s["size"] == [540, 960] and s["output"] == "out/preview.mp4"
    assert [c["segment"] for c in s["screen_zoom_clamped"]] == [4]

    (job / "work" / "sync.json").unlink()
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code != 0 and "sync" in json.dumps(s)
