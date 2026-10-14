import hashlib
import re
from fractions import Fraction

import pytest

from videocut.edl import from_dict
from videocut.tools import _layouts as lay
from videocut.tools import _render_plan as rp
from videocut.tools._geometry import overlap

OUT = (1080, 1920)
FPS = Fraction(30)


def _seg(start=10.0, end=14.0, **fields):
    return from_dict(
        {"fps": 30, "size": list(OUT), "source": "input/a.mp4",
         "segments": [{"in": start, "out": end, **fields}]}
    ).segments[0]  # fmt: skip


def _plan(spec, screen=(2880, 1800), layout="screen", start=10.0, end=14.0, out=OUT):
    seg = _seg(start, end, layout=layout, **spec)
    frames = round((end - start) * 30)
    return lay.plan_segment(
        seg, frames, FPS,
        offset=3.27, screen=screen, camera=(1920, 1080), out=out, full_out=OUT, face=None,
    )  # fmt: skip


VIDEO_SHA = "8002561d31a3011a5e163717717ce3abe896047df461feb05d67c44e086d3e06"


def test_face_only_graph_is_unchanged():
    # sha256 of the video lines origin/main produced for this EDL before layouts existed
    # (the audio lines changed with the crossfades at every cut).
    segs = [
        {"in": 0.3, "out": 1.0},
        {"in": 1.9, "out": 2.6, "crop": {"cx": 0.4, "cy": 0.5, "zoom": 1.0},
         "zoom_keys": [[2.2, 1.3]]},
    ]  # fmt: skip
    edl = from_dict({"fps": 30, "size": list(OUT), "source": "input/a.mp4", "segments": segs})
    for layouts in (None, ["face", "face"]):
        plan = rp.with_tail(rp.build(edl, (1920, 1080), OUT, layouts), 5)
        g = rp.graph(plan, "silent", "0:0", "1:a", "zscale=x")
        video = g[: g.index("[1:a]")]
        assert hashlib.sha256(video.encode()).hexdigest() == VIDEO_SHA


def test_non_face_segment_is_one_range_without_camera_zoom():
    edl = from_dict(
        {"fps": 30, "size": list(OUT), "source": "input/a.mp4",
         "segments": [{"in": 0, "out": 1}, {"in": 2, "out": 3, "zoom_keys": [[2.5, 1.5]]}]}
    )  # fmt: skip
    plan = rp.build(edl, (1920, 1080), OUT, ["face", "screen"])
    assert [(r.layout, r.seg, r.frames, r.zoom) for r in plan.ranges] == [
        ("face", 0, 30, 1.0),
        ("screen", 1, 30, 1.0),
    ]


@pytest.mark.parametrize("layout", ["screen", "pip", "split"])
def test_view_fills_width_inside_safe_zones(layout):
    v = lay.view_rect(layout, OUT, (2880, 1800))
    assert (v.w, v.h) == (1080, 676) and v.x == 0
    bottom = {"split": round(1920 * lay.SPLIT_TOP), "pip": 1570 - 368 - 32}[layout] if (
        layout != "screen") else 1570  # fmt: skip
    assert v.y >= 250 and v.y + v.h <= bottom


def test_zoom_eases_monotonically_between_keys():
    keys = [
        {"t": 11.0, "box": [0.5, 0.5, 0.9, 0.9]},
        {"t": 12.0, "box": [0.0, 0.0, 0.45, 0.45]},
        {"t": 13.5, "box": [0.0, 0.0, 1.0, 1.0]},  # full frame = no zoom
    ]
    seg, clamped = _plan({"screen_zoom": keys})
    assert not clamped
    wins = [lay.window_at(seg, n) for n in range(seg.frames + 1)]
    assert wins[0] == (0.0, 0.0, 2880.0)  # before the first key: the whole screen
    assert wins[-1][2] == pytest.approx(2880)
    bounds = [0] + [m.end for m in seg.moves]
    for a, b in zip(bounds, bounds[1:], strict=False):
        for c in range(3):
            vals = [w[c] for w in wins[a : b + 1]]
            assert vals == sorted(vals) or vals == sorted(vals, reverse=True)
    for m in seg.moves:
        assert 0 < m.end - m.start <= round(lay.EASE_S * 30)
        # Eased: the first and last steps are much smaller than the middle one (no punch).
        steps = [abs(wins[n + 1][2] - wins[n][2]) for n in range(m.start, m.end)]
        assert steps[0] < max(steps) / 3 and steps[-1] < max(steps) / 3
    for x, y, w in wins:  # always inside the frame, view aspect
        assert x >= -1e-6 and y >= -1e-6 and x + w <= 2880 + 1e-6
        assert y + w * 1800 / 2880 <= 1800 + 1e-6


def _eval(expr: str, n: int) -> float:
    py = re.sub(r"\bif\(", "_if(", expr).replace("lt(", "_lt(")
    return eval(py, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b, "n": n})


