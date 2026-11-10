"""Sound effects of the motion scenes: library, placement, track, mix analysis, job level, API.

Synthetic events.json only (no recording). The ffmpeg mix graph is exercised in
test_sfx_mix_graph_runs (ffmpeg only) and in tests/test_sfx_render.py (a full render)."""

import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from videocut import run as pipeline
from videocut import settings
from videocut.server import create_app
from videocut.tools import _disclosure, _sfx_library, _sfx_mix, _sfx_synth
from videocut.tools import sfx as sfx_mod
from videocut.tools._material_providers import ProviderError, StockHit, openverse
from videocut.tools.common import ToolError, binary, jobs_root
from videocut.tools.job import set_sfx

RATE = 48000


# --------------------------------------------------------------------------- helpers


def make_job(job_id="s1", events=None, words=None, segments=None, spans=True) -> Path:
    job = jobs_root() / job_id
    for d in ("input", "work", "out"):
        (job / d).mkdir(parents=True, exist_ok=True)
    (job / "input" / "take.mov").write_bytes(b"x")
    (job / "job.json").write_text(json.dumps({"id": job_id, "source": "input/take.mov"}))
    segs = segments or [{"in": 0.0, "out": 10.0}]
    edl = {"fps": 30, "size": [1080, 1920], "source": "input/take.mov", "segments": segs}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    if words is not None:
        (job / "work" / "words.json").write_text(json.dumps({"words": words}))
    if events is not None:
        d = job / "work" / "motion" / "a"
        d.mkdir(parents=True, exist_ok=True)
        (d / "events.json").write_text(json.dumps(events))
        if spans:
            (job / "work" / "motion" / "spans.json").write_text(
                json.dumps([{"id": "a", "t0": 0, "t1": 10, "region": [0, 0, 1, 1]}])
            )
    return job


def ev(t, kind="pop", strength=0.7, component="headline"):
    return {"t": t, "kind": kind, "strength": strength, "component": component}


def wav_file(path: Path, x: np.ndarray, rate: int = RATE) -> Path:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


def read_stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 2, 2)
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32767
    return x.reshape(-1, 2)


def has_ffmpeg() -> bool:
    try:
        binary("ffmpeg")
        return True
    except ToolError:
        return False


needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="needs ffmpeg")


# --------------------------------------------------------------------------- synthesis


@pytest.mark.parametrize("kind", list(_sfx_library.KINDS))
def test_procedural_sounds_are_short_clean_and_deterministic(kind):
    for v in range(_sfx_synth.VARIANTS):
        a, b = _sfx_synth.sound(kind, v), _sfx_synth.sound(kind, v)
        assert np.array_equal(a, b)
        assert a.dtype == np.float32 and np.isfinite(a).all()
        assert 0.005 < len(a) / RATE <= _sfx_library.KINDS[kind].max_s
        assert 0.5 < np.abs(a).max() <= 0.71
        assert abs(float(a[-1])) < 1e-3  # faded out: no click at the end
    assert not np.array_equal(_sfx_synth.sound(kind, 0), _sfx_synth.sound(kind, 1))


def test_unknown_procedural_kind():
    with pytest.raises(KeyError):
        _sfx_synth.sound("boing")


# --------------------------------------------------------------------------- processing


