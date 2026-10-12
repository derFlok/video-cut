"""Step 8 end to end with the real renderer: captions + a keyword card -> overlays ->
render (composite) -> verify, on a generated clip with the neutral bundled brand.

Checks what only the whole chain shows: the brand colours survive RGB -> ProRes 4444 ->
yuv420p H.264 (small CIE76 ΔE, read back as BT.709 like a player), overlay frame n lands
on reel frame n, and the caption contrast is really drawn (text vs. what surrounds it).
"""

import json
import subprocess
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool
from test_renderer import needs_renderer

from videocut import brand
from videocut.paths import RENDERER, tool_env
from videocut.tools.job import approve_job

pytestmark = [pytest.mark.media, needs_renderer]

SEGMENTS = [{"in": 0.5, "out": 2.0}, {"in": 3.0, "out": 4.5}]


def _ff(*args: str) -> bytes:
    return subprocess.run(
        [clips.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
        capture_output=True,
        check=True,
    ).stdout


def _words(t0: float, words: list[str]) -> dict:
    ws = [
        {
            "w": w,
            "t0": round(t0 + i * 0.35, 3),
            "t1": round(t0 + i * 0.35 + 0.33, 3),
            "emph": i == 1,
        }
        for i, w in enumerate(words)
    ]
    return {"t0": ws[0]["t0"], "t1": ws[-1]["t1"], "words": ws}


def _frame(path: Path, n: int, size=(1080, 1920)) -> np.ndarray:
    raw = _ff(
        "-i", str(path), "-vf", f"select=eq(n\\,{n}),scale=in_color_matrix=bt709:in_range=tv",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    )  # fmt: skip
    return np.frombuffer(raw, np.uint8).astype(float).reshape(size[1], size[0], 3)


def _lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb / 255
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    x, y, z = f[..., 0], f[..., 1], f[..., 2]
    return np.stack([116 * y - 16, 500 * (x - y), 200 * (y - z)], -1)


def _erode(mask: np.ndarray, n: int) -> np.ndarray:
    for _ in range(n):
        m = mask.copy()
        for axis in (0, 1):
            for d in (1, -1):
                m &= np.roll(mask, d, axis=axis)
        mask = m
    return mask


def _dilate(mask: np.ndarray, n: int) -> np.ndarray:
    return ~_erode(~mask, n)


def _luminance(rgb: np.ndarray) -> np.ndarray:
    c = rgb / 255
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c @ np.array([0.2126, 0.7152, 0.0722])


def _hex(h: str) -> np.ndarray:
    return np.array([int(h[i : i + 2], 16) for i in (1, 3, 5)], float)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("src") / "take.mp4"
    _ff(
        "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=5",
        "-f", "lavfi", "-i", "sine=f=300:d=5",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


def test_overlays_render_verify_with_the_real_renderer(capsys, source, videocut_home):
    code, s, _ = run_tool(capsys, "job", "new", str(source), "--id", "e2e", "--brand", "_example")
    assert code == 0, s
    job = Path(s["job"])
    edl = {
        "fps": 30,
        "size": [1080, 1920],
        "source": s["source"],
        "segments": SEGMENTS,
        "captions": [_words(0.1, ["Alpha", "beta", "gamma"]), _words(1.5, ["Delta", "epsilon"])],
        "overlays": [{"scene": "keyword", "t0": 0.8, "t1": 2.6, "props": {"text": "Keyword"}}],
    }
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: e2e\n")
    approve_job(job)

    code, s, _ = run_tool(capsys, "overlays", "--job", str(job))
    assert code == 0 and s["rendered"] and s["renderer"]["undrawn_items"] == 0, s
    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0 and s["overlay"] is True, s
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and s["overlay_flagged"] == [], s
    assert s["overlay_sheets"] == ["out/verify/overlays_01.jpg"]

    tokens = brand.load("_example")
    reel, doc = job / "out" / "reel.mp4", job / "work" / "overlays.json"
    # 1.6 s: the second caption group is entering (it moves every frame), so a frame lag
    # between overlay and reel shows up as a large ΔE; 2.0 s: everything has settled.
    for t in (1.6, 2.0):
        png = job / "work" / f"still_{t}.png"
        subprocess.run(
            ["node", str(RENDERER / "render.js"), str(doc), "--still", str(t), str(png)],
            check=True, capture_output=True, env=tool_env(),
        )  # fmt: skip
        ff = _ff("-i", str(png), "-f", "rawvideo", "-pix_fmt", "rgba", "-")
        ov = np.frombuffer(ff, np.uint8).astype(float).reshape(1920, 1080, 4)
        frame = _frame(reel, round(t * 30))
        opaque = _erode(ov[..., 3] == 255, 3)  # away from edges (4:2:0 chroma)
        de = np.linalg.norm(_lab(ov[..., :3][opaque]) - _lab(frame[opaque]), axis=-1)
        assert opaque.sum() > 2000 and de.mean() < 3, (t, de.mean())
        for key in ("text", "highlight", "box"):
            want = _hex(tokens["caption_colors"][key])
            sel = opaque & (np.abs(ov[..., :3] - want).sum(-1) < 6)
            if sel.sum() > 200:
                got = _lab(frame[sel]).mean(0)
                assert np.linalg.norm(got - _lab(want)) < 3, (t, key, got)

        # Contrast actually drawn: caption text vs. the 2..6 px ring around its glyphs.
        text = _erode(
            opaque & (np.abs(ov[..., :3] - _hex(tokens["caption_colors"]["text"])).sum(-1) < 6), 1
        )
        near = _dilate(text, 2)
        ring = _dilate(text, 6) & ~near
        lt = float(np.median(_luminance(frame[text])))
        for p in (10, 90):
            lr = float(np.percentile(_luminance(frame[ring]), p))
            ratio = (max(lt, lr) + 0.05) / (min(lt, lr) + 0.05)
            assert ratio >= 4.5, (t, p, ratio)
