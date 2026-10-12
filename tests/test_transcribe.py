"""Packing, retake hints and filler candidates on synthetic data (no model, no `say`)."""

import json
import wave
from pathlib import Path

import numpy as np
import pytest
from helpers import run_tool

from videocut.tools import _transcribe_energy as energy
from videocut.tools._transcribe_pack import pack, phrases, retakes

RATE = 48000


def W(w, t0, t1, conf=0.9):
    return {"w": w, "t0": t0, "t1": t1, "conf": conf}


def doc(words, duration=None):
    return {
        "model": "test",
        "language": "de",
        "duration": duration if duration is not None else words[-1]["t1"] + 0.5,
        "words": words,
    }


def sentence(text, t0, step=0.3):
    return [W(w, round(t0 + i * step, 3), round(t0 + (i + 1) * step - 0.02, 3))
            for i, w in enumerate(text.split())]  # fmt: skip


def vowel(dur, f0=140.0, level=0.3):
    """Voiced, vowel-like tone (harmonics of f0)."""
    t = np.arange(int(dur * RATE)) / RATE
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8))
    return (level * x / np.abs(x).max()).astype(np.float32)


def noise(dur, level=0.05, seed=0):
    return (level * np.random.default_rng(seed).standard_normal(int(dur * RATE))).astype(np.float32)


def silence(dur, level=0.0005, seed=1):
    return noise(dur, level, seed)


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


# --- packing -------------------------------------------------------------------------


def test_phrases_break_on_long_silence_and_sentence_end():
    words = sentence("Das ist der erste Satz.", 0.0) + sentence("Und weiter", 1.5)
    words += sentence("geht es hier", 2.15)  # gap 0.07 s: same phrase
    assert [len(g) for g in phrases(words)] == [5, 5]
    words = sentence("Eins zwei drei vier.", 0.0) + sentence("Fünf sechs", 1.22)
    assert [len(g) for g in phrases(words)] == [4, 2]  # sentence end, tiny gap


def test_pack_format_pauses_and_header():
    words = sentence("Heute zeige ich Ihnen drei Dinge.", 0.0)
    words += sentence("Das erste ist", 2.6)  # 0.82 s after "Dinge."
    words[-1]["t1"] = 3.5
    words += [W("einfach.", 3.85, 4.3)]  # 0.35 s pause inside the phrase
    text = pack(doc(words, duration=5.0))
    lines = text.splitlines()
    assert lines[0] == "# Packed transcript"
    assert "model test · language de · 5.0s · 10 words · 2 phrases" in text
    body = [line for line in lines if line.startswith(("[", "  "))]
    assert body == [
        "[000.00-001.78] Heute zeige ich Ihnen drei Dinge.",
        "  <0.82>",
        "[002.60-004.30] Das erste ist <0.35> einfach.",
        "  <0.70>",
    ]
    assert "none yet" in text  # no candidates passed


def test_pack_marks_filler_words_and_gap_voice():
    words = sentence("Also", 0.0) + [W("äh,", 0.4, 0.6)] + sentence("das geht", 0.65)
    cands = {
        "pauses": [],
        "fillers": [
            {"t0": 0.4, "t1": 0.6, "kind": "word", "text": "äh,", "score": 0.95},
            {"t0": 1.4, "t1": 1.65, "kind": "gap_energy", "text": None, "score": 0.71},
            {"t0": 0.05, "t1": 0.1, "kind": "gap_energy", "text": None, "score": 0.2},
        ],
    }
    cands["fillers"].sort(key=lambda f: f["t0"])
    text = pack(doc(words, duration=2.0), cands)
    assert "[000.00-001.23] Also {~0.05s 0.20} {äh,} das geht" in text
    assert text.rstrip().endswith("<0.77> {~0.25s 0.71}")
    assert "none yet" not in text


def test_retake_hint_for_repeated_and_restarted_sentences():
    words = sentence("Das erste ist ganz einfach.", 0.0)
    words += sentence("Das erste ist äh ganz einfach.", 2.5)
    words += sentence("Ganz anders geht es weiter.", 5.0)
    words += sentence("Und dann kommt", 7.5)
    words += sentence("Und dann kommt der Schluss.", 9.0)
    filler = {i for i, w in enumerate(words) if w["w"] == "äh"}
    groups = phrases(words)
    assert retakes(words, groups, filler) == {1: 0, 4: 3}
    text = pack(doc(words))
    assert text.count("  ^ possible retake") == 2
    assert "  ^ possible retake of [000.00-001.48] (last complete take wins)" in text


def test_pack_size_stays_small_for_minutes_of_speech():
    words, t = [], 0.0
    for i in range(60):  # ~3 minutes, ~500 words
        s = sentence(f"Satz {i} hat hier ein paar ganz normale Worte drin.", t)
        words += s
        t = s[-1]["t1"] + 0.6
    kb = len(pack(doc(words)).encode()) / 1024
    assert 3 < kb < 14


# --- candidates ----------------------------------------------------------------------