def test_process_trims_caps_and_normalises():
    t = np.arange(int(2.0 * RATE)) / RATE
    tone = 0.9 * np.sin(2 * np.pi * 800 * t) * np.exp(-t * 2)
    x = np.concatenate([np.zeros(RATE // 2), tone, np.zeros(RATE // 2)])
    y = _sfx_library.process(x, "pop")
    assert len(y) <= int(_sfx_library.KINDS["pop"].max_s * RATE)
    assert np.abs(y[:5]).max() < 0.05  # starts at the onset (1 ms kept, faded in)
    assert abs(float(y[-1])) < 1e-3
    assert _sfx_library.db(float(np.abs(y).max())) <= _sfx_library.PEAK_DBFS + 0.01
    fr = _sfx_library._frames_db(y)
    active = fr >= fr.max() - 20
    rms = 10 * np.log10(np.mean(10 ** (fr[active] / 10)))
    assert rms <= _sfx_library.REF_RMS_DB + 0.5


def test_process_refuses_silence_and_unknown_kinds():
    with pytest.raises(ToolError, match="silent"):
        _sfx_library.process(np.zeros(RATE), "pop")
    with pytest.raises(ToolError, match="unknown sound kind"):
        _sfx_library.process(np.ones(100), "boing")


# --------------------------------------------------------------------------- the library


@needs_ffmpeg
def test_upload_is_preferred_and_removable(tmp_path):
    assert _sfx_library.preferred("pop")[0].origin == "procedural"
    t = np.arange(int(0.2 * RATE)) / RATE
    src = wav_file(tmp_path / "mine.wav", 0.5 * np.sin(2 * np.pi * 600 * t) * np.exp(-t * 20))
    s = _sfx_library.add_upload("pop", src, "My pop")
    assert s.origin == "upload" and s.title == "My pop" and s.id.startswith("upload-")
    assert (_sfx_library.root() / "pop" / f"{s.id}.wav").is_file()
    pref = _sfx_library.preferred("pop")
    assert [p.id for p in pref] == [s.id]
    assert len(_sfx_library.samples(s)) == int(round(s.duration_s * RATE))
    view = sfx_mod.library_view(["pop"])["pop"]
    assert view["uses"] == "upload" and view["sounds"][0]["id"] == s.id
    assert _sfx_library.remove("pop", s.id)
    assert not _sfx_library.remove("pop", s.id)
    assert _sfx_library.preferred("pop")[0].origin == "procedural"


@needs_ffmpeg
def test_upload_refuses_non_audio(tmp_path):
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"RIFF....not audio")
    with pytest.raises(ToolError):
        _sfx_library.add_upload("pop", bad)


def test_a_changed_library_file_is_not_used(tmp_path):
    s = _sfx_library._store("tick", "openverse-abc", _sfx_synth.sound("tick"),
                            {"origin": "openverse", "title": "t"})  # fmt: skip
    wav = _sfx_library.root() / "tick" / f"{s.id}.wav"
    wav.write_bytes(wav.read_bytes()[:-2] + b"\x01\x02")
    with pytest.raises(ToolError, match="changed"):
        _sfx_library.samples(s)


def test_sidecars_outside_the_rules_are_ignored():
    d = _sfx_library.root() / "pop"
    d.mkdir(parents=True)
    (d / "upload-x.json").write_text(
        json.dumps(
            {"id": "upload-x", "kind": "tick", "origin": "upload", "title": "", "duration_s": 1}
        )
    )
    (d / "upload-x.wav").write_bytes(b"")
    (d / "evil.json").write_text(json.dumps({"id": "../evil", "kind": "pop", "origin": "upload",
                                             "title": "", "duration_s": 1}))  # fmt: skip
    assert [s.origin for s in _sfx_library.sounds("pop")] == ["procedural"] * 3


# --------------------------------------------------------------------------- Openverse


def _ov(**over):
    r = {"id": "abc-123", "title": "Swipe Whoosh", "creator": "someone", "license": "cc0",
         "license_version": "1.0", "url": "https://cdn.example.org/a.mp3", "filetype": "mp3",
         "duration": 450, "source": "freesound", "foreign_landing_url": "https://x.org/1",
         "license_url": "https://creativecommons.org/publicdomain/zero/1.0/"}  # fmt: skip
    return {**r, **over}


def test_openverse_audio_hits_are_cc0_short_and_https(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None):
        seen.update(url=url, params=params)
        return {"results": [
            _ov(), _ov(id="by", license="by"), _ov(id="long", duration=9000),
            _ov(id="http", url="http://cdn.example.org/a.mp3"), _ov(id="nodur", duration=None),
            _ov(id="pdm", license="pdm", license_version=""), _ov(id="exe", url="https://e/a.exe",
                                                                 filetype="exe"),
        ]}  # fmt: skip

    monkeypatch.setattr(openverse, "get_json", fake_get)
    hits = openverse.search_audio("whoosh", 20, 1.6)
    assert [h.id for h in hits] == ["openverse-abc-123", "openverse-pdm"]
    assert seen["url"] == openverse.AUDIO_API and seen["params"]["license"] == "cc0,pdm"
    assert set(seen["params"]) == {"q", "license", "page_size", "mature"}  # nothing else sent
    h = hits[0]
    assert h.kind == "audio" and h.duration_s == 0.45 and h.licence == "CC0 1.0"
    assert h.attribution == "Swipe Whoosh by someone (CC0) via Openverse/freesound"


def test_fetch_stores_sounds_once_with_their_credit(tmp_path):
    t = np.arange(int(0.3 * RATE)) / RATE
    raw = wav_file(tmp_path / "src.wav", 0.6 * np.sin(2 * np.pi * 500 * t) * np.exp(-t * 9))
    calls = []

    def search(q, limit, max_s):
        calls.append(q)
        return [StockHit(id=f"openverse-{i}x", kind="audio", width=0, height=0, duration_s=0.3,
                         preview_url="", file_url="https://e/a.wav", author="a", source_url="s",
                         licence="CC0 1.0", attribution=f"T{i} by a (CC0) via Openverse")
                for i in range(3)]  # fmt: skip

    def fetch_file(hit, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw.read_bytes())

    if not has_ffmpeg():
        pytest.skip("needs ffmpeg")
    sleeps = []
    rep = _sfx_library.fetch(["pop", "check"], 2, search, fetch_file, sleeps.append, lambda _: 0)
    assert rep["pop"]["have"] == 2 and len(rep["pop"]["added"]) == 2
    assert calls == ["pop", "ding"] and sleeps == [_sfx_library.SEARCH_PAUSE_S]
    s = _sfx_library.preferred("pop")[0]
    assert s.origin == "openverse" and s.attribution.endswith("via Openverse")
    again = _sfx_library.fetch(["pop"], 2, search, fetch_file, sleeps.append, lambda _: 0)
    assert again["pop"]["added"] == [] and calls == ["pop", "ding"]  # once per kind


def test_fetch_stops_on_the_rate_limit():
    def search(q, limit, max_s):
        raise ProviderError(openverse.RATE_MSG)

    rep = _sfx_library.fetch(["pop", "tick"], 2, search, lambda h, d: None, lambda s: 0,
                             lambda _: 0)  # fmt: skip
    assert "rate limit" in rep["pop"]["error"] and "tick" not in rep


def test_fetch_refuses_inside_an_agent_run(monkeypatch, capsys):
    from helpers import run_tool

    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "sfx", "fetch")
    assert code == 1 and "agent" in s["error"]


# --------------------------------------------------------------------------- placement


def test_events_are_validated_and_sorted():
    job = make_job(events=[ev(2.0), ev(1.0, "whoosh"), ev(-1), ev(20), ev(1.5, "boing"),
                           {"t": "x", "kind": "pop"}, ev(3.0, strength=float("nan")), 5,
                           {"t": 4.0, "kind": "tick"}])  # fmt: skip
    events, warnings = sfx_mod.read_events(sfx_mod.event_files(job), 10.0)
    assert [(e["t"], e["kind"]) for e in events] == [(1.0, "whoosh"), (2.0, "pop"), (4.0, "tick")]
    assert events[2]["strength"] == sfx_mod.DEFAULT_STRENGTH and events[2]["component"] is None
    assert warnings == ["a/events.json: 6 event(s) skipped (time or kind)"]


def test_events_without_spans_json_are_found_and_spans_cannot_escape():
    job = make_job(events=[ev(1.0)], spans=False)
    assert sfx_mod.event_files(job) == [job / "work" / "motion" / "a" / "events.json"]
    (job / "work" / "motion" / "spans.json").write_text(json.dumps([{"id": "../../x"}]))
    with pytest.raises(ToolError, match="leaves"):
        sfx_mod.event_files(job)


def test_spacing_family_density_and_strength():
    events = [ev(1.0, "pop"), ev(1.05, "pop"), ev(1.2, "pop"),  # pop min gap 0.1
              ev(2.0, "swoosh_out"), ev(2.15, "whoosh"),  # one "air" sound per moment
              ev(3.0, "impact"), ev(3.1, "whoosh"),  # impact outranks the whoosh
              ev(4.0, "pop", strength=0.01)]  # fmt: skip
    for e in events:
        e["span"] = "a"
    hits, skipped = sfx_mod.plan_hits(events, [], 30, 1, 10.0)
    assert [(h["t"], h["kind"]) for h in hits] == [
        (1.0, "pop"), (1.2, "pop"), (2.0, "swoosh_out"), (3.0, "impact")]  # fmt: skip
    assert {(s["t"], s["reason"]) for s in skipped} == {
        (1.05, "spacing"), (2.15, "spacing"), (3.1, "spacing"), (4.0, "strength")}  # fmt: skip
    # stronger is louder (jitter aside)
    weak = sfx_mod.plan_hits([{**events[0], "strength": 0.2}], [], 30, 1, 10)[0][0]
    strong = sfx_mod.plan_hits([{**events[0], "strength": 1.0}], [], 30, 1, 10)[0][0]
    assert strong["gain_db"] - weak["gain_db"] > 20 * math.log10(1.0 / 0.52) - 2 * 1.5


def test_density_keeps_at_most_six_per_second_by_priority():
    events = [{**ev(1 + i * 0.06, "type"), "span": "a"} for i in range(15)]
    events.append({**ev(1.3, "check"), "span": "a"})
    hits, skipped = sfx_mod.plan_hits(events, [], 30, 1, 10)
    times = [h["t"] for h in hits]
    for x in times:
        assert sum(1 for y in times if x <= y < x + 1) <= sfx_mod.MAX_PER_S
    assert any(h["kind"] == "check" for h in hits)
    assert any(s["reason"] == "density" for s in skipped)


def test_a_hit_never_lands_in_the_first_frame_of_a_word():
    events = [{**ev(2.0), "span": "a"}, {**ev(5.0), "span": "a"}]
    hits, _ = sfx_mod.plan_hits(events, [1.99, 4.5], 30, 1, 10)
    assert hits[0]["t"] == pytest.approx(1.99 - sfx_mod.ONSET_LEAD_S)
    assert hits[0]["shifted_ms"] == -40 and hits[0]["event_t"] == 2.0
    assert hits[1]["t"] == 5.0 and hits[1]["shifted_ms"] == 0  # 0.5 s after an onset: free


def test_plan_is_deterministic_and_varies_per_hit():
    events = [{**ev(1 + i * 0.5), "span": "a"} for i in range(6)]
    a = sfx_mod.plan_hits(events, [], 30, 7, 10)[0]
    assert a == sfx_mod.plan_hits(events, [], 30, 7, 10)[0]
    assert len({h["pitch"] for h in a}) > 1 and len({h["gain_db"] for h in a}) > 1
    assert all(abs(math.log2(h["pitch"]) * 12) <= sfx_mod.PITCH_SEMITONES + 1e-6 for h in a)
    assert a != sfx_mod.plan_hits(events, [], 30, 8, 10)[0]


# --------------------------------------------------------------------------- the track


def test_build_writes_the_track_and_record():
    events = [ev(1.0, "whoosh"), ev(2.0, "pop"), ev(2.5, "check"), ev(3.0, "impact"),
              ev(4.0, "type"), ev(4.06, "type"), ev(5.0, "strike"), ev(6.0, "count"),
              ev(7.0, "tick"), ev(8.0, "swoosh_out")]  # fmt: skip
    job = make_job(events=events, words=[{"w": "Hallo", "t0": 2.5, "t1": 2.9}])
    doc = sfx_mod.build(job, say=lambda _: None)
    x = read_stereo(job / "work" / "sfx.wav")
    assert len(x) == 10 * RATE
    assert doc["counts"] == {k: 1 for k in _sfx_library.KINDS if k != "type"} | {"type": 2}
    assert {s["origin"] for s in doc["sounds"].values()} == {"procedural"}
    assert doc["events"] == [{"file": "a/events.json",
                              "sha256": _sfx_library.sha256_file(
                                  job / "work" / "motion" / "a" / "events.json")}]  # fmt: skip
    check = next(h for h in doc["hits"] if h["kind"] == "check")
    assert check["t"] == pytest.approx(2.5 - sfx_mod.ONSET_LEAD_S)  # moved off the onset
    for h in doc["hits"]:  # every hit sounds where it is placed
        i = int(h["t"] * RATE)
        assert np.abs(x[i : i + int(0.03 * RATE)]).max() > 1e-3
    assert np.abs(x[int(0.5 * RATE) : int(0.9 * RATE)]).max() == 0  # silence between hits
    assert doc["peak_dbfs"] <= sfx_mod.TRACK_PEAK_DBFS + 0.1
    assert sfx_mod.is_current(job)
    assert sfx_mod.build(job, say=lambda _: None)["wav_sha256"] == doc["wav_sha256"]  # stable


def test_track_is_stale_when_events_change():
    job = make_job(events=[ev(1.0)])
    sfx_mod.build(job, say=lambda _: None)
    assert sfx_mod.is_current(job)
    (job / "work" / "motion" / "a" / "events.json").write_text(json.dumps([ev(2.0)]))
    assert not sfx_mod.is_current(job)


def test_fit_lowers_one_hit():
    job = make_job(events=[ev(1.0), ev(3.0)])
    doc = sfx_mod.build(job, say=lambda _: None)
    key = sfx_mod.hit_key(doc["hits"][1])
    fitted = sfx_mod.build(job, say=lambda _: None, fit={key: -6.0})
    assert fitted["hits"][0]["gain_db"] == doc["hits"][0]["gain_db"]
    assert fitted["hits"][1]["gain_db"] == pytest.approx(doc["hits"][1]["gain_db"] - 6)
    assert fitted["hits"][1]["fit_db"] == -6.0 and fitted["fit"] == {key: -6.0}


def test_seed_picks_a_stable_sound_per_job():
    a = sfx_mod.build(make_job("j-a", events=[ev(1.0, "whoosh")]), say=lambda _: None)
    b = sfx_mod.build(make_job("j-a2", events=[ev(1.0, "whoosh")]), say=lambda _: None)
    assert a["seed"] != b["seed"]
    again = sfx_mod.build(jobs_root() / "j-a", say=lambda _: None)
    assert again["sounds"] == a["sounds"]


# --------------------------------------------------------------------------- mix analysis


def _speech(n_s=4.0, every=0.25):
    """Voice-like: band noise bursts (180 ms syllables, one per `every` s) at about -16 dBFS
    RMS while it sounds (a normalised voice speaks at about -15)."""
    rng = np.random.default_rng(0)
    n = int(n_s * RATE)
    x = rng.standard_normal(n)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / RATE)
    spec[(f < 200) | (f > 5000)] = 0
    x = np.fft.irfft(spec, n)
    x *= 10 ** (-16 / 20) / np.sqrt((x**2).mean())
    env = np.zeros(n)
    for k in range(int(n_s / every)):
        a = int((k * every) * RATE)
        env[a : a + int(0.18 * RATE)] = 1
    return (x * env).astype(np.float32)


