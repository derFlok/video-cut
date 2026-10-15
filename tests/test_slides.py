"""Slides without media: the slides.json schema and text limits, the EDL slide fields, the
slide geometry of every layout in every format, the slide clock across cuts, reveal times,
the render graph and the verify flags."""

import copy
from fractions import Fraction

import pytest

from videocut import edl as edl_mod
from videocut.tools import _layouts, _verify_slides
from videocut.tools import _render_plan as plan_mod
from videocut.tools import slides as slides_mod
from videocut.tools._format import FORMATS, safe_zones
from videocut.tools._geometry import overlap

DOC = {
    "slides": [
        {"id": "s1", "template": "title", "content": {"title": "Drei Dinge"}, "source": "auto",
         "why": "Einstieg"},
        {"id": "s2", "template": "bullets",
         "content": {"title": "Was hilft", "items": ["Ein Eingang", "Kurze Wege", "Messen"]}},
        {"id": "s3", "template": "big_number",
         "content": {"value": "40 %", "label": "weniger Rückfragen", "count": True}},
        {"id": "s4", "template": "compare",
         "content": {"left": {"title": "Vorher", "items": ["Zettel"]},
                     "right": {"title": "Nachher", "items": ["Ein Ort"]}}},
        {"id": "s5", "template": "steps", "content": {"steps": ["Anfrage", "Termin"]}},
        {"id": "s6", "template": "timeline",
         "content": {"points": [{"label": "Januar", "text": "Start"},
                                {"label": "Juni", "text": "Zweiter Ort"}]}},
        {"id": "s7", "template": "bar_chart", "content": {"unit": "%", "bars": [
            {"label": "Vorher", "value": 12}, {"label": "Nachher", "value": 31.5}]}},
        {"id": "s8", "template": "quote", "content": {"text": "Weniger tippen.", "author": "Team"}},
        {"id": "s9", "template": "cta", "content": {"title": "Jetzt testen",
         "url": "zentrovia.example"}},
        {"id": "s10", "template": "image", "content": {"asset": "input/slides/page_01.png"},
         "source": "upload"},
    ]
}  # fmt: skip


def _doc(**change) -> dict:
    d = copy.deepcopy(DOC)
    for i, s in change.items():
        d["slides"][int(i[1:])] = s
    return d


def test_valid_doc_has_no_problems():
    assert slides_mod.validate_doc(DOC) == ([], [])
    assert slides_mod.TEMPLATES == tuple(s["template"] for s in DOC["slides"])


@pytest.mark.parametrize(
    ("slide", "want"),
    [
        ({"id": "s1", "template": "chart", "content": {}}, "template: must be one of"),
        ({"id": "s1", "template": "title", "content": {"title": "x" * 61}},
         "61 characters, at most 60"),
        ({"id": "s1", "template": "title", "content": {}}, "content.title: must be text"),
        ({"id": "s1", "template": "bullets", "content": {"items": ["a"]}},
         "items: must be a list of 2-5"),
        ({"id": "s1", "template": "bullets", "content": {"items": ["a", "x" * 71]}},
         "items[1]: 71 characters, at most 70"),
        ({"id": "s1", "template": "big_number", "content": {"value": "1234567890123",
         "label": "x"}},
         "value: 13 characters"),
        ({"id": "s1", "template": "big_number", "content": {"value": "3x", "label": "x",
         "count": 1}},
         "count: must be true or false"),
        ({"id": "s1", "template": "compare", "content": {"left": {"title": "a", "items": []},
                                                         "right": {"title": "b", "items": ["c"]}}},
         "left.items: must be a list of 1-4"),
        ({"id": "s1", "template": "timeline", "content": {"points": [
            {"label": "x" * 17, "text": "a"}, {"label": "b", "text": "c"}]}},
         "points[0].label: 17 characters, at most 16"),
        ({"id": "s1", "template": "bar_chart", "content": {"bars": [
            {"label": "a", "value": -1}, {"label": "b", "value": 2}]}},
             "bars[0].value: must be a number >= 0"),
        ({"id": "s1", "template": "quote", "content": {"text": "x" * 161}}, "161 characters"),
        ({"id": "s1", "template": "cta", "content": {"title": "a", "url": "x" * 61}}, "url: 61"),
        ({"id": "s1", "template": "image", "content": {"asset": "../secret.png"}},
         "asset: must be a path input/slides/"),
        ({"id": "s1", "template": "image", "content": {"asset": "input/slides/page.gif"}},
         "asset: must be a path input/slides/"),
        ({"id": "s1", "template": "title", "content": {"title": "a", "colour": "x"}},
         "unknown field(s) colour"),
        ({"id": "s 1", "template": "title", "content": {"title": "a"}}, ".id: letters, digits"),
        ({"id": "s1", "template": "title", "content": {"title": "a"}, "source": "web"},
         "source: must be auto or upload"),
    ],
)  # fmt: skip
def test_schema_and_limit_errors_name_the_path(slide, want):
    errs, _ = slides_mod.validate_doc(_doc(s0=slide))
    assert any(want in e and e.startswith("$.slides[0]") for e in errs), errs


