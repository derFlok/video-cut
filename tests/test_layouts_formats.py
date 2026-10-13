"""Demo layouts per output format (16:9, 1:1, 4:5; 9:16 is covered by test_layouts.py),
the speaker crop from a small camera tile, and the UI text readability estimate."""

from fractions import Fraction

import pytest

from videocut.edl import from_dict
from videocut.tools import _layouts as lay
from videocut.tools._format import FORMATS, safe_zones
from videocut.tools._geometry import overlap

FPS = Fraction(30)
SCREEN = (2880, 1800)


def _seg(layout="screen", start=10.0, end=14.0, size=(1080, 1920), **fields):
    return from_dict(
        {"fps": 30, "size": list(size), "source": "input/a.mp4",
         "segments": [{"in": start, "out": end, "layout": layout, **fields}]}
    ).segments[0]  # fmt: skip


def _plan(fmt, layout="screen", spec=None, screen=SCREEN, face=None, camera=(1920, 1080)):
    out = FORMATS[fmt]
    return lay.plan_segment(
        _seg(layout, size=out, **(spec or {})), 120, FPS, offset=0.0, screen=screen,
        camera=camera, out=out, full_out=out, face=face,
    )  # fmt: skip


def _inside(r: lay.Rect, out) -> bool:
    return r.x >= 0 and r.y >= 0 and r.x + r.w <= out[0] and r.y + r.h <= out[1]


def test_shapes():
    assert [lay.shape(FORMATS[f]) for f in ("9:16", "16:9", "1:1", "4:5")] == [
        "portrait", "landscape", "square", "portrait",
    ]  # fmt: skip


@pytest.mark.parametrize("screen", [(2880, 1800), (1920, 1080), (4096, 2304), (1170, 2532)])
def test_landscape_screen_fills_the_frame(screen):
    v = lay.view_rect("screen", (1920, 1080), screen)
    assert v.w == 1920 or v.h == 1080  # one side fills, the other is letterboxed
    assert _inside(v, (1920, 1080)) and v.w % 2 == v.h % 2 == 0
    assert abs(v.w / v.h - screen[0] / screen[1]) < 0.01
    assert abs(v.x - (1920 - v.x - v.w)) <= 1 and abs(v.y - (1080 - v.y - v.h)) <= 1
    if screen == (1920, 1080):
        assert (v.x, v.y, v.w, v.h) == (0, 0, 1920, 1080)


def test_landscape_pip_bubble_in_the_corner_above_the_captions():
    out = FORMATS["16:9"]
    z = safe_zones(out)
    seg, _ = _plan("16:9", "pip")
    r = seg.inset
    assert r.w == r.h == round(1080 * lay.PIP_BUBBLE / 2) * 2  # ~26 % of the height
    assert r.x + r.w == 1920 - z["side"] and r.y + r.h == 1080 - z["bottom"]  # bottom right
    assert seg.radius == 0.5 and seg.view == lay.view_rect("screen", out, SCREEN)
    # A zoom target in the bottom right corner sends the bubble to the bottom left.
    seg, _ = _plan("16:9", "pip", {"screen_zoom": [{"t": 10.0, "box": [0.7, 0.6, 1.0, 1.0]}]})
    assert seg.inset.x == z["side"] and seg.inset.y + seg.inset.h == 1080 - z["bottom"]
    assert sum(overlap(seg.inset.box(), f) for f in seg.focus) == 0
    # ... one on the left keeps it right.
    seg, _ = _plan("16:9", "pip", {"screen_zoom": [{"t": 10.0, "box": [0.0, 0.6, 0.3, 1.0]}]})
    assert seg.inset.x + seg.inset.w == 1920 - z["side"]


def test_landscape_split_side_by_side():
    out = FORMATS["16:9"]
    seg, _ = _plan("16:9", "split", face={"median": {"box": [0.45, 0.3, 0.55, 0.5]}, "frames": []})
    left = round(1920 * lay.SPLIT_SIDE / 2) * 2
    assert seg.view.x + seg.view.w <= left and seg.view.w == left  # 16:10 fits by the width
    assert abs(seg.view.y - (1080 - seg.view.y - seg.view.h)) <= 1  # vertically centred
    assert (seg.face.x, seg.face.y, seg.face.w, seg.face.h) == (left, 0, 1920 - left, 1080)
    cam = seg.camera
    assert cam.w / cam.h == pytest.approx(seg.face.w / seg.face.h, abs=0.01)
    assert cam.x + cam.w / 2 == pytest.approx(0.5 * 1920, abs=2)  # face-centred
    assert _inside(seg.view, out)