def test_ffmpeg_expression_matches_window_at():
    keys = [{"t": 10.0, "box": [0.1, 0.1, 0.5, 0.5]}, {"t": 10.2, "box": [0.5, 0.5, 0.9, 0.9]},
            {"t": 12.0, "box": [0.2, 0.0, 0.6, 0.4]}]  # fmt: skip
    seg, _ = _plan({"screen_zoom": keys})
    for i in range(3):
        e = lay.window_expr(seg, i, "n")
        for n in range(seg.frames):
            assert _eval(e, n) == pytest.approx(lay.window_at(seg, n)[i], abs=1e-3)


def test_sharpness_clamp_on_1920_source_with_3x_key():
    seg, clamped = _plan(
        {"screen_zoom": [{"t": 11.0, "box": [0.4, 0.4, 0.4 + 1 / 3, 0.4 + 1 / 3]}]},
        screen=(1920, 1200),
    )
    assert clamped == [{"t": 11.0, "zoom": 3.0, "max_zoom": round(1920 / 1080, 2)}]
    assert min(lay.window_at(seg, n)[2] for n in range(seg.frames)) == pytest.approx(1080)
    # The preview judges sharpness at the EDL size too.
    _, clamped = _plan(
        {"screen_zoom": [{"t": 11.0, "box": [0.4, 0.4, 0.4 + 1 / 3, 0.4 + 1 / 3]}]},
        screen=(1920, 1200), out=(540, 960),
    )  # fmt: skip
    assert clamped and clamped[0]["max_zoom"] == round(1920 / 1080, 2)
    _, clamped = _plan({"screen_zoom": [{"t": 11.0, "box": [0.25, 0.25, 0.75, 0.75]}]})
    assert not clamped  # 2x on a 2880 source keeps 1.33 source px per output px


def test_redact_frames_cover_the_range_and_snap_outward():
    seg, _ = _plan({"redact": [{"t0": 10.51, "t1": 11.0, "box": [0.1001, 0.2, 0.3, 0.4003]}]})
    (n0, n1, x, y, w, h) = seg.redact[0]
    assert (n0, n1) == (14, 30)  # frames 15..29 overlap [t0, t1], plus one frame of slack
    assert x <= 0.1001 * 2880 and x + w >= 0.3 * 2880 and y + h >= 0.4003 * 1800
    assert x % 2 == y % 2 == w % 2 == h % 2 == 0


def test_pip_inset_avoids_the_view_and_the_zoom_target():
    seg, _ = _plan({"screen_zoom": [{"t": 10.0, "box": [0.6, 0.6, 1.0, 1.0]}]}, layout="pip")
    r = seg.inset
    assert overlap(r.box(), seg.view.box()) == 0
    assert r.x < 540  # the target is on the right
    seg, _ = _plan({"screen_zoom": [{"t": 11.0, "box": [0.0, 0.5, 0.5, 1.0]}]}, layout="pip")
    assert seg.inset.x > 540
    assert r.w == round(1080 * lay.PIP_SIZE / 2) * 2
    assert r.x >= 64 and r.x + r.w <= 1080 - 64 and r.y >= 250 and r.y + r.h <= 1920 - 350
    assert seg.camera.w == seg.camera.h


def test_split_speaker_below():
    seg, _ = _plan({}, layout="split")
    assert seg.face.y == round(1920 * lay.SPLIT_TOP) and seg.face.y + seg.face.h == 1920
    assert seg.view.y + seg.view.h <= seg.face.y
    assert seg.camera.h == 1080  # landscape camera: full height, face-centred


def test_face_track_skips_screen_ranges():
    from videocut.tools._geometry import face_track

    edl = from_dict(
        {"fps": 30, "size": list(OUT), "source": "input/a.mp4",
         "segments": [{"in": 0, "out": 1}, {"in": 1, "out": 2}]}
    )  # fmt: skip
    face = {"frames": [{"t": t / 10, "found": True, "box": [0.4, 0.3, 0.6, 0.6]}
                       for t in range(20)]}  # fmt: skip
    track = face_track(rp.build(edl, (1920, 1080), OUT, ["face", "screen"]), face, (1920, 1080))
    assert track and all(s["t"] < 1 for s in track)


def test_screen_only_chain_sinks_the_camera():
    edl = from_dict(
        {"fps": 30, "size": list(OUT), "source": "input/a.mp4", "segments": [{"in": 0, "out": 1}]}
    )
    plan = rp.build(edl, (1920, 1080), OUT, ["screen"])
    seg, _ = _plan({}, start=0.0, end=1.0)
    lines = lay.video_chain(
        plan, {0: seg}, lambda r: [], "0:0", "1:v:0",
        bg="0xF4F4F4", matrix="bt709", cam_sdr="", screen_sdr="",
    )  # fmt: skip
    assert lines[0] == "[0:0]nullsink;" and "split=" not in lines[0]


def test_segment_offset_applies_drift_and_widens_redaction():
    sync = {"offset_s": 3.0, "drift_ppm": 1000.0, "drift_ref": 10.0}
    off, slack = lay.segment_offset(sync, 70.0, 20.0, FPS)
    assert off == pytest.approx(3.06) and slack == 1 + 1  # 20 s at 1000 ppm = 0.6 frames
    assert lay.segment_offset({"offset_s": 1.5, "drift_ppm": None}, 5.0, 4.0, FPS) == (1.5, 1)
