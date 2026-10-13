"""`videocut tool screen` and `sheet screen` on generated fake screen recordings.

The generator draws a fake UI with ffmpeg only (no font, no third-party media): a moving
pointer, a button that is clicked at CLICK_T (expanding ring, then the button changes), and
a list that scrolls between SCROLL_T0 and SCROLL_T1. All positions are shares of the frame,
so the same scene renders at 2880x1800 and 1920x1080.
"""

import json
import subprocess
from pathlib import Path

import clips
import pytest
from helpers import run_tool

pytestmark = pytest.mark.skipif(clips.ffmpeg() is None, reason="needs ffmpeg")

DURATION = 10
BG, PANEL, BAR = "0x2b2f3a", "0x3a4050", "0x8a93a8"
BTN, BTN_DONE, WHITE = "0x2f6fed", "0x2fbf6f", "0xffffff"
BUTTON = (0.66, 0.69, 0.79, 0.76)  # x0, y0, x1, y1
CLICK_AT = (0.72, 0.725)
CLICK_T = 3.0
LIST = (0.10, 0.22, 0.40, 0.78)
SCROLL_T0, SCROLL_T1 = 5.0, 8.0
POINTER_T0, POINTER_T1 = 1.0, 2.5
POINTER_FROM = (0.15, 0.12)


def _px(v: float, n: int) -> int:
    return int(round(v * n))


