"""Transcript vocabulary (tools/vocab.py): validation, merging, matching, the Whisper prompt,
applying to an existing job and the CLI. Invented names only."""

import json
import os
import shutil
from pathlib import Path

import pytest
from helpers import run_tool

from videocut import brand
from videocut.tools import transcribe, vocab
from videocut.tools.common import jobs_root

ZEN = {"term": "Zentrovia", "variants": ["Zentrowia", "Sentro Via"]}
LUMO = {"term": "Lumo Cloud", "variants": ["Lumocloud"]}


def words(text: str, conf: float = 0.5) -> list[dict]:
    return [
        {"w": w, "t0": i * 1.0, "t1": i * 1.0 + 0.5, "conf": conf, "t0_asr": i * 1.0,
         "t1_asr": i * 1.0 + 0.4}
        for i, w in enumerate(text.split())
    ]  # fmt: skip


def texts(ws: list[dict]) -> list[str]:
    return [w["w"] for w in ws]


# --- validation and merge ----------------------------------------------------------------


def test_check_accepts_clean_entries_and_normalises_them():
    entries, errors = vocab.check(
        [
            {
                "term": "  Zentrovia ",
                "variants": [" Zentrowia", "zentrovia", "Sentro  Via"],
                "note": "us",
            }
        ]
    )
    assert errors == []
    # the term in another case stays (it is matched anyway), spacing collapsed
    assert entries == [
        {"term": "Zentrovia", "variants": ["Zentrowia", "zentrovia", "Sentro Via"], "note": "us"}
    ]


@pytest.mark.parametrize(
    "value, fragment",
    [
        ("x", "expected a list"),
        ([{"variants": ["a"]}], ".term: required"),
        ([{"term": "  "}], ".term: required"),
        ([{"term": "!!"}], "no letters or digits"),
        ([{"term": "Zentrovia", "variants": ["Zentro.*"]}], "plain text, not a pattern"),
        ([{"term": "Zentrovia", "variants": ["Zen(trowia)"]}], "plain text, not a pattern"),
        ([{"term": "Zentrovia", "variants": ["Zentrowia", "zentro-wia"]}], "already used"),
        ([ZEN, {"term": "Kalda", "variants": ["Zentrowia"]}], "already used"),
        ([ZEN, {"term": "zentrovia"}], "already used"),
        ([{"term": "Zentrovia", "variants": "Zentrowia"}], "expected a list of strings"),
        ([{"term": "Zentrovia", "spelling": "x"}], "unknown field"),
    ],
)
def test_check_reports_invalid_entries(value, fragment):
    _, errors = vocab.check(value)
    assert any(fragment in e for e in errors), errors
    assert all(e.startswith("$.vocabulary") for e in errors)


def test_merge_lets_the_job_win_conflicts():
    b = vocab.validated(
        [
            ZEN,
            {"term": "Kalda", "variants": ["Calda"]},
            {"term": "Lumo Cloud", "variants": ["Lumo Clowd"]},
        ]
    )
    j = vocab.validated(
        [{"term": "zentroVia", "variants": ["Sentrovia"], "note": "job"},
         {"term": "Lumo", "variants": ["Kalda", "Lumo Clowd"]}]
    )  # fmt: skip
    merged = vocab.merge(b, j)
    by = {e["term"]: e for e in merged}
    # same term: the job's spelling and note, both lists of variants
    assert by["zentroVia"]["variants"] == ["Sentrovia", "Zentrowia"]  # "Sentro Via" = "Sentrovia"
    assert by["zentroVia"]["note"] == "job"
    # a brand term the job calls a misspelling is gone; a shared variant is the job's
    assert "Kalda" not in by and by["Lumo Cloud"]["variants"] == []
    assert by["Lumo"]["variants"] == ["Kalda", "Lumo Clowd"]
    _, errors = vocab.check(merged)
    assert errors == []


# --- matching -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("bei Zentrowia arbeiten", ["bei", "Zentrovia", "arbeiten"]),  # variant
        ("bei ZENTROWIA arbeiten", ["bei", "Zentrovia", "arbeiten"]),  # case
        ("bei zentrovia arbeiten", ["bei", "Zentrovia", "arbeiten"]),  # the term's own case
        ("„Zentrowia“, sagt", ["„Zentrovia“,", "sagt"]),  # punctuation kept around it
        ("das ist Sentro via.", ["das", "ist", "Zentrovia."]),  # multi-word variant
        ("das ist Zentro Via!", ["das", "ist", "Zentrovia!"]),  # the term split by the model
        ("die Zentrowias Leute", ["die", "Zentrovias", "Leute"]),  # inflection
        ("Zentrowia's team", ["Zentrovia's", "team"]),  # possessive
        ("zentrowia-Kunden fragen", ["Zentrovia-Kunden", "fragen"]),  # hyphen compound
        ("im Lumocloud Konto", ["im", "Lumo Cloud", "Konto"]),
        ("in der lumo cloud.", ["in", "der", "Lumo Cloud."]),
    ],
)
def test_matching(spoken, expected):
    out = vocab.apply_words(words(spoken), [ZEN, LUMO])
    assert texts(out) == expected


