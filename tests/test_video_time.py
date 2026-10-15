"""Video time end to end: a recording whose audio starts 157 ms after its video (AAC
priming, as phones record) and one whose audio starts first. Ingest, alignment,
candidates, snapped cuts, the rendered picture and sound, captions and the proxy must all
agree on video time (0 = the first video frame).

The clip speaks words one by one at known positions (clips.snap_clip); its picture is black
with a white flash exactly where one word starts, so picture, sound and captions can be
compared in the reel.
"""

import json
import subprocess
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool

from videocut.edl import load, output_spans
from videocut.paths import tool_env
from videocut.tools import _audio_env as ae
from videocut.tools import render as render_mod
from videocut.tools.job import approve_job
from videocut.tools.verify import mono_audio

pytestmark = [
    pytest.mark.media,
    pytest.mark.skipif(not clips.available(), reason="needs macOS say (Anna) and ffmpeg"),
]
RATE = 48000
FPS = 30
FLASH_S = 0.3
FLASH_WORD = 7  # "erste": the second word of the second kept phrase


def _clip(folder: Path, delay: float) -> tuple[Path, list[dict], float]:
    """(clip whose audio starts `delay` s after its video (negative: before), the snap
    clip's layout in video time, video time of the flash = the start of FLASH_WORD)."""
    _, layout = clips.snap_clip(folder)
    moved = [{**e, "a0": e["a0"] + delay, "a1": e["a1"] + delay} for e in layout]
    flash = [e for e in moved if e["kind"] == "word"][FLASH_WORD]["a0"]
    out = folder / f"video-time{delay:+.3f}.mp4"
    if not out.is_file():
        wav = folder / "snap-speech.wav"
        dur = clips.duration(wav)
        on = f"between(t,{flash:.4f},{flash + FLASH_S:.4f})"
        box = f"drawbox=w=iw:h=ih:color=white:t=fill:enable='{on}'"
        clips._ff(
            "-itsoffset", f"{max(-delay, 0.0):.3f}", "-f", "lavfi",
            "-i", f"color=c=black:s=1080x1920:r={FPS}:d={dur:.3f},{box}",
            "-itsoffset", f"{max(delay, 0.0):.3f}", "-i", str(wav),
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(out),
        )  # fmt: skip
    return out, moved, flash


def _first_white(video: Path) -> float:
    """Time of the first white frame of `video` (its frames are black or white)."""
    out = subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-i", str(video), "-an", "-vf",
         "setpts=PTS-STARTPTS,scale=4:4,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True, env=tool_env(),
    ).stdout  # fmt: skip
    luma = np.frombuffer(out, np.uint8).reshape(-1, 16).mean(axis=1)
    return int(np.argmax(luma > 128)) / FPS


def _lag(out: np.ndarray, t_out: float, src: np.ndarray, t_src: float) -> float:
    """Seconds by which the reel's sound at t_out lags the source's at t_src (best match
    of 0.4 s of reel audio within +-0.25 s)."""
    o = out[int(t_out * RATE) : int((t_out + 0.4) * RATE)]

    def corr(k: int) -> float:
        a = int(t_src * RATE) + k
        b = src[a : a + len(o)]
        if len(b) < len(o):
            return -1.0
        return float(np.dot(o, b) / (np.linalg.norm(o) * np.linalg.norm(b) + 1e-9))

    best = max(range(-int(0.25 * RATE), int(0.25 * RATE), 24), key=corr)
    return -best / RATE


