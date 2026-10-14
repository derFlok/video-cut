"""Feedback loops: `tool edl locate`, revision runs and their guards (agent mocked), reel
history, plan feedback, `tool plan check` and `tool sheet plan`."""

import hashlib
import json
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut import agent, run
from videocut import redaction as red
from videocut.tools import job as job_tool
from videocut.tools.sheets import _image_size

PLAN = {
    "title": "Test plan",
    "format": "16:9",
    "hook": {"text": "three things", "src": [0.0, 2.0]},
    "keep_list": [
        {"n": 1, "src": [0.0, 2.0], "text": "three things", "why": "hook"},
        {"n": 2, "src": [3.0, 5.0], "text": "the first", "why": "core"},
        {"n": 3, "src": [6.0, 7.0], "text": "the end", "why": "close"},
    ],
    "views": [],
    "overlays": [{"n": 1, "src": [3.0, 4.0], "scene": "keyword", "text": "first", "why": "x"}],
    "personal_data": [],
    "risks": ["none"],
}
CAND = {"id": 1, "kind": "email", "t0": 0.0, "t1": 5.0, "box": [0.1, 0.1, 0.3, 0.2]}
SYNC = {"offset_s": 0.0, "method": "manual", "confidence": 1.0, "overlap": [0.0, 10.0]}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _render(job: Path, tag: str, overlay: bool = False) -> None:
    """What render + verify leave behind, without ffmpeg."""
    out = job / "out"
    (out / "reel.mp4").write_bytes(tag.encode())
    (out / "cover.jpg").write_bytes(b"cover " + tag.encode())
    render = {"edl_sha256": _sha(job / "work" / "edl.json"), "preview": False}
    if overlay:
        render["overlay"] = True
    (out / "render.json").write_text(json.dumps(render))
    (out / "verify.json").write_text(json.dumps({"ok": True, "reel": "out/reel.mp4"}))


def make_job(home: Path, segments, *, plan=PLAN, cands=None, sync=None, edl_extra=None):
    job = home / "jobs" / "rev-job"
    for d in ("work", "out", "input"):
        (job / d).mkdir(parents=True, exist_ok=True)
    (job / "input" / "take.mov").write_bytes(b"x")
    meta = {"id": job.name, "source": "input/take.mov", "type": "thought", "format": "16:9"}
    (job / "job.json").write_text(json.dumps(meta))
    (job / "work" / "project.md").write_text("# Plan: test\n")
    if plan is not None:
        (job / "work" / "plan.json").write_text(json.dumps(plan))
    if cands is not None:
        (job / "work" / "redaction.json").write_text(json.dumps({"candidates": cands}))
    if sync is not None:
        (job / "work" / "sync.json").write_text(json.dumps(sync))
    job_tool.approve_job(job)
    edl = {"fps": 30, "size": [1920, 1080], "source": "input/take.mov", "segments": segments}
    edl.update(edl_extra or {})
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    _render(job, "v0")
    run.record_cut(job, job_tool.project_sha(job))
    return job


class FakeRevise:
    """Stands in for agent.run in a revise run: applies `change` to the EDL, renders."""

    def __init__(self, change=None, claim=None):
        self.change, self.claim, self.prompts = change, claim, []

    def __call__(self, prompt, job_dir, on_event, resume=None, log=None, max_turns=60):
        self.prompts.append(prompt)
        work = job_dir / "work"
        req = json.loads((work / "revision_request.json").read_text())
        edl = json.loads((work / "edl.json").read_text())
        if self.change:
            self.change(edl, job_dir)
        (work / "edl.json").write_text(json.dumps(edl))
        if self.claim is not None:
            (work / "revision.json").write_text(json.dumps(self.claim))
        _render(job_dir, f"v{req['n']}")
        return agent.Result(True, "done", "s-rev", 0.05, 4, "local", {"output_tokens": 3}, 900)