@pytest.mark.parametrize(
    "spoken",
    [
        "die Zentrowiaplattform läuft",  # a longer word that contains a variant
        "Vorzentrowia und Zentrowianer",
        "Sentro. Via ist es",  # a sentence ends between the two words
        "Sentro, Via",
        "via Zentrale",
    ],
)
def test_no_false_hits(spoken):
    ws = words(spoken)
    assert texts(vocab.apply_words(ws, [ZEN, LUMO])) == texts(ws)


def test_short_pieces_are_not_joined_into_a_short_term():
    ws = words("A I hilft")
    assert texts(vocab.apply_words(ws, [{"term": "AI", "variants": []}])) == ["A", "I", "hilft"]
    assert texts(vocab.apply_words(words("ai hilft"), [{"term": "AI", "variants": []}]))[0] == "AI"


def test_apply_keeps_the_model_text_and_restores_it():
    ws = words("Sentro via und Zentrowia und Zentrovia")
    out = vocab.apply_words(ws, [ZEN])
    merged, single = out[0], out[2]
    assert merged["w"] == "Zentrovia" and merged["w_asr"] == "Sentro via"
    assert (merged["t0"], merged["t1"], merged["t0_asr"], merged["t1_asr"]) == (0.0, 1.5, 0.0, 1.4)
    assert merged["vocab"] == "Zentrovia" and len(merged["parts"]) == 2
    assert single["w_asr"] == "Zentrowia" and single["vocab"] == "Zentrovia"
    assert "w_asr" not in out[4]  # already right: not counted
    assert vocab.counts(out) == {"applied": 2, "terms": {"Zentrovia": 2}}
    assert vocab.restore(out) == ws
    # applying again (or with another vocabulary) starts from the model's text
    assert vocab.apply_words(out, [ZEN]) == out
    assert texts(vocab.apply_words(out, [])) == texts(ws)


def test_restore_fits_merged_parts_into_moved_times():
    out = vocab.apply_words(words("Sentro via"), [ZEN])
    moved = [{**out[0], "t0": 10.0, "t1": 13.0}]  # e.g. the video time shift
    back = vocab.restore(moved)
    assert [(w["t0"], w["t1"]) for w in back] == [(10.0, 11.0), (12.0, 13.0)]


def test_captions_are_corrected_forwards():
    caps = [
        {"t0": 0.0, "t1": 2.0, "words": [
            {"w": "bei", "t0": 0.0, "t1": 0.3, "emph": False},
            {"w": "Sentro", "t0": 0.3, "t1": 0.6, "emph": False},
            {"w": "Via", "t0": 0.6, "t1": 0.9, "emph": True},
        ]},
        {"t0": 2.0, "t1": 3.0, "words": [{"w": "Zentrowia.", "t0": 2.0, "t1": 2.5}]},
        {"t0": 3.0, "t1": 4.0, "words": "not a list"},
    ]  # fmt: skip
    assert vocab.apply_captions(caps, [ZEN]) == 3
    assert [w["w"] for w in caps[0]["words"]] == ["bei", "Zentrovia"]
    joined = caps[0]["words"][1]
    assert (joined["t0"], joined["t1"], joined["emph"], joined["w_asr"]) == (
        0.3,
        0.9,
        True,
        "Sentro Via",
    )
    assert caps[1]["words"][0]["w"] == "Zentrovia."
    assert vocab.apply_captions(caps, [ZEN]) == 0  # idempotent


def test_suggestions_report_near_misses_only():
    ws = words("Zentrowya sagt Zentrowia und Zentrowya ist Lumo Clout, Zentrale Daten")
    ws[-1]["conf"] = 0.99
    out = vocab.apply_words(ws, [ZEN, LUMO])
    items = vocab.suggestions(out, [ZEN, LUMO])
    assert [(s["heard"], s["term"], s["count"]) for s in items] == [
        ("Zentrowya", "Zentrovia", 2),
        ("Lumo Clout", "Lumo Cloud", 1),
    ]
    assert texts(out)[0] == "Zentrowya"  # never applied
    confident = words("Zentrowya", conf=0.95)
    assert vocab.suggestions(confident, [ZEN]) == []
    assert len(vocab.suggestions(confident, [ZEN], all_conf=True)) == 1


