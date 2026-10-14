"""Measured redaction boxes and the coverage check of verify, end to end on generated
material: a camera clip and a 2880x1800 screen recording with a contact card (placeholder
person, example.com address, fictitious number) at known positions that scrolls up once.

The OCR tests need macOS Vision (tools/_ocr.py); the rest runs everywhere."""

import json
import shutil
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut import redaction as red
from videocut.tools import _ocr
from videocut.tools import redact as redact_tool
from videocut.tools.job import approve_job

OFFSET = 3.0  # screen time = camera time + OFFSET; the card scrolls at camera 4.3-5.1
NAME, EMAIL, PHONE = "Erika Mustermann", "erika.mustermann@example.com", "+49 30 1234567"
SW, SH = clips.SCREEN_SIZE
# the whole card (the "area" the plan names), before and after the scroll
CARD_AREA = [clips.CARD_XY[0] / SW, (clips.CARD_XY[1] - clips.CARD_SCROLL[2]) / SH,
             (clips.CARD_XY[0] + clips.CARD_SIZE[0]) / SW,
             (clips.CARD_XY[1] + clips.CARD_SIZE[1]) / SH]  # fmt: skip
SEGMENTS = {
    (1080, 1920): [
        {"in": 0.3, "out": 1.0},
        {"in": 1.0, "out": 3.5, "layout": "screen",
         "screen_zoom": [{"t": 2.0, "box": [0.3, 0.3, 0.7, 0.7]}]},
        {"in": 3.5, "out": 6.0, "layout": "pip",
         "screen_zoom": [{"t": 4.0, "box": [0.32, 0.2, 0.72, 0.6]}]},
        {"in": 6.0, "out": 7.0, "layout": "screen"},
    ],
    (1920, 1080): [
        {"in": 0.3, "out": 1.0},
        {"in": 1.0, "out": 3.5, "layout": "screen"},
        {"in": 3.5, "out": 6.0, "layout": "pip",
         "screen_zoom": [{"t": 4.0, "box": [0.25, 0.2, 0.75, 0.7]}]},
        {"in": 6.0, "out": 7.0, "layout": "split"},
    ],
}  # fmt: skip
FORMATS = {(1080, 1920): "9:16", (1920, 1080): "16:9"}

PIECE = {"t0": 0.0, "t1": 7.5, "box": [0.3, 0.4, 0.5, 0.45], "source": "ocr"}
ocr = pytest.mark.skipif(not _ocr.available(), reason="needs macOS Vision")


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def _inside(inner, outer, tol=1e-4) -> bool:
    return all(outer[k] <= inner[k] + tol for k in (0, 1)) and all(
        outer[k] >= inner[k] - tol for k in (2, 3)
    )


# ---------------------------------------------------------------- no media


def test_matches_tolerates_ocr_noise_but_not_other_text():
    assert _ocr.matches("Kontakt: Erika Mustermann", NAME) == [(9, 25)]
    assert _ocr.matches("ERIKA  MUSTERMAN", NAME)  # case, spacing, one missing letter
    assert _ocr.matches("Zuletzt bearbeitet am Montag", NAME) == []
    assert _ocr.matches("+49 (30) 123 4567", PHONE)  # punctuation is folded away
    assert _ocr.matches("+49 30 7654321", PHONE) == []
    assert _ocr.matches("Tel 1234", "1234") and not _ocr.matches("Tel 1235", "1234")


def _scroll_look(t: float) -> list[list[float]]:
    """A fake text 0.02 high that is shown 1-9 s and moves up by 0.1 between 4 and 5 s."""
    if not 1.0 <= t <= 9.0:
        return []
    y = 0.5 - 0.1 * min(max(t - 4.0, 0.0), 1.0)
    return [[0.3, y, 0.5, y + 0.02]]


def test_tracker_follows_a_moving_text_and_covers_it_at_every_time():
    tr = redact_tool.Tracker(_scroll_look, 12.0, (2880, 1800))
    tr.run((2.0, 8.0), seeds=[3.0])
    pieces = tr.pieces((2.0, 8.0), [0.2, 0.3, 0.6, 0.6])
    assert all(p["source"] == "ocr" for p in pieces), pieces
    for k in range(0, 1201):
        t = k / 100
        truth = _scroll_look(t)
        live = [p for p in pieces if p["t0"] - 1e-6 <= t <= p["t1"] + 1e-6]
        for b in truth:  # every shown position lies inside a drawn box at that time
            assert any(_inside(b, p["box"]) for p in live), (t, b, live)
    # sightings outside the plan's range were followed too (1-9 s), to MIN_DT
    assert min(p["t0"] for p in pieces) <= 1.0 and max(p["t1"] for p in pieces) >= 9.0
    assert min(p["t0"] for p in pieces) >= 1.0 - redact_tool.MEASURE_STEP


