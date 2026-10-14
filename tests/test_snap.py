"""Acoustic snapping, the cut_in_speech check and verify's speech_clipped.

Unit tests run on synthetic envelopes and signals; the media test on a generated clip with
words spoken one by one (positions known to the sample) and transcript times that are
wrong the way ASR times are (ends too early, consecutive words chained).
"""

import json
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool

from videocut.edl import remap_output_times
from videocut.tools import _audio_env as ae
from videocut.tools import _snap
from videocut.tools import _verify_audio as va
from videocut.tools.job import approve_job

RATE = 48000


def _signal(spans: list[tuple[float, float]], dur: float, level=0.3, seed=0) -> np.ndarray:
    """Noise bursts ("words") at `spans`, digital silence elsewhere."""
    rng = np.random.default_rng(seed)
    x = np.zeros(int(dur * RATE), np.float32)
    for a, b in spans:
        n = int(b * RATE) - int(a * RATE)
        x[int(a * RATE) : int(a * RATE) + n] = level * rng.standard_normal(n)
    return x


# words (true positions): w0 0.5-0.9, w1 0.9-1.3 (no gap), w2 1.45-1.8, w3 2.6-3.0, w4 3.0-3.4
TRUE = [(0.5, 0.9), (0.9, 1.3), (1.45, 1.8), (2.6, 3.0), (3.0, 3.4)]


def _env(spans=TRUE, dur=4.0) -> ae.Envelope:
    return ae.from_samples(_signal(spans, dur), RATE)


def _words(shift=0.0) -> list[dict]:
    return [
        {"w": f"w{i}", "t0": round(a + shift, 3), "t1": round(b + shift, 3), "conf": 0.9}
        for i, (a, b) in enumerate(TRUE)
    ]


def _edl(segments) -> dict:
    return {"fps": 30, "size": [1080, 1920], "source": "input/a.mp4", "segments": segments}


def test_envelope_levels_skip_digital_silence():
    env = _env()
    assert env.speech_db == pytest.approx(20 * np.log10(0.3), abs=1.0)
    assert env.in_speech(0.7) and not env.in_speech(2.2)
    assert env.level(2.2) <= env.quiet_db < env.speech_floor_db
    shifted = ae.from_samples(_signal(TRUE, 4.0), RATE, offset=0.2)  # wav starts 0.2 s late
    assert shifted.in_speech(0.9) and not shifted.in_speech(0.6)


def test_snap_moves_an_early_out_to_the_real_word_end():
    # The editor kept w0..w2, cut at "t1 + 0.08" of ASR times that end 0.15 s too early.
    env, words = _env(), _words()
    edl = _edl([{"in": 0.4, "out": 1.8 - 0.15 + 0.08}, {"in": 2.5, "out": 3.5}])
    assert [c["edge"] for c in _snap.in_speech(edl, env)] == ["out"]
    new, summary = _snap.snap(edl, env, words, 0.3, 0.08)
    seg = new["segments"][0]
    assert 1.8 + 0.02 <= seg["out"] <= 2.6 - 0.04  # after the word, before the next one
    assert seg["out_src"] == pytest.approx(1.73) and "in_src" not in seg
    assert summary["moved"] == 1 and summary["unresolved"] == []
    assert _snap.in_speech(new, env) == []
    again, s2 = _snap.snap(new, env, words, 0.3, 0.08)  # idempotent
    assert again == new and s2["moved"] == 0


def test_snap_never_pulls_in_a_dropped_word():
    # Kept: w0 only, out inside w0's tail; w1 follows without a pause and is dropped.
    env, words = _env(), _words()
    edl = _edl([{"in": 0.4, "out": 0.85}, {"in": 2.5, "out": 3.5}])
    new, summary = _snap.snap(edl, env, words, 0.3, 0.08)
    out = new["segments"][0]["out"]
    assert out <= 1.1  # never past the middle of w1, whatever is quiet behind it
    assert summary["unresolved"] and summary["unresolved"][0]["edge"] == "out"
    assert out == 0.85  # nothing quiet in reach: the cut stays and is reported


