import json
from pathlib import Path

import pytest
from helpers import run_tool

from videocut import edl


def _valid():
    return {
        "fps": 30,
        "size": [1080, 1920],
        "source": "input/take.mov",
        "segments": [
            {"in": 1.0, "out": 3.5, "fade_ms": 30},
            {
                "in": 5.0,
                "out": 6.0,
                "crop": {"cx": 0.52, "cy": 0.41, "zoom": 1.25},
                "zoom_keys": [[5.5, 1.6]],
            },
            {"in": 8.25, "out": 9.0},
        ],
        "captions": [{"t0": 0.0, "t1": 1.2, "words": [{"w": "Drei", "t0": 0.0, "t1": 0.4}]}],
        "overlays": [{"scene": "number_card", "t0": 1.0, "t1": 2.0, "props": {"value": "3"}}],
        "music": {"file": "music/x.mp3", "gain_db": -18, "duck": True},
    }


def test_valid_roundtrip_and_helpers(tmp_path):
    e = edl.from_dict(_valid(), source_duration=10.0)
    assert e.output_duration() == pytest.approx(4.25)
    assert e.cut_points() == [0.0, 2.5, 3.5, 4.25]
    assert e.segments[1].crop.zoom == 1.25 and e.segments[2].fade_ms == 30
    path = tmp_path / "edl.json"
    edl.save(e, path)
    again = edl.load(path)
    assert again == e
    assert json.loads(path.read_text())["segments"][1]["zoom_keys"] == [[5.5, 1.6]]


def test_segments_only_is_enough():
    data = {"fps": 29.97, "size": [1920, 1080], "source": "input/a.mp4"}
    data["segments"] = [{"in": 0, "out": 1}]
    assert edl.validate(data) == []


@pytest.mark.parametrize(
    "mutate, path",
    [
        (lambda d: d["segments"][0].update({"in": 4.0}), "$.segments[0]: in (4.0) must be < out"),
        (lambda d: d["segments"][0].update({"in": -1}), "$.segments[0].in: must be >= 0"),
        (lambda d: d["segments"][2].update({"out": 12.0}), "$.segments[2].out: 12.0 is beyond"),
        (lambda d: d["segments"][1]["crop"].update({"cx": 1.2}), "$.segments[1].crop.cx"),
        (lambda d: d["segments"][1]["crop"].update({"cy": -0.1}), "$.segments[1].crop.cy"),
        (lambda d: d["segments"][1]["crop"].update({"zoom": 0.9}), "$.segments[1].crop.zoom"),
        (lambda d: d["segments"][1]["crop"].update({"zoom": 2.5}), "$.segments[1].crop.zoom"),
        (lambda d: d["segments"][0].update({"fade_ms": 250}), "$.segments[0].fade_ms"),
        (lambda d: d["segments"][0].update({"fade_ms": -1}), "$.segments[0].fade_ms"),
        (
            lambda d: d["segments"][1].update({"zoom_keys": [[9.0, 1.2]]}),
            "$.segments[1].zoom_keys[0]: time",
        ),
        (lambda d: d["segments"][0].update({"out": "3"}), "$.segments[0].out: must be a number"),
        (lambda d: d["segments"][0].update({"out": True}), "$.segments[0].out: must be a number"),
        (lambda d: d.update({"segments": []}), "$.segments: must be a non-empty list"),
        (lambda d: d.update({"source": "/etc/passwd"}), "$.source"),
        (lambda d: d.update({"source": "../other/take.mov"}), "$.source"),
        (lambda d: d.update({"size": [1080]}), "$.size"),
        (lambda d: d.update({"fps": 0}), "$.fps"),
        (lambda d: d["captions"][0].update({"t1": 9.0}), "$.captions[0].t1: 9.0 is beyond"),
        (lambda d: d["overlays"][0].update({"t0": 3.0}), "$.overlays[0]: needs numbers"),
        (lambda d: d.update({"broll": [{"t0": 0, "t1": 1, "file": "../x.mp4"}]}), "$.broll[0]"),
        (lambda d: d["music"].update({"file": "/abs.mp3"}), "$.music.file"),
    ],
)
def test_invalid_cases_report_json_paths(mutate, path):
    d = _valid()
    mutate(d)
    errors = edl.validate(d, source_duration=10.0)
    assert any(e.startswith(path) for e in errors), errors
    with pytest.raises(edl.EdlError):
        edl.from_dict(d, source_duration=10.0)