def test_analysis_flags_a_sound_that_covers_speech():
    voice = _speech()
    hits = [{"t": 1.0, "kind": "whoosh", "pitch": 1.0}, {"t": 2.19, "kind": "pop", "pitch": 1.0}]
    sfx = np.zeros_like(voice)
    sfx[RATE : RATE + int(0.3 * RATE)] = _speech(0.3)[: int(0.3 * RATE)] * 0.7  # 3 dB under
    a = _sfx_mix.analyse_arrays(voice, sfx, np.abs(voice + sfx), hits,
                                {"whoosh": 0.3, "pop": 0.07})  # fmt: skip
    assert [d["t"] for d in a["masked"]] == [1.0]
    assert a["hits"][1]["margin_db"] is None or a["hits"][1]["margin_db"] > 50  # silence
    rep = {"mixed": True, "analysis": a}
    assert _sfx_mix.flags(rep) == ["sfx_masks_speech"]
    quiet = sfx * 10 ** (-30 / 20)
    a2 = _sfx_mix.analyse_arrays(voice, quiet, np.abs(voice + quiet), hits[:1], {"whoosh": 0.3})
    assert not a2["masked"] and a2["min_margin_db"] > _sfx_mix.MASK_DB
    assert _sfx_mix.flags({"mixed": True, "analysis": a2}) == []


