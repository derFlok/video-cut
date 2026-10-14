"""Word alignment against the audio: synthetic speech with known silences (no model)."""

import argparse
import json
import subprocess
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool
from test_transcribe import RATE, W, noise, silence, vowel, write_wav

from videocut import tools
from videocut.tools import _transcribe_energy as energy
from videocut.tools import align as A
from videocut.tools.transcribe import sync_alignment


def zeros(dur):
    return np.zeros(int(round(dur * RATE)), np.float32)


def shaped(dur, f0=140.0, level=0.3):
    """A vowel with 20 ms attack and 30 ms decay, like a spoken syllable."""
    x = vowel(dur, f0, level)
    n = len(x)
    env = np.ones(n, np.float32)
    a, d = int(0.02 * RATE), int(0.03 * RATE)
    env[:a] = np.linspace(0, 1, a)
    env[n - d :] = np.linspace(1, 0, d)
    return x * env


def build(parts):
    """Concatenate (kind, dur) parts; returns samples and the [t0, t1] of every part."""
    xs, spans, t = [], [], 0.0
    for kind, dur, *rest in parts:
        x = {"v": shaped, "s": silence, "z": zeros, "n": noise}[kind](dur, *rest)
        xs.append(x)
        spans.append((round(t, 3), round(t + len(x) / RATE, 3)))
        t += len(x) / RATE
    return np.concatenate(xs), spans


def aligned(x, words):
    return A.align_words(words, A.Envelope(x, RATE), len(x) / RATE)


def close(a, b, tol=0.02):
    return abs(a - b) <= tol


# --- core ------------------------------------------------------------------------------


@pytest.mark.parametrize("gap", ["s", "z"])  # room tone / digital silence (denoised audio)
def test_chained_asr_boundaries_move_to_real_silence(gap):
    x, sp = build([(gap, 0.2), ("v", 0.4), (gap, 0.25), ("v", 0.35, 180), (gap, 0.15),
                   ("v", 0.45, 120), (gap, 0.4)])  # fmt: skip
    # ASR-like: chained words, ends too early, starts too early.
    words = [W("eins", 0.2, 0.68), W("zwei", 0.68, 1.3), W("drei", 1.3, 1.7)]
    out = aligned(x, words)
    a, b, c = sp[1], sp[3], sp[5]
    assert close(out[0]["t0"], a[0]) and close(out[0]["t1"], a[1]), out[0]
    assert close(out[1]["t0"], b[0]) and close(out[1]["t1"], b[1]), out[1]
    assert close(out[2]["t0"], c[0]) and close(out[2]["t1"], c[1]), out[2]
    assert [(w["t0_asr"], w["t1_asr"]) for w in out] == [(0.2, 0.68), (0.68, 1.3), (1.3, 1.7)]


def test_word_stretched_over_a_pause_shrinks_to_its_voice():
    x, sp = build([("s", 0.2), ("v", 0.3), ("s", 0.8), ("v", 0.4, 170), ("s", 0.3)])
    words = [W("Also,", 0.2, 1.25), W("gut", 1.25, 1.7)]
    out = aligned(x, words)
    assert close(out[0]["t1"], sp[1][1]), out
    assert close(out[1]["t0"], sp[3][0]), out


def test_chained_words_in_continuous_speech_split_at_the_level_valley():
    # Two syllables joined by a short quiet stretch (still speech, no silence).
    x, sp = build([("s", 0.2), ("v", 0.4), ("v", 0.04, 140, 0.02), ("v", 0.4, 190),
                   ("s", 0.3)])  # fmt: skip
    words = [W("so", 0.2, 0.52), W("weit", 0.52, 1.04)]
    out = aligned(x, words)
    valley = sp[2]
    assert valley[0] - 0.02 <= out[0]["t1"] == out[1]["t0"] <= valley[1] + 0.02, out


def test_quiet_release_after_a_stop_closure_stays_in_the_word():
    # Vowel, 40 ms closure, quiet 80 ms release burst ("gebau-t"), then a real pause.
    x, sp = build([("s", 0.2), ("v", 0.4), ("s", 0.04), ("n", 0.08, 0.004), ("s", 0.4),
                   ("v", 0.3, 160), ("s", 0.3)])  # fmt: skip
    # Whisper-like: the word ends early, the next one starts right there (over the pause).
    words = [W("gebaut", 0.2, 0.6), W("und", 0.6, 1.3)]
    out = aligned(x, words)
    assert close(out[0]["t1"], sp[3][1]), out
    assert close(out[1]["t0"], sp[5][0]), out


