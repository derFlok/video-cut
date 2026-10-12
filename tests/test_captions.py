import json
from pathlib import Path

from helpers import run_tool

from videocut.edl import from_dict
from videocut.tools import _render_plan as rp
from videocut.tools import captions as cap


def _edl(segments, **extra):
    return from_dict(
        {"fps": 30, "size": [1080, 1920], "source": "input/a.mp4", "segments": segments, **extra}
    )


def _w(w, t0, t1):
    return {"w": w, "t0": t0, "t1": t1, "conf": 0.9}


WORDS = [
    _w("Also", 0.30, 0.60),
    _w("äh", 0.70, 0.95),  # filler, cut away below
    _w("heute", 1.00, 1.30),
    _w("zeige", 1.35, 1.60),
    _w("ich", 1.62, 1.75),
    _w("drei", 2.40, 2.70),
]


def test_words_map_to_output_time_and_cut_words_drop():
    # keep 0.3-0.65 and 1.0-2.8: the filler "äh" lies in the removed range
    edl = _edl([{"in": 0.3, "out": 0.65}, {"in": 1.0, "out": 2.8}])
    words = cap.map_words(WORDS, edl.segments, edl.fps)
    assert [w["w"] for w in words] == ["Also", "heute", "zeige", "ich", "drei"]
    also, heute, drei = words[0], words[1], words[-1]
    assert (also["t0"], also["t1"]) == (0.0, 0.3)
    # segment 2 starts where render starts it: on the output frame grid
    o = rp.build(edl, (1080, 1920), (1080, 1920)).ranges[0].frames / 30
    assert heute["t0"] == round(o, 3) and heute["t1"] == round(o + 0.3, 3)
    assert drei["t0"] == round(o + 1.4, 3)


def test_words_clipped_at_segment_edges_and_fragments_dropped():
    edl = _edl([{"in": 0.0, "out": 0.5}, {"in": 1.55, "out": 3.0}])
    words = cap.map_words(WORDS, edl.segments, edl.fps)
    names = [w["w"] for w in words]
    # "Also" (0.30-0.60) keeps 0.2 of 0.3 s -> clipped at 0.5; "zeige" keeps 0.05 s -> dropped
    assert names == ["Also", "ich", "drei"]
    assert words[0]["t1"] == 0.5


def test_grouping_breaks_at_pauses_punctuation_and_length():
    def w(text, t0, t1):
        return {"w": text, "t0": t0, "t1": t1, "emph": False}

    words = [
        w("one", 0.0, 0.2), w("two", 0.2, 0.4), w("three", 0.4, 0.6), w("four", 0.6, 0.8),
        w("pause", 1.2, 1.5),  # >= 0.25 s gap before it
        w("end.", 1.5, 1.7), w("next", 1.7, 1.8),  # punctuation breaks after "end."
        w("extraordinarily", 1.8, 2.0), w("long", 2.0, 2.2),  # > 18 chars together
    ]  # fmt: skip
    groups = cap.group(words, duration=3.0, pace="fast")
    texts = [" ".join(x["w"] for x in g["words"]) for g in groups]
    assert texts == ["one two three", "four", "pause end.", "next", "extraordinarily", "long"]
    # "four" is 0.2 s long: stretched to the minimum, not held across the real pause
    assert groups[1]["t1"] == 1.0
    # "next" (0.1 s) is held to the next group; min duration elsewhere
    assert groups[3]["t0"] == 1.7 and groups[3]["t1"] == 1.8
    assert groups[-1]["t1"] - groups[-1]["t0"] >= cap.MIN_GROUP_S - 1e-9
    for g in groups:
        assert len(g["words"]) <= cap.MAX_WORDS
        assert g["t0"] < g["t1"]


def test_min_group_duration_never_overlaps_the_next_group():
    words = [
        {"w": "a", "t0": 0.0, "t1": 0.1, "emph": False},
        {"w": "b", "t0": 0.4, "t1": 0.5, "emph": False},
    ]
    g = cap.group(words, duration=0.6, pace="fast")
    assert g[0]["t1"] == 0.4 and g[1]["t1"] == 0.6  # capped by next group / duration


def test_emphasis_from_zoom_keys_and_emphasis_file():
    edl = _edl([{"in": 0.0, "out": 3.0, "zoom_keys": [[1.40, 1.25]]}])
    words = cap.map_words(WORDS, edl.segments, edl.fps)
    cap.mark_emph(words, cap.emphasis_times(edl, [2.2]))  # 2.2 is in a pause before "drei"
    emph = [w["w"] for w in words if w["emph"]]
    assert emph == ["zeige", "drei"]


def _job(tmp_path: Path, edl: dict, words=WORDS) -> Path:
    job = tmp_path / "job"
    (job / "work").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"id": "c1", "source": "input/a.mp4"}))
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "words.json").write_text(json.dumps({"words": words}))
    return job


