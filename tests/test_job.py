import json
import os

import pytest
from helpers import run_tool

from videocut.tools.common import jobs_root


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


def test_format_on_new_show_and_invalid(capsys, tmp_path):
    v = _video(tmp_path)
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "f1")
    assert code == 0 and s["format"] == "9:16" and s["format_default"] is True
    code, s, out = run_tool(capsys, "job", "show", "f1")
    assert s["format"] == "9:16" and s["size"] == [1080, 1920] and s["format_default"]
    assert "format: 9:16 (1080x1920, default)" in out
    assert "format" not in json.loads(
        (tmp_path / "videocut-home" / "jobs" / "f1" / "job.json").read_text()
    )
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "f2", "--format", "16x9")
    assert code == 0 and s["format"] == "16:9" and s["format_default"] is False
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "f3", "--format", "21:9")
    assert code == 1 and "9:16" in s["error"] and "4:5" in s["error"]


def test_screenshot_assets_default_to_landscape(capsys, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"png")
    code, s, _ = run_tool(capsys, "job", "new", str(_video(tmp_path)), "--asset", str(shot))
    assert code == 0 and s["format"] == "16:9"


def test_job_format_sets_replaces_and_says_to_redo_a_plan(capsys, tmp_path, monkeypatch):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "f4")
    code, s, out = run_tool(capsys, "job", "format", "f4", "4:5")
    assert code == 0 and s["format"] == "4:5" and s["previous"] == "9:16" and not s["redo"]
    job = tmp_path / "videocut-home" / "jobs" / "f4"
    assert json.loads((job / "job.json").read_text())["format"] == "4:5"
    (job / "work" / "project.md").write_text("# Plan\n")
    code, s, out = run_tool(capsys, "job", "format", "--job", str(job), "1:1")
    assert code == 0 and s["redo"] and "made for 4:5" in out and "videocut run f4" in out
    code, s, _ = run_tool(capsys, "job", "format", "f4", "square")
    assert code == 1 and "1:1" in s["error"]
    # an invalid value in job.json is what the command repairs
    (job / "job.json").write_text(json.dumps({**json.loads((job / "job.json").read_text()),
                                              "format": "21:9"}))  # fmt: skip
    assert run_tool(capsys, "job", "show", "f4")[0] == 1
    code, s, out = run_tool(capsys, "job", "format", "f4", "9:16")
    assert code == 0 and s["previous"] is None and s["redo"] and "another format" in out
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    assert run_tool(capsys, "job", "format", "f4", "16:9")[0] == 1


def test_cut_style_and_lang_on_new_show_and_change(capsys, tmp_path):
    v = _video(tmp_path)
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "c1")
    assert code == 0 and (s["cut_style"], s["cut_style_default"], s["lang"]) == (
        "standard", True, "de",
    )  # fmt: skip
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"img")
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "c2", "--asset", str(shot))
    assert s["cut_style"] == "calm" and s["cut_style_default"]  # a demo job is cut calm
    code, s, _ = run_tool(
        capsys, "job", "new", str(v), "--id", "c3", "--cut-style", "Tight", "--lang", "en"
    )
    assert code == 0 and s["cut_style"] == "tight" and not s["cut_style_default"]
    data = json.loads((jobs_root() / "c3" / "job.json").read_text())
    assert (data["cut_style"], data["lang"]) == ("tight", "en")
    code, s, out = run_tool(capsys, "job", "show", "c3")
    assert s["cut_style"] == "tight" and s["cut_style_params"]["keep_pause_s"] == 0.35
    assert s["cut_style_params"]["name"] == "tight" and s["lang"] == "en"
    assert "cut style: tight (set;" in out and "language of plan and reports: en" in out
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "c4", "--cut-style", "slow")
    assert code == 1 and "unknown cut style" in s["error"]
    code, s, _ = run_tool(capsys, "job", "new", str(v), "--id", "c4", "--lang", "fr")
    assert code == 1 and "unknown language" in s["error"]
    assert not (jobs_root() / "c4").exists()

    code, s, out = run_tool(capsys, "job", "cut-style", "c1", "calm")
    assert code == 0 and (s["cut_style"], s["previous"], s["stale"]) == ("calm", "standard", False)
    code, s, out = run_tool(capsys, "job", "cut-style", "--job", "c1", "auto")
    assert code == 0 and s["cut_style"] == "standard" and s["cut_style_default"]
    assert "cut_style" not in json.loads((jobs_root() / "c1" / "job.json").read_text())
    assert run_tool(capsys, "job", "cut-style", "c1", "slow")[0] == 1