@pytest.fixture
def fake_agent(monkeypatch):
    monkeypatch.setattr(run.agent.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(run, "prep", lambda job, steps=None, say=print: None)

    def use(fake):
        monkeypatch.setattr(run.agent, "run", fake)
        return fake

    return use


def _revisions(job: Path) -> list[dict]:
    return [json.loads(x) for x in (job / "work" / "revisions.jsonl").read_text().splitlines()]


# ---------------------------------------------------------------- locate


def _locate_job(home):
    segs = [
        {"in": 10.0, "out": 11.01, "zoom_keys": [[10.5, 1.3]]},
        {"in": 20.0, "out": 22.0, "layout": "screen",
         "screen_zoom": [{"t": 20.5, "box": [0.2, 0.2, 0.6, 0.6]}]},
    ]  # fmt: skip
    extra = {
        "captions": [{"t0": 0.0, "t1": 0.8, "words": [{"w": "three", "t0": 0, "t1": 0.3},
                                                      {"w": "things", "t0": 0.3, "t1": 0.8}]}],
        "overlays": [{"scene": "keyword", "t0": 0.5, "t1": 1.5, "props": {"text": "x"}}],
    }  # fmt: skip
    return make_job(home, segs, edl_extra=extra)


def test_locate_maps_output_to_segment_source_zoom_overlays_and_captions(capsys, videocut_home):
    job = _locate_job(videocut_home)
    # segment 0 is 1.01 s long: it ends on the frame grid at 1.0 s, as render places it
    code, s, _ = run_tool(capsys, "edl", "locate", "0.6", "--job", str(job))
    assert code == 0 and s["segment"] == 0 and s["source_t"] == pytest.approx(10.6)
    assert s["layout"] == "face" and s["segment_output"] == [0.0, 1.0]
    assert s["zoom_key"] == {"kind": "zoom_keys", "index": 0, "t": 10.5, "zoom": 1.3}
    assert s["overlays"] == [0] and s["caption_text"] == "three things"
    assert s["render_current"] is True and s["tail"] is False
    _, s, _ = run_tool(capsys, "edl", "locate", "0.2", "--job", str(job))
    assert s["zoom_key"] is None and s["overlays"] == []
    _, s, _ = run_tool(capsys, "edl", "locate", "1.0", "--job", str(job))
    assert s["segment"] == 1 and s["source_t"] == pytest.approx(20.0) and s["layout"] == "screen"
    assert s["zoom_key"]["kind"] == "screen_zoom" and s["zoom_key"]["index"] == 0
    assert s["zoom_key"]["upcoming"] is True  # the whole screen until the key at 20.5
    assert s["caption_text"] is None
    # the inverse: source -> output
    _, s, _ = run_tool(capsys, "edl", "locate", "20.5", "--source", "--job", str(job))
    assert s["outputs"] == [{"segment": 1, "output_t": 1.5}]
    _, s, _ = run_tool(capsys, "edl", "locate", "15", "--source", "--job", str(job))
    assert s["outputs"] == []
    code, s, _ = run_tool(capsys, "edl", "locate", "3.5", "--job", str(job))
    assert code != 0 and "outside the reel" in s["error"]
    # the held end-card tail of the current render belongs to the last segment
    render = json.loads((job / "out" / "render.json").read_text())
    (job / "out" / "render.json").write_text(json.dumps({**render, "tail_s": 1.0}))
    _, s, _ = run_tool(capsys, "edl", "locate", "3.5", "--job", str(job))
    assert s["tail"] is True and s["segment"] == 1 and s["source_t"] == 22.0
    # a render of another EDL: its tail does not count, and the summary says so
    (job / "out" / "render.json").write_text(json.dumps({"edl_sha256": "x", "tail_s": 1.0}))
    _, s, _ = run_tool(capsys, "edl", "locate", "0.6", "--job", str(job))
    assert s["render_current"] is False


def test_output_source_helpers_round_trip():
    from videocut import edl as edl_mod

    e = edl_mod.from_dict(
        {"fps": 30, "size": [1080, 1920], "source": "input/a.mov",
         "segments": [{"in": 5, "out": 6}, {"in": 1, "out": 2.5}, {"in": 5.5, "out": 7}]}
    )  # fmt: skip
    assert edl_mod.output_spans(e) == [(0.0, 1.0), (1.0, 2.5), (2.5, 4.0)]
    assert edl_mod.output_to_source(e, 1.25) == (1, 1.25)
    assert edl_mod.output_to_source(e, 4.0) is None
    # source 5.75 is shown twice (segments 0 and 2 overlap in source time)
    assert edl_mod.source_to_output(e, 5.75) == [(0, 0.75), (2, 2.75)]
    for t in (0.1, 1.7, 3.9):
        k, src = edl_mod.output_to_source(e, t)
        assert (k, pytest.approx(t)) in [(k2, pytest.approx(o)) for k2, o in
                                         edl_mod.source_to_output(e, src)]  # fmt: skip


# ---------------------------------------------------------------- revision runs


def _zoom_less(edl, job):
    edl["segments"][0]["zoom_keys"] = [[1.0, 1.15]]


def test_revision_changes_only_what_is_asked_and_keeps_history(fake_agent, videocut_home):
    fake = fake_agent(FakeRevise(_zoom_less, claim={"summary": "zoom 1.4 -> 1.15"}))
    job = make_job(videocut_home, [{"in": 0, "out": 2, "zoom_keys": [[1.0, 1.4]]},
                                   {"in": 3, "out": 5}])  # fmt: skip
    lines = []
    code = run.run(job.name, feedback="at 0:01 zoom out less", at_s=1.2, say=lines.append)
    assert code == 0, lines
    assert fake.prompts[0].startswith(run.REVISE_PROMPT) and "16:9" in fake.prompts[0]
    req = json.loads((job / "work" / "revision_request.json").read_text())
    assert req["located"]["segment"] == 0 and req["located"]["zoom_key"]["index"] == 0
    assert req["previous_reel"] == "out/history/reel-1.mp4"
    assert (job / "out" / "history" / "reel-1.mp4").read_bytes() == b"v0"
    assert (job / "out" / "reel.mp4").read_bytes() == b"v1"
    (rev,) = _revisions(job)
    assert rev["ok"] and rev["n"] == 1 and rev["text"] == "at 0:01 zoom out less"
    assert rev["at_s"] == 1.2 and rev["cost"] == 0.05 and rev["turns"] == 4
    assert rev["changed"] == {
        "segments": {"modified": [{"before": 0, "after": 0, "fields": ["zoom_keys"]}]}
    }
    assert rev["extends_plan"] is False and rev["summary"] == "zoom 1.4 -> 1.15"
    # the revised reel is the recorded cut of the approved plan: a plain run has nothing to do
    assert run.cut_done(job) and job_tool.is_approved(job)
    runs = [json.loads(x) for x in (job / "work" / "runs.jsonl").read_text().splitlines()]
    assert [r["phase"] for r in runs] == ["revise"]


def test_reel_history_keeps_the_last_five(fake_agent, videocut_home):
    fake_agent(FakeRevise(_zoom_less))
    job = make_job(videocut_home, [{"in": 0, "out": 2, "zoom_keys": [[1.0, 1.4]]}])
    for _ in range(7):
        assert run.revise(job, "zoom out less", say=lambda _: None) == 0
    assert [n for n, _ in run.history(job)] == [3, 4, 5, 6, 7]
    assert (job / "out" / "history" / "reel-7.mp4").read_bytes() == b"v6"
    assert [r["n"] for r in _revisions(job)] == list(range(1, 8))


def test_a_failed_revision_costs_no_kept_version(fake_agent, videocut_home):
    fake_agent(FakeRevise(_zoom_less))
    job = make_job(videocut_home, [{"in": 0, "out": 2, "zoom_keys": [[1.0, 1.4]]}])
    for _ in range(5):
        run.revise(job, "zoom out less", say=lambda _: None)
    fake_agent(FakeRevise(lambda edl, j: edl["segments"].append({"in": 30.0, "out": 31.0})))
    with pytest.raises(run.RunError):
        run.revise(job, "make it better", say=lambda _: None)
    assert [n for n, _ in run.history(job)] == [1, 2, 3, 4, 5]


def test_feedback_needs_the_cut_of_the_approved_plan(fake_agent, videocut_home):
    fake_agent(FakeRevise(_zoom_less))
    job = make_job(videocut_home, [{"in": 0, "out": 2, "zoom_keys": [[1.0, 1.4]]}])
    (job / "work" / "project.md").write_text("# Plan: changed and approved again\n")
    job_tool.approve_job(job)  # the reel and EDL are still those of the old plan
    with pytest.raises(run.RunError, match="not the cut of the approved plan"):
        run.revise(job, "zoom out less", say=lambda _: None)


def test_a_new_cut_archives_the_versions(fake_agent, videocut_home):
    fake_agent(FakeRevise(_zoom_less))
    job = make_job(videocut_home, [{"in": 0, "out": 2, "zoom_keys": [[1.0, 1.4]]}])
    run.revise(job, "zoom out less", say=lambda _: None)
    run.archive_revisions(job)
    assert not (job / "work" / "revisions.jsonl").exists() and run.history(job) == []
    assert len(list((job / "work").glob("revisions-before-*.jsonl"))) == 1
    assert len(list((job / "out").glob("history-before-*/reel-1.mp4"))) == 1


def test_out_of_plan_segment_fails_and_restores_the_previous_reel(fake_agent, videocut_home):
    def add_new(edl, job):
        edl["segments"].append({"in": 8.0, "out": 9.0})

    fake_agent(FakeRevise(add_new))
    job = make_job(videocut_home, [{"in": 0, "out": 2}, {"in": 3, "out": 5}])
    edl_before = (job / "work" / "edl.json").read_bytes()
    with pytest.raises(run.RunError, match="outside the approved plan.*previous reel is back"):
        run.revise(job, "make it better", say=lambda _: None)
    assert (job / "out" / "reel.mp4").read_bytes() == b"v0"
    assert (job / "work" / "edl.json").read_bytes() == edl_before
    assert run.history(job) == [] and run.cut_done(job)
    (rev,) = _revisions(job)
    assert rev["ok"] is False and "outside the approved plan" in rev["error"]
    # a claim that quotes words the human did not write is not enough
    fake_agent(FakeRevise(add_new, claim={"extends_plan": True, "asked": "add the outro"}))
    with pytest.raises(run.RunError, match="outside the approved plan"):
        run.revise(job, "make it better", say=lambda _: None)
    # the plan's keep list is in the approved plan even when the cut left it out
    fake_agent(FakeRevise(lambda edl, j: edl["segments"].append({"in": 6.0, "out": 7.1})))
    assert run.revise(job, "keep the end", say=lambda _: None) == 0
    assert _revisions(job)[-1]["extends_plan"] is False


def test_feedback_that_asks_for_new_material_extends_the_plan(fake_agent, videocut_home):
    def add_new(edl, job):
        edl["segments"].append({"in": 8.0, "out": 9.0})

    claim = {"extends_plan": True, "asked": "Bring back  the sentence about pricing"}
    fake_agent(FakeRevise(add_new, claim=claim))
    job = make_job(videocut_home, [{"in": 0, "out": 2}])
    text = "please bring back the sentence about pricing at the end"
    assert run.revise(job, text, say=lambda _: None) == 0
    rev = _revisions(job)[-1]
    assert rev["ok"] and rev["extends_plan"] is True
    assert rev["asked"] == "bring back the sentence about pricing"
    assert rev["changed"]["segments"] == {"added": [1]}
    assert rev["changed"]["output_duration"] == [2.0, 3.0]


def _screen_job(home, decide):
    segs = [
        {"in": 0, "out": 2, "layout": "screen",
         "redact": [{"t0": 0, "t1": 2, "box": CAND["box"]}]},
        {"in": 3, "out": 5},
    ]  # fmt: skip
    job = make_job(home, segs, cands=[CAND], sync=SYNC)
    decide(job)
    run.record_cut(job, job_tool.project_sha(job))
    return job


@pytest.mark.parametrize(
    "change, match",
    [
        # the candidate list is not the revise run's to change
        (lambda edl, job: (job / "work" / "redaction.json").write_text('{"candidates": []}'),
         "personal-data candidates or decision"),
        # a block of a candidate the human decided to redact
        (lambda edl, job: edl["segments"][0].pop("redact"), "redaction_missing"),
    ],
)  # fmt: skip
def test_revision_must_not_touch_the_redaction(fake_agent, videocut_home, change, match):
    fake_agent(FakeRevise(change))
    job = _screen_job(videocut_home, lambda j: red.decide(j, None, "prompt"))
    state = run.redaction_state(job)
    with pytest.raises(run.RunError, match=match):
        run.revise(job, "zoom out less", say=lambda _: None)
    assert run.redaction_state(job) == state
    assert (job / "out" / "reel.mp4").read_bytes() == b"v0"


def test_redact_blocks_stay_while_their_picture_is_in_the_reel(fake_agent, videocut_home):
    # demo data: edl check would not miss the block, the guard does
    job = _screen_job(videocut_home, lambda j: red.decide(j, [], "flag"))
    fake_agent(FakeRevise(lambda edl, j: edl["segments"][0].pop("redact")))
    with pytest.raises(run.RunError, match="removed redaction still in the reel"):
        run.revise(job, "zoom out less", say=lambda _: None)
    # dropping the segment with the material it covered is fine
    fake_agent(FakeRevise(lambda edl, j: edl["segments"].pop(0)))
    assert run.revise(job, "drop the first part", say=lambda _: None) == 0
    assert _revisions(job)[-1]["changed"]["segments"] == {"removed": [0]}


def test_feedback_needs_an_approved_finished_reel(fake_agent, videocut_home):
    fake_agent(FakeRevise())
    job = make_job(videocut_home, [{"in": 0, "out": 2}])
    with pytest.raises(run.RunError, match="--at needs --feedback"):
        run.run(job.name, at_s=3.0, say=lambda _: None)
    with pytest.raises(run.RunError, match="cannot be combined"):
        run.run(job.name, feedback="x", replan=True, say=lambda _: None)
    with pytest.raises(run.RunError, match="outside the reel"):
        run.revise(job, "zoom", at_s=99, say=lambda _: None)
    (job / "work" / "project.md").write_text("# Plan: edited\n")
    with pytest.raises(run.RunError, match="not approved"):
        run.revise(job, "zoom", say=lambda _: None)


def test_a_revise_run_that_loses_the_overlays_fails(fake_agent, videocut_home):
    job = make_job(videocut_home, [{"in": 0, "out": 2}])
    run.update_job(job, brand="_example")
    _render(job, "v0", overlay=True)
    run._record_overlays(job, "_example", {"ok": True})
    assert run.overlays_done(job, "_example")
    fake_agent(FakeRevise(_zoom_less))  # renders without the overlay track
    with pytest.raises(run.RunError, match="lost its captions/overlays"):
        run.revise(job, "zoom", say=lambda _: None)
    assert run.overlays_done(job, "_example")  # the previous state is back


# ---------------------------------------------------------------- plan feedback


def test_plan_feedback_revises_the_plan_and_needs_a_new_approval(fake_agent, videocut_home):
    prompts, seen = [], []

    def plan_run(prompt, job_dir, on_event, resume=None, log=None, max_turns=60):
        prompts.append(prompt)
        req = job_dir / "work" / "plan_feedback.json"
        seen.append(json.loads(req.read_text()) if req.is_file() else None)
        if "shorter" in prompt or (seen[-1] and "shorter" in seen[-1]["text"]):
            (job_dir / "work" / "project.md").write_text(
                "# Plan: test\nChanged after feedback: shorter hook\n"
            )
        return agent.Result(True, "done", "s-plan", 0.02, 2, "local", {}, 100)

    fake_agent(plan_run)
    job = make_job(videocut_home, [{"in": 0, "out": 2}])
    sha = job_tool.project_sha(job)
    with pytest.raises(run.RunError, match="empty"):
        run.run(job.name, plan_feedback="  ", say=lambda _: None)
    lines = []
    code = run.run(job.name, plan_feedback="a shorter hook", say=lines.append, ask=lambda _: "no")
    assert code == 3
    assert prompts[0].startswith(run.PLAN_PROMPT) and run.PLAN_REVISE in prompts[0]
    assert seen[0] == {"text": "a shorter hook", "plan_sha256": sha}
    assert not (job / "work" / "plan_feedback.json").exists()
    assert not job_tool.is_approved(job)  # sha-bound: the changed plan needs a new approval
    assert not any("plan.json" in x for x in lines)  # the plan.json of the job is valid
    # feedback that changes nothing keeps the plan (and its approval)
    job_tool.approve_job(job)
    code = run.run(job.name, plan_feedback="keep it", say=lines.append, ask=lambda _: "no")
    assert code == 3 and job_tool.is_approved(job)
    assert any("did not change" in x for x in lines)


# ---------------------------------------------------------------- plan.json


def test_plan_check_accepts_a_valid_plan_and_names_problems(capsys, videocut_home):
    cand = {**CAND, "kind": "customer"}  # an e-mail needs a measured text (test_redact_measure)
    job = make_job(videocut_home, [{"in": 0, "out": 2}], cands=[cand], sync=SYNC)
    (job / "work" / "probe.json").write_text(json.dumps({"duration": 10.0}))
    plan = {**PLAN, "personal_data": [cand]}
    (job / "work" / "plan.json").write_text(json.dumps(plan))
    code, s, _ = run_tool(capsys, "plan", "check", "--job", str(job))
    assert code == 0 and s["keep"] == 3 and s["personal_data"] == 1
    bad = {
        **plan,
        "format": "9:16",
        "keep_list": [{"n": 1, "src": [3, 12], "text": "x", "why": "y"}],
        "views": [{"n": 1, "src": [0, 2], "layout": "screen", "zoom_box": [0.5, 0, 0.4, 1]}],
        "overlays": [{"n": 1, "src": [0, 1], "scene": "fireworks", "text": "x", "why": "y"}],
        "personal_data": [{**cand, "box": [0.1, 0.1, 0.5, 0.5]}],
    }
    (job / "work" / "plan.json").write_text(json.dumps(bad))
    code, s, out = run_tool(capsys, "plan", "check", "--job", str(job))
    assert code != 0
    for path in ("$.format", "$.keep_list[0].src", "$.views[0].layout", "$.views[0].zoom_box",
                 "$.overlays[0].scene", "$.personal_data"):  # fmt: skip
        assert path in out, path
    code, s, _ = run_tool(capsys, "plan", "check", "../../x.json", "--job", str(job))
    assert code != 0 and "outside the job" in s["error"]


@pytest.mark.skipif(clips.ffmpeg() is None, reason="needs ffmpeg")
def test_sheet_plan_draws_stills_for_views_and_personal_data(capsys, videocut_home):
    job = make_job(videocut_home, [{"in": 0, "out": 2}], cands=None, sync=SYNC)
    clips._ff("-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6",
              "-pix_fmt", "yuv420p", str(job / "work" / "proxy.mp4"))  # fmt: skip
    clips._ff("-f", "lavfi", "-i", "testsrc2=size=1920x1200:rate=30:duration=6",
              "-pix_fmt", "yuv420p", str(job / "input" / "screen.mp4"))  # fmt: skip
    clips._ff("-f", "lavfi", "-i", "testsrc=size=800x1600", "-frames:v", "1",
              str(job / "input" / "shot.png"))  # fmt: skip
    meta = json.loads((job / "job.json").read_text())
    meta.update(screen="input/screen.mp4", assets=["input/shot.png"])
    (job / "job.json").write_text(json.dumps(meta))
    shot = {"id": 2, "kind": "customer", "asset": "input/shot.png", "box": [0.1, 0.1, 0.5, 0.2]}
    cands = [{**CAND, "kind": "customer", "t1": 2.0}, shot]
    (job / "work" / "redaction.json").write_text(json.dumps({"candidates": cands}))
    plan = {
        **PLAN,
        "views": [
            {"n": 1, "src": [0.0, 2.0], "layout": "face", "why": "hook"},
            {"n": 2, "src": [3.0, 5.0], "layout": "screen", "zoom_box": [0.5, 0.3, 0.9, 0.6],
             "zoom_t": 3.5, "why": "click"},
        ],
        "personal_data": cands,
    }  # fmt: skip
    (job / "work" / "plan.json").write_text(json.dumps(plan))
    (job / "work" / "plan_stills").mkdir()
    (job / "work" / "plan_stills" / "view_09.jpg").write_bytes(b"stale")
    code, s, _ = run_tool(capsys, "sheet", "plan", "--job", str(job))
    assert code == 0, s
    names = sorted(Path(p).name for p in s["stills"])
    assert names == ["pd_01.jpg", "pd_02.jpg", "view_01.jpg", "view_02.jpg"]
    assert not (job / "work" / "plan_stills" / "view_09.jpg").exists()
    sizes = {Path(p).name: _image_size(job / p) for p in s["stills"]}
    assert sizes["view_01.jpg"] == (640, 360)
    assert sizes["view_02.jpg"] == (960, 600) and sizes["pd_01.jpg"] == (960, 600)
    assert sizes["pd_02.jpg"] == (480, 960)
    saved = json.loads((job / "work" / "plan.json").read_text())
    assert saved["views"][1]["still"] == "work/plan_stills/view_02.jpg"
    assert saved["personal_data"][1]["still"] == "work/plan_stills/pd_02.jpg"
    code, _, _ = run_tool(capsys, "plan", "check", "--job", str(job))
    assert code == 0


def test_revise_skill_names_its_tools_and_guards(monkeypatch):
    import re

    text = (agent.PLUGIN / "skills" / "revise" / "SKILL.md").read_text()
    assert text.startswith("---\nname: revise\n") and '"approved": true' in text
    for cmd in ("edl locate", "edl check", "overlays", "render --preview", "verify",
                "captions --force"):  # fmt: skip
        assert f"videocut tool {cmd}" in text, cmd
    for word in ("work/revision_request.json", "work/revision.json", "extends_plan",
                 "work/revision_report.md", "redact"):  # fmt: skip
        assert word in text
    plan = (agent.PLUGIN / "skills" / "plan" / "SKILL.md").read_text()
    for word in ("Revise-the-plan mode", "Changed after feedback:", "work/plan_feedback.json",
                 "videocut tool plan check", "videocut tool sheet plan"):  # fmt: skip
        assert word in plan
    for t in (text, plan):
        for cmd in re.findall(r"`(videocut [^`]*)`", t):
            assert cmd.startswith("videocut tool "), cmd
    monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/local/bin/claude")
    cmd, _ = agent.command(run.REVISE_PROMPT)
    assert "video-cut:revise skill" in cmd[cmd.index("-p") + 1]
