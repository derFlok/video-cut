"""Generated talking-head-like test clips. No third-party media.

Speech: macOS `say -v Anna` reading a known German text with filler words and explicit
pauses ([[slnc 800]] = 800 ms silence). Video: ffmpeg's testsrc2 pattern. 30 fps, H.264 +
AAC, in landscape (1920x1080) and portrait (1080x1920).
"""

import shutil
import subprocess
from pathlib import Path

from videocut.paths import BIN, tool_env

FPS = 30
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
PAUSE_MS = 800
# Known text: two filler words, one restart (retake), two long pauses.
TEXT = (
    "Also, äh, heute zeige ich Ihnen drei Dinge. "
    f"[[slnc {PAUSE_MS}]] Das erste ist, ähm, ganz einfach. "
    f"[[slnc {PAUSE_MS}]] Das erste ist ganz einfach."
)
WORDS = ["Also", "äh", "heute", "zeige", "ich", "Ihnen", "drei", "Dinge"]
FILLERS = ["äh", "ähm"]


def _bin(name: str) -> str | None:
    p = BIN / name
    return str(p) if p.is_file() else shutil.which(name)


def ffmpeg() -> str | None:
    return _bin("ffmpeg")


def ffprobe() -> str | None:
    return _bin("ffprobe")


def has_voice() -> bool:
    """`say` with the German voice is available (macOS only)."""
    if not shutil.which("say"):
        return False
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    return any(line.split()[:1] == ["Anna"] for line in out.stdout.splitlines())


def available() -> bool:
    return has_voice() and ffmpeg() is not None and ffprobe() is not None


def _ff(*args: str) -> None:
    subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
        check=True,
        env=tool_env(),
    )


def duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def speech(folder: Path) -> Path:
    """48 kHz mono WAV of TEXT (built once per folder)."""
    wav = folder / "speech.wav"
    if not wav.is_file():
        aiff = folder / "speech.aiff"
        subprocess.run(["say", "-v", "Anna", "-o", str(aiff), TEXT], check=True)
        _ff("-i", str(aiff), "-ar", "48000", "-ac", "1", str(wav))
    return wav


def build(folder: Path, orientation: str = "portrait", rotate: int = 0) -> Path:
    """Talking-head-like clip; `rotate` adds display-rotation metadata like a phone does."""
    w, h = SIZES[orientation]
    folder.mkdir(parents=True, exist_ok=True)
    name = f"clip-{orientation}" + (f"-rot{rotate}" if rotate else "") + ".mp4"
    out = folder / name
    if out.is_file():
        return out
    wav = speech(folder)
    plain = folder / f"clip-{orientation}.mp4"
    if not plain.is_file():
        _ff(
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={w}x{h}:rate={FPS}:duration={duration(wav):.3f}",
            "-i",
            str(wav),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(plain),
        )
    if rotate:
        _ff("-display_rotation", str(rotate), "-i", str(plain), "-c", "copy", str(out))
    return out