def test_duplicate_ids_and_unknown_slide_fields():
    d = _doc(s1={**DOC["slides"][1], "id": "s1"})
    assert any("s1 is used twice" in e for e in slides_mod.validate_doc(d)[0])
    d = _doc(s0={**DOC["slides"][0], "still": "x.jpg"})
    errs, warns = slides_mod.validate_doc(d)
    assert not errs and any("still" in w for w in warns)
    assert slides_mod.validate_doc({"slides": {}})[0] == ['$: must be {"slides": [...]}']


def test_reading_time_and_steps():
    s = DOC["slides"][1]  # "Was hilft" + 3 items: 2 + 2 + 2 + 1 = 7 words
    assert slides_mod.words(s) == 7
    assert slides_mod.reading_s(s) == 0.25 * 7 + 1.0
    assert [slides_mod.step_count(s) for s in DOC["slides"]] == [0, 3, 0, 2, 2, 2, 2, 0, 0, 0]
    assert slides_mod.words(DOC["slides"][9]) == 0  # an uploaded page: its own text is not read


# --- EDL ---------------------------------------------------------------------------------


def _edl(segments, size=(1080, 1920)) -> dict:
    return {"fps": 30, "size": list(size), "source": "input/take.mp4", "segments": segments}


@pytest.mark.parametrize(
    ("seg", "want"),
    [
        ({"in": 0, "out": 2, "layout": "slide"}, "slide: layout slide needs the id"),
        ({"in": 0, "out": 2, "layout": "slide_full", "slide": "a b"},
         "slide: layout slide_full needs"),
        ({"in": 0, "out": 2, "slide": "s1"}, "slide: needs layout slide"),
        ({"in": 0, "out": 2, "reveal": [0.5]}, "reveal: needs a slide layout"),
        ({"in": 0, "out": 2, "layout": "slide", "slide": "s1", "reveal": "0.5"},
         "reveal: must be a list"),
        ({"in": 0, "out": 2, "layout": "slide", "slide": "s1", "reveal": [1, 0.5]},
         "reveal[1]: times must increase"),
        ({"in": 0, "out": 2, "layout": "slide", "slide": "s1", "reveal": [-0.1]},
         "reveal[0]: must be >= 0"),
        ({"in": 1, "out": 2, "layout": "slide", "slide": "s1", "reveal": [1.5]},
         "past the segment's out"),
        ({"in": 0, "out": 2, "layout": "slide", "slide": "s1",
          "screen_zoom": [{"t": 1, "box": [0, 0, 0.5, 0.5]}]}, "screen_zoom: needs layout screen"),
        ({"in": 0, "out": 2, "layout": "slide_split", "slide": "s1",
          "redact": [{"t0": 0, "t1": 1, "box": [0, 0, 0.5, 0.5]}]}, "redact: needs layout screen"),
    ],
)  # fmt: skip
def test_edl_slide_field_errors(seg, want):
    errs = edl_mod.validate(_edl([seg]))
    assert any(want in e and e.startswith("$.segments[0]") for e in errs), errs