def make_screen(path: Path, w: int, h: int, *, scene: bool = True, duration: int = DURATION):
    """Write the fake recording. scene=False: only the pointer wanders, nothing else changes."""
    k = w / 2880
    ptr = max(12, round(28 * k))
    lx0, ly0, lx1, ly1 = (_px(LIST[0], w), _px(LIST[1], h), _px(LIST[2], w), _px(LIST[3], h))
    bx0, by0, bx1, by1 = (
        _px(BUTTON[0], w),
        _px(BUTTON[1], h),
        _px(BUTTON[2], w),
        _px(BUTTON[3], h),
    )
    cx, cy = _px(CLICK_AT[0], w), _px(CLICK_AT[1], h)
    px0, py0 = _px(POINTER_FROM[0], w), _px(POINTER_FROM[1], h)
    p = POINTER_T1 - POINTER_T0
    ramp = f"clip((t-{POINTER_T0})/{p}\\,0\\,1)"
    pointer_to = (cx, cy) if scene else (_px(0.5, w), _px(0.5, h))

    row = max(8, round(60 * k))  # list rows are tall enough to survive the analysis scale
    list_h = 40 * row
    strip = [f"color=c={PANEL}:s={lx1 - lx0}x{list_h}:r=30,format=yuv420p"]
    for i in range(0, 40, 2):
        strip.append(
            f"drawbox=x=8:y={i * row}:w={(lx1 - lx0) * (60 + i % 5 * 8) // 100}"
            f":h={row}:color={BAR}:t=fill"
        )
    travel = f"{(SCROLL_T1 - SCROLL_T0) * 220 * k:.1f}"
    list_y = f"{ly0}-{travel}*clip((t-{SCROLL_T0})/{SCROLL_T1 - SCROLL_T0}\\,0\\,1)"

    chain = [f"color=c={BG}:s={w}x{h}:r=30:d={duration},format=yuv420p[bg]"]
    if scene:
        chain.append(",".join(strip) + "[list]")
    steps = []
    if scene:
        steps.append(f"[bg][list]overlay=x={lx0}:y='{list_y}':eval=frame:shortest=1")
        # Mask the strip outside the list window, then draw the static chrome on top.
        steps.append(f"drawbox=x=0:y=0:w={w}:h={ly0}:color={BG}:t=fill")
        steps.append(f"drawbox=x=0:y={ly1}:w={w}:h={h - ly1}:color={BG}:t=fill")
    else:
        steps.append("[bg]null")
    steps.append(f"drawbox=x=0:y=0:w={w}:h={_px(0.06, h)}:color={PANEL}:t=fill")  # toolbar
    for i in range(6):  # small static "text" on the right
        y = _px(0.25 + i * 0.07, h)
        steps.append(
            f"drawbox=x={_px(0.50, w)}:y={y}:w={_px(0.35 - i * 0.03, w)}"
            f":h={_px(0.012, h)}:color={BAR}:t=fill"
        )
    steps.append(f"drawbox=x={bx0}:y={by0}:w={bx1 - bx0}:h={by1 - by0}:color={BTN}:t=fill")
    if scene:
        steps.append(
            f"drawbox=x={bx0}:y={by0}:w={bx1 - bx0}:h={by1 - by0}:color={BTN_DONE}:t=fill"
            f":enable='gte(t\\,{CLICK_T + 0.1})'"
        )
        # Click highlight: hollow squares growing around the pointer.
        for i in range(4):
            half = round((22 + i * 14) * k)
            t0 = CLICK_T + i * 0.08
            steps.append(
                f"drawbox=x={cx - half}:y={cy - half}:w={2 * half}:h={2 * half}:color={WHITE}"
                f":t={max(2, round(5 * k))}:enable='between(t\\,{t0:.2f}\\,{t0 + 0.08 - 0.01:.2f})'"
            )
    graph = ";".join(chain) + ";" + ",".join(steps) + "[mid]"
    graph = graph.replace("[bg]null,", "[bg]", 1)
    graph += f";color=c={WHITE}:s={ptr}x{ptr}:r=30,format=yuv420p[ptr];"
    graph += (
        f"[mid][ptr]overlay=x='{px0}+({pointer_to[0] - px0})*{ramp}'"
        f":y='{py0}+({pointer_to[1] - py0})*{ramp}':eval=frame:shortest=1[out]"
    )
    subprocess.run(
        [
            clips.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-filter_complex", graph, "-map", "[out]", "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip


@pytest.fixture(scope="module")
def clip_dir_(tmp_path_factory):
    return tmp_path_factory.mktemp("screens")


@pytest.fixture(scope="module", params=[(2880, 1800), (1920, 1080)], ids=["2880", "1920"])
def screen_clip(request, clip_dir_):
    w, h = request.param
    path = clip_dir_ / f"screen-{w}.mp4"
    make_screen(path, w, h)
    return w, h, path


def _job(capsys, src: Path, name: str) -> Path:
    code, s, _ = run_tool(capsys, "job", "new", str(src), "--id", name)
    assert code == 0, s
    job = Path(s["job"])
    data = json.loads((job / "job.json").read_text())
    data["screen"] = f"input/{src.name}"
    (job / "job.json").write_text(json.dumps(data))
    return job


@pytest.fixture
def analysed(capsys, screen_clip):
    w, h, src = screen_clip
    job = _job(capsys, src, f"screen-{w}")
    code, s, out = run_tool(capsys, "screen", "--job", str(job))
    assert code == 0, out
    return w, h, job, json.loads((job / "work" / "screen.json").read_text())


def _inside(box, x, y) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def test_contract_fields(analysed):
    w, h, _, doc = analysed
    assert doc["size"] == [w, h]
    assert doc["fps_sampled"] > 0
    for key in ("activity", "clicks", "scenes", "zoom_targets"):
        assert isinstance(doc[key], list)
    for z in doc["zoom_targets"]:
        assert z["t1"] - z["t0"] >= 1.2 - 1e-6
        assert all(0 <= v <= 1 for v in z["box"]) and z["box"][0] < z["box"][2]
        assert z["max_zoom"] >= 1


def test_clicked_button_becomes_a_zoom_target(analysed):
    _, _, _, doc = analysed
    hits = [
        z for z in doc["zoom_targets"]
        if z["t0"] <= CLICK_T + 0.2 <= z["t1"] and _inside(z["box"], *CLICK_AT)
    ]  # fmt: skip
    assert hits, doc["zoom_targets"]
    z = hits[0]
    assert abs(z["t0"] - CLICK_T) <= 0.8
    # tight on the button, not a half-screen blob (the ring must not widen it much)
    assert z["box"][2] - z["box"][0] <= 0.35 and z["box"][3] - z["box"][1] <= 0.35
    assert z["clicks"] >= 1
    clicks = [c for c in doc["clicks"] if abs(c["t"] - CLICK_T) <= 0.5]
    assert clicks, doc["clicks"]
    assert abs(clicks[0]["x"] - CLICK_AT[0]) < 0.05 and abs(clicks[0]["y"] - CLICK_AT[1]) < 0.05


def test_scrolling_list_is_activity_with_a_stable_target(analysed):
    _, _, _, doc = analysed
    act = [a for a in doc["activity"] if SCROLL_T0 + 0.5 <= a["t"] <= SCROLL_T1 - 0.3]
    assert len(act) >= 10
    list_cx, list_cy = (LIST[0] + LIST[2]) / 2, (LIST[1] + LIST[3]) / 2
    assert all(_inside(a["box"], list_cx, list_cy) for a in act)
    targets = [
        z for z in doc["zoom_targets"] if z["t0"] <= SCROLL_T0 + 1 and z["t1"] >= SCROLL_T1 - 1
    ]
    assert len(targets) == 1, doc["zoom_targets"]
    box = targets[0]["box"]
    assert _inside(box, list_cx, list_cy)
    assert box[0] >= LIST[0] - 0.09 and box[2] <= LIST[2] + 0.09  # the list column plus padding


def test_pointer_alone_is_no_target(analysed):
    _, _, _, doc = analysed
    assert not [z for z in doc["zoom_targets"] if z["t1"] <= CLICK_T - 0.3], doc["zoom_targets"]
    assert doc["cursor"], "pointer motion is reported"


def test_max_zoom_follows_the_recording_width(analysed):
    w, _, _, doc = analysed
    # A job with a screen recording defaults to 16:9: the view is 1728 (16:10) / 1920 wide.
    expect = {2880: 1.66, 1920: 1.0}[w]
    assert doc["format"] == "16:9" and doc["max_zoom"]["screen"] == expect
    assert doc["max_zoom"] == {k: v["max_zoom"] for k, v in doc["by_format"]["16:9"].items()}
    assert doc["zoom_targets"]
    assert all(z["max_zoom"] == expect for z in doc["zoom_targets"])
    assert all(z["zoom"] <= expect for z in doc["zoom_targets"])
    # Portrait: the view fills the 1080 px width.
    assert doc["by_format"]["9:16"]["screen"]["max_zoom"] == {2880: 2.66, 1920: 1.77}[w]


def test_text_size_per_format(analysed):
    w, _, _, doc = analysed
    # 2880 wide counts as HiDPI (26 source px of UI text), 1920 as 1x (13 px).
    assert doc["ui_scale"] == {2880: 2, 1920: 1}[w]
    portrait = doc["by_format"]["9:16"]["screen"]
    assert portrait["text_px"] == round(13 * doc["ui_scale"] * 1080 / w, 1)
    assert portrait["text_px"] < 18 < portrait["text_px"] * portrait["readable_zoom"] + 0.1
    landscape = doc["by_format"]["16:9"]["screen"]
    assert landscape["text_px"] > portrait["text_px"]
    for z in doc["zoom_targets"]:
        assert z["text_px"] == pytest.approx(landscape["text_px"] * z["zoom"], abs=0.2)


def test_format_change_recomputes_the_caps_without_analysis(capsys, analysed):
    w, _, job, doc = analysed
    data = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**data, "format": "9:16"}))
    code, s, out = run_tool(capsys, "screen", "--job", str(job))
    assert code == 0 and s["reused"] is True, out
    new = json.loads((job / "work" / "screen.json").read_text())
    assert new["format"] == "9:16" and new["activity"] == doc["activity"]
    assert new["max_zoom"]["screen"] == {2880: 2.66, 1920: 1.77}[w]
    assert all(z["max_zoom"] == new["max_zoom"]["screen"] for z in new["zoom_targets"])
    (job / "job.json").write_text(json.dumps(data))