# --- prompt -------------------------------------------------------------------------------


def test_prompt_appends_terms_after_the_filler_sample():
    filler = transcribe.FILLER_PROMPT["de"]
    text, used, dropped = vocab.prompt_text(filler, [ZEN, LUMO])
    assert text == f"{filler} Zentrovia, Lumo Cloud."
    assert used == ["Zentrovia", "Lumo Cloud"] and dropped == []
    assert vocab.prompt_text(None, []) == (None, [], [])


def test_prompt_stays_within_whisper_budget_and_keeps_the_filler_sample():
    filler = transcribe.FILLER_PROMPT["de"]
    many = [{"term": f"Begriffname{i:03d}", "variants": []} for i in range(200)]
    text, used, dropped = vocab.prompt_text(filler, many)
    assert vocab.prompt_tokens(text) <= vocab.PROMPT_BUDGET
    assert text.startswith(filler)  # the sample is never pushed out
    assert used and dropped and len(used) + len(dropped) == 200
    # the job's terms (last in the effective list) get the room first
    assert "Begriffname199" in used and "Begriffname000" in dropped


def test_prompt_token_count_is_whisper_tokens():
    if vocab._encoding() is None:
        pytest.skip("mlx-whisper's tokenizer file is not installed")
    n = vocab.prompt_tokens(transcribe.FILLER_PROMPT["de"])
    assert 15 < n < 60  # BPE tokens, far fewer than the bytes
    assert n < len(transcribe.FILLER_PROMPT["de"].encode())


def test_whisper_prompts_fall_back_step_by_step():
    entries = [ZEN]
    tries = transcribe.prompts("de", entries)
    filler = transcribe.FILLER_PROMPT["de"]
    assert tries == [(f"{filler} Zentrovia.", ["Zentrovia"]), (filler, []), (None, [])]
    assert transcribe.prompts("de", []) == [(filler, []), (None, [])]
    assert transcribe.prompts("fr", entries) == [("Zentrovia.", ["Zentrovia"]), (None, [])]
    assert transcribe.prompts("de", entries, prompt=False) == [(None, [])]


# --- job and brand ------------------------------------------------------------------------


def make_job(job_id="v1", **extra) -> Path:
    job = jobs_root() / job_id
    for d in ("input", "work", "out"):
        (job / d).mkdir(parents=True)
    (job / "input" / "take.mov").write_bytes(b"x")
    data = {"id": job_id, "created": "2026-10-13T10:00:00+02:00", "source": "input/take.mov",
            "type": "thought", "status": "ingested", **extra}  # fmt: skip
    (job / "job.json").write_text(json.dumps(data))
    doc = {"model": "m", "language": "de", "duration": 20.0,
           "words": words("Hallo von Zentrowia und Sentro via , Lumocloud heute")}  # fmt: skip
    (job / "work" / "words.json").write_text(json.dumps(doc))
    (job / "work" / "transcribe.json").write_text(json.dumps({"model": "m"}))
    return job


def make_brand(bid="acme", vocabulary=None) -> Path:
    d = brand.brands_root() / bid
    shutil.copytree(brand.BUNDLED_ROOT / brand.EXAMPLE_ID, d)
    data = json.loads((d / "brand.json").read_text())
    data["name"] = "Neutral"
    if vocabulary is not None:
        data["vocabulary"] = vocabulary
    (d / "brand.json").write_text(json.dumps(data))
    return d


def test_brand_check_validates_the_vocabulary(capsys):
    make_brand(vocabulary=[ZEN])
    code, s, _ = run_tool(capsys, "brand", "check", "acme")
    assert code == 0, s
    assert brand.load("acme")["vocabulary"] == [ZEN]
    make_brand("broken", vocabulary=[{"term": "Zentrovia", "variants": ["a|b"]}])
    code, s, _ = run_tool(capsys, "brand", "check", "broken")
    assert code == 1 and "$.vocabulary[0].variants[0]" in s["error"]