def test_edl_slides_need_no_screen_and_keep_one_layout():
    segs = [
        {"in": 0, "out": 2, "layout": "slide", "slide": "s1", "reveal": [0.2, 1.1]},
        {"in": 2.5, "out": 4, "layout": "slide_full", "slide": "s2"},
        {"in": 4, "out": 6, "layout": "slide_split", "slide": "s3"},
    ]
    # no sync, no screen recording: slide layouts work in any job
    assert edl_mod.validate(_edl(segs), check_sync=True) == []
    e = edl_mod.from_dict(_edl(segs))
    assert [s.slide for s in e.segments] == ["s1", "s2", "s3"]
    assert e.to_dict()["segments"][0]["reveal"] == [0.2, 1.1]
    bad = [*segs, {"in": 7, "out": 8, "layout": "slide_full", "slide": "s1"}]
    errs = edl_mod.validate(_edl(bad))
    assert any(e.startswith("$.segments[3].layout: slide s1 is shown as slide") for e in errs)


def test_reveal_counts_from_in_src():
    seg = {
        "in": 1.2,
        "out": 4,
        "in_src": 1.0,
        "layout": "slide",
        "slide": "s2",
        "reveal": [0.5, 2.9],
    }
    assert edl_mod.validate(_edl([seg])) == []
    s = edl_mod.from_dict(_edl([seg])).segments[0]
    assert s.reveal_src() == [1.5, 3.9]
    over = {**seg, "reveal": [3.1]}  # 1.0 + 3.1 > out 4
    assert any("past the segment's out" in e for e in edl_mod.validate(_edl([over])))


def test_edl_problems_against_slides_json():
    raw = _edl([
        {"in": 0, "out": 2, "layout": "slide", "slide": "nope"},
        {"in": 2, "out": 3, "layout": "slide", "slide": "s1", "reveal": [0.1]},
        {"in": 3, "out": 4, "layout": "slide", "slide": "s5", "reveal": [0.1, 0.2, 0.3]},
        {"in": 4, "out": 5, "layout": "slide", "slide": "s2", "reveal": [0.1, 0.2]},
        {"in": 5, "out": 6, "layout": "slide", "slide": "s2", "reveal": [0.1, 0.2]},
        {"in": 6, "out": 7, "layout": "slide", "slide": "s2", "reveal": [0.1]},
    ])  # fmt: skip
    errs = slides_mod.edl_problems(DOC, raw)
    assert errs == [
        "$.segments[0].slide: 'nope' is not in work/slides.json",
        "$.segments[1].reveal: template title has no build steps",
        "$.segments[2].reveal: 3 times for 2 build steps of slide s5",
        "$.segments[4].reveal: 4 times for 3 build steps of slide s2 (2 in earlier segments)",
    ]  # one error per slide: segment 5 is over as well


# --- geometry -----------------------------------------------------------------------------


def _inside(b, frame) -> bool:
    return b[0] >= 0 and b[1] >= 0 and b[2] <= frame[0] and b[3] <= frame[1]


@pytest.mark.parametrize("fmt", list(FORMATS))
@pytest.mark.parametrize("layout", edl_mod.SLIDE_LAYOUTS)
def test_slide_geometry_every_format(fmt, layout):
    out = FORMATS[fmt]
    z = safe_zones(out)
    g = _layouts.slide_geometry(layout, out)
    assert _inside(g.rect.box(), out)
    safe = [z["side"], z["top"], out[0] - z["side"], out[1] - z["bottom"]]
    for a in g.areas or [g.area]:
        assert a[0] >= safe[0] and a[1] >= safe[1] and a[2] <= safe[2] and a[3] <= safe[3], a
        assert a[2] - a[0] > 0.4 * min(out) and a[3] - a[1] > 0.3 * min(out), a  # real room
        if g.speaker is not None:  # the content box keeps off the speaker
            assert overlap(a, g.speaker.box()) == 0
    if layout == "slide_full":
        assert g.speaker is None and g.rect.box() == [0, 0, *out]
    elif layout == "slide":
        b = g.speaker
        assert g.round and b.w == b.h and len(g.areas) == 2
        assert b.y + b.h <= out[1] - z["bottom"] and b.x + b.w <= out[0] - z["side"]
    else:  # split: slide and speaker side by side / stacked, filling the frame
        r, p = g.rect, g.speaker
        assert r.w * r.h + p.w * p.h == out[0] * out[1]
        assert overlap(r.box(), p.box()) == 0
        if out[0] / out[1] > 1.2:
            assert r.h == out[1] and p.x == r.w
        else:
            assert r.w == out[0] and p.y == r.h


