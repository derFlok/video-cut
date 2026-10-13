"""End-to-end render of the demo layouts on generated material: a landscape camera clip and
a 2880x1800 "screen recording" (fake UI, a sensitive red block, a binary frame counter
burnt in) that starts OFFSET seconds earlier than the camera (screen = camera + OFFSET)."""

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import clips
import numpy as np
import pytest
from helpers import run_tool

from videocut.edl import from_dict
from videocut.tools import _layouts as lay
from videocut.tools import _verify_screen
from videocut.tools._format import safe_zones
from videocut.tools.job import approve_job

pytestmark = pytest.mark.media

OFFSET = 3.27
SW, SH = 2880, 1800
OUT_SIZE = (1080, 1920)
BITS, BIT, BIT_STEP, BIT_X, BIT_Y = 11, 160, 170, 40, 40  # frame counter blocks (source px)
SECRET = [1800, 1000, 2400, 1400]  # red block, source px
SECRET_BOX = [SECRET[0] / SW, SECRET[1] / SH, SECRET[2] / SW, SECRET[3] / SH]
SEGMENTS = [
    {"in": 0.3, "out": 1.0},
    {"in": 1.0, "out": 2.5, "layout": "screen",
     "redact": [{"t0": 1.5, "t1": 2.2, "box": SECRET_BOX}]},
    {"in": 2.5, "out": 4.0, "layout": "pip",
     "screen_zoom": [{"t": 3.3, "box": [0.5, 0.45, 0.9, 0.85]}],
     "redact": [{"t0": 2.5, "t1": 4.0, "box": SECRET_BOX}]},
    {"in": 4.0, "out": 5.5, "layout": "split"},
    {"in": 5.5, "out": 7.0, "layout": "screen",
     "screen_zoom": [{"t": 6.0, "box": [0.4, 0.4, 0.4 + 1 / 3.5, 0.4 + 1 / 3.5]}]},
]  # fmt: skip


def _screen(folder: Path, duration: float) -> Path:
    out = folder / "screen.mp4"
    if out.is_file():
        return out
    bits = ",".join(
        f"drawbox=x={BIT_X + k * BIT_STEP}:y={BIT_Y}:w={BIT}:h={BIT}:c=white:t=fill"
        f":enable='eq(mod(floor(round(t*30)/{2**k}),2),1)'"
        for k in range(BITS)
    )
    ui = ",".join(
        [
            f"color=c=0x2E3440:s={SW}x{SH}:r=30:d={duration:.3f}",
            f"drawbox=x=0:y=0:w={BIT_X * 2 + BITS * BIT_STEP}:h={BIT + 2 * BIT_Y}:c=black:t=fill",
            "drawbox=x=200:y=400:w=1300:h=1300:c=0xD8DEE9:t=fill",  # a window
            *(f"drawbox=x=260:y={480 + i * 90}:w=1180:h=60:c=0x4C566A:t=fill" for i in range(12)),
            "drawbox=x='mod(t*300,1200)+250':y=1500:w=40:h=40:c=0x5E81AC:t=fill",  # cursor
            f"drawbox=x={SECRET[0]}:y={SECRET[1]}:w={SECRET[2] - SECRET[0]}"
            f":h={SECRET[3] - SECRET[1]}:c=red:t=fill",
            bits,
        ]
    )
    clips._ff(
        "-f", "lavfi", "-i", ui, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-c:a", "aac", "-shortest", str(out),
    )  # fmt: skip
    return out


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    if not clips.available():
        pytest.skip("needs ffmpeg and the say voice")
    folder = tmp_path_factory.mktemp("layout-clips")
    camera = clips.build(folder, "landscape")
    return camera, _screen(folder, clips.duration(camera) + OFFSET + 1)


FORMAT_OF = {(1080, 1920): "9:16", (1920, 1080): "16:9", (1080, 1080): "1:1", (1080, 1350): "4:5"}


