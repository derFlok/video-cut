import json
import os
import subprocess
import time
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool

from videocut.tools.job import approve_job

pytestmark = pytest.mark.skipif(clips.ffmpeg() is None, reason="needs ffmpeg")

SEGMENTS = [{"in": 0.5, "out": 2.0}, {"in": 3.0, "out": 4.5}]
DUR = 3.0
BOX = (300, 800, 700, 1100)  # opaque overlay box in output px (x0, y0, x1, y1)
SMALL = (108, 192)  # decoded frame size for the pixel checks (1/10)


def _ff(*args: str) -> bytes:
    return subprocess.run(
        [clips.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
        capture_output=True,
        check=True,
    ).stdout


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("src") / "take.mp4"
    _ff(
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=5",
        "-f", "lavfi", "-i", "sine=f=300:d=5",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


def _overlay(path: Path) -> None:
    """Transparent ProRes 4444 at the output size with one opaque red box."""
    x0, y0, x1, y1 = BOX
    _ff(
        "-f", "lavfi", "-i", f"color=c=black@0.0:s=1080x1920:r=30:d={DUR},format=rgba",
        "-vf", f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color=red:t=fill:replace=1",
        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(path),
    )  # fmt: skip


def _rgb(path: Path, t: float) -> np.ndarray:
    raw = _ff(
        "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
        "-vf", f"scale={SMALL[0]}:{SMALL[1]}:flags=area",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    )  # fmt: skip
    return np.frombuffer(raw, np.uint8).astype(float).reshape(SMALL[1], SMALL[0], 3)


def _regions(out_size):
    """(inside the box, far away from it) as slices of a SMALL frame."""
    k = SMALL[0] / out_size[0]
    x0, y0, x1, y1 = (round(v * k) for v in BOX)
    inside = (slice(y0 + 2, y1 - 2), slice(x0 + 2, x1 - 2))
    far = (slice(y1 + 15, SMALL[1] - 5), slice(5, SMALL[0] - 5))
    return inside, far


def test_render_composites_a_fresh_overlay(capsys, source):
    code, s, _ = run_tool(capsys, "job", "new", str(source), "--id", "ovr")
    job = Path(s["job"])
    edl = {"fps": 30, "size": [1080, 1920], "source": s["source"], "segments": SEGMENTS}
    edl_path = job / "work" / "edl.json"
    edl_path.write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: overlay test\n")
    approve_job(job)

    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0 and s["overlay"] is False, s
    reel = job / "out" / "reel.mp4"
    base = [_rgb(reel, t) for t in (0.5, 2.0)]
    assert "overlay" not in json.loads((job / "out" / "render.json").read_text())

    _overlay(job / "work" / "overlay.mov")
    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0 and s["overlay"] is True, s
    assert json.loads((job / "out" / "render.json").read_text())["overlay"] is True
    assert abs(s["duration"] - DUR) <= 1 / 30 + 1e-3
    inside, far = _regions((1080, 1920))
    for t, b in zip((0.5, 2.0), base, strict=True):
        f = _rgb(reel, t)
        r, g, bl = (f[inside][..., c].mean() for c in range(3))
        assert r > 200 and g < 60 and bl < 60, (t, r, g, bl)  # the overlay is visible
        assert np.abs(f[far] - b[far]).mean() < 3  # alpha 0: the video is unchanged

    # preview composites too, scaled to the half size
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 0 and s["overlay"] is True and s["size"] == [540, 960], s
    f = _rgb(job / "out" / "preview.mp4", 1.0)
    assert f[inside][..., 0].mean() > 200 and f[inside][..., 1].mean() < 60

    # an EDL newer than the overlay: the stale overlay is left out with a warning
    later = time.time() + 5
    os.utime(edl_path, (later, later))
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 0 and s["overlay"] is False
    assert any("overlay.mov is older" in w for w in s["warnings"])