def test_analysis_flags_clipping():
    voice = _speech()
    sfx = np.zeros_like(voice)
    sfx[2 * RATE : 2 * RATE + 2000] = 0.99
    pre = np.abs(voice + sfx)
    a = _sfx_mix.analyse_arrays(voice, sfx, pre, [{"t": 2.0, "kind": "impact"}], {"impact": 0.4})
    assert a["clipped"] and a["bus_clips"]
    assert _sfx_mix.flags({"mixed": True, "analysis": a}) == ["sfx_clipping"]  # DC: no band
    assert _sfx_mix.flags({"mixed": False}) == [] and _sfx_mix.flags(None) == []


@needs_ffmpeg
def test_sfx_mix_graph_runs(tmp_path):
    """The mix graph in ffmpeg: ducked, limited, measured; quiet SFX leave the voice clear."""
    from videocut.tools.render import measure

    voice = _speech(6.0, every=0.5)  # syllables at k * 0.5 s, pauses from k * 0.5 + 0.18
    vpath = wav_file(tmp_path / "voice.wav", voice)
    pop = _sfx_library.process(_sfx_synth.sound("pop"), "pop")

    def mixed(times, level):
        track = np.zeros((6 * RATE, 2), dtype=np.float32)
        for t in times:
            i = int(t * RATE)
            track[i : i + len(pop)] += pop[:, None]
        spath = tmp_path / "sfx.wav"
        _sfx_library.write_wav(spath, track.reshape(-1), channels=2)
        g = tmp_path / "g.txt"
        lines = _sfx_mix.mix_lines("vo_voice", 1, _sfx_mix.LEVEL_DB[level], 6.0, extra_out=True)
        g.write_text("[0:a]aformat=channel_layouts=stereo[vo_voice];\n" + "\n".join(lines) + "\n")
        hits = [{"t": t, "kind": "pop", "pitch": 1.0} for t in times]
        return _sfx_mix.analyse(["-i", str(vpath), "-i", str(spath)], g, tmp_path, hits,
                                {"pop": len(pop) / RATE}, measure)  # fmt: skip

    a = mixed([0.25, 1.25, 3.25, 4.25], "subtle")  # in the pauses
    assert len(a["hits"]) == 4 and not a["masked"] and not a["clipped"]
    assert a["voice_lufs"] is not None and a["sfx_lufs"] is not None
    assert a["sfx_lufs"] < a["voice_lufs"] - 10
    assert a["pre_limiter_peak_dbfs"] <= 0
    on = mixed([2.0, 3.0], "normal")  # on the syllables' onsets, unfitted
    assert all(d["margin_db"] is not None for d in on["hits"])
    assert a["min_margin_db"] is None  # nothing over speech in the pauses
    assert not list(tmp_path.glob("sfx_bus_*.wav"))  # the buses are removed again