def test_cut_style_change_marks_a_cut_stale(capsys, tmp_path):
    from videocut.tools.job import set_cut_style

    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "s1", "--cut-style", "calm")
    job = jobs_root() / "s1"
    data = json.loads((job / "job.json").read_text())
    (job / "job.json").write_text(json.dumps({**data, "cut": {"cut_style": "calm"}}))
    (job / "out" / "reel.mp4").write_bytes(b"reel")
    said = []
    assert set_cut_style(job, "calm", said.append)["stale"] is False
    res = set_cut_style(job, "tight", said.append)
    assert res["stale"] and res["previous"] == "calm"
    assert any("made in the calm style" in x and "videocut run s1" in x for x in said)


def test_a_reel_from_before_cut_records_goes_stale_when_a_style_is_set(capsys, tmp_path):
    from videocut.tools.job import set_cut_style

    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "s0")
    job = jobs_root() / "s0"
    (job / "out" / "reel.mp4").write_bytes(b"reel")  # no job.json "cut" record
    said = []
    assert set_cut_style(job, "standard", said.append)["stale"]
    assert any("made in the tight style" in x for x in said)
    assert set_cut_style(job, "tight", said.append)["stale"] is False


def test_only_a_human_sets_the_cut_style(capsys, tmp_path, monkeypatch):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "h1")
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "job", "cut-style", "h1", "tight")
    assert code == 1 and "only a human" in s["error"]


def test_style_module_contract():
    from videocut.tools import _style

    assert set(_style.STYLES) == {"calm", "standard", "tight"}
    keys = {"keep_pause_s", "min_segment_s", "splice_in_sentence", "xfade_ms", "snap_window_s"}
    for params in _style.STYLES.values():
        assert keys <= set(params)
    calm, std, tight = (_style.STYLES[k] for k in ("calm", "standard", "tight"))
    assert calm["keep_pause_s"] > std["keep_pause_s"] > tight["keep_pause_s"]
    assert calm["min_segment_s"] > std["min_segment_s"] > tight["min_segment_s"]
    assert calm["xfade_ms"] > std["xfade_ms"] > tight["xfade_ms"]
    assert not calm["splice_in_sentence"] and not calm["splice_only"]
    assert _style.job_style({}) == "standard" and _style.job_style({"screen": "x"}) == "calm"
    assert _style.job_style({"assets": ["a"], "cut_style": "tight"}) == "tight"
    assert _style.style({"assets": ["a"]})["name"] == "calm"
    s = _style.style({})
    s["splice_only"].append("x")  # a copy: the table stays as it is
    assert "x" not in _style.STYLES["standard"]["splice_only"]
    with pytest.raises(_style.StyleError):
        _style.job_style({"cut_style": "slow"})


CAND = {"id": 1, "kind": "email", "t0": 1.0, "t1": 2.0, "box": [0.1, 0.1, 0.4, 0.2]}


def _approved_job(capsys, tmp_path, videocut_home, job_id="orig"):
    """A job with a plan, a personal-data decision, an approval and some outputs."""
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", job_id)
    job = videocut_home / "jobs" / job_id
    (job / "work" / "project.md").write_text("# plan\n")
    (job / "work" / "redaction.json").write_text(json.dumps({"candidates": [CAND]}))
    (job / "out" / "reel.mp4").write_bytes(b"reel")
    assert run_tool(capsys, "job", "redaction", job_id, "--all-demo")[0] == 0
    assert run_tool(capsys, "job", "approve", job_id)[0] == 0
    return job


