"""Personal-data candidates, the human decision and its enforcement (no media)."""

import json
import os
from pathlib import Path

import pytest
from helpers import run_tool

from videocut import redaction as red
from videocut import run
from videocut.tools import job as job_tool

SCREEN = {"id": 1, "kind": "email", "t0": 2.0, "t1": 6.0, "box": [0.4, 0.0, 0.8, 0.07],
          "reason": "e-mail in the top bar"}  # fmt: skip
SHOT = {"id": 2, "kind": "name", "asset": "input/shot.png", "box": [0.1, 0.1, 0.5, 0.2],
        "reason": "customer name"}  # fmt: skip


@pytest.fixture
def job(capsys, tmp_path, videocut_home):
    v = tmp_path / "take.mov"
    v.write_bytes(b"x")
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "pd")
    assert code == 0, s
    job = Path(s["job"])
    (job / "work" / "redaction.json").write_text(json.dumps({"candidates": [SCREEN, SHOT]}))
    (job / "work" / "project.md").write_text("# Plan\n")
    return job


def _ids(cands):
    return sorted(c["id"] for c in cands)


def test_validate_reports_paths():
    assert red.validate({"candidates": [SCREEN, SHOT]}) == []
    bad = [{**SCREEN, "kind": "x"}, {**SCREEN, "box": [1, 0, 0, 1]},
           {"id": 3, "kind": "phone", "box": [0, 0, 1, 1]}]  # fmt: skip
    errs = red.validate({"candidates": bad})
    assert any(".kind" in e for e in errs) and any(".box" in e for e in errs)
    assert any("t0 < t1" in e for e in errs) and any(".id" in e for e in errs)


def test_default_redacts_all_and_decision_paths(capsys, job):
    assert red.enforced(job) == ([SCREEN, SHOT], "default")
    code, s, _ = run_tool(capsys, "job", "redaction", "pd", "--redact", "2")
    assert code == 0 and s["redact"] == [2] and s["demo"] == [1]
    cands, source = red.enforced(job)
    assert source == "decision" and _ids(cands) == [2]
    show = run_tool(capsys, "job", "show", "pd")[1]["redaction"]
    assert show == {"candidates": 2, "decided": True, "method": "command", "redact": [2],
                    "demo": [1]}  # fmt: skip
    # the record lives outside the agent-writable job folder
    assert not red.decision_path(job).is_relative_to(job)
    code, s, _ = run_tool(capsys, "job", "redaction", "pd", "--all-demo")
    assert code == 0 and red.enforced(job) == ([], "decision")  # a human may change their mind
    code, s, _ = run_tool(capsys, "job", "redaction", "pd", "--redact", "7")
    assert code == 1 and "unknown" in s["error"]


def test_changed_candidates_invalidate_the_decision(capsys, job):
    run_tool(capsys, "job", "redaction", "pd", "--all-demo")
    assert red.enforced(job) == ([], "decision")
    new = {**SCREEN, "id": 3, "box": [0.0, 0.9, 0.3, 1.0]}
    (job / "work" / "redaction.json").write_text(json.dumps({"candidates": [SCREEN, SHOT, new]}))
    cands, source = red.enforced(job)
    assert source == "default" and _ids(cands) == [1, 2, 3]
    assert run_tool(capsys, "job", "show", "pd")[1]["redaction"]["decided"] is False


def test_approved_candidates_stay_redacted_when_the_list_shrinks(capsys, job):
    job_tool.approve_job(job)
    (job / "work" / "redaction.json").write_text(json.dumps({"candidates": [SHOT]}))
    cands, _ = job_tool.redaction_enforced(job)
    assert _ids(cands) == [1, 2]  # the agent cannot drop candidate 1 after approval


def test_agent_cannot_decide(capsys, job, monkeypatch):
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "job", "redaction", "pd", "--all-demo")
    assert code == 1 and "human" in s["error"]
    assert not red.decision_path(job).exists()
    assert os.environ["VIDEOCUT_AGENT"] == "1"


def test_invalid_list_fails_closed(capsys, job):
    (job / "work" / "redaction.json").write_text('{"candidates": [{"id": 1}]}')
    with pytest.raises(red.RedactionError):
        red.enforced(job)
    with pytest.raises(job_tool.ToolError, match="cannot approve"):
        job_tool.approve_job(job)