# --------------------------------------------------------------------------- level, record


def test_level_resolution_and_job_setting():
    job = make_job(events=[ev(1.0)])
    assert sfx_mod.level({}) == "subtle"
    settings.save(sfx_default="off")
    assert sfx_mod.level({}) == "off"
    assert sfx_mod.level({"sfx": "normal"}) == "normal"
    with pytest.raises(ToolError, match='job.json "sfx"'):
        sfx_mod.level({"sfx": "loud"})
    res = set_sfx(job, "normal", say=lambda _: None)
    assert res["sfx"] == "normal" and res["previous"] == "off"
    assert set_sfx(job, "default", say=lambda _: None)["sfx"] is None
    with pytest.raises(ToolError):
        set_sfx(job, "loud", say=lambda _: None)


def test_a_changed_level_makes_the_reel_stale(monkeypatch):
    job = make_job(events=[ev(1.0)])
    assert pipeline._sfx_used(job) == "subtle"
    assert pipeline._sfx_used(make_job("none")) is None  # no motion events: no level
    data = json.loads((job / "job.json").read_text())
    data["cut"] = {"project_sha256": "p"}  # a cut from before the field
    (job / "job.json").write_text(json.dumps(data))
    set_sfx(job, "off", say=lambda _: None)
    assert json.loads((job / "job.json").read_text())["cut"]["sfx"] == "subtle"


