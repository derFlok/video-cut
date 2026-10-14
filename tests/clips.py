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
    return script_clip(folder, "filler", FILLER_SCRIPT)


def script_clip(folder: Path, stem: str, script: list) -> tuple[Path, list[dict]]:
    """Portrait clip of a script of ("silence", seconds) and (kind, text) entries, each text
    spoken as its own `say` segment, and its layout (see filler_clip)."""
    import json
    import wave

    folder.mkdir(parents=True, exist_ok=True)
    out, layout_file = folder / f"{stem}-clip.mp4", folder / f"{stem}-clip.json"
    if out.is_file() and layout_file.is_file():
        return out, json.loads(layout_file.read_text())
    rate, pcm, layout = 48000, b"", []
    for k, (kind, item) in enumerate(script):
        if kind == "silence":
            seg = b"\0\0" * int(round(item * rate))
        else:
            aiff, wav = folder / f"{stem}-seg{k}.aiff", folder / f"{stem}-seg{k}.wav"
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
    wav = folder / f"{stem}-speech.wav"
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


# Snapping clip: words spoken one by one (positions known to the sample). A kept phrase,
# a short pause, a word the cut drops, a pause, a second kept phrase.
SNAP_SCRIPT = [
    ("silence", 0.4),
    *(("word", w) for w in ("Heute", "zeige", "ich", "Ihnen", "drei", "Dinge")),
    ("silence", 0.15),
    ("drop", "also"),
    ("silence", 0.6),
    *(("word", w) for w in ("Das", "erste", "ist", "einfach")),
    ("silence", 0.6),
]


def snap_clip(folder: Path) -> tuple[Path, list[dict]]:
    return script_clip(folder, "snap", SNAP_SCRIPT)


# Product-demo pair: a camera clip and a synthetic screen recording carrying the same voice
# (the screen recorder's microphone), the screen started `offset` seconds earlier, so
# screen_time = camera_time + offset. Both have their own leading/trailing content and noise.
DEMO_TEXT = (
    "Ich zeige Ihnen heute, wie die Übersicht funktioniert. "
    "[[slnc 500]] Links sehen Sie die Liste mit allen Einträgen. "
    "Wenn ich hier klicke, öffnet sich rechts das Detail. "
    "[[slnc 700]] Oben können Sie filtern, zum Beispiel nach Datum. "
    "Die Zahlen unten aktualisieren sich sofort. "
    "[[slnc 600]] Zum Schluss speichere ich die Ansicht, und fertig ist der Bericht."
)
CAMERA_LEAD, CAMERA_TAIL, SCREEN_TAIL = 1.5, 2.0, 3.0
SCREEN_SIZE = (2880, 1800)


def _say_wav(folder: Path, name: str, text: str):

    wav = folder / f"{name}.wav"
    if not wav.is_file():
        aiff = folder / f"{name}.aiff"
        subprocess.run(["say", "-v", "Anna", "-o", str(aiff), text], check=True)
        _ff("-i", str(aiff), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(wav))
    return _read_wav(wav)


def _read_wav(path: Path):
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as f:
        return np.frombuffer(f.readframes(f.getnframes()), "<i2").astype(np.float32) / 32768


def _write_wav(path: Path, x) -> None:
    import wave

    import numpy as np

    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(pcm)


