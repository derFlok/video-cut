"""Target length (tools/_length.py): parsing, the default rule, `job new|show|target`, the
stale plan and the approval bound to the target, and `run --target`."""

import json
from pathlib import Path

import pytest
from helpers import run_tool

from videocut import agent, run
from videocut.tools import _length
from videocut.tools import job as job_tool
from videocut.tools.common import jobs_root


def _video(tmp_path, name="take.mov"):
    v = tmp_path / name
    v.write_bytes(b"not really a video")
    return v


# ---------------------------------------------------------------- helper


@pytest.mark.parametrize(
    ("value", "secs"),
    [
        (90, 90),
        (90.5, 90.5),
        ("90", 90),
        ("90s", 90),
        ("90 s", 90),
        ("5m", 300),
        ("5 min", 300),
        ("1,5m", 90),
        ("2:30", 150),
        ("1:02:30", 3750),
        (" 45 ", 45),
        ("auto", None),
        ("AUTO", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_target(value, secs):
    assert _length.parse_target(value) == secs


@pytest.mark.parametrize("value", ["fast", "5x", "2:75", "-30", "0", "4", "4h", True, "1:2"])
def test_parse_target_rejects(value):
    with pytest.raises(_length.LengthError):
        _length.parse_target(value)


def test_target_range_default_rule_and_tolerance():
    # the real video type ids (server/app.py TYPES): thought is the short one
    assert _length.target_range({"type": "thought"}) == (30, 45)
    assert _length.target_range({"type": "tips"}) == (30, 60)
    assert _length.target_range({}) == (30, 60)
    # a demo (screen recording or screenshots) has no fixed length
    assert _length.target_range({"type": "walkthrough", "screen": "input/s.mov"}) is None
    assert _length.target_range({"type": "thought", "assets": ["input/a.png"]}) is None
    # a set target: +-15 %, rounded outwards, also for demos
    assert _length.target_range({"target_s": 300, "screen": "input/s.mov"}) == (255, 345)
    assert _length.target_range({"type": "thought", "target_s": 45}) == (38, 52)
    with pytest.raises(_length.LengthError):
        _length.target_range({"target_s": "5m"})  # job.json stores seconds only
    assert _length.label(300) == "5 min" and _length.label(150) == "2:30 min"
    assert _length.label(90) == "90 s" and _length.label(None) == "auto"
    assert "255-345 s" in _length.describe({"target_s": 300})
    assert "as long as the content needs" in _length.describe({"screen": "x"})
    assert "30-45 s" in _length.describe({"type": "thought"})


# ---------------------------------------------------------------- job new | show | target


def test_target_on_new_and_show(capsys, tmp_path):
    v = _video(tmp_path)
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "t1", "--target", "5m")
    assert code == 0 and s["target_s"] == 300 and not s["target_default"]
    assert s["target_range"] == {"min_s": 255, "max_s": 345}
    assert json.loads((jobs_root() / "t1" / "job.json").read_text())["target_s"] == 300
    code, s, out = run_tool(capsys, "job", "show", "t1")
    assert s["target_s"] == 300 and s["target_range"] == {"min_s": 255, "max_s": 345}
    assert "target length: 5 min (set; aim for 255-345 s)" in out
    assert s["plan_target_stale"] is False
    # auto: by type
    code, s, out = run_tool(
        capsys, "job", "new", str(v), "--id", "t2", "--type", "thought", "--target", "auto"
    )
    assert code == 0 and s["target_s"] is None and s["target_default"]
    assert "target_s" not in json.loads((jobs_root() / "t2" / "job.json").read_text())
    _, s, out = run_tool(capsys, "job", "show", "t2")
    assert s["target_range"] == {"min_s": 30, "max_s": 45} and "30-45 s" in out
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "t3", "--target", "soon")
    assert code == 1 and "invalid target length" in s["error"]


def _planned(capsys, tmp_path, job_id="p1", *extra) -> Path:
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", job_id, *extra)
    job = jobs_root() / job_id
    (job / "work" / "project.md").write_text("# Plan\n")
    job_tool.stamp_plan_target(job, job_tool.read_job(job).get("target_s"))
    return job


def test_changing_the_target_makes_the_plan_stale_and_revokes_the_approval(capsys, tmp_path):
    job = _planned(capsys, tmp_path, "p1", "--type", "thought")
    job_tool.approve_job(job)
    assert job_tool.is_approved(job)
    assert job_tool.read_approval(job)["target_s"] is None
    code, s, out = run_tool(capsys, "job", "target", "p1", "2:00")
    assert code == 0 and s["target_s"] == 120 and s["previous"] is None
    assert s["plan_stale"] and s["approval_revoked"] and not s["reel_stale"]
    assert "made for the target length auto" in out and "videocut run p1" in out
    assert not job_tool.is_approved(job) and job_tool.read_approval(job) is None
    assert json.loads((job / "job.json").read_text())["status"] == "ingested"
    _, s, out = run_tool(capsys, "job", "show", "p1")
    assert s["plan_target_stale"] and "stale until it is planned again" in out
    # a stale plan cannot be approved
    code, s, _ = run_tool(capsys, "job", "approve", "p1")
    assert code == 1 and "plan again" in s["error"]
    # back to the length the plan was made for: not stale any more
    code, s, _ = run_tool(capsys, "job", "target", "--job", "p1", "auto")
    assert code == 0 and not s["plan_stale"] and s["target_s"] is None
    assert run_tool(capsys, "job", "approve", "p1")[0] == 0
    # a plan run for the new length clears the staleness
    run_tool(capsys, "job", "target", "p1", "90")
    job_tool.stamp_plan_target(job, 90)
    assert not job_tool.plan_target_stale(job)
    assert run_tool(capsys, "job", "approve", "p1")[0] == 0 and job_tool.is_approved(job)


def test_approval_is_bound_to_the_target(capsys, tmp_path):
    job = _planned(capsys, tmp_path, "b1", "--target", "60")
    job_tool.approve_job(job)
    assert job_tool.is_approved(job)
    data = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**data, "target_s": 300}))  # edited by hand
    assert not job_tool.is_approved(job)