def test_snap_in_edges_joins_and_speech_cut_ok():
    env, words = _env(), _words()
    edl = _edl(
        [
            {"in": 2.65, "out": 2.9},  # in clips w3's onset; out inside w3 (w3 kept)
            {"in": 2.9, "out": 3.5},  # continues the first: a join, not a cut
        ]
    )
    new, _ = _snap.snap(edl, env, words, 0.3, 0.08)
    a, b = new["segments"]
    assert 1.8 + 0.04 <= a["in"] <= 2.6 - 0.02  # before w3 starts, after w2 ended
    assert a["out"] == 2.9 and b["in"] == 2.9  # the join stays
    assert _snap.in_speech(new, env) == []
    # in speech, but marked as intended: no error
    marked = _edl([{"in": 0.7, "out": 1.0, "speech_cut_ok": "jump into the sentence"}])
    assert _snap.in_speech(marked, env) == []
    assert _snap.snap(marked, env, words, 0.3, 0.08)[0] == marked  # left as intended
    assert [c["edge"] for c in _snap.in_speech(_edl([{"in": 0.7, "out": 1.0}]), env)] == [
        "in",
        "out",
    ]


def test_snap_restarts_from_an_edited_cut_point_and_moves_keys():
    env, words = _env(), _words()
    edl = _edl(
        [
            {
                "in": 0.4,
                "out": 1.73,
                "zoom_keys": [[0.4, 1.2], [1.73, 1.4]],
                "layout": "screen",
                "redact": [{"t0": 1.0, "t1": 1.73, "box": [0, 0, 0.5, 0.1]}],
            }
        ]
    )
    new, _ = _snap.snap(edl, env, words, 0.3, 0.08)
    seg = new["segments"][0]
    assert seg["zoom_keys"][1][0] == seg["out"]  # a key on the edge follows it
    assert seg["redact"][0]["t1"] == seg["out"]  # a redact block keeps covering the end
    seg["out"] = 1.2  # the editor shortens it (drops w2 and the end of w1)
    again, s2 = _snap.snap(new, env, words, 0.3, 0.08)
    seg2 = again["segments"][0]
    assert seg2["out_src"] == 1.2 and s2["moved"] == 1
    assert 1.3 <= seg2["out"] <= 1.42  # into the pause after w1, not into w2


def test_snap_legacy_author_offset():
    # A legacy job: words and cut points in audio-stream time, the audio 0.157 s late.
    off = 0.157
    env = ae.from_samples(_signal(TRUE, 4.0), RATE, offset=off)
    words = _words(off)  # moved into video time
    edl = _edl([{"in": 0.4, "out": 1.83}, {"in": 2.5, "out": 3.5}])  # placed in audio time
    new, _ = _snap.snap(edl, env, words, 0.3, 0.08, author_offset=off)
    out = new["segments"][0]["out"]
    assert 1.8 + off + 0.02 <= out <= 2.6 + off - 0.04
    assert new["segments"][0]["out_src"] == 1.83


def test_remap_output_times_follows_the_segments():
    before = [{"in": 1.0, "out": 2.0}, {"in": 5.0, "out": 6.0}]
    after = [{"in": 0.9, "out": 2.1}, {"in": 4.95, "out": 6.0}]
    data = {
        "captions": [{"t0": 0.5, "t1": 1.0, "words": [{"w": "x", "t0": 0.5, "t1": 0.8}]}],
        "overlays": [{"t0": 1.2, "t1": 2.0, "scene": "a"}],
        "cover": 1.5,
    }
    out = remap_output_times(data, before, after)
    cap = out["captions"][0]
    assert cap["t0"] == pytest.approx(0.6) and cap["t1"] == pytest.approx(1.1)
    assert cap["words"][0]["t1"] == pytest.approx(0.9)
    assert out["overlays"][0]["t0"] == pytest.approx(1.2 + 0.2 + 0.05)  # into segment 2
    assert out["overlays"][0]["t1"] == pytest.approx(2.25)  # the end, shifted with it
    assert out["cover"] == pytest.approx(1.5 + 0.2 + 0.05)


def _crossfade(a: np.ndarray, b: np.ndarray, n: int) -> np.ndarray:
    g = np.sin(np.linspace(0, np.pi / 2, n))
    return np.concatenate([a[:-n], a[-n:] * g[::-1] + b[:n] * g, b[n:]])


