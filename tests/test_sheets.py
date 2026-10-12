import json
import subprocess
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut.tools import _image

pytestmark = pytest.mark.skipif(clips.ffmpeg() is None, reason="needs ffmpeg")

DURATION = 8


def _source(folder: Path, w: int, h: int) -> Path:
    out = folder / f"src-{w}x{h}.mp4"
    subprocess.run(
        [
            clips.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration={DURATION}",
            "-f", "lavfi", "-i", f"sine=f=300:r=48000:d={DURATION}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(out),
        ],
        check=True,
    )  # fmt: skip
    return out


@pytest.fixture(scope="module", params=["portrait", "landscape"])
def source(request, tmp_path_factory):
    w, h = clips.SIZES[request.param]
    return request.param, _source(tmp_path_factory.mktemp(request.param), w, h)


@pytest.fixture
def job(capsys, source):
    _, src = source
    code, s, _ = run_tool(capsys, "job", "new", str(src), "--id", "sheets")
    assert code == 0, s
    code, s, _ = run_tool(capsys, "ingest", "--job", s["job"])
    assert code == 0, s
    return Path(s["job"])


def _size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [clips.ffprobe(), "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0"]
        + [str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    w, h = out.stdout.strip().split(",")
    return int(w), int(h)


def test_contact_sheets(capsys, source, job):
    orientation, _ = source
    code, s, out = run_tool(capsys, "sheet", "contact", "--job", str(job), "--every", "0.5")
    assert code == 0, s
    # 8 s at 30 fps, a tile every 0.5 s = 16 tiles = 2 sheets of 3x3 (the last one partial)
    assert s["count"] == 2 and s["labels"] is (_image.find_font() is not None)
    assert [(x["t0"], x["t1"], x["tiles"]) for x in s["sheets"]] == [(0, 4, 9), (4.5, 7.5, 7)]
    size = (1080, 1920) if orientation == "portrait" else (1920, 1080)
    for x in s["sheets"]:
        assert _size(job / x["file"]) == size
    assert "contact_02.jpg" in out
    # idempotent: a coarser run removes sheets of the earlier run
    code, s, _ = run_tool(capsys, "sheet", "contact", "--job", str(job), "--every", "2")
    assert code == 0 and s["count"] == 1
    assert not (job / "work" / "sheets" / "contact_02.jpg").exists()


def test_labels_are_drawn(capsys, job, monkeypatch):
    if _image.find_font() is None:
        pytest.skip("no system font")
    run_tool(capsys, "sheet", "contact", "--job", str(job), "--every", "2")
    with_font = (job / "work" / "sheets" / "contact_01.jpg").read_bytes()
    monkeypatch.setenv("VIDEOCUT_FONT", str(job / "nonexistent.ttf"))
    code, s, _ = run_tool(capsys, "sheet", "contact", "--job", str(job), "--every", "2")
    assert code == 0 and s["labels"] is False  # no font: sheets without text
    assert (job / "work" / "sheets" / "contact_01.jpg").read_bytes() != with_font


def test_burst_every_frame_and_sampled(capsys, source, job):
    orientation, _ = source
    code, s, _ = run_tool(capsys, "sheet", "burst", "2", "--span", "0.1", "--job", str(job))
    assert code == 0, s
    assert s["frames"] == [57, 58, 59, 60, 61, 62, 63]  # every frame in 1.9 .. 2.1 s
    assert s["wave_window"] == [1.9, 2.1]
    code, s, _ = run_tool(capsys, "sheet", "burst", "2.5", "--job", str(job))
    assert code == 0, s
    assert len(s["frames"]) == 9 and 75 in s["frames"]  # sampled, includes the frame at t
    assert s["file"] == "work/sheets/burst_2.5.jpg"
    w, h = _size(job / s["file"])
    assert max(w, h) <= 2000 and (h <= 1920 if orientation == "portrait" else h <= 1080)


def test_burst_near_the_start_clamps(capsys, job):
    code, s, _ = run_tool(capsys, "sheet", "burst", "0.1", "--job", str(job))
    assert code == 0, s
    assert s["frames"][0] == 0 and s["wave_window"][0] == 0


def test_timeline_with_and_without_words(capsys, source, job):
    code, s, _ = run_tool(capsys, "sheet", "timeline", "1", "5", "--job", str(job))
    assert code == 0, s
    assert s["words"] is None and s["pauses"] is None and s["file"].endswith("timeline_1_5.jpg")
    plain = _size(job / s["file"])
    (job / "work" / "words.json").write_text(
        json.dumps(
            {
                "model": "m",
                "language": "de",
                "duration": DURATION,
                "words": [
                    {"w": "Also", "t0": 1.0, "t1": 1.4, "conf": 0.9},
                    {"w": "don't", "t0": 1.9, "t1": 2.4, "conf": None},
                    {"w": "später", "t0": 6.0, "t1": 6.5, "conf": 0.5},
                ],
            }
        )
    )
    (job / "work" / "candidates.json").write_text(
        json.dumps(
            {
                "pauses": [{"t0": 3.2, "t1": 3.9, "dur": 0.7}],
                "fillers": [{"t0": 1.5, "t1": 1.8, "kind": "word", "text": "äh", "score": 0.9}],
            }
        )
    )
    code, s, _ = run_tool(capsys, "sheet", "timeline", "1", "5", "--job", str(job))
    assert code == 0, s
    assert (s["words"], s["pauses"], s["fillers"]) == (2, 1, 1)
    w, h = _size(job / s["file"])
    assert w == plain[0] and h > plain[1] and max(w, h) <= 2000  # extra word track


@pytest.mark.parametrize(
    "argv",
    [
        ("sheet", "timeline", "5", "2"),
        ("sheet", "timeline", "20", "30"),
        ("sheet", "burst", "99"),
        ("sheet", "contact", "--every", "0"),
    ],
)
def test_bad_arguments_fail_cleanly(capsys, job, argv):
    code, s, _ = run_tool(capsys, *argv, "--job", str(job))
    assert code == 1 and s["ok"] is False and s["error"]


def test_requires_ingest(capsys, source, tmp_path):
    _, src = source
    code, s, _ = run_tool(capsys, "job", "new", str(src), "--id", "bare")
    code, s, _ = run_tool(capsys, "sheet", "contact", "--job", s["job"])
    assert code == 1 and "ingest" in s["error"]