def _screen_video(folder: Path, size: tuple[int, int], seconds: float, wav, vfr: bool, out: Path):
    """Fake app recording: window chrome, a sidebar list that scrolls, text-like bars, a
    moving cursor and click highlights; neutral greys only. Static parts are drawn once
    into images, so only the moving parts are filtered per frame."""
    w, h = size
    u = w / 2880  # every coordinate is designed for 2880 px width

    def px(v: float) -> int:
        return max(2, int(v * u))

    still, rows = folder / f"ui-{w}x{h}.png", folder / f"ui-rows-{w}x{h}.png"
    if not still.is_file():
        boxes = [
            f"drawbox=x=0:y=0:w={w}:h={px(70)}:color=gray:t=fill",  # menu bar
            f"drawbox=x={px(160)}:y={px(160)}:w={px(2560)}:h={px(1500)}:color=white:t=fill",
            f"drawbox=x={px(160)}:y={px(160)}:w={px(2560)}:h={px(90)}:color=0x707070:t=fill",
        ]
        for i in range(18):  # detail pane "text"
            bw = px(1500 - (i * 97) % 600)
            boxes.append(
                f"drawbox=x={px(960)}:y={px(320 + i * 60)}:w={bw}:h={px(12)}:color=black:t=fill"
            )
        _ff("-f", "lavfi", "-i", f"color=c=0x303030:s={w}x{h}", "-vf", ",".join(boxes),
            "-frames:v", "1", str(still))  # fmt: skip
        list_h = px(2 * 1300)  # two copies of the list, so a crop can scroll endlessly
        boxes = []
        for i in range(28):
            y = px(i * 100)
            boxes.append(f"drawbox=x={px(40)}:y={y}:w={px(560)}:h={px(14)}:color=black:t=fill")
            boxes.append(
                f"drawbox=x={px(40)}:y={y + px(26)}:w={px(360)}:h={px(10)}:color=gray:t=fill"
            )
        _ff("-f", "lavfi", "-i", f"color=c=0xD0D0D0:s={px(700)}x{list_h}", "-vf",
            ",".join(boxes), "-frames:v", "1", str(rows))  # fmt: skip
    cx = f"{px(1000)}+{px(700)}*sin(t/3)"
    cy = f"{px(800)}+{px(400)}*cos(t/4)"
    moving = [
        f"[1:v]crop=w=iw:h={px(1300)}:x=0:y='mod(t*{px(40)}\\,{px(1300)})'[list]",
        f"[0:v][list]overlay=x={px(160)}:y={px(300)}",
    ]
    for t0 in range(3, int(seconds), 6):  # click highlights
        moving.append(
            f"drawbox=x='{cx}-{px(40)}':y='{cy}-{px(40)}':w={px(80)}:h={px(80)}"
            f":color=yellow@0.6:t=fill:enable='between(t\\,{t0}\\,{t0 + 0.4})'"
        )
    moving.append(f"drawbox=x='{cx}':y='{cy}':w={px(24)}:h={px(36)}:color=black:t=fill")
    if vfr:  # keep every 4th frame between 5 and 9 s, like a recorder writing changes only
        moving.append("select='not(between(t\\,5\\,9))+not(mod(n\\,4))'")
    graph = moving[0] + ";" + moving[1] + "," + ",".join(moving[2:]) + "[v]"
    audio_in = ["-i", str(wav)] if wav else []
    audio_out = ["-map", "2:a", "-c:a", "aac"] if wav else []
    _ff(
        "-loop", "1", "-framerate", str(FPS), "-i", str(still),
        "-loop", "1", "-framerate", str(FPS), "-i", str(rows),
        *audio_in, "-filter_complex", graph, "-map", "[v]", *audio_out,
        *(["-fps_mode", "vfr"] if vfr else []),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-t", f"{seconds:.3f}", str(out),
    )  # fmt: skip


