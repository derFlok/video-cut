"""Scene library (renderer/scenes): one overlays.json with every scene, rendered with the
neutral bundled brand to ProRes 4444 + boxes; screenshot redaction never leaks a pixel;
image files outside the job folder are refused."""

import json
import subprocess
from pathlib import Path

import pytest
from clips import ffmpeg
from test_renderer import FACE, FPS, SAFE, SIZE, needs_renderer, probe, resolved_tokens

from videocut.paths import BIN, RENDERER, ROOT, tool_env
from videocut.tools import _ocr

pytestmark = pytest.mark.media

DURATION = 12.0
SHOT_AT = (5.6, 7.6)
# The screenshot is grey with one pure red box: the part that must be redacted.
SECRET = (0.15, 0.2, 0.55, 0.35)
SCENES = [
    "bar_chart",
    "caption",
    "chat_bubble",
    "cta_end",
    "keyword",
    "list_item",
    "lower_third",
    "number_card",
    "screenshot_frame",
    "split_compare",
]


def make_job(tmp: Path, home: Path) -> tuple[Path, Path]:
    job = tmp / "job"
    (job / "input").mkdir(parents=True)
    (job / "work").mkdir()
    w, h = 1600, 1000
    x0, y0, x1, y1 = SECRET
    subprocess.run(
        [
            ffmpeg(),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x808080:s={w}x{h}",
            "-vf",
            f"drawbox=x={int(x0 * w)}:y={int(y0 * h)}:w={int((x1 - x0) * w)}"
            f":h={int((y1 - y0) * h)}:color=0xFF0000:t=fill",
            "-frames:v",
            "1",
            str(job / "input" / "shot.png"),
        ],
        check=True,
    )
    logo = home / "brands" / "test" / "logo-light.svg"
    logo.parent.mkdir(parents=True)
    logo.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="100">'
        '<rect width="300" height="100" fill="#333"/></svg>'
    )
    return job, logo


def spec(tokens: dict, logo: Path | None, redact: bool = True) -> dict:
    tokens = {**tokens, "logo": {"light": str(logo) if logo else None, "placement": "outro"}}
    words = [
        {"w": w, "t0": round(0.4 + i * 0.3, 2), "t1": round(0.68 + i * 0.3, 2)}
        for i, w in enumerate(["every", "scene", "once"])
    ]
    shot = {"file": "input/shot.png", "focus": [0.5, 0.5, 0.9, 0.9]}
    if redact:
        shot["redact"] = [list(SECRET)]

    def o(scene, t0, t1, **props):
        return {"scene": scene, "t0": t0, "t1": t1, "props": props}

    return {
        "size": list(SIZE),
        "fps": FPS,
        "duration": DURATION,
        "tokens": tokens,
        "safe": SAFE,
        "face": [{"t": i / 2, "box": FACE} for i in range(int(DURATION * 2) + 1)],
        "captions": [{"t0": words[0]["t0"], "t1": words[-1]["t1"], "words": words}],
        "overlays": [
            o("keyword", 0.2, 1.4, text="Big word"),
            o("lower_third", 3.0, 4.2, title="Topic of the day", subtitle="Part 2 of 5"),
            o("number_card", 1.5, 2.9, value="1,250", label="answers a day"),
            o("list_item", 3.0, 4.2, index=2, text="Write the hook first", total=3),
            o("chat_bubble", 4.3, 5.5, text="How long does it take?", **{"from": "viewer"}),
            o("screenshot_frame", *SHOT_AT, **shot),
            o(
                "split_compare",
                7.7,
                8.9,
                left={"title": "Myth", "text": "You need expensive gear"},
                right={"title": "Reality", "text": "A phone is enough"},
            ),
            o(
                "bar_chart",
                9.0,
                10.4,
                bars=[{"label": "Before", "value": 12}, {"label": "After", "value": 31}],
                unit="%",
            ),
            o("cta_end", 10.5, 12.0, text="Send this to a friend", logo=True),
        ],
    }


def render(s: dict, job: Path, *extra: str) -> tuple[int, dict]:
    src = job / "work" / "overlays.json"
    src.write_text(json.dumps(s))
    env = {**tool_env(), "VIDEOCUT_JOB_DIR": str(job)}
    proc = subprocess.run(
        [str(BIN / "node"), str(RENDERER / "render.js"), str(src), *extra],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])


def reddish(rgba: bytes) -> int:
    """Visible pixels that look like the secret (pure red) region."""
    n = 0
    for i in range(0, len(rgba), 4):
        r, g, b, a = rgba[i : i + 4]
        if a > 8 and r > 150 and g < 100 and b < 100:
            n += 1
    return n


