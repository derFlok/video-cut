import json
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut import edl
from videocut.tools import sync as sync_tool

pytestmark = pytest.mark.media
FRAME = 1 / 30


def _job(capsys, cam, scr, job_id="s1"):
    code, s, _ = run_tool(capsys, "job", "new", str(cam), "--screen", str(scr), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    code, s, _ = run_tool(capsys, "ingest", "--job", str(job))
    assert code == 0, s
    return job


def _sync(capsys, job, *extra):
    code, s, out = run_tool(capsys, "sync", "--job", str(job), *extra)
    return code, s, out


def test_offset_within_one_frame(capsys, demo_pair):
    cam, scr, meta = demo_pair
    job = _job(capsys, cam, scr)
    code, s, _ = _sync(capsys, job)
    assert code == 0, s
    assert s["method"] == "audio_xcorr" and s["confidence"] > 0.5
    assert abs(s["offset_s"] - meta["offset"]) <= FRAME
    assert abs(s["drift_ppm"] or 0) < 500
    saved = edl.read_sync(job / "work" / "sync.json")
    assert saved is not None and saved["offset_s"] == s["offset_s"]
    o0, o1 = saved["overlap"]
    assert o0 == 0 and o1 == pytest.approx(
        min(meta["camera_duration"], meta["screen_duration"] - saved["offset_s"]), abs=0.05
    )
    assert edl.screen_time(2.0, saved) == pytest.approx(2.0 + saved["offset_s"])


def test_kept_until_redo_or_manual(capsys, demo_pair):
    cam, scr, _ = demo_pair
    job = _job(capsys, cam, scr)
    assert _sync(capsys, job)[1]["kept"] is False
    assert _sync(capsys, job)[1]["kept"] is True
    assert _sync(capsys, job, "--redo")[1]["kept"] is False
    code, s, _ = _sync(capsys, job, "--offset", "1.5")
    assert code == 0 and s["method"] == "manual" and s["offset_s"] == 1.5
    assert s["confidence"] == 1.0 and s["drift_ppm"] is None
    assert edl.read_sync(job / "work" / "sync.json")["offset_s"] == 1.5


def test_drift_is_reported(capsys, clip_dir):
    cam, scr, meta = clips.screen_pair(clip_dir, drift=0.001, size=(1440, 900))
    job = _job(capsys, cam, scr, "drift")
    code, s, _ = _sync(capsys, job)
    assert code == 0, s
    assert s["drift_ppm"] == pytest.approx(1000, abs=250), s
    assert abs(s["offset_s"] - meta["offset"]) < 0.1


def test_no_screen_audio_fails_clearly_and_manual_works(capsys, clip_dir):
    cam, scr, _ = clips.screen_pair(clip_dir, screen_audio=False, size=(1440, 900))
    job = _job(capsys, cam, scr, "mute")
    assert not (job / "work" / "screen_audio.wav").exists()
    code, s, _ = _sync(capsys, job)
    assert code == 1 and "no audio" in s["error"] and "--offset" in s["error"]
    assert not (job / "work" / "sync.json").exists()
    code, s, _ = _sync(capsys, job, "--offset", "3.27")
    assert code == 0 and s["method"] == "manual"


def test_manual_offset_without_overlap_fails(capsys, demo_pair):
    cam, scr, meta = demo_pair
    job = _job(capsys, cam, scr, "far")
    code, s, _ = _sync(capsys, job, "--offset", "500")
    assert code == 1 and "do not overlap" in s["error"]


def test_job_without_screen_fails(capsys, portrait_clip):
    run_tool(capsys, "job", "new", str(portrait_clip), "--id", "nos")
    code, s, _ = _sync(capsys, "nos")
    assert code == 1 and "no screen recording" in s["error"]


# --- estimator on synthetic signals (no media tools) ---


def _speechlike(seconds, seed):
    """Noise with an irregular syllable-like loudness pattern (not periodic)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = int(seconds * sync_tool.RATE)
    blocks = rng.random(n // 2400 + 1) ** 3  # 50 ms steps, mostly quiet, some loud
    env = np.convolve(blocks.repeat(2400)[:n], np.ones(960) / 960, mode="same")
    return (rng.standard_normal(n) * env * 0.3).astype(np.float32)


@pytest.mark.parametrize("lag", [0.0, 2.5, -1.25])
def test_estimate_recovers_lag(lag):
    import numpy as np

    rate = sync_tool.RATE
    voice = _speechlike(30, 1)
    pad = np.zeros(int(abs(lag) * rate), np.float32)
    cam = np.concatenate([pad, voice]) if lag < 0 else voice
    scr = np.concatenate([pad, voice]) if lag > 0 else voice
    est = sync_tool.estimate(cam, scr)
    assert est["method"] == "audio_xcorr"
    assert est["lag"] == pytest.approx(lag, abs=0.005)


def test_estimate_silent_screen_fails():
    import numpy as np

    with pytest.raises(sync_tool.SyncError, match="--offset"):
        sync_tool.estimate(_speechlike(5, 1), np.zeros(5 * sync_tool.RATE, np.float32))


def test_estimate_clap_fallback():
    import numpy as np

    rate = sync_tool.RATE
    rng = np.random.default_rng(3)

    def track(seed, clap_at):
        x = _speechlike(12, seed) * 0.05  # different talk in each: no envelope match
        if clap_at is not None:
            i = int(clap_at * rate)
            x[i : i + 400] += (rng.standard_normal(400) * 0.9).astype(np.float32)
        return x

    lag, conf = sync_tool.clap(track(10, 1.0), track(11, 2.2))
    assert lag == pytest.approx(1.2, abs=0.002) and conf > 0.5
    with pytest.raises(sync_tool.SyncError, match="--offset"):  # unrelated talk, no clap
        sync_tool.estimate(track(10, None), track(11, None))


def test_sync_json_is_valid_json_with_contract_fields(capsys, demo_pair):
    cam, scr, _ = demo_pair
    job = _job(capsys, cam, scr, "contract")
    _sync(capsys, job)
    data = json.loads((job / "work" / "sync.json").read_text())
    assert {"offset_s", "method", "confidence", "drift_ppm", "overlap"} <= set(data)


def test_estimate_rejects_tracks_shorter_than_a_second():
    import numpy as np

    short = (np.random.default_rng(2).standard_normal(sync_tool.RATE // 4) * 0.1).astype("f4")
    with pytest.raises(sync_tool.SyncError, match="shorter than 1 s"):
        sync_tool.estimate(_speechlike(5, 1), short)