def test_all_errors_reported_at_once():
    d = _valid()
    d["segments"][0]["fade_ms"] = 500
    d["segments"][2]["in"] = 20
    assert len(edl.validate(d)) == 2


def test_load_reports_bad_json(tmp_path):
    p = tmp_path / "edl.json"
    p.write_text("{nope")
    with pytest.raises(edl.EdlError, match=r"\$: invalid JSON at line 1"):
        edl.load(p)


def test_save_refuses_invalid(tmp_path):
    e = edl.from_dict(_valid())
    e.segments[0].end = 0.5
    with pytest.raises(edl.EdlError):
        edl.save(e, tmp_path / "edl.json")
    assert not (tmp_path / "edl.json").exists()


def _job_with_edl(capsys, tmp_path, data, duration=None):
    v = tmp_path / "take.mov"
    v.write_bytes(b"x")
    _, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "e1")
    job = Path(s["job"])
    (job / "work" / "edl.json").write_text(json.dumps(data))
    if duration is not None:
        (job / "work" / "probe.json").write_text(json.dumps({"duration": duration}))
    return job


def test_edl_check_tool(capsys, tmp_path, monkeypatch):
    job = _job_with_edl(capsys, tmp_path, _valid(), duration=10.0)
    monkeypatch.chdir(job)
    code, s, _ = run_tool(capsys, "edl", "check")
    assert code == 0, s
    assert s["segments"] == 3 and s["output_duration"] == 4.25 and s["cuts"] == [2.5, 3.5]
    assert s["source_duration"] == 10.0


def test_edl_check_tool_uses_probe_duration(capsys, tmp_path):
    job = _job_with_edl(capsys, tmp_path, _valid(), duration=8.5)
    code, s, out = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "1 problem" in s["error"]
    assert "$.segments[2].out: 9.0 is beyond the source duration 8.5" in out


def test_edl_check_warns_when_the_edl_size_is_not_the_job_format(capsys, tmp_path):
    job = _job_with_edl(capsys, tmp_path, _valid(), duration=10.0)
    run_tool(capsys, "job", "format", "e1", "16:9")
    code, s, out = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0, s
    assert "is not the job's format 16:9 (1920x1080)" in out


def test_edl_check_tool_explicit_file(capsys, tmp_path, monkeypatch):
    p = tmp_path / "draft.json"
    p.write_text(json.dumps(_valid()))
    monkeypatch.chdir(tmp_path)
    assert run_tool(capsys, "edl", "check", "draft.json")[0] == 0
    assert run_tool(capsys, "edl", "check", "missing.json")[0] == 1


# --- screen layouts (product demos) ---

SYNC = {
    "offset_s": 3.27,
    "method": "audio_xcorr",
    "confidence": 0.9,
    "drift_ppm": 0.2,
    "overlap": [0.0, 9.0],
}
BOX = [0.1, 0.2, 0.5, 0.6]


def _screen_edl(**seg):
    data = _valid()
    data["segments"][1].update({"layout": "screen", **seg})
    return data


def test_screen_segment_roundtrip(tmp_path):
    data = _screen_edl(
        screen_zoom=[{"t": 5.0, "box": [0, 0, 1, 1]}, {"t": 5.8, "box": BOX}],
        redact=[{"t0": 5.2, "t1": 5.6, "box": [0.7, 0.1, 0.9, 0.2]}],
    )
    assert edl.validate(data, sync=SYNC, check_sync=True) == []
    e = edl.from_dict(data)
    assert [s.layout for s in e.segments] == ["face", "screen", "face"]
    out = e.to_dict()
    assert out["segments"][1]["screen_zoom"] == data["segments"][1]["screen_zoom"]
    assert "layout" not in out["segments"][0]  # default is not written
    edl.save(e, tmp_path / "e.json")
    assert edl.load(tmp_path / "e.json").segments[1].redact == data["segments"][1]["redact"]