def test_brand_vocab_cli_edits_only_user_brands(capsys):
    d = make_brand()
    code, s, _ = run_tool(capsys, "brand", "vocab", "acme", "add", "Zentrovia", "Zentrowia")
    assert code == 0 and s["vocabulary"] == [{"term": "Zentrovia", "variants": ["Zentrowia"]}]
    code, s, _ = run_tool(capsys, "brand", "vocab", "acme", "add", "zentrovia", "Sentrovia",
                          "--note", "the company")  # fmt: skip
    assert s["vocabulary"] == [
        {"term": "Zentrovia", "variants": ["Zentrowia", "Sentrovia"], "note": "the company"}
    ]
    code, s, _ = run_tool(capsys, "brand", "vocab", "acme", "remove", "Zentrovia", "Zentrowia")
    assert s["vocabulary"][0]["variants"] == ["Sentrovia"]
    code, s, out = run_tool(capsys, "brand", "vocab", "acme", "list")
    assert code == 0 and "Zentrovia\tSentrovia" in out
    stored = json.loads((d / "brand.json").read_text())
    assert stored["name"] == "Neutral" and stored["vocabulary"] == s["vocabulary"]
    code, s, _ = run_tool(capsys, "brand", "vocab", "acme", "add", "Lumo", "Lu*")
    assert code == 1 and "pattern" in s["error"]
    code, s, _ = run_tool(capsys, "brand", "vocab", "acme", "remove", "Kalda")
    assert code == 1 and "not in the vocabulary" in s["error"]
    code, s, _ = run_tool(capsys, "brand", "vocab", "_example", "add", "Zentrovia")
    assert code == 1 and "read-only" in s["error"]


def test_job_vocab_cli_and_effective_vocabulary_in_job_show(capsys, monkeypatch):
    make_brand(vocabulary=[ZEN])
    make_job(brand="acme")
    code, s, _ = run_tool(capsys, "job", "vocab", "v1", "add", "Lumo Cloud", "Lumocloud")
    assert code == 0 and s["vocabulary"] == [LUMO]
    assert s["effective"] == [ZEN, LUMO]
    code, s, out = run_tool(capsys, "job", "show", "v1")
    assert s["vocabulary_effective"] == [ZEN, LUMO]
    assert "Zentrovia (not: Zentrowia, Sentro Via); Lumo Cloud (not: Lumocloud)" in out
    code, s, _ = run_tool(capsys, "vocab", "show", "v1")
    assert s["brand"] == [ZEN] and s["job"] == [LUMO] and s["applied"] is None
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")  # an agent run reads, never edits
    code, s, _ = run_tool(capsys, "job", "vocab", "v1", "add", "Kalda")
    assert code == 1 and "only a human" in s["error"]
    code, s, _ = run_tool(capsys, "job", "vocab", "v1", "list")
    assert code == 0


def test_job_new_takes_vocab_flags(capsys, tmp_path):
    video = tmp_path / "take.mov"
    video.write_bytes(b"x")
    code, s, _ = run_tool(capsys, "job", "new", str(video), "--id", "n1",
                          "--vocab", "Zentrovia=Zentrowia, Sentro Via",
                          "--vocab", "Kalda")  # fmt: skip
    assert code == 0, s
    data = json.loads((jobs_root() / "n1" / "job.json").read_text())
    assert data["vocabulary"] == [
        {"term": "Zentrovia", "variants": ["Zentrowia", "Sentro Via"]},
        {"term": "Kalda", "variants": []},
    ]
    code, s, _ = run_tool(capsys, "job", "new", str(video), "--id", "n2", "--vocab", "=x")
    assert code == 1 and "term" in s["error"] and not (jobs_root() / "n2").exists()