def test_cursor_only_recording_has_no_targets(capsys, clip_dir_):
    src = clip_dir_ / "pointer-only.mp4"
    make_screen(src, 1920, 1080, scene=False, duration=5)
    job = _job(capsys, src, "pointer-only")
    code, _, out = run_tool(capsys, "screen", "--job", str(job))
    assert code == 0, out
    doc = json.loads((job / "work" / "screen.json").read_text())
    assert doc["zoom_targets"] == [] and doc["clicks"] == [] and doc["activity"] == []
    assert doc["cursor"]


def test_idempotent(capsys, analysed):
    _, _, job, doc = analysed
    code, s, _ = run_tool(capsys, "screen", "--job", str(job))
    assert code == 0 and s["reused"] is True
    assert json.loads((job / "work" / "screen.json").read_text()) == doc


def test_no_screen_in_job_is_an_error(capsys, screen_clip):
    _, _, src = screen_clip
    code, s, _ = run_tool(capsys, "job", "new", str(src), "--id", "no-screen")
    code, s2, _ = run_tool(capsys, "screen", "--job", s["job"])
    assert code != 0


def test_sheet_screen(capsys, analysed):
    _, _, job, doc = analysed
    code, s, out = run_tool(capsys, "sheet", "screen", "2", "9", "--job", str(job))
    assert code == 0, out
    f = job / s["file"]
    assert f.is_file() and s["overlays"] is True
    assert s["marks"]["zoom_targets"] >= 1 and s["marks"]["activity"] >= 1
    probe = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0"]
        + [str(f)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    sw, sh = (int(v) for v in probe.stdout.strip().split(","))
    assert sw <= 1080 and sh <= 1920
    # no times: an overview of the whole recording (what `videocut run` prep makes)
    code, s, out = run_tool(capsys, "sheet", "screen", "--job", str(job))
    assert code == 0, out
    assert s["t0"] == 0 and s["t1"] == pytest.approx(doc["duration"], abs=0.1) and s["tiles"] >= 6
    # without screen.json the strip is still made, just without overlays
    (job / "work" / "screen.json").unlink()
    code, s, out = run_tool(capsys, "sheet", "screen", "0", "3", "--job", str(job))
    assert code == 0, out
    assert s["overlays"] is False
