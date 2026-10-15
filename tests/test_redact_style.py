"""Redaction styles (tools/_redact_style.py): blur, crop, block and auto — the choice per box,
the crop geometry and layout fit, the filter graph per style, the blur strength (text
recognition reads nothing), the job setting and its staleness, the API."""

import json
import re
from fractions import Fraction

import numpy as np
import pytest
from helpers import run_tool
from test_server import app, client, cut_job, fake, planned_job  # noqa: F401 (fixtures)

from videocut import run as pipeline
from videocut.edl import from_dict
from videocut.tools import _layouts as lay
from videocut.tools import _ocr, _redact_blur, _redact_style
from videocut.tools.common import ToolError, jobs_root
from videocut.tools.job import approve_job, set_redact_style

SCREEN = (2880, 1800)
OUT = (1920, 1080)
FPS = Fraction(30)
BAR = [0.0, 0.0, 1.0, 0.075]  # browser tabs + address + bookmark bar
DIALOG = [0.25, 0.3, 0.74, 0.68]
CAND = {"id": 1, "kind": "customer", "t0": 0.0, "t1": 2.0, "box": BAR, "reason": "tab titles"}


def _plan(redact, style, zoom=None, layout="screen", out=OUT, screen=SCREEN):
    seg = from_dict(
        {"fps": 30, "size": list(out), "source": "input/a.mp4",
         "segments": [{"in": 10.0, "out": 14.0, "layout": layout,
                       "redact": [{"t0": 10.5, "t1": 13.0, "box": b} for b in redact],
                       **({"screen_zoom": zoom} if zoom else {})}]}
    ).segments[0]  # fmt: skip
    return lay.plan_segment(
        seg, 120, FPS, offset=1.0, screen=screen, camera=(1920, 1080), out=out, full_out=out,
        face=None, redact_style=style,
    )[0]  # fmt: skip


# ---------------------------------------------------------------- choice per box


@pytest.mark.parametrize(
    ("box", "style", "edge"),
    [
        (BAR, "auto", "top"),
        (BAR, "crop", "top"),
        (BAR, "blur", None),
        (BAR, "block", None),
        ([0.03, 0.95, 0.97, 1.0], "auto", "bottom"),
        ([0.0, 0.0, 0.12, 1.0], "auto", None),  # a sidebar: auto blurs it
        ([0.0, 0.0, 0.12, 1.0], "crop", "left"),
        ([0.9, 0.02, 1.0, 0.98], "crop", "right"),
        ([0.0, 0.02, 1.0, 0.08], "auto", None),  # does not touch the edge
        ([0.1, 0.0, 0.85, 0.07], "auto", None),  # not the full width
        (DIALOG, "crop", None),
    ],
)
def test_band_edge(box, style, edge):
    assert lay.band_edge(box, style) == edge


def test_crop_plan_removes_the_band_and_falls_back_to_blur_over_the_limit():
    kept, edges, notes = lay.crop_plan([BAR, DIALOG], SCREEN, "auto")
    assert kept == (0, 136, 2880, 1664) and edges == {0: "top"} and notes == {}
    # snapped inward to even px: the whole band (0.075 * 1800 = 135 px) is gone
    assert kept[1] >= BAR[3] * SCREEN[1] and kept[1] % 2 == 0
    kept, edges, notes = lay.crop_plan([BAR, [0.0, 0.85, 1.0, 1.0]], SCREEN, "auto")
    assert kept == (0, 0, *SCREEN) and edges == {}  # 7.5 % + 15 % > CROP_MAX
    assert set(notes) == {0, 1} and all("blurred instead" in n for n in notes.values())
    kept, edges, _ = lay.crop_plan([[0.0, 0.0, 0.1, 1.0], BAR], SCREEN, "crop")
    assert kept == (288, 136, 2592, 1664) and edges == {0: "left", 1: "top"}