@pytest.mark.parametrize(
    "seg, path",
    [
        ({"layout": "cinema"}, "$.segments[1].layout"),
        ({"layout": "face", "screen_zoom": [{"t": 5.5, "box": BOX}]}, "$.segments[1].screen_zoom"),
        ({"screen_zoom": {"t": 5.5}}, "$.segments[1].screen_zoom"),
        ({"screen_zoom": [{"t": 4.0, "box": BOX}]}, "screen_zoom[0].t: 4.0 is outside"),
        ({"screen_zoom": [{"t": 5.5}]}, "screen_zoom[0].box"),
        ({"screen_zoom": [{"t": 5.5, "box": [0.1, 0.2, 1.2, 0.6]}]}, "screen_zoom[0].box"),
        ({"screen_zoom": [{"t": 5.5, "box": [0.5, 0.2, 0.5, 0.6]}]}, "degenerate"),
        ({"screen_zoom": [{"t": 5.5, "box": [0.6, 0.2, 0.1, 0.6]}]}, "degenerate"),
        ({"screen_zoom": [{"t": 5.5, "box": [0.1, 0.2, 0.101, 0.9]}]}, "degenerate"),
        (
            {"screen_zoom": [{"t": 5.5, "box": BOX}, {"t": 5.5, "box": BOX}]},
            "screen_zoom[1].t: keyframes must be in increasing",
        ),
        ({"screen_zoom": [{"t": "5", "box": BOX}]}, "screen_zoom[0].t"),
        ({"redact": [{"t0": 5.4, "t1": 5.2, "box": BOX}]}, "redact[0]: needs numbers t0 < t1"),
        ({"redact": [{"t0": 5.2, "t1": 6.5, "box": BOX}]}, "redact[0]: [5.2, 6.5] is outside"),
        ({"redact": [{"t0": 5.2, "t1": 5.4, "box": [0, 0, 0, 1]}]}, "redact[0].box"),
        ({"redact": [{"t0": 5.2, "t1": 5.4, "box": True}]}, "redact[0].box"),
    ],
)
def test_screen_invalid_cases(seg, path):
    errs = edl.validate(_screen_edl(**seg))
    assert any(path in e for e in errs), errs


def test_redact_needs_a_screen_layout():
    data = _valid()
    data["segments"][1]["redact"] = [{"t0": 5.2, "t1": 5.4, "box": BOX}]
    assert any("redact: needs layout" in e for e in edl.validate(data))


def test_overlap_check():
    data = _screen_edl()
    assert edl.validate(data, sync=SYNC, check_sync=True) == []
    short = {**SYNC, "overlap": [0.0, 5.5]}
    errs = edl.validate(data, sync=short, check_sync=True)
    assert len(errs) == 1 and errs[0].startswith("$.segments[1]: [5.0, 6.0] is outside")
    assert edl.validate(data, sync=short) == []  # only checked on request
    late = {**SYNC, "overlap": [5.5, 9.0]}
    assert edl.validate(data, sync=late, check_sync=True)
    errs = edl.validate(data, sync=None, check_sync=True)
    assert len(errs) == 1 and "needs a valid work/sync.json" in errs[0]
    assert edl.validate(_valid(), sync=None, check_sync=True) == []  # face-only needs no sync


def test_sync_helpers(tmp_path):
    assert edl.validate_sync(SYNC) == []
    assert edl.screen_time(2.0, SYNC) == pytest.approx(5.27)
    p = tmp_path / "sync.json"
    assert edl.read_sync(p) is None
    p.write_text(json.dumps(SYNC))
    assert edl.read_sync(p) == SYNC
    p.write_text("{not json")
    assert edl.read_sync(p) is None
    for bad in (
        {**SYNC, "method": "magic"},
        {**SYNC, "confidence": 1.5},
        {**SYNC, "offset_s": "3"},
        {**SYNC, "overlap": [4, 2]},
        {**SYNC, "drift_ppm": "x"},
        {k: v for k, v in SYNC.items() if k != "overlap"},
        [],
    ):
        assert edl.validate_sync(bad), bad
    assert edl.validate_sync({**SYNC, "drift_ppm": None, "method": "manual"}) == []


def test_edl_check_tool_screen_layouts(capsys, tmp_path):
    job = _job_with_edl(capsys, tmp_path, _screen_edl(), duration=10.0)
    code, s, out = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "needs a valid work/sync.json" in out
    (job / "work" / "sync.json").write_text(json.dumps({**SYNC, "overlap": [0, 5.5]}))
    code, s, out = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 1 and "outside the time both recordings cover" in out
    (job / "work" / "sync.json").write_text(json.dumps(SYNC))
    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0, s
    assert s["layouts"] == ["face", "screen"] and s["sync"] == "audio_xcorr"
