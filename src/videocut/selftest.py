"""End-to-end smoke test on a generated clip. Uses no third-party footage.

The test voice is macOS `say` (German "Anna"), reading a sentence with filler
words. Video is ffmpeg's test pattern. Nothing leaves the machine.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .paths import BIN, HOME, MODELS, RENDERER, tool_env

WORK = HOME / "selftest"
TEXT = (
    "Also, äh, heute zeige ich Ihnen, ähm, wie man in drei Minuten ein Video schneidet. "
    "Das ist, äh, wirklich einfach. Das ist wirklich einfach."
)


def _ff(*args: str) -> str:
    out = subprocess.run(
        [str(BIN / "ffmpeg"), "-hide_banner", "-y", *args],
        capture_output=True,
        text=True,
        env=tool_env(),
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-800:])
    return out.stderr


def _duration(path: Path) -> float:
    out = subprocess.run(
        [
            str(BIN / "ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        env=tool_env(),
        check=True,
    )
    return float(out.stdout.strip())


def _stream(path: Path, field: str) -> str:
    out = subprocess.run(
        [
            str(BIN / "ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            f"stream={field}",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        env=tool_env(),
        check=True,
    )
    return out.stdout.strip()


def make_clip() -> str:
    aiff = WORK / "speech.aiff"
    subprocess.run(["say", "-v", "Anna", "-o", str(aiff), TEXT], check=True)
    _ff("-i", str(aiff), "-ar", "48000", "-ac", "1", str(WORK / "speech.wav"))
    dur = _duration(WORK / "speech.wav")
    _ff(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=1080x1920:rate=30:duration={dur:.2f}",
        "-i",
        str(WORK / "speech.wav"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(WORK / "clip.mp4"),
    )
    return f"{dur:.1f} s test clip (voice: macOS 'Anna')"


def denoise() -> str:
    out = WORK / "denoised"
    out.mkdir(exist_ok=True)
    subprocess.run(
        [str(BIN / "deep-filter"), "-o", str(out), str(WORK / "speech.wav")],
        capture_output=True,
        env=tool_env(),
        check=True,
    )
    return f"DeepFilterNet wrote {next(out.glob('*.wav')).name}"


def loudness() -> str:
    err = _ff(
        "-i",
        str(WORK / "speech.wav"),
        "-af",
        "loudnorm=I=-14:TP=-1:print_format=json",
        "-f",
        "null",
        "-",
    )
    stats = json.loads(err[err.rindex("{") : err.rindex("}") + 1])
    return f"measured {float(stats['input_i']):.1f} LUFS, target -14"


def transcribe() -> str:
    import mlx_whisper

    model = MODELS / "whisper-large-v3-turbo"
    result = mlx_whisper.transcribe(
        str(WORK / "speech.wav"), path_or_hf_repo=str(model), word_timestamps=True, language="de"
    )
    words = [w for s in result["segments"] for w in s.get("words", [])]
    if len(words) < 8:
        raise RuntimeError(f"only {len(words)} words recognised")
    starts = [w["start"] for w in words]
    if starts != sorted(starts):
        raise RuntimeError("word timestamps are not monotonic")
    (WORK / "words.json").write_text(json.dumps(words, ensure_ascii=False, indent=1))
    fillers = [
        w["word"].strip()
        for w in words
        if re.fullmatch(r"\W*ähm?\W*", w["word"].strip(), re.IGNORECASE)
    ]
    return f"{len(words)} words with timestamps; fillers kept: {len(fillers)} of 3 (informational)"


def face() -> str:
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions

    frame = WORK / "frame.png"
    _ff("-ss", "0.5", "-i", str(WORK / "clip.mp4"), "-frames:v", "1", str(frame))
    opts = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODELS / "face_landmarker.task")),
        output_face_blendshapes=True,
    )
    with FaceLandmarker.create_from_options(opts) as lm:
        res = lm.detect(mp.Image.create_from_file(str(frame)))
    return f"face model runs ({len(res.face_landmarks)} faces on the test pattern, 0 expected)"


def overlay() -> str:
    mov = WORK / "overlay.mov"
    subprocess.run(
        [str(BIN / "node"), "check.js", str(mov), "30"],
        cwd=RENDERER,
        env=tool_env(),
        check=True,
        capture_output=True,
    )
    pix = _stream(mov, "pix_fmt")
    if "yuva" not in pix:
        raise RuntimeError(f"overlay has no alpha channel ({pix})")
    _ff(
        "-i",
        str(WORK / "clip.mp4"),
        "-i",
        str(mov),
        "-filter_complex",
        "[0:v][1:v]overlay",
        "-t",
        "1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(WORK / "overlaid.mp4"),
    )
    return f"transparent canvas → ProRes 4444 ({pix}) → overlaid on video"


def cut() -> str:
    src = WORK / "clip.mp4"
    keep = [(0.0, 1.5), (3.0, 4.5)]
    parts = "".join(
        f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[v{i}];[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,"
        f"afade=t=in:d=0.03,afade=t=out:st={b - a - 0.03}:d=0.03[a{i}];"
        for i, (a, b) in enumerate(keep)
    )
    chain = "".join(f"[v{i}][a{i}]" for i in range(len(keep)))
    out = WORK / "cut.mp4"
    _ff(
        "-i",
        str(src),
        "-filter_complex",
        f"{parts}{chain}concat=n={len(keep)}:v=1:a=1[v][a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(out),
    )
    dur = _duration(out)
    if abs(dur - 3.0) > 0.1:
        raise RuntimeError(f"cut is {dur:.2f} s, expected 3.0 s")
    return f"two segments joined with 30 ms fades → {dur:.2f} s"


def _tool(*argv: str) -> dict:
    """`videocut tool <argv>` in-process; its summary line, RuntimeError when it failed
    (verify: when it could not run; its own judgement is in the summary)."""
    from .cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["tool", *argv])
    lines = buf.getvalue().strip().splitlines()
    summary = json.loads(lines[-1]) if lines else {}
    if code != 0 or not (summary.get("ok") or argv[0] == "verify"):
        raise RuntimeError(f"tool {argv[0]} failed: {summary.get('error', code)}")
    return summary


def landscape() -> str:
    """A short 16:9 job (job.json "format") through render and verify, in a scratch
    VIDEOCUT_HOME under WORK so no user job or approval is touched."""
    from .tools.job import approve_job

    clip = WORK / "landscape.mp4"
    _ff(
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=3",
        "-i", str(WORK / "speech.wav"), "-t", "3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip),
    )  # fmt: skip
    old = os.environ.get("VIDEOCUT_HOME")
    os.environ["VIDEOCUT_HOME"] = str(WORK / "home")
    try:
        job = Path(_tool("job", "new", str(clip), "--id", "landscape", "--format", "16:9")["job"])
        edl = {"fps": 30, "size": [1920, 1080], "source": "input/landscape.mp4",
               "segments": [{"in": 0.2, "out": 1.2}, {"in": 1.6, "out": 2.8}]}  # fmt: skip
        (job / "work" / "edl.json").write_text(json.dumps(edl))
        (job / "work" / "project.md").write_text("# Plan: self-test\n")
        approve_job(job)
        r = _tool("render", "--job", str(job))
        v = _tool("verify", "--job", str(job))
    finally:
        if old is None:
            os.environ.pop("VIDEOCUT_HOME", None)
        else:
            os.environ["VIDEOCUT_HOME"] = old
    size = _stream(job / "out" / "reel.mp4", "width,height").replace(",", "x")
    if size != "1920x1080" or r["size"] != [1920, 1080] or "size" in v.get("failed", []):
        raise RuntimeError(f"16:9 render is {size}, verify failed {v.get('failed')}")
    # A 2 s test pattern may miss the loudness target or flag a jump cut; only the size counts.
    other = f"; verify notes: {', '.join(v['failed'])}" if v.get("failed") else ""
    return f"{size} reel rendered, size verified ({r['duration']:.1f} s){other}"


STEPS: list[tuple[str, Callable[[], str]]] = [
    ("generate test clip", make_clip),
    ("clean audio", denoise),
    ("measure loudness", loudness),
    ("transcribe (local)", transcribe),
    ("face tracking", face),
    ("overlay renderer", overlay),
    ("cut + join", cut),
    ("16:9 render", landscape),
]


def run() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    failed = 0
    for name, fn in STEPS:
        t0 = time.monotonic()
        try:
            detail = fn()
            print(f"  \033[32m✓\033[0m {name.ljust(20)} {detail}  ({time.monotonic() - t0:.1f} s)")
        except Exception as e:  # noqa: BLE001 — report every step, keep going
            failed += 1
            print(f"  \033[31m✗\033[0m {name.ljust(20)} {e}")
    print(f"\n  Output in {WORK}")
    print("  Self-test passed." if not failed else f"  {failed} step(s) failed.")
    return 1 if failed else 0