def _job(capsys, material, job_id, size=(1080, 1920)) -> Path:
    camera, screen = material
    code, s, _ = run_tool(capsys, "job", "new", str(camera), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    shutil.copy(screen, job / "input" / "screen.mp4")
    meta = json.loads((job / "job.json").read_text())
    meta |= {"screen": "input/screen.mp4", "format": FORMAT_OF[tuple(size)]}
    (job / "job.json").write_text(json.dumps(meta))
    sync = {"offset_s": OFFSET, "method": "manual", "confidence": 1.0, "drift_ppm": None,
            "overlap": [0, 8]}  # fmt: skip
    (job / "work" / "sync.json").write_text(json.dumps(sync))
    edl = {"fps": 30, "size": list(size), "source": s["source"], "segments": SEGMENTS}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: layout test\n")
    approve_job(job)
    return job


def _frames(path: Path, f0: int, f1: int, crop: tuple[int, int, int, int], fmt="gray"):
    """Output frames f0..f1-1, cropped to (w, h, x, y), as an array [n, h, w(, 3)]."""
    w, h, x, y = crop
    raw = subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-i", str(path), "-vf",
         f"trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS,crop={w}:{h}:{x}:{y}",
         "-f", "rawvideo", "-pix_fmt", fmt, "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    shape = (-1, h, w, 3) if fmt == "rgb24" else (-1, h, w)
    return np.frombuffer(raw, np.uint8).reshape(shape).astype(int)


def _starts() -> list[int]:
    out, t = [0], 0.0
    for s in SEGMENTS:
        t += s["out"] - s["in"]
        out.append(round(t * 30))
    return out


def _seg_plan(i: int, out=(1080, 1920), full=None):
    """Drawn segment i at `out` for an EDL of size `full` (default: `out`; 9:16 for a
    preview size)."""
    full = full or (out if out in FORMAT_OF else (1080, 1920))
    edl = from_dict(
        {"fps": 30, "size": list(full), "source": "input/a.mp4", "segments": [SEGMENTS[i]]}
    )
    n = _starts()[i + 1] - _starts()[i]
    return lay.plan_segment(
        edl.segments[0], n, Fraction(30), offset=OFFSET, screen=(SW, SH),
        camera=(1920, 1080), out=out, full_out=full, face=None,
    )[0]  # fmt: skip


def test_layouts_render(capsys, material):
    job = _job(capsys, material, "layouts")
    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, (s, out)
    reel = job / "out" / "reel.mp4"
    probe = json.loads(
        subprocess.run(
            [clips.ffprobe(), "-v", "error", "-show_streams", "-of", "json", reel],
            capture_output=True,
            text=True,
            check=True,
        ).stdout  # fmt: skip
    )
    v = next(x for x in probe["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert int(v["nb_frames"]) == _starts()[-1]
    assert any(x["codec_type"] == "audio" for x in probe["streams"])
    report = json.loads((job / "out" / "render.json").read_text())
    assert report["layouts"] == {"face": 1, "screen": 2, "pip": 1, "split": 1}
    assert [c["segment"] for c in report["screen_zoom_clamped"]] == [4]
    assert report["screen_zoom_clamped"][0]["max_zoom"] == round(SW / 1080, 2)
    assert any("blurry" in w for w in s["warnings"])
    starts = _starts()

    # Screen time: the burnt-in counter of segment 1 is camera time + OFFSET.
    seg = _seg_plan(1)
    view, k = seg.view, seg.view.w / SW
    strip = _frames(reel, starts[1], starts[2], (1080, 120, 0, view.y))
    centres = [round((BIT_X + j * BIT_STEP + BIT / 2) * k) for j in range(BITS)]
    cy = round((BIT_Y + BIT / 2) * k)
    for j, img in enumerate(strip):
        got = sum(1 << b for b, x in enumerate(centres) if img[cy, x] > 128)
        assert abs(got - round((SEGMENTS[1]["in"] + j / 30 + OFFSET) * 30)) <= 1, j

    # Redaction: no red inside the block in any redacted frame (also while zooming); the
    # red block is visible before the redaction starts.
    for i in (1, 2):
        seg = _seg_plan(i)
        v = seg.view
        imgs = _frames(reel, starts[i], starts[i + 1], (v.w, v.h, v.x, v.y), "rgb24")
        for n, img in enumerate(imgs):
            x, y, w = lay.window_at(seg, n)
            kk = v.w / w
            x0, x1 = (SECRET[0] - x) * kk + 4, (SECRET[2] - x) * kk - 4
            y0, y1 = (SECRET[1] - y) * kk + 4, (SECRET[3] - y) * kk - 4
            box = img[max(0, int(y0)) : max(0, int(y1)), max(0, int(x0)) : max(0, int(x1))]
            red = (box[..., 0] > 150) & (box[..., 1] < 100) & (box[..., 2] < 100)
            redacted = any(r[0] <= n <= r[1] for r in seg.redact)
            if redacted:
                assert box.size and not red.any(), (i, n)
            elif box.size:
                assert red.mean() > 0.9, (i, n)
    assert any(not any(r[0] <= n <= r[1] for r in _seg_plan(1).redact) for n in range(10))

    # Zoom lands where the window says: after the move of segment 4 the (unredacted) red
    # block shows at its mapped place; in segment 2 (zoomed pip) the block there is bg.
    for i, n, want in ((4, 30, "red"), (2, 40, "bg")):
        seg = _seg_plan(i)
        v = seg.view
        x, y, w = lay.window_at(seg, n)
        kk = v.w / w
        x0, x1 = max(int((SECRET[0] - x) * kk) + 6, 0), min(int((SECRET[2] - x) * kk) - 6, v.w)
        y0, y1 = max(int((SECRET[1] - y) * kk) + 6, 0), min(int((SECRET[3] - y) * kk) - 6, v.h)
        assert x1 - x0 > 50 and y1 - y0 > 50, (i, x0, x1, y0, y1)
        img = _frames(reel, starts[i] + n, starts[i] + n + 1, (v.w, v.h, v.x, v.y), "rgb24")[0]
        box = img[y0:y1, x0:x1]
        if want == "red":
            assert ((box[..., 0] > 150) & (box[..., 1] < 100)).mean() > 0.9, i
        else:
            assert np.abs(box - 244).max() < 12, i

    # The moving window is frame-exact: during the move of segment 4 the red block's
    # centre sits where window_at(n) puts it, not one frame ahead (perspective's `in`
    # counts from 1).
    seg = _seg_plan(4)
    v, m = seg.view, seg.moves[0]
    imgs = _frames(reel, starts[4] + m.start, starts[4] + m.end, (v.w, v.h, v.x, v.y), "rgb24")

    def centre(n):
        x, y, w = lay.window_at(seg, n)
        kk = v.w / w
        x0, x1 = max((SECRET[0] - x) * kk, 0), min((SECRET[2] - x) * kk, v.w)
        y0, y1 = max((SECRET[1] - y) * kk, 0), min((SECRET[3] - y) * kk, v.h)
        return np.array([(x0 + x1) / 2, (y0 + y1) / 2])

    errs = []
    for j, img in enumerate(imgs[2:-2], start=2):
        n = m.start + j
        ys, xs = np.nonzero((img[..., 0] > 150) & (img[..., 1] < 100) & (img[..., 2] < 100))
        if len(xs) < 500:
            continue
        got = np.array([xs.mean(), ys.mean()])
        here, ahead = np.linalg.norm(got - centre(n)), np.linalg.norm(got - centre(n + 1))
        errs.append(here)
        assert here < ahead and here < 3, (n, got, centre(n), centre(n + 1))
    assert len(errs) > 5

    # Layout look: screen keeps the bottom on the brand background, split shows the
    # camera there, pip has the inset below the view.
    def bottom(i):
        mid = (starts[i] + starts[i + 1]) // 2
        return _frames(reel, mid, mid + 1, (1080, 300, 0, 1500))[0]

    assert np.abs(bottom(1) - 244).max() < 6  # colors.bg #F4F4F4 of the _example brand
    assert bottom(3).std() > 20
    inset = _seg_plan(2).inset
    mid = (starts[2] + starts[3]) // 2
    pip = _frames(reel, mid, mid + 1, (inset.w, inset.h, inset.x, inset.y))[0]
    assert pip.std() > 20 and abs(pip[2, 2] - 244) < 6  # camera inside, rounded corner


def test_layouts_preview_and_errors(capsys, material):
    job = _job(capsys, material, "layouts-prev")
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    assert s["size"] == [540, 960] and s["output"] == "out/preview.mp4"
    assert [c["segment"] for c in s["screen_zoom_clamped"]] == [4]

    (job / "work" / "sync.json").unlink()
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code != 0 and "sync" in json.dumps(s)


def test_layouts_verify(capsys, material):
    job = _job(capsys, material, "layouts-verify")
    code, s, _ = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, s
    code, s, out = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, s
    assert s["screen_flagged"] == [], out
    assert [c["segment"] for c in s["screen_zoom_clamped"]] == [4]
    doc = json.loads((job / "out" / "verify.json").read_text())
    red = doc["screen"]["redaction"]
    assert [r["segment"] for r in red] == [1, 2]
    assert all(x["bg_share"] >= 0.9 for r in red for x in r["samples"]) and red[1]["samples"]
    assert len(doc["screen"]["screen_time"]) == 4 and doc["checks"]["screen_overlap"]["ok"]
    # No frozen flag for the still screen, no visual_jump at the view changes.
    assert doc["checks"]["black_or_frozen"]["ok"], doc["checks"]["black_or_frozen"]
    assert not any("visual_jump" in c["flags"] for c in doc["cuts"])

    # A redact block the render did not draw, and a wrong offset, are both caught.
    edl = json.loads((job / "work" / "edl.json").read_text())
    edl["segments"][4]["redact"] = [{"t0": 5.6, "t1": 5.9, "box": SECRET_BOX}]
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    sync = json.loads((job / "work" / "sync.json").read_text())
    (job / "work" / "sync.json").write_text(json.dumps({**sync, "offset_s": OFFSET + 0.5}))
    code, s, out = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and not s["ok"]
    got = {(d["check"], d["segment"], f) for d in s["screen_flagged"] for f in d["flags"]}
    assert ("redaction", 4, "redaction_missing") in got, out
    assert any(c == "screen_time" and f == "screen_time_mismatch" for c, _, f in got), out


def test_layouts_overlays_doc(capsys, material):
    job = _job(capsys, material, "layouts-ov")
    code, s, out = run_tool(capsys, "overlays", "--no-render", "--job", str(job))
    assert code == 0, (s, out)
    doc = json.loads((job / "work" / "overlays.json").read_text())
    views = doc["views"]
    assert [v["layout"] for v in views] == ["screen", "pip", "split", "screen"]
    starts = _starts()
    assert [v["t0"] for v in views] == [round(starts[i] / 30, 6) for i in (1, 2, 3, 4)]
    pip, split = views[1], views[2]
    assert pip["screen"] == _seg_plan(2).view.box() and pip["speaker"] == _seg_plan(2).inset.box()
    assert split["speaker"][1] >= split["screen"][3]  # the speaker is below the screen view
    # Face samples: the camera face only in the face segment, the speaker area in pip/split.
    for f in doc["face"]:
        inside = [v for v in views if v["t0"] <= f["t"] < v["t1"]]
        if inside:
            assert inside[0]["layout"] != "screen" and f["box"] == inside[0]["speaker"], f


def test_landscape_views_keep_off_the_targets_not_the_whole_screen(capsys, material):
    job = _job(capsys, material, "layouts-ov-16x9", size=(1920, 1080))
    code, s, out = run_tool(capsys, "overlays", "--no-render", "--job", str(job))
    assert code == 0, (s, out)
    doc = json.loads((job / "work" / "overlays.json").read_text())
    views = {v["layout"]: v for v in doc["views"]}
    # The screen fills a 16:9 frame: no whole-screen block in screen/pip, only in split.
    assert views["screen"]["screen"] is None and views["pip"]["screen"] is None
    assert views["split"]["screen"] == _seg_plan(3, (1920, 1080)).view.box()
    pip = _seg_plan(2, (1920, 1080))
    (focus,) = views["pip"]["focus"]
    assert focus["box"] == [round(v) for v in pip.focus[0]]
    assert views["pip"]["t0"] < focus["t0"] and focus["t1"] == views["pip"]["t1"]
    assert views["pip"]["speaker"] == pip.inset.box()
    # verify reads the same areas: the targets, never the whole frame.
    blocks = _verify_screen.load_views(job)
    assert all(b["screen"] != [0, 0, 1920, 1080] for b in blocks)
    assert any(b["screen"] == focus["box"] for b in blocks)


def test_hdr_camera_in_pip_and_split_is_tonemapped(capsys, material, tmp_path):
    from test_render import _hlg_clip

    sdr, hlg = _hlg_clip(tmp_path)
    _, screen = material
    segs = [{"in": 0.2, "out": 1.0}, {"in": 1.0, "out": 2.0, "layout": "pip"},
            {"in": 2.0, "out": 2.9, "layout": "split"}]  # fmt: skip
    means = {}
    for name, cam in (("sdr", sdr), ("hlg", hlg)):
        code, s, _ = run_tool(capsys, "job", "new", str(cam), "--id", f"hdr-{name}")
        assert code == 0, s
        job = Path(s["job"])
        shutil.copy(screen, job / "input" / "screen.mp4")
        meta = json.loads((job / "job.json").read_text())
        meta |= {"screen": "input/screen.mp4", "format": "9:16"}
        (job / "job.json").write_text(json.dumps(meta))
        sync = {"offset_s": OFFSET, "method": "manual", "confidence": 1.0, "drift_ppm": None,
                "overlap": [0, 3]}  # fmt: skip
        (job / "work" / "sync.json").write_text(json.dumps(sync))
        edl = {"fps": 30, "size": [1080, 1920], "source": s["source"], "segments": segs}
        (job / "work" / "edl.json").write_text(json.dumps(edl))
        (job / "work" / "project.md").write_text("# Plan: hdr layout test\n")
        approve_job(job)
        code, s, out = run_tool(capsys, "render", "--job", str(job))
        assert code == 0, (s, out)
        assert s["hdr_tonemapped"] == (name == "hlg") and s["layouts"]["pip"] == 1
        reel = job / "out" / "reel.mp4"
        v = next(x for x in json.loads(subprocess.run(
            [clips.ffprobe(), "-v", "error", "-show_streams", "-of", "json", reel],
            capture_output=True, text=True, check=True).stdout)["streams"]
            if x["codec_type"] == "video")  # fmt: skip
        assert v["pix_fmt"] == "yuv420p"
        if name == "hlg":
            assert (v["color_transfer"], v["color_primaries"]) == ("bt709", "bt709")
        inset = lay.inset_rects(OUT_SIZE, lay.view_rect("pip", OUT_SIZE, (SW, SH)))[0]  # right
        means[name] = (
            _frames(reel, 15, 16, (1080, 1920, 0, 0))[0].mean(),  # face segment
            _frames(reel, 40, 41, (inset.w, inset.h, inset.x, inset.y))[0].mean(),  # pip inset
            _frames(reel, 70, 71, (1080, 700, 0, 1150))[0].mean(),  # split speaker part
        )
    assert 40 < means["sdr"][1] < 200, means  # the inset shows the camera, not the background
    for a, b in zip(means["sdr"], means["hlg"], strict=True):
        assert abs(a - b) < 45, means  # tonemapped, not washed out or crushed


def test_undecided_candidate_is_redacted_by_render_and_flagged(capsys, material):
    job = _job(capsys, material, "layouts-pd")
    # the red block, visible in segment 4 (screen, 5.5-7.0), listed but not in the EDL
    cand = {"id": 1, "kind": "customer", "t0": 5.5, "t1": 7.0, "box": SECRET_BOX,
            "reason": "test"}  # fmt: skip
    (job / "work" / "redaction.json").write_text(json.dumps({"candidates": [cand]}))
    code, s, out = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "redaction_missing" in out and "$.segments[4].redact" in out
    code, s, out = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, (s, out)
    assert s["redaction"] == {"source": "default", "candidates": [1]}
    code, s, out = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and not s["ok"]
    assert {"check": "decision", "id": 1, "where": "$.segments[4].redact",
            "flags": ["redaction_missing"]} in s["screen_flagged"]  # fmt: skip
    doc = json.loads((job / "out" / "verify.json").read_text())
    seg4 = [r for r in doc["screen"]["redaction"] if r["segment"] == 4]
    assert seg4 and all(x["bg_share"] >= 0.9 for r in seg4 for x in r["samples"])  # drawn
    # demo data by the human: nothing to redact, nothing missing
    run_tool(capsys, "job", "redaction", str(job), "--all-demo")
    code, s, out = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0, out


def test_landscape_layouts_render_and_readability(capsys, material):
    from videocut.tools import _verify_screen

    out = (1920, 1080)
    job = _job(capsys, material, "layouts-16x9", size=out)
    # The whole 2880 px screen in a 1728 px view: UI text ~15.6 px, flagged for the agent.
    code, s, text = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0, text
    small = [w for w in s["warnings"] if "screen_text_small" in w]
    assert small and all(w.startswith("segments[") for w in small)
    assert any(w.startswith("segments[1]: ") and "15.6 px" in w for w in small), small
    data = json.loads((job / "work" / "edl.json").read_text())
    ctx = _verify_screen.context(job, data, out)
    docs = _verify_screen.readability(ctx)
    assert {d["segment"] for d in docs} == {1, 2, 3, 4}
    assert any(d["flags"] == ["screen_text_small"] for d in docs)

    code, s, text = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, (s, text)
    reel = job / "out" / "reel.mp4"
    starts = _starts()

    def frame(i):
        mid = (starts[i] + starts[i + 1]) // 2
        return _frames(reel, mid, mid + 1, (out[0], out[1], 0, 0), "rgb24")[0]

    # screen: the 16:10 view fills the height, letterboxed at the sides in colors.bg.
    seg = _seg_plan(1, out)
    assert (seg.view.h, seg.view.w) == (1080, 1728)
    img = frame(1)
    assert np.abs(img[:, : seg.view.x - 2] - 244).max() < 6
    # pip: a round camera bubble over the screen, above the caption zone, in a bottom corner
    # that leaves the zoom target of segment 2 free.
    seg = _seg_plan(2, out)
    r = seg.inset
    assert r.y + r.h == out[1] - safe_zones(out)["bottom"] and seg.focus
    assert not any(lay.overlap(r.box(), f) for f in seg.focus)
    img = frame(2)
    bubble = img[r.y : r.y + r.h, r.x : r.x + r.w]
    assert bubble[r.h // 4 : -r.h // 4, r.w // 4 : -r.w // 4].std() > 20  # the camera
    # split: the screen in the left 64 %, the camera on the right.
    seg = _seg_plan(3, out)
    assert (
        seg.face.x == round(out[0] * lay.SPLIT_SIDE / 2) * 2 and seg.face.w + seg.face.x == out[0]
    )
    img = frame(3)
    assert img[:, seg.face.x + 4 :].std() > 20
    assert seg.view.x + seg.view.w <= seg.face.x
    # The screen checks of verify hold in 16:9 too (the bubble is left out of them).
    render = json.loads((job / "out" / "render.json").read_text())
    demo = _verify_screen.check(job, data, render, reel, out)
    assert "error" not in demo, demo
    assert not [d for d in demo["redaction"] + demo["screen_time"] if d["flags"]], demo
    assert demo["readability"] == docs


@pytest.mark.parametrize("fmt", ["1:1", "4:5"])
def test_square_and_4_5_layouts_render(capsys, material, fmt):
    out = next(size for size, f in FORMAT_OF.items() if f == fmt)
    job = _job(capsys, material, f"layouts-{fmt.replace(':', 'x')}", size=out)
    code, s, text = run_tool(capsys, "render", "--job", str(job), "--preview")
    assert code == 0, (s, text)
    prev = (out[0] // 4 * 2, out[1] // 4 * 2)
    assert s["size"] == list(prev)
    reel = job / "out" / "preview.mp4"
    starts = _starts()
    mid = (starts[2] + starts[3]) // 2
    seg = _seg_plan(2, prev, out)  # the preview: the plan of the EDL size at half size
    r, v = seg.inset, seg.view
    pip = _frames(reel, mid, mid + 1, (r.w, r.h, r.x, r.y))[0]
    assert pip[r.h // 4 : -r.h // 4, r.w // 4 : -r.w // 4].std() > 20  # the camera
    assert r.y + r.h <= prev[1] - safe_zones(prev)["bottom"]
    if fmt == "4:5":  # portrait: the inset sits below the view
        assert r.y >= v.y + v.h
    mid = (starts[3] + starts[4]) // 2  # split: stacked, the camera below the screen
    seg3 = _seg_plan(3, prev, out)
    y0 = seg3.face.y // 2 * 2 + 2
    part = _frames(reel, mid, mid + 1, (prev[0], (prev[1] - y0) // 2 * 2, 0, y0))
    assert part[0].std() > 20