def test_speech_clipped_flags_a_cut_into_a_word_not_a_clean_one():
    words = _signal([(0.2, 0.6), (0.6, 1.0), (1.3, 1.7)], 2.0)
    later = _signal([(0.3, 0.7), (0.9, 1.4)], 2.0, seed=1)
    x = 4800  # 100 ms crossfade, centred on the cut
    # clean: out at 1.15 (pause after the second word), in at 0.8 (pause before "0.9")
    clean = _crossfade(words[: int(1.15 * RATE) + x // 2], later[int(0.8 * RATE) - x // 2 :], x)
    # clipped: out at 0.8 (inside the second word), in at 1.1 (inside a word)
    cut = _crossfade(words[: int(0.8 * RATE) + x // 2], later[int(1.1 * RATE) - x // 2 :], x)
    r_clean = va.check(clean, RATE, [1.15])
    r_cut = va.check(cut, RATE, [0.8])
    assert r_clean["flagged"] == [], r_clean
    assert [d["cut"] for d in r_cut["flagged"]] == [1] and r_cut["flagged"][0]["clipped"] == "both"
    # marked as intended: info, not flagged; a join is no cut at all
    ok = va.check(cut, RATE, [0.8], ok_reasons=["jump into the sentence"])
    assert ok["flagged"] == [] and ok["cuts"][0]["speech_cut_ok"]
    assert va.check(cut, RATE, [0.8], joins=[True])["cuts"] == []


# ---------------------------------------------------------------- media (say)


@pytest.mark.media
@pytest.mark.skipif(not clips.available(), reason="needs macOS say (Anna) and ffmpeg")
def test_snap_render_verify_on_a_generated_clip(capsys, videocut_home, clip_dir):
    clip, layout = clips.snap_clip(clip_dir)
    kept = [e for e in layout if e["kind"] == "word"]
    first, second = kept[:6], kept[6:]
    drop = next(e for e in layout if e["kind"] == "drop")
    # ASR-like times: every word ends 0.15 s early and the next one starts right there
    words = []
    for e in kept + [drop]:
        words.append({"w": e["text"], "t0": e["a0"], "t1": e["a1"] - 0.15, "conf": 0.9})
    words.sort(key=lambda w: w["t0"])
    for w, nxt in zip(words, words[1:], strict=False):
        nxt["t0"] = min(nxt["t0"], w["t1"])
    asr = {w["w"]: w for w in words}
    # what the cut skill writes from those times: in = t0 - 0.05, out = t1 + 0.08
    segments = [
        {"in": round(asr["Heute"]["t0"] - 0.05, 3), "out": round(asr["Dinge"]["t1"] + 0.08, 3)},
        {"in": round(second[0]["a0"] + 0.06, 3), "out": round(second[-1]["a1"] + 0.2, 3)},
    ]
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", "snap")
    assert code == 0, s
    job = Path(s["job"])
    edl = {"fps": 30, "size": [1080, 1920], "source": s["source"], "segments": segments}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: snap test\n")
    approve_job(job)

    # 1. Without work/audio.wav nothing can be snapped: the reel clips both words.
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 0 and s["snap"]["skipped"], s
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and "speech_clipped" in s["failed"], s
    assert [c["cut"] for c in s["flagged"] if "speech_clipped" in c["flags"]] == [1], s

    # 2. With the audio: edl check finds both cut points, snap moves them to the pauses.
    code, s, _ = run_tool(capsys, "ingest", "--job", str(job))
    assert code == 0, s
    (job / "work" / "words.json").write_text(json.dumps({"words": words}))
    code, s, out = run_tool(capsys, "edl", "check", "--job", str(job))
    lines = [ln for ln in out.splitlines() if ln.startswith("$.") and "cut_in_speech" in ln]
    assert code == 1 and len(lines) == 2, out
    code, s, _ = run_tool(capsys, "edl", "snap", "--job", str(job))
    assert code == 0 and s["moved"] == 2 and s["unresolved"] == [], s
    a, b = json.loads((job / "work" / "edl.json").read_text())["segments"]
    assert first[-1]["a1"] + 0.01 <= a["out"] < drop["a0"]  # the word ends, "also" stays out
    assert drop["a1"] < b["in"] <= second[0]["a0"] - 0.01
    assert a["out_src"] == segments[0]["out"] and b["in_src"] == segments[1]["in"]
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0, s
    code, s, _ = run_tool(capsys, "edl", "snap", "--job", str(job))
    assert code == 0 and s["moved"] == 0, s  # idempotent

    # 3. The snapped, crossfaded reel passes.
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 0 and s["snap"]["moved"] == 0, s
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and "speech_clipped" not in s["failed"], s


def test_quiet_room_limits_the_crossfade():
    env = _env()  # w2 ends at 1.8, w3 starts at 2.6
    assert _snap.quiet_room(env, 2.2, 0.06) == pytest.approx(0.06)
    assert _snap.quiet_room(env, 1.83, 0.06) <= 0.03 + 1e-9  # w2 ends 30 ms before
    caps = _snap.xfade_caps(_edl([{"in": 0.4, "out": 2.2}, {"in": 2.25, "out": 3.5}]), env, 0.06)
    assert caps == {1: pytest.approx(0.12)}


@pytest.mark.media
@pytest.mark.skipif(not clips.available(), reason="needs macOS say (Anna) and ffmpeg")
def test_crossfaded_audio_stays_in_video_time_when_the_audio_starts_late(
    capsys, videocut_home, clip_dir
):
    """The audio stream starts 0.2 s after the video: render must cut it by timestamp."""
    wav = clips.speech(clip_dir)
    late = clip_dir / "late-audio.mp4"
    if not late.is_file():
        clips._ff(
            "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate=30:duration={clips.duration(wav)}",
            "-itsoffset", "0.2", "-i", str(wav), "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(late),
        )  # fmt: skip
    code, s, _ = run_tool(capsys, "job", "new", str(late), "--id", "late")
    assert code == 0, s
    job = Path(s["job"])
    segs = [{"in": 0.0, "out": 1.0}, {"in": 1.0, "out": 2.5}, {"in": 4.0, "out": 5.5}]
    edl = {"fps": 30, "size": [1080, 1920], "source": s["source"], "segments": segs}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: late audio\n")
    approve_job(job)
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 0, s
    from videocut.tools.verify import mono_audio

    out, (src, _) = mono_audio(job / "out" / "preview.mp4"), ae.read_mono(wav)
    lags = range(-int(0.3 * RATE), int(0.3 * RATE), 48)
    # the first segment starts before the audio does; output t = source t in both parts
    for t_out in (0.6, 1.3):
        o = out[int(t_out * RATE) : int((t_out + 0.5) * RATE)]

        def corr(lag, o=o, t_out=t_out):
            a = int((t_out - 0.2) * RATE) + lag  # source t = wav t + 0.2
            b = src[a : a + len(o)]
            return float(np.dot(o, b) / (np.linalg.norm(o) * np.linalg.norm(b) + 1e-9))

        best = max(lags, key=corr)
        assert abs(best) <= 0.01 * RATE, (t_out, best / RATE)  # not 0.2 s off


def test_silent_audio_has_no_speech_to_clip():
    env = ae.from_samples(np.zeros(3 * RATE, np.float32), RATE)
    edl = _edl([{"in": 0.5, "out": 1.0}, {"in": 2.0, "out": 2.5}])
    assert _snap.in_speech(edl, env) == []
    new, summary = _snap.snap(edl, env, [], 0.3, 0.08)
    assert new["segments"] == edl["segments"] and summary["unresolved"] == []
    assert va.check(np.zeros(3 * RATE, np.float32), RATE, [0.5])["flagged"] == []


def test_snap_keeps_screen_segments_inside_the_sync_overlap():
    env, words = _env(), _words()
    # in at 2.58 clips w3's onset; the nearest point with crossfade room is 2.54, but the
    # screen recording only starts at 2.56
    edl = _edl([{"in": 2.58, "out": 3.5, "layout": "screen"}])
    new, _ = _snap.snap(edl, env, words, 0.3, 0.12, overlap=(2.56, 10.0))
    assert new["segments"][0]["in"] >= 2.56
    free, _ = _snap.snap(edl, env, words, 0.3, 0.12)
    assert free["segments"][0]["in"] < 2.56  # without the overlap it would have moved out
