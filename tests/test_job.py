import json
import os

from helpers import run_tool


def _video(tmp_path):
    v = tmp_path / "My Take 01.mov"
    v.write_bytes(b"not really a video")
    return v


def test_new_creates_layout_without_moving_source(capsys, tmp_path, videocut_home):
    v = _video(tmp_path)
    code, s, out = run_tool(
        capsys, "job", "new", str(v), "--type", "thought", "--brief", "short", "--id", "j1"
    )
    assert code == 0 and s["ok"]
    job = videocut_home / "jobs" / "j1"
    assert s["job"] == str(job) and str(job) in out
    assert {p.name for p in job.iterdir()} == {"input", "work", "out", "job.json"}
    assert v.is_file()  # never moved
    copied = job / "input" / v.name
    assert copied.read_bytes() == v.read_bytes()
    if s["imported"] == "hardlink":
        assert os.stat(copied).st_ino == os.stat(v).st_ino
    data = json.loads((job / "job.json").read_text())
    assert data["id"] == "j1" and data["source"] == f"input/{v.name}"
    assert data["type"] == "thought" and data["brief"] == "short" and data["status"] == "new"
    assert data["created"]


def test_default_id_is_timestamp_and_slug(capsys, tmp_path):
    _, s, _ = run_tool(capsys, "job", "new", str(_video(tmp_path)))
    assert s["id"].endswith("-my-take-01")


def test_new_rejects_bad_input(capsys, tmp_path):
    v = _video(tmp_path)
    assert run_tool(capsys, "job", "new", str(tmp_path / "missing.mov"))[0] == 1
    assert run_tool(capsys, "job", "new", str(v), "--id", "../escape")[0] == 1
    assert run_tool(capsys, "job", "new", str(v), "--id", "a")[0] == 0
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "a")
    assert code == 1 and "exists" in s["error"]


def test_show_resolves_by_id_path_and_cwd(capsys, tmp_path, monkeypatch, videocut_home):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "j2")
    job = videocut_home / "jobs" / "j2"
    (job / "work" / "probe.json").write_text("{}")
    (job / "out" / "final.mp4").write_bytes(b"")
    for argv in (["j2"], [str(job)], ["--job", "j2"]):
        code, s, _ = run_tool(capsys, "job", "show", *argv)
        assert code == 0 and s["id"] == "j2"
    assert s["artefacts"] == ["work/probe.json"] and s["out"] == ["final.mp4"]
    monkeypatch.chdir(job / "work")  # Claude runs inside the job folder, maybe deeper
    assert run_tool(capsys, "job", "show")[1]["job"] == str(job.resolve())


def test_show_outside_a_job_fails(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, s, _ = run_tool(capsys, "job", "show")
    assert code == 1 and "not inside a job" in s["error"]
    assert run_tool(capsys, "job", "show", "nope")[0] == 1


def test_failed_import_leaves_no_job(capsys, tmp_path, monkeypatch, videocut_home):
    from videocut.tools import job as job_mod

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(job_mod, "_link_or_copy", boom)
    code, s, _ = run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "f1")
    assert code == 1 and "disk full" in s["error"]
    assert not (videocut_home / "jobs" / "f1").exists()