def test_breath_before_a_word_is_not_its_onset():
    # Denoised audio: digital silence, a quiet breath, silence again, then the word.
    x, sp = build([("z", 0.5), ("n", 0.2, 0.002), ("z", 0.25), ("v", 0.4), ("z", 0.3)])
    words = [W("Dann", 0.9, 1.4)]
    out = aligned(x, words)
    assert close(out[0]["t0"], sp[3][0]), out


def test_long_asr_gap_with_unrecognised_speech_does_not_pull_words_across_it():
    # Speech the model did not transcribe sits between two words; neither may jump to it.
    x, sp = build([("s", 0.2), ("v", 0.4), ("s", 0.2), ("v", 0.5, 150), ("s", 0.1),
                   ("v", 0.6, 160), ("s", 0.2), ("v", 0.4, 200), ("s", 0.3)])  # fmt: skip
    words = [W("eins", 0.2, 0.6), W("zwei", sp[7][0] - 0.05, sp[7][1])]
    out = aligned(x, words)
    assert close(out[0]["t1"], sp[1][1]), out
    assert close(out[1]["t0"], sp[7][0]), out


def test_words_in_silence_keep_their_asr_times():
    # A hallucinated phrase in digital silence between two real words is not stretched.
    x, sp = build([("z", 0.2), ("v", 0.4), ("z", 3.0), ("v", 0.4, 170), ("z", 0.3)])
    words = [W("eins", 0.2, 0.6), W("Fast", 1.5, 1.8), W("Forward.", 1.8, 2.1),
             W("zwei", 3.6, 4.0)]  # fmt: skip
    out = aligned(x, words)
    assert [(w["t0"], w["t1"]) for w in out[1:3]] == [(1.5, 1.8), (1.8, 2.1)]
    assert close(out[0]["t1"], sp[1][1]) and close(out[3]["t0"], sp[3][0]), out


def test_word_at_the_very_end_of_the_recording_is_kept():
    x, _ = build([("s", 0.2), ("v", 0.4), ("s", 0.2), ("v", 0.3, 170)])
    duration = len(x) / RATE
    words = [W("eins", 0.2, 0.6), W("zwei", duration - 0.03, duration)]
    out = aligned(x, words)
    assert out[1]["t1"] == pytest.approx(duration, abs=0.01) and out[1]["t0"] > 0.6, out


def test_alignment_is_idempotent_and_keeps_fields():
    x, _ = build([("s", 0.2), ("v", 0.4), ("s", 0.25), ("v", 0.35, 180), ("s", 0.3)])
    words = [W("eins", 0.2, 0.7, 0.8), W("zwei", 0.7, 1.1, None)]
    once = aligned(x, words)
    assert aligned(x, once) == once
    assert [w["conf"] for w in once] == [0.8, None]
    assert A.strip(once) == [{**w} for w in words]
    for w in once:
        assert w["t0"] <= w["t1"]
    assert all(p["t1"] <= q["t0"] for p, q in zip(once, once[1:], strict=False))


def test_silent_recording_and_empty_words_do_not_fail():
    x = silence(1.0)
    assert aligned(x, []) == []
    out = aligned(x, [W("x", 0.2, 0.5)])
    assert out[0]["t0_asr"] == 0.2 and 0.0 <= out[0]["t0"] <= out[0]["t1"] <= 1.0


# --- tool ------------------------------------------------------------------------------


def _job(videocut_home: Path, x: np.ndarray, words: list[dict]) -> Path:
    job = videocut_home / "jobs" / "t"
    (job / "work").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"id": "t", "source": "input/x.mp4"}))
    write_wav(job / "work" / "audio.wav", x)
    doc = {"model": "m", "language": "de", "duration": len(x) / RATE, "words": words}
    (job / "work" / "words.json").write_text(json.dumps(doc))
    (job / "work" / "transcribe.json").write_text(json.dumps({"model": "m", "seconds": 1.0}))
    return job


def test_align_tool_realigns_records_and_refreshes_candidates(capsys, videocut_home):
    x, sp = build([("s", 0.2), ("v", 0.4), ("s", 0.4), ("v", 0.35, 180), ("s", 0.3)])
    job = _job(videocut_home, x, [W("eins", 0.2, 0.75), W("zwei", 0.75, 1.2)])
    code, s, _ = run_tool(capsys, "align", "--job", "t")
    assert code == 0, s
    assert s["median_shift_ms"] > 0 and s["zero_gap_share"] == 0.0 and s["pauses"] >= 1
    meta = json.loads((job / "work" / "transcribe.json").read_text())
    assert meta["model"] == "m" and meta["seconds"] == 1.0
    assert {"method", "version", "median_shift_ms"} <= set(meta["align"])
    assert meta["align"]["method"] == "energy"
    words = json.loads((job / "work" / "words.json").read_text())["words"]
    assert close(words[0]["t1"], sp[1][1]) and words[0]["t1_asr"] == 0.75
    assert (job / "work" / "candidates.json").is_file()
    # Re-running gives the same words (alignment starts from the ASR times).
    code, s2, _ = run_tool(capsys, "align", "--job", "t", "--no-candidates")
    assert code == 0 and "pauses" not in s2
    assert json.loads((job / "work" / "words.json").read_text())["words"] == words