def test_brand_pip_token_moves_and_sizes_the_bubble():
    out = FORMATS["9:16"]
    default = _layouts.slide_geometry("slide", out)
    left = _layouts.slide_geometry("slide", out, {"pip": {"corner": "left", "size": 0.25}})
    assert left.speaker.x == safe_zones(out)["side"] and left.speaker.w == 270
    assert default.speaker.w == _layouts.inset_side(out)
    huge = _layouts.slide_geometry("slide", out, {"pip": {"size": 3}})
    assert huge.speaker.w == round(out[0] * 0.45 / 2) * 2


# --- the clock and the graph ----------------------------------------------------------------


def _plan(segments, size=(1080, 1920)):
    e = edl_mod.from_dict(_edl(segments, size))
    return e, plan_mod.build(e, (1920, 1080), size, [s.layout for s in e.segments])


def test_a_slide_spanning_a_cut_keeps_its_clock():
    segs = [
        {"in": 0, "out": 1},
        {"in": 2, "out": 3.5, "layout": "slide", "slide": "s2", "reveal": [0.2]},
        {"in": 4, "out": 5, "in_src": 3.9, "layout": "slide", "slide": "s2", "reveal": [0.6]},
        {"in": 6, "out": 7, "layout": "slide_full", "slide": "s1"},
        {"in": 8, "out": 9, "layout": "slide", "slide": "s2"},
    ]
    e, plan = _plan(segs)
    runs = _layouts.slide_runs(e, plan)
    assert [r["clip_frame"] for r in runs["s2"]] == [0, 45, 75]  # never restarts at a cut
    assert [r["out_frame"] for r in runs["s2"]] == [30, 75, 135]
    pl = slides_mod.placements(e)
    assert pl["s2"]["frames"] == 105 and pl["s2"]["seconds"] == 3.5
    # reveal: 0.2 s after in (first segment); 0.6 s after in_src 3.9 = source 4.5, 0.5 s
    # into the second segment, whose clip time starts at 1.5 s
    assert pl["s2"]["reveal"] == [0.2, 2.0]
    assert pl["s1"]["layout"] == "slide_full" and pl["s1"]["reveal"] == []
    drawn = _layouts.plan_slides(e, plan, (1920, 1080), None, (1080, 1920))
    segs_ = drawn["segs"]
    lines = _layouts.compose_slide(
        2, segs_[2], 4.0, Fraction(30), (1080, 1920), slide_label="3:v:0", camera_label="c2",
        bg="0xF4F4F4", matrix="bt709", cam_sdr="",
    )  # fmt: skip
    assert lines[0].startswith("[3:v:0]trim=start_frame=45:end_frame=75,")
    assert "alphamerge" in "".join(lines) and lines[-1].endswith("[vc2];")
    full = _layouts.compose_slide(
        3, segs_[3], 6.0, Fraction(30), (1080, 1920), slide_label="4:v:0", camera_label=None,
        bg="0xF4F4F4", matrix="bt709", cam_sdr="",
    )  # fmt: skip
    assert len(full) == 2 and "[c3]" not in "".join(full)  # slide_full: no camera