def screen_pair(
    folder: Path,
    offset: float = 3.27,
    drift: float = 0.0,
    screen_audio: bool = True,
    size: tuple[int, int] = SCREEN_SIZE,
    vfr: bool = True,
) -> tuple[Path, Path, dict]:
    """Camera clip (portrait, 30 fps) and screen recording (`size`, .mov) of DEMO_TEXT.

    `offset` (s): screen_time = camera_time + offset for the speech start. `drift`: the
    screen clock runs fast by this fraction (0.001 = +1000 ppm), i.e. the screen copy of the
    speech is stretched by 1 + drift. `vfr` drops frames in a stretch like a screen
    recorder that only writes changed frames. Returns (camera, screen, meta) with meta
    {"offset", "drift_ppm", "speech": [camera_t0, camera_t1], "camera_duration",
    "screen_duration"}."""
    import json

    import numpy as np

    rate = 48000
    folder.mkdir(parents=True, exist_ok=True)
    tag = f"o{offset:+.3f}-d{drift * 1e6:.0f}-a{int(screen_audio)}-{size[0]}x{size[1]}-v{int(vfr)}"
    cam, scr, meta_file = (
        folder / f"cam-{tag}.mp4",
        folder / f"screen-{tag}.mov",
        folder / f"pair-{tag}.json",
    )
    if cam.is_file() and scr.is_file() and meta_file.is_file():
        return cam, scr, json.loads(meta_file.read_text())
    speech = _say_wav(folder, "demo-speech", DEMO_TEXT)
    cam_intro = _say_wav(folder, "demo-cam-intro", "Läuft?")
    scr_intro = _say_wav(folder, "demo-screen-intro", "Aufnahme startet.")
    rng = np.random.default_rng(7)

    def noise(n: int, level: float):
        return (rng.standard_normal(n) * level).astype(np.float32)

    lead = int(CAMERA_LEAD * rate)
    cam_a = np.concatenate([noise(lead, 0.002), speech, noise(int(CAMERA_TAIL * rate), 0.002)])
    cam_a[: len(cam_intro)] += cam_intro[:lead] * 0.5 if len(cam_intro) > lead else 0
    cam_a += noise(len(cam_a), 0.003)
    # Screen copy of the speech: another microphone (low-passed, quieter), its own clock.
    s = speech
    if drift:
        n = int(round(len(s) * (1 + drift)))
        s = np.interp(np.arange(n) / (1 + drift), np.arange(len(s)), s).astype(np.float32)
    s = np.convolve(s, np.ones(6, np.float32) / 6, mode="same") * 0.7
    scr_lead = int(round((CAMERA_LEAD + offset) * rate))
    if scr_lead < 0:
        raise ValueError("offset must not start the screen after the speech")
    scr_a = np.concatenate([noise(scr_lead, 0.004), s, noise(int(SCREEN_TAIL * rate), 0.004)])
    k = min(len(scr_intro), max(scr_lead - rate // 2, 0))
    scr_a[:k] += scr_intro[:k] * 0.6
    scr_a += noise(len(scr_a), 0.004)
    cam_wav, scr_wav = folder / f"cam-{tag}.wav", folder / f"screen-{tag}.wav"
    _write_wav(cam_wav, cam_a)
    _write_wav(scr_wav, scr_a)
    cam_dur, scr_dur = len(cam_a) / rate, len(scr_a) / rate
    w, h = SIZES["portrait"]
    _ff(
        "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={FPS}:duration={cam_dur:.3f}",
        "-i", str(cam_wav),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(cam),
    )  # fmt: skip
    _screen_video(folder, size, scr_dur, scr_wav if screen_audio else None, vfr, scr)
    cam_wav.unlink()
    scr_wav.unlink()
    meta = {
        "offset": offset,
        "drift_ppm": drift * 1e6,
        "speech": [CAMERA_LEAD, CAMERA_LEAD + len(speech) / rate],
        "camera_duration": cam_dur,
        "screen_duration": scr_dur,
    }
    meta_file.write_text(json.dumps(meta))
    return cam, scr, meta


# Contact-card screen recording for the redaction tests: a fake app with a contact card
# (placeholder person "Erika Mustermann", an example.com address, a fictitious number) at
# known positions; the card scrolls up once. Ground truth: the ink box of every field.
CARD_FIELDS = [  # (key, text, x, y, size) in card px; "label"/"note" lines are not personal
    ("label", "Kontakt", 60, 50, 40),
    ("note", "Zuletzt bearbeitet am Montag", 60, 130, 30),
    ("name", "Erika Mustermann", 60, 200, 44),
    ("email", "erika.mustermann@example.com", 60, 280, 34),
    ("phone", "+49 30 1234567", 60, 340, 34),
    ("note2", "Termin bestätigt", 60, 410, 30),
]
CARD_SIZE = (1200, 520)
CARD_XY = (900, 700)  # where the card sits on the 2880x1800 screen before the scroll
CARD_SCROLL = (7.3, 0.8, 360)  # screen time it starts, seconds it takes, px it moves up
FONTS = ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
         "/Library/Fonts/Arial.ttf")  # fmt: skip


def font() -> str | None:
    return next((f for f in FONTS if Path(f).is_file()), None)


def _card_png(folder: Path, out: Path, keys: list[str]) -> None:
    w, h = CARD_SIZE
    parts = [f"drawbox=x=0:y=0:w={w}:h=90:color=0xE8E8E8:t=fill"]
    for key, text, x, y, size in CARD_FIELDS:
        if key not in keys:
            continue
        tf = folder / f"card-{key}.txt"
        tf.write_text(text)
        parts.append(
            f"drawtext=fontfile={font()}:textfile={tf}:x={x}:y={y}:fontsize={size}"
            ":fontcolor=0x202020"
        )
    _ff("-f", "lavfi", "-i", f"color=c=white:s={w}x{h}", "-vf", ",".join(parts),
        "-frames:v", "1", str(out))  # fmt: skip


def _gray_png(path: Path, size: tuple[int, int]):
    import numpy as np

    raw = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, np.uint8).reshape(size[1], size[0]).astype(int)