def test_disclosure_lists_the_sounds():
    sounds = [
        {"kind": "whoosh", "id": "openverse-1", "origin": "openverse", "title": "Swipe",
         "author": "a", "licence": "CC0 1.0", "source_url": "https://x.org/1",
         "attribution": "Swipe by a (CC0) via Openverse/freesound"},
        {"kind": "pop", "id": "procedural-pop-1", "origin": "procedural", "title": "pop",
         "licence": "generated locally", "attribution": ""},
    ]  # fmt: skip
    doc = _disclosure.build([], "en", None, sounds)
    assert [s["id"] for s in doc["sounds"]] == ["openverse-1", "procedural-pop-1"]
    assert doc["snippet"] == "Sound effects: Swipe by a (CC0) via Openverse/freesound."
    md = _disclosure.markdown(doc)
    assert "## Sound effects" in md and "generated locally" in md and "https://x.org/1" in md
    assert "Kein KI-generiertes" in _disclosure.markdown(_disclosure.build([], "de", None, []))


# --------------------------------------------------------------------------- API

TOKEN, BASE = "t", "http://127.0.0.1:4300"


@pytest.fixture
def client():
    c = TestClient(create_app(token=TOKEN, port=4300), base_url=BASE)
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303
    c.headers.update({"Origin": BASE})
    return c