@pytest.mark.parametrize("fmt", ["1:1", "4:5"])
@pytest.mark.parametrize("layout", ["screen", "pip", "split"])
@pytest.mark.parametrize("screen", [(2880, 1800), (1920, 1080), (1170, 2532)])
def test_square_and_4_5_stay_inside_the_safe_zones(fmt, layout, screen):
    out = FORMATS[fmt]
    z = safe_zones(out)
    seg, _ = _plan(fmt, layout, screen=screen)
    v = seg.view
    assert _inside(v, out) and v.y >= z["top"] and v.y + v.h <= out[1] - z["bottom"]
    if layout == "pip":
        r = seg.inset
        assert _inside(r, out) and r.y + r.h <= out[1] - z["bottom"]
        assert z["side"] <= r.x and r.x + r.w <= out[0] - z["side"]
        if fmt == "4:5":  # portrait: below the view, never over the screen
            assert overlap(r.box(), v.box()) == 0
    if layout == "split":  # stacked: the screen above, the speaker below
        assert v.y + v.h <= seg.face.y and seg.face.y + seg.face.h == out[1]


def test_sharpness_cap_follows_the_format():
    key = {"screen_zoom": [{"t": 11.0, "box": [0.4, 0.4, 0.6, 0.6]}]}  # 5x asked
    _, clamped = _plan("16:9", spec=key, screen=(1920, 1080))
    assert clamped[0]["max_zoom"] == 1.0  # a 1920 screen fills 1920 px: no sharp zoom
    _, clamped = _plan("9:16", spec=key, screen=(1920, 1080))
    assert clamped[0]["max_zoom"] == round(1920 / 1080, 2)
    seg, clamped = _plan("16:9", spec=key, screen=(2880, 1800))
    assert clamped[0]["max_zoom"] == round(2880 / 1728, 2)
    assert min(lay.window_at(seg, n)[2] for n in range(seg.frames)) == pytest.approx(1728)


# --- speaker crop -------------------------------------------------------------------------

# A video-call recording: the speaker is a small tile at the right of a screen share, the
# face (normalised box) about 6 % of the frame high.
TILE_FACE = [0.835, 0.43, 0.87, 0.49]


def _meeting_face(early: int = 5, late: int = 40) -> dict:
    """face.json of a call: the camera fills the frame for `early` frames, then the screen
    share starts and the face sits in the small tile."""
    big = [0.35, 0.2, 0.65, 0.7]

    def frame(t, b):
        return {"t": t, "found": True, "box": b, "cx": (b[0] + b[2]) / 2, "cy": (b[1] + b[3]) / 2}

    frames = [frame(i / 10, big) for i in range(early)]
    frames += [frame(10 + i / 10, TILE_FACE) for i in range(late)]
    return {"median": {"box": big, "cx": 0.5, "cy": 0.45}, "frames": frames}


@pytest.mark.parametrize("fmt", ["9:16", "16:9", "1:1"])
def test_pip_inset_frames_the_small_face(fmt):
    face = _meeting_face()
    seg, _ = _plan(fmt, "pip", face=face, camera=(1920, 1080))
    cam, cx = seg.camera, (TILE_FACE[0] + TILE_FACE[2]) / 2 * 1920
    side = lay.inset_side(FORMATS[fmt])
    # Head and shoulders: face height x PIP_FACE, but never below SPEAKER_MIN_PX.
    want = max((TILE_FACE[3] - TILE_FACE[1]) * 1080 * lay.PIP_FACE, side * lay.SPEAKER_MIN_PX)
    assert cam.w == cam.h and abs(cam.h - want) <= 2
    assert cam.x <= cx <= cam.x + cam.w and abs(cam.x + cam.w / 2 - cx) <= 2
    assert cam.h < 1080 / 2  # tighter than the EDL crop zoom cap (2.0) would allow
    assert seg.speaker_px == round(cam.h / side, 2) and seg.speaker_px >= lay.SPEAKER_MIN_PX