@pytest.mark.parametrize("delay", [0.157, -0.12], ids=["audio-late", "audio-early"])
def test_words_cuts_picture_sound_and_captions_agree_on_video_time(
    capsys, clip_dir, delay, monkeypatch
):
    clip, layout, flash = _clip(clip_dir, delay)
    job_id = f"vt{round(delay * 1000)}"
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    code, s, _ = run_tool(capsys, "ingest", "--job", job_id)
    assert code == 0, s
    meta = json.loads((job / "work" / "audio.json").read_text())
    assert meta["time_base"] == "video"
    assert meta["audio_offset_s"] == pytest.approx(delay, abs=0.03)

    # The proxy (plan stills, bursts, face) is in video time too.
    proxy = job / "work" / "proxy.mp4"
    # (one frame: the flash starts on the frame grid of the source)
    assert abs(_first_white(proxy) - flash) <= 1.01 / FPS

    # ASR-like words in video time (ends 0.15 s early, chained), then alignment on the
    # video-time wav: the words land on the voice.
    kept = [e for e in layout if e["kind"] == "word"]
    drop = next(e for e in layout if e["kind"] == "drop")
    truth = sorted(kept + [drop], key=lambda e: e["a0"])
    words = [{"w": e["text"], "t0": round(e["a0"], 3), "t1": round(e["a1"] - 0.15, 3),
              "conf": 0.9} for e in truth]  # fmt: skip
    for w, nxt in zip(words, words[1:], strict=False):
        nxt["t0"] = min(nxt["t0"], w["t1"])
    doc = {"language": "de", "duration": clips.duration(job / "work" / "audio.wav"),
           "words": words}  # fmt: skip
    (job / "work" / "words.json").write_text(json.dumps(doc))
    (job / "work" / "transcribe.json").write_text(json.dumps({"model": "m", "language": "de"}))
    code, s, _ = run_tool(capsys, "align", "--job", job_id)
    assert code == 0, s
    aligned = json.loads((job / "work" / "words.json").read_text())["words"]
    # at every pause edge (where cuts go) the words land on the voice; a time base off
    # by the audio's start would miss by 120-157 ms
    by_truth = {e["text"]: e for e in truth}
    for w in aligned:
        e = by_truth[w["w"]]
        if w["w"] in ("Heute", "also", "Das"):
            assert abs(w["t0"] - e["a0"]) < 0.04, (w, e)
        if w["w"] in ("Dinge", "also", "einfach"):
            assert abs(w["t1"] - e["a1"]) < 0.07, (w, e)  # a soft release
    # the long pause after the dropped word is a candidate at its video time
    pauses = json.loads((job / "work" / "candidates.json").read_text())["pauses"]
    second = kept[6]
    assert any(abs(p["t0"] - drop["a1"]) < 0.06 and abs(p["t1"] - second["a0"]) < 0.06
               for p in pauses), pauses  # fmt: skip

    # Cut on the aligned words: the dropped word goes, snapping finds the pauses.
    by = {w["w"]: w for w in aligned}
    segments = [
        {"in": round(by["Heute"]["t0"] - 0.05, 3), "out": round(by["Dinge"]["t1"] + 0.03, 3)},
        {"in": round(by["Das"]["t0"] - 0.05, 3), "out": round(by["einfach"]["t1"] + 0.2, 3)},
    ]
    edl = {"fps": FPS, "size": [1080, 1920], "source": f"input/{clip.name}",
           "segments": segments}  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: video time\n")
    approve_job(job)
    code, s, _ = run_tool(capsys, "edl", "snap", "--job", job_id)
    assert code == 0 and s["unresolved"] == [], s
    code, s, _ = run_tool(capsys, "captions", "--job", job_id)
    assert code == 0, s
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", job_id)
    assert code == 0 and s["audio_from"] == "work/audio.wav", s
    code, v, _ = run_tool(capsys, "verify", "--job", job_id)
    assert code == 0 and "speech_clipped" not in v["failed"], v

    # In the reel the flash (picture), the word (sound) and its caption meet.
    reel = job / "out" / "preview.mp4"
    cut = load(job / "work" / "edl.json")
    o2 = output_spans(cut)[1][0]
    t_flash = o2 + flash - cut.segments[1].start
    assert abs(_first_white(reel) - t_flash) <= 1.01 / FPS
    out = mono_audio(reel)
    src, _ = ae.read_mono(job / "work" / "audio.wav")
    assert abs(_lag(out, t_flash, src, flash)) <= 0.01
    data = json.loads((job / "work" / "edl.json").read_text())
    starts = [w["t0"] for c in data["captions"] for w in c.get("words", [])]
    assert min(abs(t - t_flash) for t in starts) < 0.05, (t_flash, starts)

    # Audio identity: cutting the video-time wav gives the sound cutting the source
    # stream by its timestamps gives.
    wav_reel = out
    monkeypatch.setattr(
        render_mod, "_audio_input",
        lambda job, src, rel, info: ([], f"0:{info['audio_index']}", "source stream"),
    )  # fmt: skip
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", job_id)
    assert code == 0 and s["audio_from"] == "source stream", s
    stream_reel = mono_audio(reel)
    n = min(len(wav_reel), len(stream_reel))
    assert abs(len(wav_reel) - len(stream_reel)) <= RATE // 100
    for t in (0.5, t_flash, n / RATE - 0.6):
        assert abs(_lag(stream_reel, t, wav_reel, t)) <= 0.001, t
    a, b = wav_reel[:n], stream_reel[:n]
    assert float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))) > 0.98