def test_a_plan_from_before_target_lengths_was_made_for_auto(capsys, tmp_path):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "o1")
    job = jobs_root() / "o1"
    (job / "work" / "project.md").write_text("# Plan\n")  # no plan_target record
    assert not job_tool.plan_target_stale(job)
    job_tool.approve_job(job)
    (job / "out" / "reel.mp4").write_bytes(b"reel")
    code, s, out = run_tool(capsys, "job", "target", "o1", "300")
    assert s["plan_stale"] and s["reel_stale"] and s["approval_revoked"]
    assert "out/reel.mp4) was made for another target length" in out
    assert json.loads((job / "job.json").read_text())["plan_target"] == {"target_s": None}


def test_only_a_human_sets_the_target(capsys, tmp_path, monkeypatch):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "h1")
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "job", "target", "h1", "300")
    assert code == 1 and "only a human" in s["error"]
    assert "target_s" not in json.loads((jobs_root() / "h1" / "job.json").read_text())


# ---------------------------------------------------------------- run --target


class _Claude:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, job_dir, on_event, resume=None, log=None, max_turns=60):
        self.prompts.append(prompt)
        work, out = job_dir / "work", job_dir / "out"
        if prompt.startswith(run.PLAN_PROMPT):
            (work / "project.md").write_text(f"# Plan {len(self.prompts)}\n")
        else:
            edl = {"fps": 30, "source": "input/take.mov", "segments": [{"in": 0, "out": 1}]}
            (work / "edl.json").write_text(json.dumps(edl))
            (out / "render.json").write_text("{}")
            (out / "reel.mp4").write_bytes(b"x")
            (out / "verify.json").write_text('{"ok": true, "reel": "out/reel.mp4"}')
        return agent.Result(True, "done", "s-1", 0.1, 3, "local", None, 1000)


@pytest.fixture
def claude(monkeypatch):
    c = _Claude()
    monkeypatch.setattr(run.agent, "run", c)
    monkeypatch.setattr(run.agent.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(run, "prep", lambda job, steps=None, say=print: None)
    return c


def test_run_target_names_it_and_a_changed_target_plans_again(claude, tmp_path):
    lines = []
    code = run.run(str(_video(tmp_path)), target="5m", approve=True, say=lines.append)
    assert code == 0, lines
    job = Path(lines[0].split("job: ")[1])
    assert "Target length" in claude.prompts[0] and "255-345 s" in claude.prompts[0]
    data = json.loads((job / "job.json").read_text())
    assert data["target_s"] == 300 and data["plan_target"] == {"target_s": 300}
    assert data["cut"]["target_s"] == 300
    # a new target: the approved plan is stale, the run plans again and asks again
    n = len(claude.prompts)
    code = run.run(job.name, target="90", say=lines.append, ask=lambda _: "no")
    assert code == 3
    assert any("made for the target length 5 min; planning again" in x for x in lines)
    assert claude.prompts[n].startswith(run.PLAN_PROMPT) and "76-104 s" in claude.prompts[n]
    assert not job_tool.is_approved(job) and not job_tool.plan_target_stale(job)
    assert run.stale_why(job).startswith("the plan changed")


def test_plan_feedback_after_a_target_change_is_told_the_new_length(claude, tmp_path):
    lines = []
    run.run(str(_video(tmp_path)), say=lines.append, ask=lambda _: "no")
    job = Path(lines[0].split("job: ")[1])
    job_tool.set_target(job, "3m", say=lines.append)
    assert job_tool.plan_target_stale(job)
    run.run(job.name, plan_feedback="fit the new length", say=lines.append, ask=lambda _: "no")
    assert "changed the target length" in claude.prompts[-1]
    assert "made for auto" in claude.prompts[-1] and "3 min" in claude.prompts[-1]
    assert not job_tool.plan_target_stale(job)


def test_cli_run_passes_target(monkeypatch):
    from videocut import cli

    seen = {}
    monkeypatch.setattr(run, "run", lambda ref, **k: seen.update(ref=ref, **k) or 0)
    assert cli.main(["run", "take.mov", "--target", "5m"]) == 0
    assert seen["ref"] == "take.mov" and seen["target"] == "5m"
    assert cli.main(["run", "job-1"]) == 0 and seen["target"] is None


def test_feedback_cannot_be_combined_with_target(claude):
    with pytest.raises(run.RunError, match="--target"):
        run.run("x", feedback="shorter", target="60")


def test_a_plan_is_stamped_with_the_target_it_was_asked_for(capsys, tmp_path):
    """The target changes while the plan run works: the plan stays made for the old one."""
    job = _planned(capsys, tmp_path, "r1", "--target", "60")
    run_tool(capsys, "job", "target", "r1", "5m")  # (during the run)
    job_tool.stamp_plan_target(job, 60)  # what run.py stamps: the target before the run
    assert job_tool.plan_target_stale(job)
    assert run_tool(capsys, "job", "approve", "r1")[0] == 1


def test_a_plan_written_outside_run_can_be_approved_with_a_target(capsys, tmp_path):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "o1", "--target", "90")
    job = jobs_root() / "o1"
    (job / "work" / "project.md").write_text("# Plan\n")  # no plan_target record
    assert not job_tool.plan_target_stale(job)
    assert run_tool(capsys, "job", "approve", "o1")[0] == 0 and job_tool.is_approved(job)
