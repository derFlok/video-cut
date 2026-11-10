"""Overlay renderer (renderer/render.js): transparent ProRes 4444, boxes, still, sheet.

Renders a 3 s overlays.json (caption groups + a keyword card) with the neutral bundled
brand, and, when present on this machine, with a local test brand that ships font files.
"""

import json
import subprocess
from pathlib import Path

import pytest
from clips import ffmpeg, ffprobe

from videocut import brand
from videocut.paths import BIN, BROWSERS, RENDERER, ROOT, tool_env

pytestmark = pytest.mark.media

SIZE = (1080, 1920)
FPS = 30
DURATION = 3.0
SAFE = {"top": 250, "bottom": 350, "side": 65}
FACE = [380, 780, 700, 1120]
# Local only, never in the repository: a real brand with font files (skipped if absent).
LOCAL_BRAND = Path.home() / "VideoCut" / "brands" / "brand-c"


def _ready() -> bool:
    return (
        (BIN / "node").is_file()
        and (RENDERER / "node_modules" / "playwright-core").is_dir()
        and any(BROWSERS.glob("chromium*"))
        and ffmpeg() is not None
        and ffprobe() is not None
    )


needs_renderer = pytest.mark.skipif(
    not _ready(), reason="needs node, playwright-core, Chromium, ffmpeg"
)


def resolved_tokens(brand_dir: Path, monkeypatch) -> dict:
    """Resolved tokens (videocut.brand.load) of a bundled or $VIDEOCUT_HOME brand folder."""
    if brand_dir.name != brand.EXAMPLE_ID:
        monkeypatch.setenv("VIDEOCUT_HOME", str(brand_dir.parent.parent))
    return brand.load(brand_dir.name)


def overlays(tokens: dict) -> dict:
    def group(t0: float, words: list[str]) -> dict:
        ws = [
            {
                "w": w,
                "t0": round(t0 + i * 0.3, 2),
                "t1": round(t0 + i * 0.3 + 0.28, 2),
                "emph": i == 1,
            }
            for i, w in enumerate(words)
        ]
        return {"t0": ws[0]["t0"], "t1": ws[-1]["t1"], "words": ws}

    return {
        "size": list(SIZE),
        "fps": FPS,
        "duration": DURATION,
        "tokens": tokens,
        "safe": SAFE,
        "face": [{"t": i / 5, "box": FACE} for i in range(int(DURATION * 5) + 1)],
        "captions": [
            group(0.4, ["one", "two", "three", "four"]),
            group(1.6, ["five", "six", "seven"]),
        ],
        "overlays": [{"scene": "keyword", "t0": 1.0, "t1": 2.7, "props": {"text": "Big word"}}],
    }