def frames_rgba(mov: Path, t0: float, t1: float) -> list[bytes]:
    w, h = SIZE[0] // 4, SIZE[1] // 4
    raw = subprocess.run(
        [
            ffmpeg(),
            "-v",
            "error",
            "-i",
            str(mov),
            "-vf",
            f"trim=start={t0}:end={t1},scale={w}:{h}:flags=area",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    n = w * h * 4
    return [raw[i : i + n] for i in range(0, len(raw), n)]


def overlap(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


@needs_renderer
def test_every_scene_renders_with_example_brand(tmp_path, monkeypatch, videocut_home):
    tokens = resolved_tokens(ROOT / "brands" / "_example", monkeypatch)
    job, logo = make_job(tmp_path, videocut_home)
    mov, boxes = job / "out" / "o.mov", job / "work" / "boxes.json"
    mov.parent.mkdir()
    code, s = render(spec(tokens, logo), job, "--out", str(mov), "--boxes", str(boxes))
    assert code == 0, s
    assert s["overlaps"] == {"face": 0, "blocks": 0, "safe_zone": 0}
    assert s["images"] == 2 and s["undrawn_items"] == 0
    st = probe(mov)
    assert st["codec_name"] == "prores" and "4444" in st["profile"]
    assert st["pix_fmt"].startswith("yuva444") and (st["width"], st["height"]) == SIZE
    assert int(st["nb_read_frames"]) == int(DURATION * FPS)

    drawn = json.loads(boxes.read_text())
    assert sorted(b["scene"] for b in drawn) == SCENES
    w, h = SIZE
    for b in drawn:
        x0, y0, x1, y1 = b["box"]
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, b
        if b["scene"] == "cta_end":
            assert b["box"] == [0, 0, w, h]  # full-frame end plate
            continue
        assert y0 >= SAFE["top"] and y1 <= h - SAFE["bottom"], b
        assert x0 >= SAFE["side"] and x1 <= w - SAFE["side"], b
        assert not overlap(b["box"], FACE), b
    for i, a in enumerate(drawn):
        for b in drawn[i + 1 :]:
            if "cta_end" in (a["scene"], b["scene"]):
                continue
            if a["t0"] < b["t1"] and b["t0"] < a["t1"]:
                assert not overlap(a["box"], b["box"]), (a, b)

    # No frame shows a single unredacted pixel of the secret region.
    frames = frames_rgba(mov, SHOT_AT[0] - 0.1, SHOT_AT[1] + 0.1)
    assert len(frames) >= int((SHOT_AT[1] - SHOT_AT[0]) * FPS)
    visible = [f for f in frames if max(f[3::4]) > 0]
    assert visible, "the screenshot is on screen"
    assert [reddish(f) for f in visible] == [0] * len(visible)


@needs_renderer
def test_leak_check_sees_an_unredacted_screenshot(tmp_path, monkeypatch, videocut_home):
    """Control for the redaction test: without redact the red region is visible."""
    tokens = resolved_tokens(ROOT / "brands" / "_example", monkeypatch)
    job, _ = make_job(tmp_path, videocut_home)
    still = job / "work" / "s.png"
    code, s = render(spec(tokens, None, redact=False), job, "--still", "6.0", str(still))
    assert code == 0, s
    rgba = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(still), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True,
        check=True,
    ).stdout
    assert reddish(rgba) > 1000


@needs_renderer
@pytest.mark.parametrize("ref", ["../secret.png", "{tmp}/secret.png", "input/escape.png"])
def test_screenshot_outside_job_folder_is_refused(tmp_path, monkeypatch, videocut_home, ref):
    tokens = resolved_tokens(ROOT / "brands" / "_example", monkeypatch)
    job, _ = make_job(tmp_path, videocut_home)
    secret = tmp_path / "secret.png"
    secret.write_bytes((job / "input" / "shot.png").read_bytes())
    (job / "input" / "escape.png").symlink_to(secret)
    s = spec(tokens, None)
    shot = next(o for o in s["overlays"] if o["scene"] == "screenshot_frame")
    shot["props"]["file"] = ref.format(tmp=tmp_path)
    code, out = render(s, job, "--still", "6.0", str(job / "work" / "s.png"))
    assert code == 1 and out["ok"] is False
    assert "outside the job folder" in out["error"]


@needs_renderer
@pytest.mark.skipif(not _ocr.available(), reason="needs macOS text recognition")
def test_blurred_screenshot_text_is_unreadable(tmp_path, monkeypatch, videocut_home):
    """props.redact_style "blur": the text in the box is gone for text recognition, the box
    keeps the screenshot's colours (no solid fill); the same still without redaction reads."""
    import clips
    import numpy as np

    if not clips.font():
        pytest.skip("needs a system font")
    tokens = resolved_tokens(ROOT / "brands" / "_example", monkeypatch)
    job, _ = make_job(tmp_path, videocut_home)
    w, h = 1600, 1000
    x0, y0, x1, y1 = SECRET
    text = tmp_path / "secret.txt"
    text.write_text("mara.zentrovia@example.org")
    subprocess.run(
        [ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0xE8EEF4:s={w}x{h}",
         "-vf", f"drawtext=fontfile={clips.font()}:textfile={text}:x={int(x0 * w) + 20}"
         f":y={int(y0 * h) + 40}:fontsize=64:fontcolor=0x1A2433",
         "-frames:v", "1", str(job / "input" / "shot.png")],
        check=True,
    )  # fmt: skip

    def still(redact_style: str | None) -> np.ndarray:
        s = spec(tokens, None, redact=redact_style is not None)
        shot = next(o for o in s["overlays"] if o["scene"] == "screenshot_frame")
        shot["props"].pop("focus")  # the whole image, the box at its largest
        if redact_style:
            shot["props"]["redact_style"] = redact_style
        out = job / "work" / f"still-{redact_style}.png"
        code, res = render(s, job, "--still", "6.0", str(out))
        assert code == 0, res
        raw = subprocess.run(
            [ffmpeg(), "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        return np.frombuffer(raw, np.uint8).reshape(SIZE[1], SIZE[0], 4)

    needle = "zentrovia@example.org"
    rgb = lambda img: np.ascontiguousarray(img[..., :3])  # noqa: E731
    assert _ocr.find(rgb(still(None)), needle), "control: the text is readable unredacted"
    blurred = still("blur")
    assert not _ocr.find(rgb(blurred), needle)
    assert not [x for x in _ocr.recognize(rgb(blurred)) if "zentrovia" in x["text"].lower()]
    # the blur keeps the light screenshot colours; the block is a dark solid fill
    dark = lambda img: int(((img[..., 3] > 200) & (img[..., :3].max(axis=2) < 60)).sum())  # noqa: E731
    assert dark(still("block")) > 20 * dark(blurred) + 1000
