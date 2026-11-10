"""Motion scenes (tools/motion.py, _job_motion.py, _verify_motion.py): plan moments, spans
from the approved plan only, the band in overlays, the render record and the composite."""

import json
import subprocess
import sys
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut.tools import _job_motion, motion, overlays
from videocut.tools import face as face_mod
from videocut.tools.common import ToolError

WORDS = [
    {"w": "eins", "t0": 0.2, "t1": 0.6},
    {"w": "zwei", "t0": 0.7, "t1": 1.2},
    {"w": "drei", "t0": 2.1, "t1": 2.5},
    {"w": "vier", "t0": 2.6, "t1": 3.2},
    {"w": "fünf", "t0": 3.6, "t1": 3.9},
]
M1 = {
    "id": "m1", "src": [0.2, 3.3], "components": ["checklist"], "shows": "four items ticking",
    "beats": {"b1": {"word": "zwei"}, "b2": {"word": "vier", "edge": "end"}}, "why": "list",
}  # fmt: skip
M2 = {
    "id": "m2", "src": [3.6, 3.9], "components": ["counter"], "shows": "the five",
    "beats": {"f": {"word": "fünf"}}, "why": "number",
}  # fmt: skip

# Stand-in for renderer/motion.js (its CLI contract): <motion.json> --region-only --out <mov>
# --events <json>; one JSON summary line last.
FAKE_MOTION = """
import json, sys
a = sys.argv[1:]
spec = json.load(open(a[0]))
out = a[a.index("--out") + 1]
open(out, "wb").write(b"clip" + json.dumps(spec["beats"]).encode())
ev = a[a.index("--events") + 1]
json.dump([{"t": spec["offset"] + 0.5, "kind": "pop", "component": "checklist", "strength": 1}],
          open(ev, "w"))
print(json.dumps({"ok": True, "frames": 10, "out_size": [1080, 500], "place_at": [0, 184],
                  "seconds": 0.1}))
"""


# --- units --------------------------------------------------------------------------------


def test_resolve_beats_occurrence_edge_offset_and_phrases():
    ws = [{"w": "Ja,", "t0": 0.0, "t1": 0.2}, {"w": "drei", "t0": 0.3, "t1": 0.6},
          {"w": "Jahre", "t0": 0.6, "t1": 0.9}, {"w": "drei", "t0": 1.0, "t1": 1.4}]  # fmt: skip
    got = motion.resolve_beats(
        {"a": {"word": "drei", "n": 2}, "b": {"word": "drei jahre", "edge": "end"},
         "c": {"word": "ja", "offset": -0.1}}, ws,
    )  # fmt: skip
    assert got == {"a": 1.0, "b": 0.9, "c": -0.1}
    with pytest.raises(ToolError, match="not spoken"):
        motion.resolve_beats({"x": {"word": "vier"}}, ws)


def test_plan_errors():
    keep = [[0.0, 1.5], [2.0, 3.5]]
    assert motion.plan_errors({"motion": [M1]}, "auto", WORDS, keep) == []
    assert motion.plan_errors({"motion": [M1]}, "off", WORDS, keep) == [
        "$.motion: the job's motion setting is off; no motion moments"
    ]
    bad = {**M1, "id": "x", "components": ["nope"], "beats": {"b": {"word": "elf", "edge": "x"}}}
    errs = motion.plan_errors({"motion": [bad]}, "auto", WORDS, keep)
    assert any(".id" in e for e in errs) and any("unknown component" in e for e in errs)
    assert any("edge" in e for e in errs)
    errs = motion.plan_errors({"motion": [{**M1, "beats": {"b": {"word": "elf"}}}]}, "auto",
                              WORDS, keep)  # fmt: skip
    assert errs == ['$.motion[0].beats.b: beat "b": "elf" (#1) is not spoken there']
    errs = motion.plan_errors({"motion": [M1, {**M1, "id": "m2", "src": [3.0, 3.4]}]}, "auto",
                              WORDS, keep)  # fmt: skip
    assert any("overlaps" in e for e in errs)
    errs = motion.plan_errors({"motion": [{**M1, "src": [1.6, 3.0]}]}, "auto", None, keep)
    assert errs == ["$.motion[0].src: must lie inside the keep rows"]
    assert motion.plan_errors({"motion": [{**M1, "id": "m1"}, {**M2, "id": "m1"}]}, None, None,
                              None)  # fmt: skip