def test_api_settings_and_library(client, tmp_path):
    r = client.get("/api/settings/sfx").json()
    assert r["sfx_default"] == "subtle" and r["levels"] == ["off", "subtle", "normal"]
    assert set(r["kinds"]) == set(_sfx_library.KINDS)
    assert r["kinds"]["pop"]["uses"] == "procedural"
    assert client.put("/api/settings/sfx", json={"sfx_default": "normal"}).json()[
        "sfx_default"] == "normal"  # fmt: skip
    assert client.put("/api/settings/sfx", json={"sfx_default": "loud"}).status_code == 422
    au = client.get("/api/settings/sfx/pop/procedural-pop-1/audio")
    assert au.status_code == 200 and au.content[:4] == b"RIFF"
    assert client.get("/api/settings/sfx/boing/x/audio").status_code == 404
    assert client.delete("/api/settings/sfx/pop/procedural-pop-1").status_code == 404
    if has_ffmpeg():
        t = np.arange(int(0.2 * RATE)) / RATE
        src = wav_file(tmp_path / "m.wav", 0.5 * np.sin(2 * np.pi * 700 * t) * np.exp(-t * 20))
        with src.open("rb") as f:
            up = client.post("/api/settings/sfx/check", files={"sound": ("m.wav", f, "audio/wav")},
                             data={"title": "Mine"})  # fmt: skip
        assert up.status_code == 200, up.text
        added = up.json()["added"]
        assert up.json()["kinds"]["check"]["uses"] == "upload" and added["title"] == "Mine"
        gone = client.delete(f"/api/settings/sfx/check/{added['id']}").json()
        assert gone["kinds"]["check"]["uses"] == "procedural"
        bad = client.post("/api/settings/sfx/check",
                          files={"sound": ("x.wav", b"RIFFjunk", "audio/wav")})  # fmt: skip
        assert bad.status_code == 415


def test_api_job_level(client):
    job = make_job("api1", events=[ev(1.0)])
    view = client.get("/api/jobs/api1").json()
    assert (view["sfx"], view["sfx_resolved"], view["sfx_events"]) == (None, "subtle", True)
    r = client.put("/api/jobs/api1/sfx", json={"sfx": "off"})
    assert r.status_code == 200 and r.json()["sfx"] == "off"
    assert json.loads((job / "job.json").read_text())["sfx"] == "off"
    assert client.put("/api/jobs/api1/sfx", json={"sfx": "loud"}).status_code == 422
    assert client.put("/api/jobs/api1/sfx", json={"sfx": "default"}).json()["sfx"] is None
    assert client.put("/api/jobs/nope/sfx", json={"sfx": "off"}).status_code == 404
