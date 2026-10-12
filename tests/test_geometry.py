from videocut.edl import from_dict
from videocut.tools import _geometry as geo
from videocut.tools import _render_plan as rp

OUT = (1080, 1920)


def _edl(segments):
    return from_dict({"fps": 30, "size": list(OUT), "source": "input/a.mp4", "segments": segments})


def _face(samples):
    """face.json frames: (t, normalised box)."""
    return {"frames": [{"t": t, "found": True, "box": b} for t, b in samples]}


def test_render_plan_uses_the_shared_crop_window():
    assert rp.crop_window is geo.crop_window and rp.Window is geo.Window


def test_map_box_through_landscape_crop():
    src = (1920, 1080)
    win = geo.crop_window(src, OUT, 0.5, 0.5, 1.0)  # 608x1080 at x=656
    box = [656 / 1920, 0.25, (656 + 304) / 1920, 0.5]  # left half of the window, rows 270..540
    assert geo.map_box(box, src, win, OUT) == [0, 480, 540, 960]
    # outside the window -> None; partly outside -> clipped
    assert geo.map_box([0.0, 0.0, 0.1, 0.1], src, win, OUT) is None
    assert geo.map_box([0.3, 0.0, 0.4, 0.1], src, win, OUT)[0] == 0


def test_face_track_follows_cuts_and_zoom_keys():
    src = (1080, 1920)
    # segment 1: 0..1 s at zoom 1; segment 2: 5..7 s, punch-in to 2x at 6 s
    edl = _edl([{"in": 0.0, "out": 1.0}, {"in": 5.0, "out": 7.0, "zoom_keys": [[6.0, 2.0]]}])
    plan = rp.build(edl, src, OUT)
    centre = [0.4, 0.4, 0.6, 0.6]
    face = _face([(0.5, centre), (3.0, centre), (5.5, centre), (6.5, centre)])
    track = geo.face_track(plan, face, src)
    # the sample at 3.0 s is cut away; times shift to output time
    assert [s["t"] for s in track] == [0.5, 1.5, 2.5]
    assert track[0]["box"] == [432, 768, 648, 1152]  # zoom 1: source px = output px
    assert track[1]["box"] == track[0]["box"]
    # zoom 2 around the centre: window 540x960 at (270, 480), box doubles in size
    assert track[2]["box"] == [324, 576, 756, 1344]
    assert geo.face_at(track, 1.6) == track[1]["box"]
    assert geo.face_at(track, 10.0) is None


def test_face_track_matches_the_rendered_crop():
    """The box maps through the same window render crops with (explicit crop centre)."""
    src = (1920, 1080)
    edl = _edl([{"in": 0.0, "out": 2.0, "crop": {"cx": 0.3, "cy": 0.5, "zoom": 1.25}}])
    plan = rp.build(edl, src, OUT)
    w = plan.ranges[0].window
    # a face box that exactly fills the crop window lands on the full output frame
    box = [w.x / 1920, w.y / 1080, (w.x + w.w) / 1920, (w.y + w.h) / 1080]
    (s,) = geo.face_track(plan, _face([(1.0, box)]), src)
    assert s["box"] == [0, 0, 1080, 1920]


def test_safe_zones_scale_with_size():
    assert geo.safe_zones((1080, 1920)) == {"top": 250, "bottom": 350, "side": 65}
    assert geo.safe_zones((540, 960)) == {"top": 125, "bottom": 175, "side": 32}


def test_overlap():
    assert geo.overlap([0, 0, 10, 10], [5, 5, 20, 20]) == 25
    assert geo.overlap([0, 0, 10, 10], [10, 10, 20, 20]) == 0