def contact_screen(folder: Path, seconds: float) -> tuple[Path, dict]:
    """Screen recording (2880x1800, 30 fps) with the contact card. Returns (file, meta):
    meta {"fields": {key: [x0, y0, x1, y1] screen px before the scroll}, "scroll": [t0, dt,
    dy], "size"}; box_at(meta, key, t) gives a field's box at screen time t."""
    import json

    import numpy as np

    folder.mkdir(parents=True, exist_ok=True)
    out, meta_file = folder / f"contact-{seconds:.2f}.mp4", folder / f"contact-{seconds:.2f}.json"
    if out.is_file() and meta_file.is_file():
        return out, json.loads(meta_file.read_text())
    card = folder / "card.png"
    keys = [k for k, *_ in CARD_FIELDS]
    _card_png(folder, card, keys)
    empty = folder / "card-empty.png"
    _card_png(folder, empty, [])
    base = _gray_png(empty, CARD_SIZE)
    fields = {}
    for key in keys:
        one = folder / f"card-only-{key}.png"
        _card_png(folder, one, [key])
        ink = np.abs(_gray_png(one, CARD_SIZE) - base) > 40
        rows, cols = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
        fields[key] = [int(CARD_XY[0] + cols[0]), int(CARD_XY[1] + rows[0]),
                       int(CARD_XY[0] + cols[-1] + 1), int(CARD_XY[1] + rows[-1] + 1)]  # fmt: skip
    w, h = SCREEN_SIZE
    bg = folder / "contact-bg.png"
    _ff("-f", "lavfi", "-i", f"color=c=0x303030:s={w}x{h}", "-vf",
        f"drawbox=x=0:y=0:w={w}:h=70:color=gray:t=fill,"
        "drawbox=x=160:y=160:w=2560:h=1500:color=0xF4F4F4:t=fill,"
        "drawbox=x=160:y=160:w=2560:h=90:color=0x707070:t=fill",
        "-frames:v", "1", str(bg))  # fmt: skip
    s0, dt, dy = CARD_SCROLL
    y = f"{CARD_XY[1]}-{dy}*clip((t-{s0})/{dt}\\,0\\,1)"
    _ff(
        "-loop", "1", "-framerate", str(FPS), "-i", str(bg),
        "-loop", "1", "-framerate", str(FPS), "-i", str(card),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-filter_complex", f"[0:v][1:v]overlay=x={CARD_XY[0]}:y='{y}',format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
        "-colorspace", "bt709", "-c:a", "aac", "-t", f"{seconds:.3f}", str(out),
    )  # fmt: skip
    meta = {"fields": fields, "scroll": [s0, dt, dy], "size": [w, h]}
    meta_file.write_text(json.dumps(meta))
    return out, meta


def box_at(meta: dict, key: str, t: float) -> list[float]:
    """Normalised box of field `key` at screen time t (frame times on the 30 fps grid)."""
    s0, dt, dy = meta["scroll"]
    shift = dy * min(max((t - s0) / dt, 0.0), 1.0)
    x0, y0, x1, y1 = meta["fields"][key]
    w, h = meta["size"]
    return [x0 / w, (y0 - shift) / h, x1 / w, (y1 - shift) / h]