def test_auto_crops_the_bar_and_blurs_the_rest():
    seg = _plan([BAR, DIALOG], "auto")
    assert [b["style"] for b in seg.redaction] == ["crop", "blur"]
    assert seg.redaction[0]["edge"] == "top" and seg.redact == []
    assert seg.origin == (0, 136) and seg.screen == (2880, 1664) and seg.full_screen == SCREEN
    # the dialog block in the coordinates of the screen shown (snapped outward, even)
    (n0, n1, x, y, w, h, cell) = seg.blur[0]
    assert (x, y, w, h) == (720, 540 - 136, 2132 - 720, 1224 - 540)
    assert cell == lay.blur_cell(w, h, SCREEN) and cell >= lay.TEXT_PT * 2


def test_block_and_blur_keep_the_whole_screen():
    for style, drawn in (("block", "redact"), ("blur", "blur")):
        seg = _plan([BAR, DIALOG], style)
        assert seg.origin == (0, 0) and seg.screen == SCREEN
        assert len(getattr(seg, drawn)) == 2 and {b["style"] for b in seg.redaction} == {style}


def test_a_crop_over_the_limit_blurs_and_says_so():
    seg = _plan([[0.0, 0.0, 1.0, 0.25]], "auto")
    assert seg.screen == SCREEN and len(seg.blur) == 1
    assert "blurred instead" in seg.redaction[0]["note"]


@pytest.mark.parametrize(("out", "layout"), [((1920, 1080), "screen"), ((1080, 1920), "pip"),
                                             ((1080, 1080), "split")])  # fmt: skip
def test_crop_refits_the_view_and_keeps_zoom_coordinates(out, layout):
    zoom = [{"t": 12.0, "box": [0.4, 0.5, 0.7, 0.8]}]
    plain, cropped = (
        _plan([DIALOG], "auto", zoom, layout, out),
        _plan([BAR], "auto", zoom, layout, out),
    )
    # the view is laid out for the screen shown (its aspect where it is fitted)
    assert cropped.view == lay.view_rect(layout, out, cropped.screen)
    if not lay.filled(layout, out, cropped.screen):
        assert abs(cropped.view.w / cropped.view.h - 2880 / 1664) < 0.02
    # the zoom target lands on the same recording px: window (in shown px) + origin
    for seg in (plain, cropped):
        x, y, w = lay.window_at(seg, 119)
        h = w * seg.view.h / seg.view.w
        cx, cy = x + w / 2 + seg.origin[0], y + h / 2 + seg.origin[1]
        assert abs(cx - 0.55 * 2880) < 2 and abs(cy - 0.65 * 1800) < 2, (seg.origin, x, y, w)
    # the target in output px matches the recording px of the target box
    fx0, fy0 = cropped.focus[0][:2]
    x, y, w = lay.window_at(cropped, 119)
    k = cropped.view.w / w
    assert abs(cropped.view.x + (0.4 * 2880 - x) * k - fx0) < 1
    assert abs(cropped.view.y + (0.5 * 1800 - 136 - y) * k - fy0) < 1


def test_a_zoom_inside_the_band_stays_inside_the_screen_shown():
    seg = _plan([BAR], "auto", [{"t": 11.0, "box": [0.1, 0.0, 0.3, 0.05]}])
    x, y, w = lay.window_at(seg, 119)
    assert x >= 0 and y >= 0 and x + w <= seg.screen[0] + 1e-6


# ---------------------------------------------------------------- filter graph


def _lines(seg) -> str:
    return "\n".join(lay.screen_lines(3, seg, FPS, "0xF4F4F4", "bt709", "", "s3"))