def test_duplicate_needs_its_own_approval(capsys, tmp_path, videocut_home):
    from videocut import redaction as red
    from videocut.tools.job import approval_path, is_approved

    job = _approved_job(capsys, tmp_path, videocut_home)
    code, s, out = run_tool(capsys, "job", "duplicate", "orig", "--title", "  Zentrovia  copy ")
    assert code == 0, s
    copy = videocut_home / "jobs" / "orig-copy-1"
    assert s["id"] == "orig-copy-1" and s["copy_of"] == "orig" and s["title"] == "Zentrovia copy"
    assert s["approval_kept"] is False and s["approved"] is False
    assert "own approval" in out
    data = json.loads((copy / "job.json").read_text())
    assert data["id"] == "orig-copy-1" and data["copy_of"] == "orig"
    assert data["status"] == "ingested" and "approval" not in data
    assert data["source"] == "input/My Take 01.mov"
    # input hard-linked, work and out copied (writing the copy leaves the original alone)
    src, dst = job / "input" / "My Take 01.mov", copy / "input" / "My Take 01.mov"
    assert dst.read_bytes() == src.read_bytes()
    assert os.stat(dst).st_ino == os.stat(src).st_ino
    assert (copy / "work" / "project.md").read_text() == "# plan\n"
    (copy / "out" / "reel.mp4").write_bytes(b"changed")
    assert (job / "out" / "reel.mp4").read_bytes() == b"reel"
    plan = "work/project.md"
    assert os.stat(copy / plan).st_ino != os.stat(job / plan).st_ino
    # no human decision moved along; the original keeps its own
    assert not approval_path(copy).exists() and red.read_decision(copy) is None
    assert is_approved(job) and red.valid_decision(job) is not None
    assert run_tool(capsys, "job", "show", "orig-copy-1")[1]["approved"] is False
    # the next copy (also of a copy) counts on from the base id
    assert run_tool(capsys, "job", "duplicate", "orig")[1]["id"] == "orig-copy-2"
    code, s, _ = run_tool(capsys, "job", "duplicate", "orig-copy-1")
    assert s["id"] == "orig-copy-3" and s["copy_of"] == "orig-copy-1"
    assert s["title"] == "Zentrovia copy"  # a copy of a titled copy keeps the title


def test_duplicate_keep_approval_takes_over_both_decisions(capsys, tmp_path, videocut_home):
    from videocut import redaction as red
    from videocut.tools.job import is_approved, read_approval

    _approved_job(capsys, tmp_path, videocut_home)
    code, s, _ = run_tool(capsys, "job", "duplicate", "orig", "--keep-approval")
    assert code == 0, s
    copy = videocut_home / "jobs" / "orig-copy-1"
    assert s["approval_kept"] is True and s["approved"] is True and s["redaction_kept"] is True
    assert is_approved(copy)
    assert read_approval(copy)["job"] == str(copy.resolve())
    assert red.valid_decision(copy)["demo"] == [1]
    assert json.loads((copy / "job.json").read_text())["status"] == "approved"
    # the copy's approval is its own: a changed plan there does not touch the original
    (copy / "work" / "project.md").write_text("# other plan\n")
    assert not is_approved(copy)
    assert is_approved(videocut_home / "jobs" / "orig")


def test_duplicate_and_title_are_human_only(capsys, tmp_path, videocut_home, monkeypatch):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "h")
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "job", "duplicate", "h")
    assert code == 1 and "human" in s["error"]
    code, s, _ = run_tool(capsys, "job", "title", "h", "x")
    assert code == 1 and "human" in s["error"]
    assert not (videocut_home / "jobs" / "h-copy-1").exists()


def test_title_sets_the_display_title(capsys, tmp_path, videocut_home):
    run_tool(capsys, "job", "new", str(_video(tmp_path)), "--id", "t")
    code, s, _ = run_tool(capsys, "job", "title", "t", "Zentrovia  in   one minute")
    assert code == 0 and s["title"] == "Zentrovia in one minute"
    assert json.loads((videocut_home / "jobs/t/job.json").read_text())["title"] == s["title"]
    assert run_tool(capsys, "job", "title", "t", "   ")[0] == 1
    assert run_tool(capsys, "job", "title", "t", "x" * 121)[0] == 1


def test_copy_id_stays_a_valid_id():
    from videocut.tools.common import ID_RE
    from videocut.tools.job import copy_id

    long = "a" * 80
    cid = copy_id(long, taken=lambda i: False)
    assert ID_RE.fullmatch(cid) and cid.endswith("-copy-1")
    taken = {"x-copy-1", "x-copy-2"}
    assert copy_id("x-copy-1", taken=taken.__contains__) == "x-copy-3"


def test_a_reused_copy_id_inherits_no_stale_records(capsys, tmp_path, videocut_home):
    """A deleted copy leaves its approval and decision outside the folder; the next copy
    with the same id must not count as approved."""
    import shutil

    from videocut import redaction as red
    from videocut.tools.job import is_approved

    _approved_job(capsys, tmp_path, videocut_home)
    assert run_tool(capsys, "job", "duplicate", "orig", "--keep-approval")[1]["approved"]
    copy = videocut_home / "jobs" / "orig-copy-1"
    shutil.move(copy, videocut_home / "trash-copy")  # what the app's delete does
    code, s, _ = run_tool(capsys, "job", "duplicate", "orig")
    assert code == 0 and s["id"] == "orig-copy-1" and s["approved"] is False
    assert not is_approved(copy) and red.read_decision(copy) is None