def test_caption_track_and_views():
    track = [{"t": 0.5, "box": [0, 100, 100, 200]}, {"t": 5.0, "box": [0, 100, 100, 200]}]
    spans = [{"id": "m1", "t0": 0.0, "t1": 1.0, "region": [65, 250, 1015, 600]}]
    out = motion.caption_track(track, spans)
    assert out[0]["box"] == [0, 100, 100, 172] and out[1]["box"] == [0, 100, 100, 200]
    assert motion.views(spans) == [
        {"t0": 0.0, "t1": 1.0, "screen": [65, 250, 1015, 600], "layout": "motion", "motion": "m1"}
    ]


def test_composite_graph_places_each_clip_on_the_frame_grid():
    clips_ = [{"t0": 1.0, "size": [1080, 500], "place_at": [0, 184]},
              {"t0": 4.5, "size": [1080, 520], "place_at": [0, 184]}]  # fmt: skip
    text, out = motion.composite_graph(clips_, 3, 30, 0.5, "[vo]")
    assert out == "[mo1]"
    assert "[3:v]setpts=PTS-STARTPTS,fps=30,settb=1/30,setpts=N+30," in text
    assert "setpts=N+135," in text and "scale=540:250" in text
    assert "[vo]settb=1/30" in text and "[mb1][mc1]overlay=0:92" in text


def test_job_motion_setting_is_human_only_and_makes_the_plan_stale(capsys, tmp_path, monkeypatch):
    job = tmp_path / "j"
    (job / "work").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"id": "j"}))
    assert _job_motion.view(job) == {"mode": "off", "wish": "", "stale": False}
    (job / "work" / "project.md").write_text("# plan")
    res = _job_motion.set_motion(job, wish="Zahlen als Zähler", say=lambda _: None)
    assert res["mode"] == "wish" and res["plan_stale"]  # a wish alone switches off → wish
    _job_motion.stamp_plan(job)
    assert not _job_motion.plan_stale(job)
    with pytest.raises(ToolError, match="unknown motion mode"):
        _job_motion.parse_mode("loud")
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "job", "motion", str(job), "--mode", "off")
    assert code == 1 and "only a human" in s["error"]