def test_filler_word_list():
    assert energy.filler_score("Ähm,") and energy.filler_score("äääh...")
    assert energy.filler_score("Um,", "en") and energy.filler_score("hmm")
    assert energy.filler_score("Mmm") and energy.filler_score("um") is None  # German "um"
    assert energy.filler_score("...") is None
    assert energy.filler_score("er") is None and energy.filler_score("Ich") is None


def test_gap_energy_finds_voice_but_not_breath():
    # speech 0-1, [gap: silence, vowel "äh" 0.25 s, silence], speech, [gap: breath noise]
    parts = [
        vowel(0.4, 120),
        silence(0.3),
        vowel(0.25, 150),
        silence(0.3),
        vowel(0.4, 120),
        silence(0.2),
        noise(0.3, 0.04),
        silence(0.4),
        vowel(0.4, 120),
    ]
    x = np.concatenate(parts)
    bounds = np.cumsum([0] + [len(p) / RATE for p in parts])
    words = [
        W("eins", bounds[0], bounds[1]),
        W("zwei", bounds[4], bounds[5]),
        W("drei", bounds[8], bounds[9]),
    ]
    c = energy.find(words, energy.Frames(x, RATE), len(x) / RATE)
    gaps = [f for f in c["fillers"] if f["kind"] == "gap_energy"]
    assert len(gaps) == 1, gaps
    assert gaps[0]["t0"] == pytest.approx(bounds[2], abs=0.03)
    assert gaps[0]["t1"] == pytest.approx(bounds[3], abs=0.03)
    assert gaps[0]["score"] > 0.6
    assert [round(p["dur"], 2) for p in c["pauses"]] == [0.85, 0.9]


def test_overlong_word_with_hidden_filler():
    # "Also" stamped across "Also" + pause + "äh".
    x = np.concatenate([vowel(0.35), silence(0.15), vowel(0.3, 160), silence(0.5), vowel(0.4)])
    words = [W("Also,", 0.0, 0.8), W("gut", 1.3, 1.7)]
    c = energy.find(words, energy.Frames(x, RATE), len(x) / RATE)
    (hit,) = [f for f in c["fillers"] if f["text"] == "Also,"]
    assert hit["kind"] == "gap_energy"
    assert hit["t0"] == pytest.approx(0.5, abs=0.03) and hit["t1"] == pytest.approx(0.8, abs=0.03)


def test_isolated_short_word_is_a_filler_suspect():
    words = sentence("Das ist gut.", 0.0) + [W("Im.", 1.3, 1.6)] + sentence("Weiter", 2.0)
    x = silence(3.0)
    c = energy.find(words, energy.Frames(x, RATE), 3.0)
    (hit,) = [f for f in c["fillers"] if f["kind"] == "word"]
    assert hit["text"] == "Im." and hit["score"] == energy.ISOLATED_SCORE


# --- tool wiring ---------------------------------------------------------------------


def _bare_job(videocut_home: Path, x: np.ndarray) -> Path:
    job = videocut_home / "jobs" / "t"
    (job / "work").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"id": "t", "source": "input/x.mp4"}))
    write_wav(job / "work" / "audio.wav", x)
    return job


def test_candidates_tool_writes_contract_and_repacks(capsys, videocut_home):
    x = np.concatenate([vowel(0.4), silence(0.3), vowel(0.25, 150), silence(0.3), vowel(0.4)])
    job = _bare_job(videocut_home, x)
    words = {
        "model": "whisper-large-v3-turbo",
        "language": "de",
        "duration": 1.65,
        "words": [W("Eins.", 0.0, 0.4, None), W("Zwei.", 1.25, 1.65, 0.5)],
    }
    (job / "work" / "words.json").write_text(json.dumps(words))
    code, s, _ = run_tool(capsys, "candidates", "--job", "t")
    assert code == 0, s
    assert s["pauses"] == 1 and s["gap_energy"] == 1 and s["filler_words"] == 0
    c = json.loads((job / "work" / "candidates.json").read_text())
    assert set(c) == {"pauses", "fillers"}
    assert set(c["pauses"][0]) == {"t0", "t1", "dur"}
    assert set(c["fillers"][0]) == {"t0", "t1", "kind", "text", "score"}
    packed = (job / "work" / "takes_packed.md").read_text()
    assert "{~0.25s" in packed


def test_transcribe_without_model_names_the_pull_command(capsys, videocut_home):
    _bare_job(videocut_home, silence(1.0))
    for model, name in (("parakeet", "parakeet-tdt-0.6b-v3"), ("turbo", "whisper-large-v3-turbo")):
        code, s, _ = run_tool(capsys, "transcribe", "--job", "t", "--model", model)
        assert code == 1 and not s["ok"]
        assert f"videocut models pull {name}" in s["error"]


def test_tools_need_ingest_and_transcribe_first(capsys, videocut_home):
    job = _bare_job(videocut_home, silence(1.0))
    code, s, _ = run_tool(capsys, "candidates", "--job", "t")
    assert code == 1 and "transcribe" in s["error"]
    (job / "work" / "audio.wav").unlink()
    code, s, _ = run_tool(capsys, "transcribe", "--job", "t")
    assert code == 1 and "ingest" in s["error"]
