"""Filler candidates and transcription on a generated clip with known filler positions."""

import json
import os
from pathlib import Path

import clips
import pytest
from helpers import run_tool

pytestmark = pytest.mark.media

# Captured at import, before the autouse fixture points VIDEOCUT_HOME at a temp folder.
REAL_MODELS = Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut")) / "models"


@pytest.fixture(scope="session")
def filler_clip(clip_dir):
    return clips.filler_clip(clip_dir / "fillers")


@pytest.fixture
def ingested(capsys, filler_clip):
    clip, layout = filler_clip
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", "fill")
    assert code == 0, s
    job = Path(s["job"])
    code, s, _ = run_tool(capsys, "ingest", "--job", "fill")
    assert code == 0, s
    return job, layout


def _hits(fillers, layout, kinds=("word", "gap_energy")):
    """Known fillers that some candidate of `kinds` overlaps."""
    known = [e for e in layout if e["kind"] == "filler"]
    cands = [c for c in fillers if c["kind"] in kinds]
    return [k for k in known if any(c["t0"] < k["a1"] and c["t1"] > k["a0"] for c in cands)]


def _layout_words(layout):
    """Exact word timestamps for the phrases only, as a model that drops fillers would give
    them: words spread evenly over each phrase's audible span."""
    words = []
    for e in layout:
        if e["kind"] != "phrase":
            continue
        toks = e["text"].split()
        step = (e["a1"] - e["a0"]) / len(toks)
        for i, w in enumerate(toks):
            t0 = e["a0"] + i * step
            words.append({"w": w, "t0": round(t0, 3), "t1": round(t0 + step, 3), "conf": None})
    return words


def test_gap_energy_recall_and_pauses_on_known_fillers(capsys, ingested):
    job, layout = ingested
    duration = layout[-1]["t1"]
    doc = {"model": "layout", "language": "de", "duration": duration}
    doc["words"] = _layout_words(layout)
    (job / "work" / "words.json").write_text(json.dumps(doc, ensure_ascii=False))
    code, s, _ = run_tool(capsys, "candidates", "--job", "fill")
    assert code == 0, s
    c = json.loads((job / "work" / "candidates.json").read_text())
    hits = _hits(c["fillers"], layout, kinds=("gap_energy",))
    assert len(hits) >= 2, (hits, c["fillers"])
    # Every gap_energy candidate lies on a known filler (no false alarms on clean TTS).
    known = [e for e in layout if e["kind"] == "filler"]
    for f in c["fillers"]:
        assert any(f["t0"] < k["a1"] + 0.05 and f["t1"] > k["a0"] - 0.05 for k in known), f
    # The plain 0.8 s silence between two phrases is a pause.
    pause = next(e for e in layout if e["kind"] == "silence" and e["t1"] - e["t0"] > 0.7)
    assert any(
        p["t0"] <= pause["t0"] + 0.05 and p["t1"] >= pause["t1"] - 0.05 for p in c["pauses"]
    ), c["pauses"]
    packed = (job / "work" / "takes_packed.md").read_text()
    assert packed.count("{~") >= 2 and "[000.00-" in packed


@pytest.mark.skipif(
    not (REAL_MODELS / "whisper-large-v3-turbo" / "weights.safetensors").is_file(),
    reason="whisper-large-v3-turbo not installed (videocut models pull)",
)
def test_transcribe_turbo_end_to_end(capsys, ingested, videocut_home):
    job, layout = ingested
    (videocut_home / "models").symlink_to(REAL_MODELS)
    code, s, _ = run_tool(capsys, "transcribe", "--job", "fill")
    assert code == 0, s
    assert s["transcribed"] and s["language"] == "de" and s["words"] >= 15
    words = json.loads((job / "work" / "words.json").read_text())
    assert set(words) == {"model", "language", "duration", "words"}
    assert words["duration"] == pytest.approx(layout[-1]["t1"], abs=0.05)
    starts = [w["t0"] for w in words["words"]]
    assert starts == sorted(starts)
    keys = {"w", "t0", "t1", "conf", "t0_asr", "t1_asr"}  # aligned, model times kept
    assert all(w["t1"] >= w["t0"] and set(w) == keys for w in words["words"])
    assert s["aligned"] and s["align_shift_ms"] is not None
    c = json.loads((job / "work" / "candidates.json").read_text())
    assert len(_hits(c["fillers"], layout)) >= 2, c["fillers"]
    assert (job / "work" / "takes_packed.md").is_file()
    # Second run keeps the transcript and only redoes candidates.
    code, s, _ = run_tool(capsys, "transcribe", "--job", "fill")
    assert code == 0 and s["transcribed"] is False
    code, s, _ = run_tool(capsys, "transcribe", "--job", "fill", "--no-align")
    assert code == 0 and s["transcribed"] is False and not s["aligned"]
    plain = json.loads((job / "work" / "words.json").read_text())["words"]
    assert all(set(w) == {"w", "t0", "t1", "conf"} for w in plain)
