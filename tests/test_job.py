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


def test_approve_needs_project_and_tracks_its_content(capsys, tmp_path, videocut_home):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "ap")
    job = videocut_home / "jobs" / "ap"
    code, s, _ = run_tool(capsys, "job", "approve", "ap")
    assert code == 1 and "project.md" in s["error"]
    (job / "work" / "project.md").write_text("# plan\n")
    code, s, _ = run_tool(capsys, "job", "approve", "ap")
    assert code == 0 and s["status"] == "approved" and len(s["project_sha256"]) == 64
    assert run_tool(capsys, "job", "show", "ap")[1]["approved"] is True
    # a plan changed after approval is no longer approved
    (job / "work" / "project.md").write_text("# plan, rewritten\n")
    _, s, _ = run_tool(capsys, "job", "show", "ap")
    assert s["approved"] is False and "work/project.md" in s["artefacts"]


def test_agent_runs_cannot_approve(capsys, tmp_path, videocut_home, monkeypatch):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "ag")
    (videocut_home / "jobs" / "ag" / "work" / "project.md").write_text("# plan\n")
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "job", "approve", "ag")
    assert code == 1 and "human" in s["error"]
    assert run_tool(capsys, "job", "show", "ag")[1]["approved"] is False
    code, s, _ = run_tool(capsys, "job", "brand", "_example", "--job", "ag")
    assert code == 1 and "human" in s["error"]


def test_new_with_assets(capsys, tmp_path, videocut_home):
    shot = tmp_path / "screen.png"
    shot.write_bytes(b"png")
    code, s, _ = run_tool(
        capsys, "job", "new", str(_video(tmp_path)), "--id", "a", "--asset", str(shot)
    )
    assert code == 0 and s["assets"] == ["input/screen.png"], s
    assert (videocut_home / "jobs/a/input/screen.png").read_bytes() == b"png"
    assert json.loads((videocut_home / "jobs/a/job.json").read_text())["assets"] == s["assets"]
    bad = tmp_path / "notes.txt"
    bad.write_text("x")
    code, s, _ = run_tool(
        capsys, "job", "new", str(_video(tmp_path)), "--id", "b", "--asset", str(bad)
    )
    assert code == 1 and "must be one of" in s["error"]
    assert not (videocut_home / "jobs/b").exists()


def test_new_with_brand_and_job_brand_switch(capsys, tmp_path, videocut_home):
    from test_brand import make_brand

    make_brand(videocut_home, "one")
    make_brand(videocut_home, "two")
    code, s, _ = run_tool(
        capsys, "job", "new", str(_video(tmp_path)), "--id", "j", "--brand", "one"
    )
    assert code == 0 and s["brand"] == "one"
    assert json.loads((videocut_home / "jobs/j/job.json").read_text())["brand"] == "one"
    code, s, _ = run_tool(capsys, "job", "brand", "two", "--job", "j")
    assert code == 0 and s["brand"] == "two"
    assert json.loads((videocut_home / "jobs/j/job.json").read_text())["brand"] == "two"
    code, s, _ = run_tool(capsys, "job", "brand", "_example", "--job", "j")
    assert code == 0 and s["brand"] == "_example"


def test_unknown_or_invalid_brand_is_refused(capsys, tmp_path, videocut_home):
    code, s, _ = run_tool(
        capsys, "job", "new", str(_video(tmp_path)), "--id", "j", "--brand", "ghost"
    )
    assert code == 1 and "no brand" in s["error"]
    assert not (videocut_home / "jobs/j").exists()
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "k")
    code, s, _ = run_tool(capsys, "job", "brand", "ghost", "--job", "k")
    assert code == 1
    assert "brand" not in json.loads((videocut_home / "jobs/k/job.json").read_text())


def test_new_with_screen_and_job_screen(capsys, tmp_path, videocut_home):
    cam = _video(tmp_path)
    scr = tmp_path / "Screen Recording.mov"
    scr.write_bytes(b"screen")
    code, s, _ = run_tool(capsys, "job", "new", str(cam), "--screen", str(scr), "--id", "d1")
    assert code == 0 and s["screen"] == "input/Screen Recording.mov"
    job = videocut_home / "jobs" / "d1"
    assert (job / s["screen"]).read_bytes() == b"screen" and scr.is_file()
    assert json.loads((job / "job.json").read_text())["screen"] == s["screen"]

    # replacing: new file linked, old one and everything made from it removed
    (job / "work" / "sync.json").write_text("{}")
    (job / "work" / "screen_proxy.mp4").write_bytes(b"x")
    scr2 = tmp_path / "Second.mp4"
    scr2.write_bytes(b"screen2")
    code, s, _ = run_tool(capsys, "job", "screen", "--job", "d1", str(scr2))
    assert (
        code == 0
        and s["screen"] == "input/Second.mp4"
        and s["replaced"] == "input/Screen Recording.mov"
    )
    assert not (job / "input" / "Screen Recording.mov").exists()
    assert not (job / "work" / "sync.json").exists()
    assert not (job / "work" / "screen_proxy.mp4").exists()
    assert json.loads((job / "job.json").read_text())["screen"] == "input/Second.mp4"
    shown = run_tool(capsys, "job", "show", "--job", "d1")[1]
    assert "screen" in json.dumps(shown)


def test_screen_rejections(capsys, tmp_path, monkeypatch):
    cam = _video(tmp_path)
    assert run_tool(capsys, "job", "new", str(cam), "--screen", str(tmp_path / "no.mov"))[0] == 1
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    code, s, _ = run_tool(capsys, "job", "new", str(cam), "--screen", str(txt))
    assert code == 1 and "must be one of" in s["error"]
    code, s, _ = run_tool(capsys, "job", "new", str(cam), "--screen", str(cam))
    assert code == 1 and "both named" in s["error"]
    run_tool(capsys, "job", "new", str(cam), "--id", "r1")
    from videocut.tools import job as job_tool

    monkeypatch.setenv(job_tool.AGENT_ENV, "1")
    scr = tmp_path / "s.mov"
    scr.write_bytes(b"x")
    code, s, _ = run_tool(capsys, "job", "screen", "--job", "r1", str(scr))
    assert code == 1 and "only a human" in s["error"]


def test_new_with_many_assets_in_one_flag(capsys, tmp_path, videocut_home):
    shots = []
    for name in ("one.png", "two.jpg", "three.webp"):
        (tmp_path / name).write_bytes(b"img")
        shots.append(str(tmp_path / name))
    code, s, _ = run_tool(
        capsys, "job", "new", str(_video(tmp_path)), "--id", "m", "--asset", *shots[:2],
        "--asset", shots[2],
    )  # fmt: skip
    assert code == 0, s
    assert s["assets"] == ["input/one.png", "input/two.jpg", "input/three.webp"]