def test_tool_writes_captions_keeps_other_fields_and_respects_force(capsys, tmp_path):
    edl = {
        "fps": 30, "size": [1080, 1920], "source": "input/a.mp4", "cover": 1.0,
        "segments": [{"in": 0.3, "out": 0.65}, {"in": 1.0, "out": 2.8}],
        "overlays": [{"scene": "keyword", "t0": 0.5, "t1": 1.5, "props": {}}],
    }  # fmt: skip
    job = _job(tmp_path, edl)
    (job / "work" / "emphasis.json").write_text(json.dumps({"times": [2.5]}))
    code, s, _ = run_tool(capsys, "captions", "--job", str(job))
    assert code == 0 and s["written"] and s["emph"] == 1, s
    data = json.loads((job / "work" / "edl.json").read_text())
    assert data["cover"] == 1.0 and data["overlays"] == edl["overlays"]
    words = [w["w"] for g in data["captions"] for w in g["words"]]
    assert "äh" not in words and words[0] == "Also"
    assert all(w["emph"] == (w["w"] == "drei") for g in data["captions"] for w in g["words"])

    code, s, _ = run_tool(capsys, "captions", "--job", str(job))
    assert code == 0 and s["written"] is False
    data["captions"] = [{"t0": 0.0, "t1": 0.2, "words": []}]
    (job / "work" / "edl.json").write_text(json.dumps(data))
    code, s, _ = run_tool(capsys, "captions", "--force", "--job", str(job))
    assert code == 0 and s["written"] and s["groups"] > 1


def test_tool_without_words_fails(capsys, tmp_path):
    edl = {"fps": 30, "size": [1080, 1920], "source": "a.mp4", "segments": [{"in": 0, "out": 1}]}
    job = _job(tmp_path, edl)
    (job / "work" / "words.json").unlink()
    code, s, _ = run_tool(capsys, "captions", "--job", str(job))
    assert code == 1 and "words.json" in s["error"]


def test_zoom_key_on_a_word_boundary_marks_the_next_word():
    edl = _edl([{"in": 0.0, "out": 3.0, "zoom_keys": [[1.0, 1.25]]}])
    words = cap.map_words([_w("a", 0.5, 1.0), _w("b", 1.0, 1.4)], edl.segments, edl.fps)
    cap.mark_emph(words, cap.emphasis_times(edl, []))
    assert [w["emph"] for w in words] == [False, True]


def _speech(text: str, t0: float = 0.0, word_s: float = 0.3, gap: float = 0.05, pauses=None):
    """Words of `text` at a steady pace; `pauses` maps a word index to the pause before it."""
    out, t = [], t0
    for i, w in enumerate(text.split()):
        t += (pauses or {}).get(i, 0.0)
        out.append({"w": w, "t0": round(t, 3), "t1": round(t + word_s, 3), "emph": False})
        t += word_s + gap
    return out


SPEECH = (
    "Heute zeige ich dir drei Dinge, die mir beim Laufen am Meer wirklich geholfen haben. "
    "Erstens: früh los. Zweitens, langsam anfangen und dann steigern. Ja. Drittens, Wasser."
)


def test_calm_blocks_are_phrases_of_two_lines_at_most():
    words = _speech(SPEECH, pauses={14: 0.3})
    groups = cap.group(words, duration=words[-1]["t1"] + 1.0, pace="calm")
    p = cap.PACES["calm"]
    for g in groups:
        text = " ".join(w["w"] for w in g["words"])
        assert len(g["words"]) <= p.max_words and len(text) <= p.max_chars, text
    texts = [" ".join(w["w"] for w in g["words"]) for g in groups]
    # a full block breaks at its last comma; a sentence end ends a block that is long enough
    assert "Heute zeige ich dir drei Dinge," in texts, texts
    assert texts[-1].endswith("Wasser.") and any(t.endswith("steigern.") for t in texts), texts
    # far fewer, longer blocks than the fast pace
    fast = cap.group(words, duration=words[-1]["t1"] + 1.0, pace="fast")
    assert len(groups) * 2 <= len(fast)


def test_calm_minimum_duration_merges_short_neighbours():
    words = _speech(SPEECH)
    groups = cap.group(words, duration=words[-1]["t1"] + 1.0, pace="calm")
    for g in groups:
        assert g["t1"] - g["t0"] >= cap.PACES["calm"].min_s - 1e-9, g
    # "Ja." alone would be 0.25 s: it joined a neighbour
    assert not any([w["w"] for w in g["words"]] == ["Ja."] for g in groups)


def test_calm_no_flicker_in_short_gaps_hidden_in_long_pauses():
    words = _speech("eins zwei drei. vier fünf sechs. sieben acht neun.", pauses={3: 0.4, 6: 1.5})
    groups = cap.group(words, duration=words[-1]["t1"] + 1.0, pace="calm")
    assert len(groups) == 3
    assert groups[0]["t1"] == groups[1]["t0"]  # 0.45 s gap < 0.6 s: held to the next block
    assert groups[2]["t0"] - groups[1]["t1"] > 1.0  # 1.55 s pause: hidden
    assert groups[1]["t1"] - groups[1]["t0"] >= 1.0


def test_pace_comes_from_the_brand_and_defaults_to_calm(capsys, tmp_path, videocut_home):
    brand_dir = videocut_home / "brands" / "brand-x"
    brand_dir.mkdir(parents=True)
    tokens = json.loads(
        (Path(__file__).parents[1] / "brands" / "_example" / "brand.json").read_text()
    )
    tokens["captions"] = {**tokens["captions"], "pace": "fast"}
    (brand_dir / "brand.json").write_text(json.dumps({**tokens, "name": "brand-x"}))
    job = tmp_path / "j"
    (job / "work").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"id": "j"}))
    assert cap.job_pace(job) == "calm"
    (job / "job.json").write_text(json.dumps({"id": "j", "brand": "brand-x"}))
    assert cap.job_pace(job) == "fast"