def test_tracker_fails_closed_to_the_area_where_the_text_is_not_found():
    tr = redact_tool.Tracker(lambda t: [], 10.0, (2880, 1800))
    tr.run((2.0, 6.0), seeds=[])
    pieces = tr.pieces((2.0, 6.0), [0.2, 0.3, 0.6, 0.6])
    assert pieces == [{"t0": 2.0, "t1": 6.0, "source": "area",
                       "box": redact_tool._pad_area([0.2, 0.3, 0.6, 0.6])}]  # fmt: skip
    assert redact_tool._status(pieces, cam=True)[0] == "failed"


def test_track_validation_and_pieces():
    track = [
        {"t0": 1.0, "t1": 3.0, "box": [0.2, 0.2, 0.3, 0.25],
         "text_box": [0.21, 0.21, 0.29, 0.24], "source": "ocr"},
        {"t0": 3.0, "t1": 5.0, "box": [0.1, 0.1, 0.5, 0.5], "source": "area"},
    ]  # fmt: skip
    c = {"id": 1, "kind": "name", "t0": 1.0, "t1": 5.0, "box": [0.1, 0.1, 0.5, 0.5],
         "reason": "r", "text": NAME, "range": [1.0, 5.0], "measured": "ok", "note": "",
         "track": track}  # fmt: skip
    assert red.validate({"candidates": [c]}) == []
    assert red.pieces(c) == [{"t0": 1.0, "t1": 3.0, "box": [0.2, 0.2, 0.3, 0.25]},
                             {"t0": 3.0, "t1": 5.0, "box": [0.1, 0.1, 0.5, 0.5]}]  # fmt: skip
    assert red.screen_blocks([c], 2.0, 4.0) == [
        {"t0": 2.0, "t1": 3.0, "box": [0.2, 0.2, 0.3, 0.25]},
        {"t0": 3.0, "t1": 4.0, "box": [0.1, 0.1, 0.5, 0.5]},
    ]
    bad = {**c, "measured": "maybe", "track": [{"t0": 2, "t1": 1, "box": [0, 0, 1, 1],
                                                "source": "eye"}]}  # fmt: skip
    errs = red.validate({"candidates": [bad]})
    assert any(".measured" in e for e in errs) and any(".source" in e for e in errs)
    assert any("t0 < t1" in e for e in errs)


# ---------------------------------------------------------------- media


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    if not clips.available() or not clips.font():
        pytest.skip("needs ffmpeg, the say voice and a system font")
    folder = tmp_path_factory.mktemp("redact-clips")
    camera = clips.build(folder, "landscape")
    screen, meta = clips.contact_screen(folder, clips.duration(camera) + OFFSET + 1)
    return camera, screen, meta


def _cands() -> list[dict]:
    return [
        {"id": 1, "kind": "name", "t0": 0.0, "t1": 7.5, "box": CARD_AREA, "text": NAME,
         "reason": "name on the contact card"},
        {"id": 2, "kind": "email", "t0": 0.0, "t1": 7.5, "box": CARD_AREA, "text": EMAIL,
         "reason": "e-mail on the contact card"},
        {"id": 3, "kind": "phone", "t0": 0.0, "t1": 7.5, "box": CARD_AREA, "text": PHONE,
         "reason": "phone number on the contact card"},
    ]  # fmt: skip


