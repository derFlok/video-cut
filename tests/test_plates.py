from videocut.edl import from_dict
from videocut.tools import _plates

CTA = {"scene": "cta_end", "t0": 4.0, "t1": 6.0, "props": {"text": "x"}}
KW = {"scene": "keyword", "t0": 1.0, "t1": 2.0, "props": {"text": "y"}}


def test_end_card_before_the_last_word_is_shifted_and_gets_a_held_tail():
    s = _plates.schedule([KW, CTA], last_word=5.5, content_s=6.0, fps=30)
    assert s.overlays[0] == KW  # other scenes stay where they are
    assert s.overlays[1]["t0"] == 5.7 and s.overlays[1]["t1"] == 7.7  # length kept
    assert s.shifted == [{"scene": "cta_end", "from": 4.0, "to": 5.7}]
    assert s.tail_frames == 51  # 1.7 s at 30 fps


def test_end_card_after_the_speech_is_left_alone():
    s = _plates.schedule([CTA], last_word=3.7, content_s=6.0, fps=30)
    assert s.overlays == [CTA] and s.shifted == [] and s.tail_frames == 0
    assert _plates.schedule([CTA], last_word=None, content_s=6.0, fps=30).tail_frames == 0


def test_tail_is_whole_frames_at_ntsc_rates():
    s = _plates.schedule([CTA], last_word=5.9, content_s=6.0, fps=29.97)
    assert s.overlays[0]["t1"] == 8.1
    assert s.tail_frames == 63  # ceil(2.1 s * 30000/1001)


def test_last_word_from_words_json_else_from_the_captions(tmp_path):
    edl = from_dict(
        {
            "fps": 30, "size": [1080, 1920], "source": "input/a.mp4",
            "segments": [{"in": 0.0, "out": 2.0}, {"in": 5.0, "out": 6.0}],
            "captions": [{"t0": 0.0, "t1": 1.0, "words": [{"w": "a", "t0": 0.1, "t1": 0.9}]}],
        }
    )  # fmt: skip
    (tmp_path / "work").mkdir()
    assert _plates.last_word_end(tmp_path, edl) == 0.9  # no words.json yet
    words = {"words": [{"w": "x", "t0": 1.0, "t1": 1.5}, {"w": "y", "t0": 5.2, "t1": 5.6},
                       {"w": "cut", "t0": 3.0, "t1": 3.5}]}  # fmt: skip
    (tmp_path / "work" / "words.json").write_text(__import__("json").dumps(words))
    assert _plates.last_word_end(tmp_path, edl) == 2.6  # 5.6 s source -> 2.6 s output