def test_missing_checks_screen_segments_and_screenshots(tmp_path):
    data = {
        "segments": [
            {"in": 0.0, "out": 3.0},  # face: nothing shown
            {"in": 3.0, "out": 5.0, "layout": "screen",
             "redact": [{"t0": 3.0, "t1": 5.0, "box": [0.39, 0.0, 0.81, 0.08]}]},
            {"in": 5.0, "out": 8.0, "layout": "pip"},
        ],
        "overlays": [{"scene": "screenshot_frame", "t0": 1, "t1": 3,
                      "props": {"file": "input/shot.png", "redact": [[0.1, 0.1, 0.3, 0.2]]}}],
    }  # fmt: skip
    got = red.missing([SCREEN, SHOT], data, tmp_path)
    assert got == [
        {"id": 1, "where": "$.segments[2].redact",
         "need": {"t0": 5.0, "t1": 6.0, "box": SCREEN["box"]}},
        {"id": 2, "where": "$.overlays[0].props.redact", "need": SHOT["box"]},
    ]  # fmt: skip
    assert red.screen_blocks([SCREEN, SHOT], 5.0, 8.0) == [
        {"t0": 5.0, "t1": 6.0, "box": SCREEN["box"]}
    ]


@pytest.mark.parametrize(
    ("answers", "redact"), [(["a"], []), (["r"], [1, 2]), (["s", "2"], [2]), (["x"], [1, 2])]
)
def test_run_asks_at_the_gate(job, answers, redact):
    it = iter(answers)
    run.decide_personal_data(job, False, True, lambda _: next(it), say=lambda _: None)
    assert red.read_decision(job)["redact"] == redact
    assert red.read_decision(job)["method"] == "prompt"


def test_demo_data_flag_and_non_interactive_default(job):
    lines = []
    run.decide_personal_data(job, False, False, input, say=lines.append)
    assert red.read_decision(job) is None and any("all of them are redacted" in x for x in lines)
    run.decide_personal_data(job, True, False, input, say=lines.append)
    rec = red.read_decision(job)
    assert rec["method"] == "flag" and rec["redact"] == [] and rec["demo"] == [1, 2]


def test_overlays_add_candidate_boxes_to_their_screenshot(tmp_path):
    from videocut.tools.overlays import _redacted

    job = tmp_path
    item = {"scene": "screenshot_frame", "t0": 1, "t1": 3,
            "props": {"file": "input/shot.png", "redact": [[0, 0, 0.1, 0.1]]}}  # fmt: skip
    out = _redacted(job, item, [SCREEN, SHOT])
    assert out["props"]["redact"] == [[0, 0, 0.1, 0.1], SHOT["box"]]
    assert item["props"]["redact"] == [[0, 0, 0.1, 0.1]]  # the EDL item is not changed
    other = {**item, "props": {"file": "input/other.png"}}
    assert _redacted(job, other, [SHOT]) is other


@pytest.mark.parametrize(
    "ref", ["./input/shot.png", "input//shot.png", "work/../input/shot.png", "file://{job}/input/shot.png",
            " input/shot.png", "work/copy.png"],
)  # fmt: skip
def test_any_spelling_or_copy_of_a_screenshot_is_redacted(tmp_path, ref):
    from videocut.tools.overlays import _redacted

    (tmp_path / "input").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "input" / "shot.png").write_bytes(b"png bytes")
    (tmp_path / "work" / "copy.png").write_bytes(b"png bytes")
    (tmp_path / "input" / "other.png").write_bytes(b"other bytes")
    ref = ref.format(job=tmp_path)
    item = {"scene": "screenshot_frame", "t0": 1, "t1": 3, "props": {"file": ref}}
    assert _redacted(tmp_path, item, [SHOT])["props"]["redact"] == [SHOT["box"]]
    assert red.missing([SHOT], {"overlays": [item]}, tmp_path) == [
        {"id": 2, "where": "$.overlays[0].props.redact", "need": SHOT["box"]}
    ]
    other = {**item, "props": {"file": "./input/other.png"}}
    assert _redacted(tmp_path, other, [SHOT]) is other
    assert red.missing([SHOT], {"overlays": [other]}, tmp_path) == []
    assert not red.same_asset(tmp_path, "https://example.com/input/shot.png", SHOT["asset"])


def test_a_changed_decision_means_the_cut_is_not_done(job):
    (job / "out").mkdir(exist_ok=True)
    (job / "out" / "reel.mp4").write_bytes(b"x")
    (job / "out" / "verify.json").write_text('{"ok": true, "reel": "out/reel.mp4"}')
    (job / "work" / "edl.json").write_text('{"segments": []}')
    job_tool.approve_job(job)
    run.record_cut(job, job_tool.read_approval(job)["project_sha256"])
    assert run.cut_done(job)
    red.decide(job, [1], "cli")  # the screenshot is demo data: the reel must change
    assert not run.cut_done(job)
    run.record_cut(job, job_tool.read_approval(job)["project_sha256"])
    assert run.cut_done(job)
    red.decide(job, None, "cli")  # redact all again
    assert not run.cut_done(job)
    # a record from before the field keeps its reel
    data = json.loads((job / "job.json").read_text())
    data["cut"].pop("redaction_sha256")
    (job / "job.json").write_text(json.dumps(data))
    assert run.cut_done(job)
