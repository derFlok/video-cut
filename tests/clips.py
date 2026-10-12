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


# Known-filler clip: phrases and fillers spoken as separate `say` segments, joined with
# exact silences, so every filler's position is known to the sample.
FILLER_SCRIPT = [
    ("phrase", "Also, heute zeige ich Ihnen drei Dinge."),
    ("silence", 0.4),
    ("filler", "ähm"),
    ("silence", 0.4),
    ("phrase", "Das erste ist ganz einfach."),
    ("silence", 0.35),
    ("filler", "äh"),
    ("silence", 0.35),
    ("phrase", "Das zweite ist etwas schwieriger."),
    ("silence", 0.8),
    ("phrase", "Und das dritte"),
    ("silence", 0.4),
    ("filler", "öhm"),
    ("silence", 0.4),
    ("phrase", "zeige ich Ihnen morgen."),
]


def _audible(samples: bytes, rate: int) -> tuple[float, float]:
    """First and last audible sample (above ~-50 dBFS) of 16-bit mono PCM, in seconds."""
    import array

    a = array.array("h", samples)
    loud = [i for i, v in enumerate(a) if abs(v) > 100]
    return (loud[0] / rate, (loud[-1] + 1) / rate) if loud else (0.0, 0.0)


def filler_clip(folder: Path) -> tuple[Path, list[dict]]:
    """Portrait clip of FILLER_SCRIPT and its layout: one entry per phrase/filler/silence
    with "kind", "text", segment "t0"/"t1" and audible "a0"/"a1" (source seconds)."""
    import json
    import wave

    folder.mkdir(parents=True, exist_ok=True)
    out, layout_file = folder / "filler-clip.mp4", folder / "filler-clip.json"
    if out.is_file() and layout_file.is_file():
        return out, json.loads(layout_file.read_text())
    rate, pcm, layout = 48000, b"", []
    for k, (kind, item) in enumerate(FILLER_SCRIPT):
        if kind == "silence":
            seg = b"\0\0" * int(round(item * rate))
        else:
            aiff, wav = folder / f"seg{k}.aiff", folder / f"seg{k}.wav"
            subprocess.run(["say", "-v", "Anna", "-o", str(aiff), item], check=True)
            _ff("-i", str(aiff), "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(wav))
            with wave.open(str(wav), "rb") as f:
                seg = f.readframes(f.getnframes())
        t0 = len(pcm) / 2 / rate
        a0, a1 = _audible(seg, rate)
        entry = {"kind": kind, "text": None if kind == "silence" else item}
        entry.update(t0=t0, t1=t0 + len(seg) / 2 / rate, a0=t0 + a0, a1=t0 + a1)
        layout.append(entry)
        pcm += seg
    wav = folder / "filler-speech.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)
    w, h = SIZES["portrait"]
    _ff(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={w}x{h}:rate={FPS}:duration={len(pcm) / 2 / rate:.3f}",
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
        str(out),
    )
    layout_file.write_text(json.dumps(layout, ensure_ascii=False, indent=1))
    return out, layout