def _job(capsys, material, job_id, size=(1080, 1920)) -> Path:
    camera, screen, _ = material
    code, s, _ = run_tool(capsys, "job", "new", str(camera), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    shutil.copy(screen, job / "input" / "screen.mp4")
    meta = json.loads((job / "job.json").read_text())
    meta |= {"screen": "input/screen.mp4", "format": FORMATS[size]}
    (job / "job.json").write_text(json.dumps(meta))
    sync = {"offset_s": OFFSET, "method": "manual", "confidence": 1.0, "drift_ppm": None,
            "overlap": [0, 8]}  # fmt: skip
    (job / "work" / "sync.json").write_text(json.dumps(sync))
    (job / "work" / "redaction.json").write_text(json.dumps({"candidates": _cands()}))
    (job / "work" / "project.md").write_text("# Plan: redaction test\n")
    edl = {"fps": 30, "size": list(size), "source": s["source"], "segments": SEGMENTS[size]}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    return job


def _cut(job: Path) -> None:
    """Write the redact blocks the EDL needs (what the cut agent does), then approve."""
    data = json.loads((job / "work" / "edl.json").read_text())
    cands = red.load(job)
    for g in red.missing(cands, data, job):
        i = int(g["where"].split("[")[1].split("]")[0])
        data["segments"][i].setdefault("redact", []).append(g["need"])
    (job / "work" / "edl.json").write_text(json.dumps(data))
    approve_job(job)


@pytest.fixture(scope="module")
def measured(material, tmp_path_factory):
    """One measured job folder, shared: (job, summary)."""
    import io
    from contextlib import redirect_stdout

    from videocut.cli import main

    home = tmp_path_factory.mktemp("home")
    mp = pytest.MonkeyPatch()
    mp.setenv("VIDEOCUT_HOME", str(home))
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert main(["tool", "job", "new", str(material[0]), "--id", "measured"]) == 0
        job = home / "jobs" / "measured"
        assert job.is_dir(), buf.getvalue()
        shutil.copy(material[1], job / "input" / "screen.mp4")
        meta = json.loads((job / "job.json").read_text())
        (job / "job.json").write_text(json.dumps({**meta, "screen": "input/screen.mp4"}))
        sync = {"offset_s": OFFSET, "method": "manual", "confidence": 1.0, "drift_ppm": None,
                "overlap": [0, 8]}  # fmt: skip
        (job / "work" / "sync.json").write_text(json.dumps(sync))
        (job / "work" / "redaction.json").write_text(json.dumps({"candidates": _cands()}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["tool", "redact", "measure", "--job", str(job)])
        assert code == 0, buf.getvalue()
        summary = json.loads(buf.getvalue().strip().splitlines()[-1])
        yield job, summary
    finally:
        mp.undo()


@ocr
def test_screen_text_lists_the_card_and_suggests_its_data(capsys, material, tmp_path,
                                                          videocut_home):  # fmt: skip
    job = _job(capsys, material, "text")
    code, s, out = run_tool(capsys, "redact", "text", "--job", str(job))
    assert code == 0, out
    texts = {e["text"] for e in s["texts"]}
    assert {NAME, EMAIL, PHONE, "Kontakt"} <= texts, texts
    kinds = {(h["kind"], h["text"]) for h in s["suggest"]}
    assert ("email", EMAIL) in kinds and ("phone", PHONE) in kinds, kinds
    doc = json.loads((job / redact_tool.FILE).read_text())
    line = next(ln for ln in doc["lines"] if ln["text"] == PHONE and ln["t"] == 0.0)
    assert line["camera_t"] == -OFFSET
    # a line box (with Vision's line spacing) around the known text
    assert _inside(clips.box_at(material[2], "phone", 0.0), line["box"], tol=0.002)


@ocr
def test_measured_boxes_match_the_known_positions(material, measured):
    job, summary = measured
    assert [c["measured"] for c in summary["candidates"]] == ["ok", "ok", "ok"], summary
    meta = material[2]
    samples = json.loads((job / redact_tool.SAMPLES_FILE).read_text())
    s0, dt, _ = meta["scroll"]
    ious = []
    for cid, key in (("1", "name"), ("2", "email"), ("3", "phone")):
        for smp in samples[cid]:
            t = round(smp["t"] * 30) / 30  # the frame shown
            if s0 - 0.1 <= t <= s0 + dt + 0.1 or not smp["boxes"]:
                continue  # moving: the frame time is not exact enough for an IoU
            truth = clips.box_at(meta, key, t)
            ious.append(max(_iou(b, truth) for b in smp["boxes"]))
    assert len(ious) > 20 and min(ious) > 0.8, sorted(ious)[:5]
    # The drawn track covers the known text at every frame, also while it scrolls.
    cands = {c["id"]: c for c in red.load(job)}
    for cid, key in ((1, "name"), (2, "email"), (3, "phone")):
        c = cands[cid]
        assert c["t0"] <= 0.0 and c["t1"] >= 7.5 and all(p["source"] == "ocr" for p in c["track"])
        for n in range(0, 8 * 30):
            cam_t = n / 30
            truth = clips.box_at(meta, key, cam_t + OFFSET)
            live = [p for p in c["track"] if p["t0"] - 1e-3 <= cam_t <= p["t1"] + 1e-3]
            assert any(_inside(truth, p["box"]) for p in live), (cid, cam_t, truth, live)
    # the plan's area stays the candidate's box; the measured boxes are a single line
    name = cands[1]
    assert name["box"] == CARD_AREA and name["range"] == [0.0, 7.5]
    assert max(p["box"][3] - p["box"][1] for p in name["track"]) < 0.1


@ocr
def test_text_that_is_not_found_covers_the_whole_area(capsys, material, videocut_home):
    job = _job(capsys, material, "fail-closed")
    cand = {"id": 1, "kind": "name", "t0": 1.0, "t1": 3.0, "box": CARD_AREA,
            "text": "Max Beispielname", "reason": "a name that is not shown"}  # fmt: skip
    (job / "work" / "redaction.json").write_text(json.dumps({"candidates": [cand]}))
    code, s, out = run_tool(capsys, "redact", "measure", "--job", str(job))
    assert code == 0, out
    c = red.load(job)[0]
    assert c["measured"] == "failed" and "whole marked area" in c["note"]
    area = redact_tool._r(redact_tool._pad_area(CARD_AREA))
    assert c["track"] == [{"t0": 1.0, "t1": 3.0, "box": area, "source": "area"}]


def _render_verify(capsys, job: Path) -> tuple[dict, dict]:
    code, s, out = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, (s, out)
    code, s, out = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, out
    return s, json.loads((job / "out" / "verify.json").read_text())


@ocr
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_rendered_reel_has_no_leak(capsys, material, measured, size):
    job = _job(capsys, material, f"no-leak-{size[0]}")
    # the measured candidates (measured once, shared)
    shutil.copy(measured[0] / red.FILE, job / red.FILE)
    _cut(job)
    s, doc = _render_verify(capsys, job)
    assert s["screen_flagged"] == [], s["screen_flagged"]
    cov = doc["screen"]["coverage"]
    checked = [d for d in cov if d["source"] == "screen_ocr"]
    assert {d["id"] for d in checked} == {1, 2, 3} and all(d["share"] >= 0.98 for d in checked)
    assert {d["segment"] for d in checked} == {1, 2, 3}
    assert not [d for d in cov if d["source"] == "reel_ocr"]


@ocr
def test_a_box_one_line_too_high_fails_verify(capsys, material, measured):
    """The bug this guards: boxes estimated by eye landed a text line above the name."""
    job = _job(capsys, material, "shifted")
    data = json.loads((measured[0] / red.FILE).read_text())
    for c in data["candidates"]:
        if c["id"] == 1:
            line = 80 / SH  # the card's line spacing
            for p in c["track"]:
                for k in ("box", "text_box"):
                    p[k] = [p[k][0], p[k][1] - line, p[k][2], p[k][3] - line]
    data["candidates"] = [c for c in data["candidates"] if c["id"] == 1]
    (job / red.FILE).write_text(json.dumps(data))
    _cut(job)
    s, doc = _render_verify(capsys, job)
    assert not s["ok"]
    leaks = [d for d in s["screen_flagged"] if "redaction_leak" in d["flags"]]
    assert leaks and {d["id"] for d in leaks} == {1}, s["screen_flagged"]
    assert all("t" in d and "where" in d for d in leaks)
    assert any(d["source"] == "reel_ocr" for d in doc["screen"]["coverage"])


@ocr
def test_screenshot_text_is_listed_and_measured(capsys, material, tmp_path, videocut_home):
    camera, _, meta = material
    card = tmp_path / "card.png"
    clips._card_png(tmp_path, card, [k for k, *_ in clips.CARD_FIELDS])
    code, s, out = run_tool(capsys, "job", "new", str(camera), "--asset", str(card),
                            "--id", "shot")  # fmt: skip
    assert code == 0, out
    job = Path(s["job"])
    asset = json.loads((job / "job.json").read_text())["assets"][0]
    code, s, out = run_tool(capsys, "redact", "text", "--job", str(job))
    assert code == 0, out
    assert {ln["text"] for ln in s["asset_texts"]} >= {NAME, EMAIL, PHONE}
    assert ("email", EMAIL) in {(h["kind"], h["text"]) for h in s["suggest"]}
    cand = {"id": 1, "kind": "phone", "asset": asset, "box": [0.0, 0.0, 1.0, 1.0],
            "text": PHONE, "reason": "phone on the card"}  # fmt: skip
    (job / red.FILE).write_text(json.dumps({"candidates": [cand]}))
    code, s, out = run_tool(capsys, "redact", "measure", "--job", str(job))
    assert code == 0, out
    c = red.load(job)[0]
    (x0, y0, x1, y1), (cx, cy), (cw, ch) = meta["fields"]["phone"], clips.CARD_XY, clips.CARD_SIZE
    truth = [(x0 - cx) / cw, (y0 - cy) / ch, (x1 - cx) / cw, (y1 - cy) / ch]
    assert c["measured"] == "ok" and len(c["track"]) == 1
    assert _iou(c["track"][0]["text_box"], truth) > 0.8
    assert _inside(truth, red.asset_boxes(c)[0])


def test_plan_check_wants_text_candidates_measured():
    from videocut.tools import plan as plan_tool

    plan = {"title": "t", "format": "9:16", "hook": {"text": "h"},
            "keep_list": [{"n": 1, "src": [0, 1], "text": "x", "why": "y"}]}  # fmt: skip
    cands = _cands()
    cands[2].pop("text")
    for c in cands:
        plan.setdefault("personal_data", []).append(c)
    errs = plan_tool.validate(plan, candidates=cands)
    assert any("candidate 1: not measured" in e for e in errs), errs
    assert any('candidate 3: needs "text"' in e for e in errs), errs
    other = {"id": 4, "kind": "other", "t0": 0.0, "t1": 1.0, "box": [0, 0, 0.2, 0.2],
             "reason": "a face"}  # fmt: skip
    done = []
    for c in _cands():
        c |= {"measured": "ok", "range": [c["t0"], c["t1"]], "track": [dict(PIECE)]}
        done.append({**c, "measure_sig": red.measure_sig(c)})
    plan["personal_data"] = [*done, other]
    assert plan_tool.validate(plan, candidates=[*done, other]) == []
    # a measurement does not carry over to another text or area
    edited = [{**done[0], "text": "Max Beispielname"}, *done[1:], other]
    plan["personal_data"] = edited
    errs = plan_tool.validate(plan, candidates=edited)
    assert errs == [errs[0]] and "candidate 1: not measured (or changed since)" in errs[0]


@ocr
def test_plan_stills_show_the_measured_boxes(capsys, measured):
    job = measured[0]
    cands = red.load(job)
    meta = json.loads((job / "job.json").read_text())
    plan = {"title": "t", "format": "16:9", "hook": {"text": "h"},
            "keep_list": [{"n": 1, "src": [0, 1], "text": "x", "why": "y"}],
            "personal_data": cands}  # fmt: skip
    (job / "job.json").write_text(json.dumps({**meta, "format": "16:9"}))
    (job / "work" / "plan.json").write_text(json.dumps(plan))
    code, s, out = run_tool(capsys, "sheet", "plan", "--job", str(job))
    assert code == 0, out
    pd = json.loads((job / "work" / "plan.json").read_text())["personal_data"]
    for c, got in zip(cands, pd, strict=True):
        live = [p["box"] for p in c["track"] if p["t0"] <= got["still_t"] <= p["t1"]]
        assert got["still_boxes"] == live and got["still"].endswith(f"pd_{c['id']:02d}.jpg")
        assert all(b[3] - b[1] < 0.1 for b in got["still_boxes"])  # a line, not the area


def test_grab_takes_the_frame_shown_on_a_variable_frame_rate_recording(tmp_path):
    """macOS writes a frame only when the screen changes: the frame shown at t is the last
    one before t, not the next one (what a plain seek returns)."""
    if clips.ffmpeg() is None:
        pytest.skip("needs ffmpeg")
    for name, colour in (("a", "red"), ("b", "blue")):
        clips._ff("-f", "lavfi", "-i", f"color=c={colour}:s=64x48:d=1", "-frames:v", "1",
                  str(tmp_path / f"{name}.png"))  # fmt: skip
    (tmp_path / "list.txt").write_text(
        "file a.png\nduration 5\nfile b.png\nduration 3\nfile b.png\n"
    )
    vfr = tmp_path / "vfr.mp4"
    clips._ff("-f", "concat", "-i", str(tmp_path / "list.txt"), "-fps_mode", "vfr",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vfr))  # fmt: skip
    info = {"duration": 8.0, "size": (64, 48)}
    red_at = {t: redact_tool.grab_screen(vfr, info, t)[24, 32, 0] > 200 for t in (1, 4.9, 5.0, 7)}
    assert red_at == {1: True, 4.9: True, 5.0: False, 7: False}


def test_a_sighting_outside_the_named_area_does_not_count_as_covering_it():
    """The text is shown twice; recognition only finds the copy outside the named area
    between 3 and 5 s: the whole area is covered there."""

    def look(t):
        title = [[0.05, 0.02, 0.25, 0.04]]
        return title if 3.0 <= t <= 5.0 else [*title, [0.6, 0.5, 0.8, 0.52]]

    tr = redact_tool.Tracker(look, 8.0, (2880, 1800))
    tr.run((1.0, 7.0), seeds=[])
    pieces = tr.pieces((1.0, 7.0), [0.55, 0.45, 0.9, 0.6])
    area = [p for p in pieces if p["source"] == "area"]
    assert area and min(p["t0"] for p in area) <= 3.0 and max(p["t1"] for p in area) >= 5.0
    assert all(p["t0"] >= 2.9 and p["t1"] <= 5.1 for p in area), area
    assert redact_tool._status(pieces, cam=True)[0] == "partial"


def test_verify_does_not_trust_unmeasured_or_edited_candidates():
    from videocut.tools import _verify_screen as vs

    c = {**_cands()[0], "range": [0.0, 7.5], "measured": "ok", "track": [dict(PIECE)]}
    c["measure_sig"] = red.measure_sig(c)
    plain = {"id": 2, "kind": "phone", "t0": 0.0, "t1": 7.5, "box": CARD_AREA, "reason": "r"}
    face = {"id": 3, "kind": "other", "t0": 0.0, "t1": 7.5, "box": CARD_AREA, "reason": "r"}
    assert vs.unchecked([c, face]) == []
    got = vs.unchecked([{**c, "text": "Max Beispielname"}, plain])
    assert [(d["id"], d["flags"]) for d in got] == [(1, ["redaction_unchecked"]),
                                                    (2, ["redaction_unchecked"])]  # fmt: skip


@ocr
def test_screenshot_box_off_the_text_is_a_leak(capsys, material, tmp_path, videocut_home):
    from videocut.tools import _verify_screen as vs

    card = tmp_path / "card.png"
    clips._card_png(tmp_path, card, [k for k, *_ in clips.CARD_FIELDS])
    code, s, out = run_tool(capsys, "job", "new", str(material[0]), "--asset", str(card),
                            "--id", "shot-leak")  # fmt: skip
    assert code == 0, out
    job = Path(s["job"])
    asset = json.loads((job / "job.json").read_text())["assets"][0]
    cand = {"id": 1, "kind": "name", "asset": asset, "box": [0.0, 0.0, 1.0, 1.0],
            "text": NAME, "reason": "name on the card"}  # fmt: skip
    (job / red.FILE).write_text(json.dumps({"candidates": [cand]}))
    assert run_tool(capsys, "redact", "measure", "--job", str(job))[0] == 0
    c = red.load(job)[0]
    item = {"scene": "screenshot_frame", "t0": 1, "t1": 3, "props": {"file": asset}}
    docs = vs.asset_coverage(job, {"overlays": [item]}, [c])
    assert docs and not [d for d in docs if d["flags"]], docs
    line = 80 / clips.CARD_SIZE[1]
    for p in c["track"]:  # one line too high, as estimated by eye
        p["box"] = [p["box"][0], p["box"][1] - line, p["box"][2], p["box"][3] - line]
    docs = vs.asset_coverage(job, {"overlays": [item]}, [c])
    assert any(d["flags"] == ["redaction_leak"] and d["overlay"] == 0 for d in docs), docs