def test_video_chain_feeds_the_camera_only_where_it_is_seen():
    segs = [
        {"in": 0, "out": 1},
        {"in": 1, "out": 2, "layout": "slide_full", "slide": "s1"},
        {"in": 2, "out": 3, "layout": "slide_split", "slide": "s2"},
    ]
    e, plan = _plan(segs)
    sl = _layouts.plan_slides(e, plan, (1920, 1080), None, (1080, 1920))["segs"]
    lines = _layouts.video_chain(
        plan, {}, lambda r: ["null"], "0:0", None, bg="0x000000", matrix="bt709", cam_sdr="",
        screen_sdr="", slides=sl, slide_in={1: "2:v:0", 2: "3:v:0"},
    )  # fmt: skip
    assert lines[0] == "[0:0]setpts=PTS-STARTPTS,split=2[c0][c2];"
    graph = "".join(lines)
    assert "[2:v:0]trim=start_frame=0:end_frame=30" in graph
    assert "[3:v:0]trim=start_frame=0:end_frame=30" in graph
    assert lines[-1].startswith("[vc0][vc1][vc2]concat=n=3")


def test_slides_need_no_redaction_blocks():
    segs = [{"in": 0, "out": 2, "layout": "slide_full", "slide": "s1"}]
    e = edl_mod.from_dict(_edl(segs))
    cand = [{"id": 1, "kind": "email", "t0": 0, "t1": 2, "box": [0.1, 0.1, 0.3, 0.2]}]
    assert _layouts.with_redaction(e, cand).segments[0].redact == []


# --- verify -------------------------------------------------------------------------------


def _geo(layout="slide", fmt="9:16"):
    return slides_mod.geometry_doc(layout, FORMATS[fmt], {})


def test_verify_flags_text_over_the_bubble_and_overflow():
    geo = _geo()
    area = geo["area"]
    ok = {
        "fits": True,
        "texts": [{"text": "a", "box": [area[0], area[1], area[0] + 200, area[1] + 60]}],
    }
    assert _verify_slides.text_flags(ok, geo) == []
    sp = geo["speaker"]
    cx, cy = (sp[0] + sp[2]) / 2, (sp[1] + sp[3]) / 2
    on = {"fits": True, "texts": [{"text": "a", "box": [cx - 10, cy - 10, cx + 10, cy + 10]}]}
    assert _verify_slides.text_flags(on, geo) == ["slide_text_overflow", "slide_bubble_over_text"]
    # inside the bubble's square but outside the circle: not covered
    corner = {"fits": True, "area": [0, 0, 1080, 1920],
              "texts": [{"text": "a", "box": [sp[0], sp[1], sp[0] + 20, sp[1] + 20]}]}  # fmt: skip
    assert _verify_slides.text_flags(corner, geo) == []
    assert _verify_slides.text_flags({"fits": False, "texts": []}, geo) == ["slide_text_overflow"]
    split = _geo("slide_split")
    low = {"fits": True, "area": [0, 0, 1080, 1920],
           "texts": [{"text": "a", "box": [100, 1200, 300, 1260]}]}  # fmt: skip
    assert _verify_slides.text_flags(low, split) == ["slide_bubble_over_text"]


def test_huge_numbers_are_errors_not_crashes():
    bars = {"bars": [{"label": "a", "value": 10**400}, {"label": "b", "value": 1}]}
    d = _doc(s0={"id": "s1", "template": "bar_chart", "content": bars})
    assert any("bars[0].value: must be a number" in e for e in slides_mod.validate_doc(d)[0])


def test_spec_hash_follows_the_bytes_of_an_uploaded_page(tmp_path):
    page = tmp_path / "input" / "slides" / "page_01.png"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"one")
    sp = slides_mod.spec(DOC["slides"][9], "slide_full", FORMATS["9:16"], 30, 3.0, [], {}, "de")
    first = slides_mod.spec_sha(sp, tmp_path)
    assert slides_mod.spec_sha(sp, tmp_path) == first
    page.write_bytes(b"two")  # same path, corrected page
    assert slides_mod.spec_sha(sp, tmp_path) != first