def test_filter_graph_per_style():
    block = _lines(_plan([DIALOG], "block"))
    assert "drawbox=" in block and "t=fill" in block and "gblur" not in block
    assert block.count("[sv3];") == 1 and "crop=2880:1664" not in block

    blur = _lines(_plan([DIALOG], "blur"))
    assert "drawbox" not in blur and "split=2[rm3][rc3_0];" in blur
    m = re.search(r"\[rc3_0\]crop=(\d+):(\d+):(\d+):(\d+),scale=(\d+):(\d+):flags=area,"
                  r"scale=\1:\2:flags=neighbor,gblur=sigma=(\d+)", blur)  # fmt: skip
    assert m, blur
    w, h, cw, ch, sigma = int(m[1]), int(m[2]), int(m[5]), int(m[6]), int(m[7])
    cell = lay.blur_cell(w, h, SCREEN)
    assert (cw, ch) == (-(-w // cell), -(-h // cell)) and sigma == cell // 2
    assert re.search(r"\[rm3\]\[rb3_0\]overlay=x=\d+:y=\d+:format=auto:enable='between\(t,", blur)

    auto = _lines(_plan([BAR, DIALOG], "auto"))
    first = auto.splitlines()[0]
    assert "crop=2880:1664:0:136" in first and first.index("crop=2880:1664") < first.index("split")
    assert "gblur" in auto and "drawbox" not in auto


def test_a_face_only_or_unredacted_segment_draws_as_before():
    seg = _plan([], "auto")
    assert seg.screen == SCREEN and seg.origin == (0, 0)
    assert lay.screen_lines(3, seg, FPS, "0x0", "bt709", "", "s3") == [
        "[s3]" + ",".join(lay.screen_view(seg, FPS, "0x0", "bt709", "")) + "[sv3];"
    ]


# ---------------------------------------------------------------- blur strength


def _text_image(text: str, size: int, w: int = 1800) -> np.ndarray:
    import subprocess

    import clips

    if not clips.font():
        pytest.skip("needs a system font")
    h = int(size * 1.8)
    raw = subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", f"color=c=0xF2F2F2:s={w}x{h}",
         "-vf", f"drawtext=fontfile={clips.font()}:text='{text}':x=12:y=(h-text_h)/2"
         f":fontsize={size}:fontcolor=0x1A1A1A", "-frames:v", "1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


ocr = pytest.mark.skipif(not _ocr.available(), reason="needs macOS text recognition")


@ocr
@pytest.mark.media
@pytest.mark.parametrize("size", [26, 40, 72, 140])
@pytest.mark.parametrize("screen", [(1920, 1080), (2880, 1800)])
def test_blur_leaves_nothing_readable_for_small_and_large_text(size, screen):
    img = _text_image("Zentrovia Kundentermin mara at zentrovia dot example", size)
    assert _redact_blur.readable(img)  # the control: readable before
    cell = lay.blur_cell(img.shape[1], img.shape[0], screen)
    out = _redact_blur.pixelate(img, cell)
    assert _redact_blur.readable(out) == []
    if size <= 72:  # up to ~5x the UI text half the cell is still unreadable: margin (for
        # headline-sized text render's calibration makes the cell larger when needed)
        assert _redact_blur.readable(_redact_blur.pixelate(img, max(2, cell // 2))) == []
    # the box keeps its colours (no solid fill) and has no sharp edges
    assert abs(out.mean() - img.mean()) < 6 and _redact_blur.edge_share(out) == 0.0
    assert _redact_blur.edge_share(img) > 0.02


def test_calibrate_makes_a_readable_blur_stronger_or_a_block(monkeypatch):
    frame = np.full((1800, 2880, 3), 200, np.uint8)
    seg = _plan([DIALOG, [0.1, 0.8, 0.2, 0.85]], "blur")
    drawn = {"segs": {0: seg}, "info": {"size": list(SCREEN), "duration": 60.0},
             "screen": "unused.mov"}  # fmt: skip
    monkeypatch.setattr(_ocr, "available", lambda: True)
    monkeypatch.setattr("videocut.tools.redact.grab_screen", lambda *a, **k: frame)
    first, small = seg.blur[0][6], seg.blur[1][6]
    state = {}

    def pixelate(img, cell):
        state["cell"] = cell
        return img

    def readable(img):  # the dialog reads nothing from 4x the cell on, the small box never
        return [] if img.shape[0] > 200 and state["cell"] >= first * 4 else ["Zentrovia"]

    monkeypatch.setattr(_redact_blur, "pixelate", pixelate)
    monkeypatch.setattr(_redact_blur, "readable", readable)
    changed = _redact_blur.calibrate(drawn, FPS)
    assert [c["was"] for c in changed] == [first, small]
    assert seg.blur == [(*seg.blur[0][:6], first * 4)]  # stronger, never weaker
    assert "made stronger" in seg.redaction[0]["note"] and seg.redaction[0]["cell"] == first * 4
    # the small box stays readable even as one cell: a solid block instead (fail closed)
    assert len(seg.redact) == 1 and seg.redaction[1]["style"] == "block"
    assert "solid block" in seg.redaction[1]["note"] and "cell" not in seg.redaction[1]


def test_calibrate_without_text_recognition_changes_nothing(monkeypatch):
    seg = _plan([DIALOG], "blur")
    monkeypatch.setattr(_ocr, "available", lambda: False)
    assert _redact_blur.calibrate({"segs": {0: seg}}, FPS) == [] and len(seg.blur) == 1


# ---------------------------------------------------------------- setting


def test_style_resolution_job_then_brand_then_auto(videocut_home):
    assert _redact_style.resolved({}) == "auto"
    assert _redact_style.resolved({"redact_style": "BLOCK "}) == "block"
    with pytest.raises(ToolError, match="redact_style"):
        _redact_style.resolved({"redact_style": "smudge"})
    b = videocut_home / "brands" / "zentrovia"
    b.mkdir(parents=True)
    brand = json.loads((lay.brand.BUNDLED_ROOT / "_example" / "brand.json").read_text())
    (b / "brand.json").write_text(json.dumps({**brand, "redaction": {"style": "crop"}}))
    assert _redact_style.resolved({"brand": "zentrovia"}) == "crop"
    assert _redact_style.resolved({"brand": "zentrovia", "redact_style": "blur"}) == "blur"
    (b / "brand.json").write_text(json.dumps({**brand, "redaction": {"style": "smear"}}))
    _, errors, _ = lay.brand.inspect("zentrovia")
    assert any("$.redaction.style" in e for e in errors)
    assert _redact_style.screenshot("crop") == _redact_style.screenshot("auto") == "blur"
    assert _redact_style.screenshot("block") == "block"


def test_job_redact_style_command_and_staleness(capsys, tmp_path, monkeypatch):
    v = tmp_path / "take.mov"
    v.write_bytes(b"x")
    run_tool(capsys, "job", "new", str(v), "--id", "r1")
    job = jobs_root() / "r1"
    code, s, _ = run_tool(capsys, "job", "redact-style", "r1", "blur")
    assert code == 0 and s["redact_style"] == "blur" and s["stale"] is False
    assert json.loads((job / "job.json").read_text())["redact_style"] == "blur"
    assert run_tool(capsys, "job", "show", "r1")[1]["redact_style"] == "blur"
    assert run_tool(capsys, "job", "redact-style", "r1", "smudge")[0] == 1
    # a reel drawn with blocks (a cut record from before styles, with redaction) goes stale
    data = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**data, "cut": {"redaction_sha256": "abc"}}))
    (job / "out" / "reel.mp4").write_bytes(b"reel")
    said = []
    res = set_redact_style(job, "crop", said.append)
    assert res["stale"] and any("as block" in x and "videocut run r1" in x for x in said)
    assert set_redact_style(job, "block", said.append)["stale"] is False
    from videocut.tools.job import AGENT_ENV

    monkeypatch.setenv(AGENT_ENV, "1")
    code, s, _ = run_tool(capsys, "job", "redact-style", "r1", "blur")
    assert code == 1 and "only a human" in json.dumps(s)


def test_a_changed_style_makes_the_cut_stale_only_with_redaction(client):  # noqa: F811
    job = planned_job("p1", candidates=[CAND])
    approve_job(job)
    cut_job(job)
    rec = json.loads((job / "job.json").read_text())["cut"]
    assert rec["redact_style"] == "auto" and pipeline.cut_done(job)
    r = client.put("/api/jobs/p1/redact-style", json={"redact_style": "blur"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": "p1", "redact_style": "blur", "previous": "auto", "cut_stale": True}
    assert "personal data is now shown as blur" in pipeline.stale_why(job)
    shown = client.get("/api/jobs/p1").json()
    assert (shown["redact_style"], shown["redact_style_default"], shown["cut_stale"]) == (
        "blur", False, True)  # fmt: skip
    assert (
        client.put("/api/jobs/p1/redact-style", json={"redact_style": "auto"}).json()["cut_stale"]
        is False
    )
    assert client.put("/api/jobs/p1/redact-style", json={"redact_style": "x"}).status_code == 422
    assert (
        client.put("/api/jobs/nope/redact-style", json={"redact_style": "blur"}).status_code == 404
    )
    # nothing to redact: the style does not matter for the cut
    plain = planned_job("p2")
    approve_job(plain)
    cut_job(plain)
    client.put("/api/jobs/p2/redact-style", json={"redact_style": "block"})
    assert pipeline.cut_done(plain)


def test_screenshot_items_get_the_style(tmp_path):
    from videocut.tools.overlays import _redacted

    item = {"scene": "screenshot_frame", "t0": 0, "t1": 2,
            "props": {"file": "input/shot.png", "redact": [[0.1, 0.1, 0.3, 0.2]]}}  # fmt: skip
    assert _redacted(tmp_path, item, [], "blur")["props"]["redact_style"] == "blur"
    assert _redacted(tmp_path, item, [], "block")["props"]["redact_style"] == "block"
    bare = {"scene": "screenshot_frame", "t0": 0, "t1": 2, "props": {"file": "input/shot.png"}}
    assert _redacted(tmp_path, bare, [], "blur") is bare


@pytest.mark.media
@pytest.mark.parametrize(("w", "h", "cell"), [(70, 30, 20), (200, 120, 32)])
def test_the_reference_pixelation_matches_the_render_chain(w, h, cell):
    """calibrate judges what the reel shows: blur_chain makes ceil(side / cell) cells, so a
    box between one and two cells high gets cells smaller than `cell`."""
    import subprocess

    import clips

    rng = np.random.default_rng(1)
    img = (rng.random((h, w, 3)) * 255).astype(np.uint8)
    img[h // 3 : h // 2] = 255
    out = subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-i", "-", "-vf", lay.blur_chain(w, h, cell), "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-"],
        input=img.tobytes(), capture_output=True, check=True,
    ).stdout  # fmt: skip
    ref = np.frombuffer(out, np.uint8).reshape(h, w, 3).astype(float)
    diff = np.abs(ref - _redact_blur.pixelate(img, cell).astype(float))
    assert diff.mean() < 4 and diff.max() <= 12, (diff.mean(), diff.max())


def test_a_reel_from_before_cut_records_goes_stale_when_the_style_changes(client):  # noqa: F811
    job = planned_job("p3", candidates=[CAND])
    approve_job(job)
    cut_job(job)
    data = json.loads((job / "job.json").read_text())
    data.pop("cut")
    (job / "job.json").write_text(json.dumps(data))
    assert pipeline.cut_done(job)  # an old reel without a record: it drew blocks
    said = []
    assert set_redact_style(job, "blur", said.append)["stale"]
    assert any("as block" in x for x in said) and not pipeline.cut_done(job)
    assert set_redact_style(job, "block", said.append)["stale"] is False
    assert pipeline.cut_done(job)


def test_show_reports_an_invalid_style_instead_of_failing(capsys, tmp_path):
    v = tmp_path / "take.mov"
    v.write_bytes(b"x")
    run_tool(capsys, "job", "new", str(v), "--id", "r2")
    job = jobs_root() / "r2"
    data = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**data, "redact_style": "smudge"}))
    code, s, _ = run_tool(capsys, "job", "show", "r2")
    assert code == 0 and s["redact_style"] == "invalid" and "smudge" in s["redact_style_error"]


@pytest.mark.media
def test_the_new_job_form_stores_a_style_auto_means_the_brand(client, portrait_clip):  # noqa: F811
    def post(i, value):
        with portrait_clip.open("rb") as cam:
            cam_file = (f"take{i}.mp4", cam, "video/quicktime")
            return client.post("/api/jobs", data={"redact_style": value},
                               files=[("camera", cam_file)])  # fmt: skip

    for i, (value, stored) in enumerate((("auto", None), ("crop", "crop"), ("", None))):
        r = post(i, value)
        assert r.status_code == 200, r.text
        data = json.loads((jobs_root() / r.json()["id"] / "job.json").read_text())
        assert data.get("redact_style") == stored
    r = post(9, "smear")
    assert r.status_code == 400 and "redact" in r.text


# ---------------------------------------------------------------- grown boxes


def test_a_box_grows_over_the_lines_it_cuts_and_never_shrinks():
    from videocut.tools import _redact_grow as grow

    box = [0.0, 0.0, 1.0, 0.075]
    cut = [0.02, 0.07, 0.3, 0.09]  # a tab title, its upper quarter under the band
    below = [0.02, 0.12, 0.3, 0.14]  # the next line: not touched
    inside = [0.02, 0.01, 0.3, 0.03]
    assert grow.cuts(cut, box) and not grow.cuts(below, box) and not grow.cuts(inside, box)
    # a line that only grazes the box (less than LINE_CUT of its height) is not cut
    assert not grow.cuts([0.02, 0.0745, 0.3, 0.0945], box)
    out = grow._over_text(box, [cut, below, inside])
    assert out[3] == pytest.approx(0.09 + grow.MARGIN * 0.02) and out[:3] == box[:3]
    # a name box that cuts a line across grows over the whole line, on every side
    name = [0.4, 0.5, 0.5, 0.52]
    line = [0.3, 0.505, 0.7, 0.525]
    out = grow._over_text(name, [line])
    m = grow.MARGIN * 0.02
    assert out == pytest.approx([0.3 - m, 0.505 - m, 0.7 + m, 0.525 + m])


def _chrome(h=1600, w=2560, bar=150, step=40):
    """A frame: a browser chrome `bar` px high (grey 40), the page below (grey 40 + step)."""
    f = np.full((h, w, 3), 40 + step, np.uint8)
    f[:bar] = 40
    return f


def test_a_band_snaps_to_the_next_ui_boundary_within_the_limit(monkeypatch):
    from videocut.tools import _redact_grow as grow

    monkeypatch.setattr(grow, "_lines", lambda *a: [])  # no text: only the boundary
    screen = (2560, 1600)  # 2x: text 26 px, the limit 78 px
    box, why = grow.grow([0.0, 0.0, 1.0, 120 / 1600], [_chrome(bar=150)], screen)
    assert box[3] == pytest.approx(150 / 1600, abs=1e-4) and why == ["band"]
    # the boundary out of reach (or a step too small): the band stays
    assert grow.grow([0.0, 0.0, 1.0, 50 / 1600], [_chrome(bar=150)], screen)[1] == []
    assert grow.grow([0.0, 0.0, 1.0, 120 / 1600], [_chrome(step=4)], screen)[1] == []
    # a bottom band moves up, a box that is not a band never snaps
    f = _chrome()[::-1].copy()
    box, _ = grow.grow([0.0, 1 - 120 / 1600, 1.0, 1.0], [f], screen)
    assert box[1] == pytest.approx(1 - 150 / 1600, abs=1e-4)
    assert grow.grow([0.3, 0.0, 0.6, 120 / 1600], [_chrome()], screen)[1] == []


def _band_frame(text: str, top: int, size: int = 26, shape=(1080, 1920)) -> np.ndarray:
    """A light screen with one line of `text` whose image starts `top` px down."""
    img = _text_image(text, size, w=shape[1])
    f = np.full((*shape, 3), 0xF2, np.uint8)
    f[top : top + img.shape[0]] = img
    return f


@ocr
@pytest.mark.media
def test_grow_pulls_a_band_over_the_text_line_it_cuts():
    from videocut.tools import _redact_grow as grow

    f = _band_frame("Zentrovia Kundentermin Montag  Angebot Nordlicht", 60)
    ink = np.flatnonzero((f.min(axis=2) < 128).any(axis=1))
    mid = (ink[0] + ink[-1]) / 2
    box, why = grow.grow([0.0, 0.0, 1.0, mid / 1080], [f], (1920, 1080))
    assert "text" in why and box[3] * 1080 >= ink[-1] + 1, (box, ink[-1])
    assert _redact_blur.readable(f[int(np.ceil(box[3] * 1080)) :]) == []


@ocr
@pytest.mark.media
def test_verify_flags_a_line_a_crop_cuts_through(monkeypatch):
    from types import SimpleNamespace

    from videocut.tools import _verify_screen

    f = _band_frame("Zentrovia Kundentermin Montag  Angebot Nordlicht", 60)
    ink = np.flatnonzero((f.min(axis=2) < 128).any(axis=1))
    seg = _plan([], "auto")
    plan = SimpleNamespace(ranges=[SimpleNamespace(seg=1)], fps=FPS)
    info = {"duration": 10.0, "size": [1920, 1080]}
    monkeypatch.setattr(_verify_screen, "grab_screen", lambda *a: f)

    def docs(cut_y: int) -> list[dict]:
        seg.full_screen, seg.origin, seg.screen = (1920, 1080), (0, cut_y), (1920, 1080 - cut_y)
        ctx = {"segs": {0: seg}, "plan": plan, "starts": [0], "info": info, "screen": None}
        return _verify_screen._crop_cut_docs(ctx)

    through = docs(int((ink[0] + ink[-1]) / 2) // 2 * 2)
    assert through[0]["flags"] == ["redaction_partial"] and through[0]["samples"][0]["read"]
    below = docs(int(ink[-1]) + 12)
    assert below[0]["flags"] == [] and len(below[0]["samples"]) >= 1


def test_grow_cache_skips_failed_grabs_and_drops_on_retune(tmp_path, monkeypatch):
    from videocut.tools import _redact_grow as rg
    from videocut.tools import redact

    (tmp_path / "work").mkdir()
    src = tmp_path / "screen.mov"
    src.write_bytes(b"x")
    info = {"size": [1000, 800], "duration": 10.0}
    block = {"t0": 1.0, "t1": 2.0, "box": [0.1, 0.1, 0.3, 0.2], "offset": 0.0}
    grabs = []
    monkeypatch.setattr(_ocr, "available", lambda: True)
    monkeypatch.setattr(redact, "shown_pts", lambda s, i, t, fps: t)
    monkeypatch.setattr(redact, "grab_screen", lambda s, i, t, fps: grabs.append(t))
    monkeypatch.setattr(rg, "grow", lambda box, frames, screen: ([0.1, 0.1, 0.3, 0.25], ["text"]))
    cache = tmp_path / "work" / rg.CACHE

    assert rg.apply(tmp_path, [block], src, info, 30) == [{"box": block["box"]}]
    assert grabs and not cache.exists()  # nothing read: nothing cached, tried again next time

    monkeypatch.setattr(redact, "grab_screen", lambda s, i, t, fps: grabs.append(t) or np.zeros(1))
    grabs.clear()
    out = rg.apply(tmp_path, [block], src, info, 30)
    assert out[0]["box"] == [0.1, 0.1, 0.3, 0.25] and grabs
    grabs.clear()
    assert rg.apply(tmp_path, [block], src, info, 30) == out and not grabs  # cached

    monkeypatch.setattr(rg, "PARAMS", [*rg.PARAMS, 1])
    assert rg.apply(tmp_path, [block], src, info, 30) == out and grabs  # retuned: read again