def test_split_speaker_frames_the_small_face_and_reports_the_px_ratio():
    seg, _ = _plan("16:9", "split", face=_meeting_face(), camera=(1920, 1080))
    part = seg.face
    assert seg.camera.h == pytest.approx(part.h * lay.SPEAKER_MIN_PX, abs=2)
    assert seg.speaker_px == pytest.approx(0.75, abs=0.01)
    fy = (TILE_FACE[1] + TILE_FACE[3]) / 2 * 1080
    assert seg.camera.y <= fy <= seg.camera.y + seg.camera.h


def test_segment_without_own_face_frames_uses_the_global_median():
    face = {"median": {"box": [0.4, 0.3, 0.6, 0.6], "cx": 0.5, "cy": 0.45}, "frames": []}
    seg, _ = _plan("16:9", "pip", face=face)
    side = lay.inset_side(FORMATS["16:9"])
    assert seg.camera.h == pytest.approx(0.3 * 1080 * lay.PIP_FACE, abs=2)
    assert seg.speaker_px == round(seg.camera.h / side, 2) > 1


def test_inset_mask_is_a_circle_in_landscape():
    assert "0.5" in lay.mask(100, FPS, 10, 0.5) or "50" in lay.mask(100, FPS, 10, 0.5)
    seg, _ = _plan("16:9", "pip")
    lines = lay.compose(
        0, seg, 1.0, FPS, FORMATS["16:9"], screen_label="s", camera_label="c",
        bg="0x000000", matrix="bt709", cam_sdr="", screen_sdr="",
    )  # fmt: skip
    assert lay.mask(seg.inset.w, FPS, seg.frames, 0.5) in "".join(lines)


# --- readability --------------------------------------------------------------------------


def test_ui_scale_heuristic():
    assert lay.ui_scale((4096, 2304)) == lay.ui_scale((2880, 1800)) == 2
    assert lay.ui_scale((1920, 1080)) == lay.ui_scale((2560, 1440)) == 1


def test_wide_portrait_view_of_a_mac_recording_is_flagged():
    # The real-test finding: a 4096-wide recording at zoom ~1.3 in a 1080 px wide view.
    screen = (4096, 2304)
    box = [0.1, 0.1, 0.1 + 1 / 1.3, 0.1 + 1 / 1.3]
    seg = _seg("screen", screen_zoom=[{"t": 11.0, "box": box}])
    docs = lay.readability(seg, screen, (1080, 1920))
    assert [d["t"] for d in docs] == [10.0, 11.0]  # the whole screen, then the key
    whole, zoomed = docs
    assert whole["text_px"] == round(26 * 1080 / 4096, 1) and whole["zoom"] == 1.0
    assert zoomed["zoom"] == pytest.approx(1.3, abs=0.01) and zoomed["text_px"] < 18
    assert all(d["flags"] == ["screen_text_small"] for d in docs)
    assert whole["readable_zoom"] == round(18 * 4096 / (26 * 1080), 2)
    # 16:9 shows the same window at 1920 px: zoom 2 is readable there.
    seg = _seg("screen", size=(1920, 1080), screen_zoom=[{"t": 10.0, "box": [0, 0, 0.5, 0.5]}])
    (doc,) = lay.readability(seg, screen, (1920, 1080))
    assert doc["text_px"] == round(26 * 1920 / 2048, 1) and doc["flags"] == []


def test_readability_uses_the_sharpness_clamped_window():
    # 1920x1080 screen, 16:9: any zoom is clamped to 1.0, so the text stays at 13 px.
    seg = _seg("screen", size=(1920, 1080), screen_zoom=[{"t": 10.0, "box": [0, 0, 0.3, 0.3]}])
    (doc,) = lay.readability(seg, (1920, 1080), (1920, 1080))
    assert doc["zoom"] == 1.0 and doc["text_px"] == 13.0 and doc["flags"]
