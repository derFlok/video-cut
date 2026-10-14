"""Video time base: ingest puts the wavs in video time; old jobs are shifted in place."""

import json
import time
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool

from videocut.tools import _transcribe_energy as energy

pytestmark = pytest.mark.media
FRAME = 1 / 30
TURBO = "whisper-large-v3-turbo"


def remux(src: Path, dst: Path, audio_delay: float = 0.0, video_delay: float = 0.0) -> Path:
    """Copy of `src` whose audio stream starts `audio_delay` s later (or the video later)."""
    if not dst.is_file():
        clips._ff("-itsoffset", str(video_delay), "-i", str(src), "-itsoffset",
                  str(audio_delay), "-i", str(src), "-map", "0:v", "-map", "1:a", "-c", "copy",
                  str(dst))  # fmt: skip
    return dst


def lag(a: np.ndarray, b: np.ndarray, rate: int) -> float:
    """Seconds by which `b` is later than `a` (cross-correlation of the waveforms)."""
    n = 1 << (len(a) + len(b)).bit_length()
    c = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    k = int(np.argmax(c))
    return (k if k < n // 2 else k - n) / rate


def ingest(capsys, clip: Path, job_id: str, *extra: str) -> Path:
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", job_id, *extra)
    assert code == 0, s
    job = Path(s["job"])
    code, s, _ = run_tool(capsys, "ingest", "--job", job_id)
    assert code == 0, s
    return job


def wav(job: Path, name: str = "audio.wav") -> tuple[np.ndarray, int]:
    return energy.read_wav(job / "work" / name)


@pytest.mark.parametrize("delay", [0.25, -0.3])  # audio late (pad) / video late (trim)
def test_wavs_are_in_video_time(capsys, clip_dir, portrait_clip, delay):
    shifted = remux(
        portrait_clip,
        clip_dir / f"tb{delay:+.2f}.mp4",
        audio_delay=max(delay, 0.0),
        video_delay=max(-delay, 0.0),
    )
    ref = ingest(capsys, portrait_clip, f"ref{round(delay * 100)}")
    job = ingest(capsys, shifted, f"tb{round(delay * 100)}")
    m0 = json.loads((ref / "work" / "audio.json").read_text())
    m1 = json.loads((job / "work" / "audio.json").read_text())
    assert m1["time_base"] == "video"
    assert m1["audio_offset_s"] - m0["audio_offset_s"] == pytest.approx(delay, abs=0.03)
    # In video time the voice moved exactly by the remux delay (audio later against the
    # same pictures, or pictures later against the same audio); codec priming included.
    (a, rate), (b, _) = wav(ref), wav(job)
    assert lag(a, b, rate) == pytest.approx(delay, abs=0.002)
    raw, _ = wav(job, "audio_raw.wav")
    assert len(raw) == len(b)


def test_padded_audio_keeps_the_voice_at_its_video_time(capsys, clip_dir, portrait_clip):
    shifted = remux(portrait_clip, clip_dir / "tb-pad.mp4", audio_delay=0.4)
    ref = ingest(capsys, portrait_clip, "ref-pad")
    job = ingest(capsys, shifted, "pad")
    (a, rate), (b, _) = wav(ref), wav(job)
    # In video time the voice comes 0.4 s later than in the reference.
    assert lag(a, b, rate) == pytest.approx(0.4, abs=0.002)
    off = json.loads((job / "work" / "audio.json").read_text())["audio_offset_s"]
    assert off == pytest.approx(0.4, abs=0.03)
    assert np.abs(b[: int((off - 0.01) * rate)]).max() == 0.0  # padded with silence


def test_old_job_is_shifted_in_place_without_transcribing(capsys, clip_dir, portrait_clip):
    shifted = remux(portrait_clip, clip_dir / "tb-old.mp4", audio_delay=0.2)
    job = ingest(capsys, shifted, "old")
    work = job / "work"
    off = json.loads((work / "audio.json").read_text())["audio_offset_s"]
    new_audio = work / "audio.wav"
    expected, rate = energy.read_wav(new_audio)
    # Turn it into a job ingested and transcribed before the video time base.
    for name in ("audio_raw.wav", "audio.wav"):
        clips._ff("-y", "-i", str(shifted), "-map", "0:a", "-ac", "1", "-ar", "48000",
                  "-c:a", "pcm_s16le", str(work / name))  # fmt: skip
        time.sleep(0.01)
    (work / "audio.json").write_text(json.dumps({"denoised": False, "rate": 48000, "channels": 1}))
    words = [
        {"w": "Also", "t0": 0.5, "t1": 0.9, "conf": 0.9},
        {"w": "heute", "t0": 0.9, "t1": 1.4, "conf": 0.9},
    ]
    doc = {"model": TURBO, "language": "de", "duration": 5.0, "words": words}
    time.sleep(0.01)
    (work / "words.json").write_text(json.dumps(doc))
    (work / "transcribe.json").write_text(
        json.dumps({"model": TURBO, "language": "de", "filler_prompt": True})
    )
    code, s, _ = run_tool(capsys, "ingest", "--job", "old")
    assert code == 0, s
    assert set(s["upgraded"]) == {"audio", "words"} and s["audio_offset_s"] == off
    got, _ = energy.read_wav(new_audio)
    assert len(got) == len(expected) and lag(expected, got, rate) == pytest.approx(0, abs=0.001)
    meta = json.loads((work / "transcribe.json").read_text())
    assert meta["time_base"] == "video" and meta["shifted_s"] == off
    moved = json.loads((work / "words.json").read_text())["words"]
    assert [w["t0"] for w in moved] == [round(0.5 + off, 3), round(0.9 + off, 3)]
    assert (work / "candidates.json").is_file()
    # Transcribe now keeps those words (no model needed: nothing is transcribed again).
    code, s, _ = run_tool(capsys, "transcribe", "--job", "old", "--model", "turbo")
    assert code == 0 and s["transcribed"] is False and s["words"] == 2, s
    # A second ingest changes nothing.
    code, s, _ = run_tool(capsys, "ingest", "--job", "old")
    assert code == 0 and s["upgraded"] == [] and s["done"] == []


def test_sync_with_different_audio_starts(capsys, clip_dir, demo_pair):
    cam, scr, meta = demo_pair
    d_cam, d_scr = 0.2, 0.45  # each audio stream starts this much after its pictures
    cam2 = remux(cam, clip_dir / "tb-cam.mp4", audio_delay=d_cam)
    scr2 = remux(scr, clip_dir / "tb-scr.mov", audio_delay=d_scr)
    job = ingest(capsys, cam2, "tbsync", "--screen", str(scr2))
    code, s, _ = run_tool(capsys, "sync", "--job", "tbsync")
    assert code == 0, s
    cam_off = json.loads((job / "work" / "audio.json").read_text())["audio_offset_s"]
    scr_off = json.loads((job / "work" / "screen_audio.json").read_text())["audio_offset_s"]
    assert cam_off == pytest.approx(d_cam, abs=0.03) and scr_off == pytest.approx(d_scr, abs=0.03)
    # The voice is d_scr later in screen time and d_cam later in camera time.
    assert s["offset_s"] == pytest.approx(meta["offset"] + d_scr - d_cam, abs=FRAME)
    # A job ingested before the video time base: sync compensates from the probes.
    ref = s["offset_s"]
    for name, rec, src, index_key in (
        ("audio_raw.wav", "audio.json", cam2, "audio_index"),
        ("screen_audio.wav", "screen_audio.json", scr2, "audio_index"),
    ):
        probe = "probe.json" if name == "audio_raw.wav" else "screen_probe.json"
        idx = json.loads((job / "work" / probe).read_text())[index_key]
        clips._ff("-y", "-i", str(src), "-map", f"0:{idx}", "-ac", "1", "-ar", "48000",
                  "-c:a", "pcm_s16le", str(job / "work" / name))  # fmt: skip
        record = json.loads((job / "work" / rec).read_text())
        record.pop("time_base")
        (job / "work" / rec).write_text(json.dumps(record))
    code, s, _ = run_tool(capsys, "sync", "--job", "tbsync", "--redo")
    assert code == 0 and s["offset_s"] == pytest.approx(ref, abs=0.002), s


def test_interrupted_upgrade_rebuilds_instead_of_shifting_twice(capsys, clip_dir, portrait_clip):
    shifted = remux(portrait_clip, clip_dir / "tb-crash.mp4", audio_delay=0.2)
    job = ingest(capsys, shifted, "crash")
    work = job / "work"
    expected, rate = energy.read_wav(work / "audio.wav")
    # A crash after the wavs were shifted but before the record said so.
    meta = json.loads((work / "audio.json").read_text())
    (work / "audio.json").write_text(json.dumps({**meta, "time_base": "shifting"}))
    (work / "screen_audio.json").unlink(missing_ok=True)
    code, s, _ = run_tool(capsys, "ingest", "--job", "crash")
    assert code == 0 and s["upgraded"] == [] and "audio_raw" in s["done"], s
    got, _ = energy.read_wav(work / "audio.wav")
    assert len(got) == len(expected) and lag(expected, got, rate) == pytest.approx(0, abs=0.001)
    assert json.loads((work / "audio.json").read_text())["time_base"] == "video"
