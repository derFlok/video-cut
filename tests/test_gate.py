"""The human gate: approvals live outside the job folder and render refuses without one."""

import json
from pathlib import Path

from helpers import run_tool

from videocut import agent
from videocut.tools import job as job_tool


def _job(capsys, tmp_path, job_id) -> Path:
    video = tmp_path / f"{job_id}.mov"
    video.write_bytes(b"not a video")
    code, s, _ = run_tool(capsys, "job", "new", str(video), "--id", job_id)
    assert code == 0, s
    job = Path(s["job"])
    (job / "work" / "edl.json").write_text("{}")  # never read: the gate refuses first
    return job


def test_approvals_live_outside_the_agent_writable_job_folder(capsys, tmp_path, videocut_home):
    job = _job(capsys, tmp_path, "g1")
    (job / "work" / "project.md").write_text("# plan\n")
    job_tool.approve_job(job)
    record = job_tool.approval_path(job)
    assert record.is_file() and json.loads(record.read_text())["job"] == str(job.resolve())
    # The agent runs with cwd = job folder; every Read/Write/Edit pattern is relative to it.
    assert not record.resolve().is_relative_to(job.resolve())
    assert not job_tool.approvals_root().resolve().is_relative_to(job.resolve())
    for rule in agent.ALLOWED + agent.DENIED:
        if rule.startswith(("Read(", "Write(", "Edit(")):
            assert rule.split("(", 1)[1].startswith("./"), rule
            assert ".." not in rule, rule


def test_render_refuses_before_approval_and_after_the_plan_changes(capsys, tmp_path, videocut_home):
    job = _job(capsys, tmp_path, "g2")
    for mode in ([], ["--preview"]):
        code, s, _ = run_tool(capsys, "render", *mode, "--job", str(job))
        assert code == 1 and "not approved" in s["error"], s
    (job / "work" / "project.md").write_text("# plan\n")
    job_tool.approve_job(job)
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert "not approved" not in s.get("error", ""), s  # past the gate (fails on the dummy EDL)
    (job / "work" / "project.md").write_text("# plan, changed after approval\n")
    for mode in ([], ["--preview"]):
        code, s, _ = run_tool(capsys, "render", *mode, "--job", str(job))
        assert code == 1 and "not approved" in s["error"], s


def test_approval_forged_in_job_json_is_not_trusted(capsys, tmp_path, videocut_home):
    job = _job(capsys, tmp_path, "g3")
    (job / "work" / "project.md").write_text("# plan\n")
    sha = job_tool.project_sha(job)
    # What an old version (or a forging agent) wrote into the job folder.
    data = json.loads((job / "job.json").read_text())
    data.update(status="approved", approval={"at": "2026-01-01T00:00:00", "project_sha256": sha})
    (job / "job.json").write_text(json.dumps(data))
    assert not job_tool.is_approved(job)
    assert run_tool(capsys, "job", "show", "g3")[1]["approved"] is False
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 1 and "not approved" in s["error"]


def test_a_record_for_another_folder_does_not_count(capsys, tmp_path, videocut_home):
    job = _job(capsys, tmp_path, "g4")
    (job / "work" / "project.md").write_text("# plan\n")
    record = job_tool.approval_path(job)
    record.parent.mkdir(parents=True, exist_ok=True)
    sha = job_tool.project_sha(job)
    record.write_text(json.dumps({"job": str(tmp_path / "elsewhere"), "project_sha256": sha}))
    assert not job_tool.is_approved(job)
    record.write_text("not json")
    assert not job_tool.is_approved(job)