# --- a job with an EDL ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    if clips.ffmpeg() is None:
        pytest.skip("needs ffmpeg")
    path = tmp_path_factory.mktemp("src") / "take.mp4"
    subprocess.run(
        [
            clips.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=f=300:d=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture
def fake_motion(tmp_path, monkeypatch):
    script = tmp_path / "fake_motion.py"
    script.write_text(FAKE_MOTION)
    monkeypatch.setattr(motion, "renderer_cmd", lambda: [sys.executable, str(script)])
    return script


def _job(capsys, source, moments=(M1, M2), approve=True) -> Path:
    code, s, _ = run_tool(capsys, "job", "new", str(source), "--id", "mo1")
    assert code == 0, s
    job = Path(s["job"])
    w = job / "work"
    (w / "words.json").write_text(json.dumps({"words": WORDS}))
    edl = {"fps": 30, "size": [1080, 1920], "source": s["source"],
           "segments": [{"in": 0.0, "out": 1.5}, {"in": 2.0, "out": 3.5}]}  # fmt: skip
    (w / "edl.json").write_text(json.dumps(edl))
    frames = [
        {"t": t / 10, "found": True, "box": [0.45, 0.3, 0.55, 0.5], "cx": 0.5, "cy": 0.4,
         "blink": 0.1, "gaze_off": 0.1}
        for t in range(40)
    ]  # fmt: skip
    (w / "face.json").write_text(json.dumps({
        "version": face_mod.FACE_VERSION, "source": s["source"], "fps_sampled": 10,
        "size": [480, 270], "frames": frames, "median": face_mod.median_of(frames), "blinks": [],
    }))  # fmt: skip
    (w / "project.md").write_text("# Plan: t\n")
    plan = {"title": "t", "format": "9:16", "hook": {"text": "eins"},
            "keep_list": [{"n": 1, "src": [0.0, 1.5], "text": "a", "why": "b"},
                          {"n": 2, "src": [2.0, 4.0], "text": "c", "why": "d"}],
            "motion": list(moments)}  # fmt: skip
    (w / "plan.json").write_text(json.dumps(plan))
    _job_motion.set_motion(job, "auto", say=lambda _: None)
    _job_motion.stamp_plan(job)
    if approve:
        code, s, _ = run_tool(capsys, "job", "approve", str(job))
        assert code == 0, s
    return job


def test_spans_come_from_the_approved_plan_in_output_time(capsys, source):
    job = _job(capsys, source)
    code, s, _ = run_tool(capsys, "motion", "spans", "--job", str(job))
    assert code == 0, s
    (sp,) = s["spans"]
    assert sp["id"] == "m1" and sp["t0"] == 0.05 and sp["t1"] == 3.0  # clipped to the reel
    assert sp["beat_times"] == {"b1": 0.65, "b2": 2.65}  # scene seconds (output 0.7, 2.7)
    assert sp["words"] == "eins zwei drei vier"
    x0, y0, x1, y1 = sp["region"]
    assert (x0, y0, x1) == (65, 250, 1015) and y1 > y0
    assert s["dropped"] == [{"id": "m2", "why": "its words were cut"}]
    on_disk = json.loads((job / "work/motion/spans.json").read_text())
    assert on_disk == [{k: sp[k] for k in ("id", "t0", "t1", "region", "scene")}
                       | {"beats": M1["beats"]}]  # fmt: skip
    spec = json.loads((job / "work/motion/m1/motion.json").read_text())
    assert spec["offset"] == 0.05 and spec["duration"] == 2.95 and spec["tokens"]["name"]


def test_no_spans_without_approval_or_after_the_plan_changed(capsys, source):
    job = _job(capsys, source, approve=False)
    assert motion.build_spans(job)[0] == []
    code, s, _ = run_tool(capsys, "job", "approve", str(job))
    assert code == 0
    assert [x["id"] for x in motion.build_spans(job)[0]] == ["m1"]
    plan = json.loads((job / "work/plan.json").read_text())
    plan["motion"][0]["beats"]["b3"] = {"word": "eins"}  # an agent adding a beat afterwards
    (job / "work/plan.json").write_text(json.dumps(plan))
    spans, _, meta = motion.build_spans(job)
    assert spans == [] and "not approved" in meta["why"]


def test_overlays_keep_captions_and_items_off_the_band(capsys, source, monkeypatch, tmp_path):
    job = _job(capsys, source)
    edl = json.loads((job / "work/edl.json").read_text())
    edl["overlays"] = [{"scene": "keyword", "t0": 0.5, "t1": 2.0, "props": {"text": "x"}}]
    (job / "work/edl.json").write_text(json.dumps(edl))
    doc = overlays.build(job)
    (v,) = [v for v in doc["views"] if v.get("layout") == "motion"]
    assert v["motion"] == "m1" and v["t0"] == 0.05 and v["t1"] == 3.0
    assert doc["motion"] == [{"id": "m1", "t0": 0.05, "t1": 3.0, "region": v["screen"]}]
    assert doc["motion_dropped"] == [{"id": "m2", "why": "its words were cut"}]
    assert doc["overlays"] == []  # the card lay inside the band's span: dropped
    inside = [f for f in doc["face"] if 0.05 <= f["t"] <= 3.0]
    raw = motion.face_track(*_plan_and_face(job))
    assert inside and all(
        f["box"][3] < r["box"][3] for f, r in zip(inside, raw, strict=False) if r["t"] == f["t"]
    )


def _plan_and_face(job):
    edl, plan, size, face = motion._edl_ctx(job)
    return plan, face, size


def test_render_records_clips_and_detects_tampering(capsys, source, fake_motion, videocut_home):
    job = _job(capsys, source)
    scene = job / "work/motion/m1/scene.js"
    scene.parent.mkdir(parents=True, exist_ok=True)
    code, s, _ = run_tool(capsys, "motion", "render", "--job", str(job))
    assert code == 0 and s["no_scene"] == ["m1"] and s["rendered"] == []
    scene.write_text("export default function scene(vc) {}\n")
    code, s, _ = run_tool(capsys, "motion", "render", "--job", str(job))
    assert code == 0 and s["rendered"] == ["m1"], s
    (c,) = s["clips"]
    assert c["place_at"] == [0, 184] and c["size"] == [1080, 500]
    ev = json.loads((job / "work/motion/m1/events.json").read_text())
    assert ev == [{"t": 0.55, "kind": "pop", "component": "checklist", "strength": 1}]
    rec = motion.record_path(job)
    assert rec.is_relative_to(videocut_home / "state") and not rec.is_relative_to(job)
    code, s, _ = run_tool(capsys, "motion", "render", "--job", str(job))
    assert s["rendered"] == [] and s["kept"] == ["m1"]
    code, s, _ = run_tool(capsys, "motion", "show", "--job", str(job))
    assert s["spans"][0]["status"] == "ok"
    (job / "work/motion/m1/events.json").write_text("[]")  # an agent editing the cues
    assert run_tool(capsys, "motion", "show", "--job", str(job))[1]["spans"][0]["status"] == (
        "untrusted"
    )
    run_tool(capsys, "motion", "render", "--job", str(job))
    scene.write_text("export default function scene(vc) { vc.x = 1; }\n")
    assert run_tool(capsys, "motion", "show", "--job", str(job))[1]["spans"][0]["status"] == "stale"


def test_verify_flags_missing_scene_and_dropped_moment(capsys, source):
    from videocut.tools import _verify_motion

    job = _job(capsys, source)
    res = _verify_motion.check(job, {}, [], [], [])
    assert {(f["id"], tuple(f["flags"])) for f in res["flagged"]} == {
        ("m2", ("motion_dropped",)),
        ("m1", ("motion_missing",)),
    }


def test_repeat_tokens():
    from videocut.tools._verify_motion import tokens

    assert tokens("Drei Jahre, 3 % hier!") == {"drei", "jahre", "hier"}


def test_plan_labels_are_short_texts():
    keep = [[0.0, 1.5], [2.0, 3.5]]
    ok = {**M1, "labels": ["Schritt", "Ergebnis"]}
    assert motion.plan_errors({"motion": [ok]}, "auto", WORDS, keep) == []
    bad = {**M1, "labels": ["x" * 41]}
    assert any(".labels" in e for e in motion.plan_errors({"motion": [bad]}, "auto", WORDS, keep))


def test_band_keeps_the_eye_line_free():
    # face box (output px) from y 400 to 800: eyes at 400 + 0.26 * 400 = 504 px
    track = [{"t": 1.0, "box": [180, 400, 420, 800]}]
    region = motion.band_region(track, 0.0, 2.0, (600, 1000))
    eye = 400 + motion.EYE_LINE_SHARE * 400
    assert region[3] == int(eye - motion.EYE_CLEAR * 400) == 472


def test_invented_band_text():
    from videocut.tools._verify_motion import invented, said_tokens

    allowed = said_tokens("Die drei Schritte zum ersten Termin, 45 Minuten") | said_tokens("Ablauf")
    band = said_tokens("Schritten 45 Minuten Empfehlung Lummerland entdecken 3")
    assert invented(band, allowed) == {"empfehlung", "lummerland", "entdecken"}
    assert said_tokens("4,8 von 5") == {"von"}


def test_stage_ends_one_capture_margin_above_the_band():
    js = (motion.RENDERER / "motion.js").read_text()
    assert f"pageSpec.unit * {motion.CAPTURE_MARGIN}" in js  # the renderer's capture margin
    band = [65, 250, 1015, 641]
    stage = motion.stage_region(band, 1080)
    assert stage[:3] == band[:3] and stage[3] + round(1080 * motion.CAPTURE_MARGIN) == band[3]