def test_vocab_apply_on_an_existing_job(capsys):
    make_brand(vocabulary=[ZEN])
    job = make_job(brand="acme", vocabulary=[LUMO])
    caption = {"t0": 0.0, "t1": 2.0, "words": [{"w": "von", "t0": 0, "t1": 1},
                                               {"w": "Zentrowia", "t0": 1, "t1": 2}]}  # fmt: skip
    edl = {"fps": 30, "size": [1080, 1920], "source": "input/take.mov",
           "segments": [{"in": 0.0, "out": 9.0}], "captions": [caption]}  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "overlays.json").write_text(json.dumps({"captions": edl["captions"]}))
    data = json.loads((job / "job.json").read_text())
    data["overlays"] = {"brand": "acme", "verify_ok": True}
    (job / "job.json").write_text(json.dumps(data))

    code, s, out = run_tool(capsys, "vocab", "apply", "v1")
    assert code == 0, s
    assert (s["applied"], s["terms"]) == (3, {"Lumo Cloud": 1, "Zentrovia": 2})
    assert s["caption_words"] == 1 and s["overlays_stale"] is True
    assert "overlays are stale" in out
    ws = json.loads((job / "work" / "words.json").read_text())["words"]
    assert texts(ws) == [
        "Hallo",
        "von",
        "Zentrovia",
        "und",
        "Zentrovia",
        ",",
        "Lumo Cloud",
        "heute",
    ]
    meta = json.loads((job / "work" / "transcribe.json").read_text())
    assert meta["vocabulary"] == {"applied": 3, "terms": {"Lumo Cloud": 1, "Zentrovia": 2},
                                  "entries": 2}  # fmt: skip
    for name in ("edl.json", "overlays.json"):
        caps = json.loads((job / "work" / name).read_text())["captions"]
        assert caps[0]["words"][1]["w"] == "Zentrovia"
    assert json.loads((job / "job.json").read_text())["overlays"]["stale"] == "vocabulary"

    # again: nothing new, the counts stay
    code, s, _ = run_tool(capsys, "vocab", "apply", "v1")
    assert s["applied"] == 3 and not s["words_changed"] and s["caption_words"] == 0

    # a variant removed: that word goes back to what the model heard
    run_tool(capsys, "job", "vocab", "v1", "remove", "Lumo Cloud")
    code, s, _ = run_tool(capsys, "vocab", "apply", "v1")
    assert s["applied"] == 2 and s["words_changed"]
    ws = json.loads((job / "work" / "words.json").read_text())["words"]
    assert "Lumocloud" in texts(ws)

    code, s, _ = run_tool(capsys, "vocab", "suggest", "v1")
    assert code == 0 and s["suggestions"] == []


def test_vocab_apply_needs_a_transcript(capsys):
    job = make_job()
    (job / "work" / "words.json").unlink()
    code, s, _ = run_tool(capsys, "vocab", "apply", "v1")
    assert code == 1 and "words.json is missing" in s["error"]


def test_stale_overlays_are_not_done(capsys):
    from videocut import run as pipeline

    job = make_job(brand="acme")
    (job / "work" / "edl.json").write_text("{}")
    (job / "out" / "render.json").write_text(json.dumps({"overlay": True}))
    rec = {"brand": "acme", "edl_sha256": pipeline._file_sha(job / "work" / "edl.json"),
           "verify_ok": True}  # fmt: skip
    data = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**data, "overlays": rec}))
    assert pipeline.overlays_done(job, "acme")
    (job / "job.json").write_text(json.dumps({**data, "overlays": {**rec, "stale": "vocabulary"}}))
    assert not pipeline.overlays_done(job, "acme")


def test_run_vocab_only_for_a_new_job(tmp_path):
    from videocut import run as pipeline

    make_job()
    with pytest.raises(pipeline.RunError, match="--vocab only works"):
        pipeline.open_job("v1", None, None, vocab=["Zentrovia=Zentrowia"])


REAL_MODELS = Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut")) / "models"


@pytest.mark.media
@pytest.mark.skipif(
    not (REAL_MODELS / "whisper-large-v3-turbo" / "weights.safetensors").is_file(),
    reason="whisper-large-v3-turbo not installed (videocut models pull)",
)
def test_turbo_with_vocabulary_keeps_the_fillers(capsys, clip_dir, videocut_home):
    """The terms in the prompt must not cost the filler behaviour (measured: 3/3 fillers,
    no fallback to sampling, with 3 to 40 terms on the generated clips)."""
    import clips

    clip, layout = clips.filler_clip(clip_dir / "fillers")
    code, s, _ = run_tool(capsys, "job", "new", str(clip), "--id", "vf",
                          "--vocab", "Zentrovia=Zentrowia", "--vocab", "Lumo Cloud")  # fmt: skip
    assert code == 0, s
    assert run_tool(capsys, "ingest", "--job", "vf")[0] == 0
    (videocut_home / "models").symlink_to(REAL_MODELS)
    code, s, _ = run_tool(capsys, "transcribe", "--job", "vf")
    assert code == 0, s
    meta = json.loads((jobs_root() / "vf" / "work" / "transcribe.json").read_text())
    assert meta["prompt_used"] and meta["prompt_terms"] == ["Zentrovia", "Lumo Cloud"]
    assert meta["vocabulary"]["applied"] == 0 and s["vocabulary"]["applied"] == 0
    known = sum(1 for e in layout if e["kind"] == "filler")
    assert s["filler_words"] + s["gap_energy"] >= known - 1, s