def test_align_tool_needs_words(capsys, videocut_home):
    job = _job(videocut_home, silence(0.5), [])
    (job / "work" / "words.json").unlink()
    code, s, _ = run_tool(capsys, "align", "--job", "t")
    assert code == 1 and "transcribe" in s["error"]


def test_sync_alignment_follows_the_flag(videocut_home):
    x, _ = build([("s", 0.2), ("v", 0.4), ("s", 0.4), ("v", 0.35, 180), ("s", 0.3)])
    original = [W("eins", 0.2, 0.75), W("zwei", 0.75, 1.2)]
    job = _job(videocut_home, x, original)
    work = job / "work"
    meta = json.loads((work / "transcribe.json").read_text())
    info = sync_alignment(work, meta, True)
    assert info["method"] == A.METHOD
    meta = json.loads((work / "transcribe.json").read_text())
    before = (work / "words.json").read_text()
    assert sync_alignment(work, meta, True) == meta["align"]  # current: not redone
    assert (work / "words.json").read_text() == before
    assert sync_alignment(work, meta, False) is None  # --no-align: model times back
    assert json.loads((work / "words.json").read_text())["words"] == original
    assert "align" not in json.loads((work / "transcribe.json").read_text())


def test_align_and_no_align_are_registered():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="tool_cmd", required=True)
    tools.register_all(sub)
    assert callable(p.parse_args(["align", "--no-candidates"]).handler)
    assert p.parse_args(["transcribe", "--no-align"]).no_align


# --- generated speech ------------------------------------------------------------------

PHRASES = ["Heute zeige ich Ihnen", "drei Dinge", "die wichtig sind.", "Das erste ist",
           "ganz einfach", "und das zweite", "kommt gleich danach."]  # fmt: skip
GAPS = [0.12, 0.3, 0.2, 0.45, 0.16, 0.7]


@pytest.mark.media
def test_say_phrases_with_known_silences(tmp_path):
    """Chained, ASR-like word times on real TTS speech land on the known silences."""
    pieces, truth, t = [zeros(0.4)], [], 0.4
    for i, text in enumerate(PHRASES):
        aiff = tmp_path / f"p{i}.aiff"
        subprocess.run(["say", "-v", "Anna", "-o", str(aiff), text], check=True)
        raw = tmp_path / f"p{i}.f32"
        clips._ff("-i", str(aiff), "-ar", str(RATE), "-ac", "1", "-f", "f32le", str(raw))
        y = np.fromfile(raw, dtype="<f4")
        on = np.flatnonzero(np.abs(y) > 1e-3)
        y = y[on[0] : on[-1] + 1]
        truth.append((t, t + len(y) / RATE))
        pieces += [y, zeros(GAPS[i] if i < len(GAPS) else 0.5)]
        t += (len(y) + len(pieces[-1])) / RATE
    x = np.concatenate(pieces)
    x = 0.7 * x / np.abs(x).max() + noise(len(x) / RATE, 10 ** (-66 / 20), seed=2)[: len(x)]
    write_wav(tmp_path / "s.wav", x)
    x, rate = energy.read_wav(tmp_path / "s.wav")
    # Whisper-like (as measured on these clips): every phrase ends 0.12 s before its voice
    # does, and the next phrase's first word starts right there, stretched over the pause.
    words, start = [], truth[0][0] - 0.1
    for text, (p0, p1) in zip(PHRASES, truth, strict=True):
        toks = text.split()
        step = (p1 - 0.12 - p0) / len(toks)
        times = [(p0 + k * step, p0 + (k + 1) * step) for k in range(len(toks))]
        times[0] = (start, times[0][1])
        words += [W(w, round(a, 3), round(b, 3)) for w, (a, b) in zip(toks, times, strict=True)]
        start = times[-1][1]
    out = A.align_words(words, A.Envelope(x, rate), len(x) / rate)
    ends, k = [], 0
    for text, (p0, p1) in zip(PHRASES, truth, strict=True):
        n = len(text.split())
        ends += [(out[k]["t0"] - p0), (out[k + n - 1]["t1"] - p1)]
        k += n
    errs = np.abs(ends)
    assert errs.max() < 0.04, np.round(ends, 3).tolist()