def render(spec: dict, tmp: Path, *extra: str) -> dict:
    src = tmp / "overlays.json"
    src.write_text(json.dumps(spec))
    proc = subprocess.run(
        [str(BIN / "node"), str(RENDERER / "render.js"), str(src), *extra],
        cwd=ROOT,
        env=tool_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert proc.returncode == 0, (summary, proc.stderr[-2000:])
    return summary


def probe(mov: Path) -> dict:
    out = subprocess.run(
        [
            ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=codec_name,profile,pix_fmt,width,height,nb_read_frames,color_space",
            "-of",
            "json",
            str(mov),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)["streams"][0]


def alpha_frames(mov: Path) -> list[bytes]:
    w, h = SIZE[0] // 4, SIZE[1] // 4
    raw = subprocess.run(
        [
            ffmpeg(),
            "-v",
            "error",
            "-i",
            str(mov),
            "-vf",
            f"alphaextract,scale={w}:{h}:flags=neighbor",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    n = w * h
    return [raw[i : i + n] for i in range(0, len(raw), n)]


def overlap(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


@needs_renderer
def test_render_example_brand(tmp_path, monkeypatch):
    tokens = resolved_tokens(ROOT / "brands" / "_example", monkeypatch)
    mov, boxes, still, sheet = (tmp_path / n for n in ("o.mov", "boxes.json", "s.png", "sheet.jpg"))
    s = render(
        overlays(tokens),
        tmp_path,
        "--out",
        str(mov),
        "--boxes",
        str(boxes),
        "--still",
        "1.5",
        str(still),
        "--sheet",
        str(sheet),
    )
    assert s["ok"] and s["tool"] == "overlay_render"
    assert s["frames"] == int(DURATION * FPS) and s["frames_per_second"] > 0
    assert s["fonts"]["fallback"] is True
    assert s["overlaps"] == {"face": 0, "blocks": 0, "safe_zone": 0}
    assert s["undrawn_items"] == 0

    st = probe(mov)
    assert st["codec_name"] == "prores" and "4444" in st["profile"]
    # Encoded as yuva444p10le; ffprobe reports the ProRes decoder's 4:4:4:4 output format.
    assert st["pix_fmt"].startswith("yuva444")
    assert (st["width"], st["height"]) == SIZE
    assert st.get("color_space") == "bt709"  # composited into a BT.709 reel
    assert int(st["nb_read_frames"]) == int(DURATION * FPS)

    frames = alpha_frames(mov)
    assert len(frames) == int(DURATION * FPS)
    assert max(frames[0]) == 0, "nothing is on screen at t=0: fully transparent"
    opaque = [f for f in frames if max(f) == 255]
    assert opaque, "some frames have fully opaque pixels"
    assert all(min(f) == 0 for f in opaque), "drawn frames stay transparent around the overlay"

    drawn = json.loads(boxes.read_text())
    assert sorted(b["scene"] for b in drawn) == ["caption", "caption", "keyword"]
    w, h = SIZE
    for b in drawn:
        x0, y0, x1, y1 = b["box"]
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h
        assert y0 >= SAFE["top"] and y1 <= h - SAFE["bottom"], b
        assert x0 >= SAFE["side"] and x1 <= w - SAFE["side"], b
        assert not overlap(b["box"], FACE), b
    # Blocks on screen at the same time do not overlap each other.
    for i, a in enumerate(drawn):
        for b in drawn[i + 1 :]:
            if a["t0"] < b["t1"] and b["t0"] < a["t1"]:
                assert not overlap(a["box"], b["box"]), (a, b)

    assert still.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert still.read_bytes()[25] == 6, "PNG colour type RGBA"
    assert sheet.read_bytes()[:2] == b"\xff\xd8"


@needs_renderer
def test_still_is_deterministic_and_blur_runs(tmp_path, monkeypatch):
    spec = overlays(resolved_tokens(ROOT / "brands" / "_example", monkeypatch))
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    render(spec, tmp_path, "--still", "1.2", str(a))
    render(spec, tmp_path, "--still", "1.2", str(b))
    assert a.read_bytes() == b.read_bytes()
    c = tmp_path / "c.png"
    s = render(spec, tmp_path, "--still", "1.2", str(c), "--blur", "4")
    assert s["ok"] and c.read_bytes() != a.read_bytes()


@needs_renderer
def test_bad_input_fails_with_summary(tmp_path):
    src = tmp_path / "overlays.json"
    src.write_text(json.dumps({"size": [10, 10], "fps": 0, "duration": 1, "tokens": {}}))
    proc = subprocess.run(
        [
            str(BIN / "node"),
            str(RENDERER / "render.js"),
            str(src),
            "--out",
            str(tmp_path / "x.mov"),
        ],
        env=tool_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 1
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["ok"] is False and "fps" in summary["error"]


@needs_renderer
@pytest.mark.skipif(
    not (LOCAL_BRAND / "brand.json").is_file(), reason="local test brand not present"
)
def test_local_brand_fonts_load(tmp_path, monkeypatch):
    tokens = resolved_tokens(LOCAL_BRAND, monkeypatch)
    files = tokens.get("fonts", {}).get("files") or {}
    if not files:
        pytest.skip("local test brand has no font files")
    mov = tmp_path / "o.mov"
    s = render(overlays(tokens), tmp_path, "--out", str(mov))
    assert s["fonts"] == {"files": len(files), "loaded": len(files), "fallback": False}
    assert probe(mov)["pix_fmt"].startswith("yuva444")


@needs_renderer
def test_ffmpeg_failure_and_edge_items(tmp_path, monkeypatch):
    spec = overlays(resolved_tokens(ROOT / "brands" / "_example", monkeypatch))
    src = tmp_path / "overlays.json"
    src.write_text(json.dumps(spec))
    bad = tmp_path / "missing-dir" / "o.mov"
    proc = subprocess.run(
        [str(BIN / "node"), str(RENDERER / "render.js"), str(src), "--out", str(bad)],
        env=tool_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout.strip().splitlines()[-1])["ok"] is False

    # Empty groups and a caption scene among overlays draw nothing; boxes without --out
    # still cover the whole duration.
    spec["captions"].append({"t0": 2.0, "t1": 2.5, "words": []})
    spec["overlays"].append({"scene": "caption", "t0": 0.5, "t1": 1.0, "props": {}})
    boxes, still = tmp_path / "b.json", tmp_path / "s.png"
    s = render(spec, tmp_path, "--boxes", str(boxes), "--still", "0.1", str(still))
    assert s["undrawn_items"] == 1
    assert sorted(b["scene"] for b in json.loads(boxes.read_text())) == [
        "caption",
        "caption",
        "keyword",
    ]


@needs_renderer
def test_brand_sheet_shows_every_scene(tmp_path, capsys):
    from helpers import run_tool

    from videocut.tools import brand as brand_tool

    out = tmp_path / "preview.jpg"
    code, s, _ = run_tool(capsys, "brand", "sheet", "_example", "--out", str(out))
    assert code == 0, s
    scenes = brand_tool.scene_names()
    assert s["scenes"][0] == "caption" and set(s["scenes"]) == set(scenes)
    assert s["skipped_scenes"] == [] and s["no_sample"] == []
    st = probe_image(out)
    rows = -(-len(scenes) // 3)
    # the scene grid, then the motion row (motion-scene stills in the brand's motion style)
    assert st["width"] == 1080 and rows * 640 + 100 < st["height"] < rows * 640 + 400
    assert s["motion"]["theme"] == "auto" and s["motion"]["depth"] == "soft"


def probe_image(path: Path) -> dict:
    out = subprocess.run(
        [
            ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)["streams"][0]
