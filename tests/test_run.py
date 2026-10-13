"""`videocut run`: agent command per phase, orchestration with a fake Claude, and an
end-to-end prep -> hand-written EDL -> render -> verify on a generated clip (no Claude)."""

import json
import os
import re
import shutil
from pathlib import Path

import clips
import pytest
from helpers import run_tool

from videocut import agent, run, settings
from videocut.tools import job as job_tool

REAL_PREP = run.prep  # the `fake` fixture replaces run.prep

# Captured at import, before the autouse fixture points VIDEOCUT_HOME at a temp folder.
REAL_MODELS = Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut")) / "models"


# ---------------------------------------------------------------- agent command


@pytest.mark.parametrize("prompt", [run.PLAN_PROMPT, run.CUT_PROMPT, run.OVERLAYS_PROMPT])
def test_plan_and_cut_runs_are_restricted(monkeypatch, prompt):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(settings, "load", lambda: {"auth": "local", "model": None})
    cmd, env = agent.command(prompt, resume="sess-1", max_turns=40)

    def values(flag):
        i = cmd.index(flag) + 1
        out = []
        while i < len(cmd) and not cmd[i].startswith("--"):
            out.append(cmd[i])
            i += 1
        return out

    assert values("-p") == [prompt]
    assert values("--plugin-dir") == [str(agent.PLUGIN)]
    skill = re.search(r"video-cut:(\w+) skill", prompt).group(1)
    assert (agent.PLUGIN / "skills" / skill / "SKILL.md").is_file()
    assert values("--tools") == ["Bash,Read,Write,Edit,Skill"]
    assert values("--allowedTools") == agent.ALLOWED
    assert [a for a in agent.ALLOWED if a.startswith("Bash")] == ["Bash(videocut tool *)"]
    assert not any(a.startswith(("Task", "Agent", "WebFetch", "WebSearch")) for a in agent.ALLOWED)
    writes = [a for a in agent.ALLOWED if a.startswith(("Write", "Edit"))]
    assert writes == ["Write(./work/**)", "Edit(./work/**)", "Write(./pong.txt)"]
    denied = values("--disallowedTools")
    for path in ("./job.json", "./.claude/**", "./CLAUDE.md", "./.mcp.json"):
        assert f"Write({path})" in denied and f"Edit({path})" in denied
    assert values("--permission-mode") == ["dontAsk"]
    assert values("--max-turns") == ["40"] and values("--resume") == ["sess-1"]
    assert env["VIDEOCUT_AGENT"] == "1"


def test_skills_name_their_tools_and_the_gate():
    plan = (agent.PLUGIN / "skills" / "plan" / "SKILL.md").read_text()
    cut = (agent.PLUGIN / "skills" / "cut" / "SKILL.md").read_text()
    assert plan.startswith("---\nname: plan\n") and cut.startswith("---\nname: cut\n")
    assert "work/project.md" in plan and "Never** write `work/edl.json`" in plan
    assert '"approved": true' in cut and "work/cut_report.md" in cut
    for cmd in ("render --preview", "verify", "edl check", "sheet burst"):
        assert f"videocut tool {cmd}" in cut
    assert "## Overlay ideas" in plan and "forbidden_words" in plan
    ov = (agent.PLUGIN / "skills" / "overlays" / "SKILL.md").read_text()
    assert ov.startswith("---\nname: overlays\n") and '"approved": true' in ov
    for cmd in ("captions", "overlays", "render --preview", "verify", "edl check", "brand show"):
        assert f"videocut tool {cmd}" in ov
    assert "work/overlay_report.md" in ov and "forbidden_words" in ov
    # format-aware: the EDL size comes from the job, every skill knows the landscape views
    assert "[1080, 1920]" not in cut and "Format: <format>" in plan
    assert all("16:9" in text for text in (plan, cut, ov))
    # every command a skill names is reachable through the one allowed Bash pattern
    for text in (plan, cut, ov):
        for cmd in re.findall(r"`(videocut [^`]*)`", text):
            assert cmd.startswith("videocut tool "), cmd


# ---------------------------------------------------------------- orchestration


class FakeClaude:
    """Stands in for agent.run: plan writes project.md, cut writes the outputs."""

    def __init__(self):
        self.calls = []
        self.prompts = []
        self.overlay_segments = None  # set to make the overlay run change the cut

    def __call__(self, prompt, job_dir, on_event, resume=None, log=None, max_turns=60):
        phase = "plan" if prompt.startswith(run.PLAN_PROMPT) else "cut"
        phase = "overlays" if prompt.startswith(run.OVERLAYS_PROMPT) else phase
        self.calls.append((phase, resume, max_turns))
        self.prompts.append(prompt)
        work, out = job_dir / "work", job_dir / "out"
        if phase == "plan":
            (work / "project.md").write_text("# Plan: test\n")
        else:
            assert job_tool.is_approved(job_dir)
            edl = {"fps": 30, "source": "input/take.mov", "segments": [{"in": 0, "out": 1}]}
            if phase == "overlays":
                edl["segments"] = self.overlay_segments or edl["segments"]
                edl["captions"] = [{"t0": 0, "t1": 1, "words": []}]
            (work / "edl.json").write_text(json.dumps(edl))
            (out / "render.json").write_text(json.dumps({"overlay": phase == "overlays"}))
            (out / "reel.mp4").write_bytes(b"x")
            (out / "verify.json").write_text('{"ok": true, "reel": "out/reel.mp4"}')
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}
        return agent.Result(True, "done", f"s-{phase}", 0.12, 7, "local", usage, 3000)


@pytest.fixture
def fake(monkeypatch, tmp_path, capsys):
    claude = FakeClaude()
    monkeypatch.setattr(run.agent, "run", claude)
    monkeypatch.setattr(run.agent.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(run, "prep", lambda job, steps=None, say=print: None)
    v = tmp_path / "take.mov"
    v.write_bytes(b"not a video")
    return claude, v


def test_run_stops_without_approval(fake):
    claude, video = fake
    lines = []
    code = run.run(str(video), type_="thought", say=lines.append, ask=lambda _: "no")
    assert code == 3 and [c[0] for c in claude.calls] == ["plan"]
    job = Path(lines[0].split("job: ")[1])
    assert json.loads((job / "job.json").read_text())["type"] == "thought"
    assert not job_tool.is_approved(job)
    # the next run offers the same plan again instead of re-planning; "yes" cuts fresh
    code = run.run(job.name, say=lines.append, ask=lambda _: "yes")
    assert code == 0 and [c[0] for c in claude.calls] == ["plan", "cut"]
    assert claude.calls[-1][1] is None  # no plan session in this invocation: fresh cut run


def test_run_with_approve_resumes_the_plan_session_and_logs_runs(fake):
    claude, video = fake
    lines = []
    code = run.run(str(video), approve=True, max_turns=33, say=lines.append)
    assert code == 0, lines
    assert claude.calls == [("plan", None, 33), ("cut", "s-plan", 33)]
    job = Path(lines[0].split("job: ")[1])
    runs = [json.loads(x) for x in (job / "work" / "runs.jsonl").read_text().splitlines()]
    assert [r["phase"] for r in runs] == ["plan", "cut"]
    assert runs[1]["resumed"] is True and runs[0]["turns"] == 7
    assert runs[0]["usage"]["output_tokens"] == 5 and runs[0]["wall_s"] >= 0
    assert any("115 tokens" in x for x in lines)


def test_run_refuses_a_plan_run_that_writes_an_edl(fake, monkeypatch):
    claude, video = fake

    def bad(prompt, job_dir, *a, **k):
        (job_dir / "work" / "edl.json").write_text("{}")
        return claude(prompt, job_dir, *a, **k)

    monkeypatch.setattr(run.agent, "run", bad)
    with pytest.raises(run.RunError, match="must only plan"):
        run.run(str(video), approve=True, say=lambda _: None)


# ---------------------------------------------------------------- end to end (no Claude)


def _layout_words(layout):
    words = []
    for e in layout:
        if e["kind"] != "phrase":
            continue
        toks = e["text"].split()
        step = (e["a1"] - e["a0"]) / len(toks)
        for i, w in enumerate(toks):
            t0 = e["a0"] + i * step
            words.append({"w": w, "t0": round(t0, 3), "t1": round(t0 + step, 3), "conf": None})
    return words


@pytest.mark.media
def test_prep_then_handwritten_edl_renders_and_verifies(capsys, clip_dir, videocut_home):
    clip, layout = clips.filler_clip(clip_dir / "fillers")
    job = run.open_job(str(clip), "thought", "end-to-end test")
    have_model = (REAL_MODELS / "whisper-large-v3-turbo" / "weights.safetensors").is_file()
    denoise = shutil.which("deep-filter") or (run.ROOT / ".tools" / "bin" / "deep-filter").exists()
    ingest = ["ingest", "--denoise"] if denoise else ["ingest"]
    face = [["face"]] if (REAL_MODELS / "face_landmarker.task").is_file() else []
    if have_model:
        (videocut_home / "models").symlink_to(REAL_MODELS)
        steps = [ingest, *face, ["transcribe"], ["sheet", "contact"]]
    else:  # CI: exact word times instead of a model
        doc = {"model": "layout", "language": "de", "duration": layout[-1]["t1"]}
        (job / "work" / "words.json").write_text(
            json.dumps({**doc, "words": _layout_words(layout)})
        )
        if face:
            (videocut_home / "models").mkdir(parents=True)
            (videocut_home / "models" / "face_landmarker.task").symlink_to(
                REAL_MODELS / "face_landmarker.task"
            )
        steps = [ingest, *face, ["candidates"], ["sheet", "contact"]]
    run.prep(job, steps, say=lambda _: None)
    for f in ("work/takes_packed.md", "work/candidates.json", "work/sheets/contact_01.jpg"):
        assert (job / f).is_file(), f
    assert not face or (job / "work" / "face.json").is_file()

    # What the cut skill would write: every phrase with a margin inside the silence around
    # it (the measured onset `a0` lies a few ms after the true one), fillers left out.
    phrases = [e for e in layout if e["kind"] == "phrase"]
    end = json.loads((job / "work" / "probe.json").read_text())["duration"]
    segments = [
        {"in": round(max(0.0, e["a0"] - 0.15), 3), "out": round(min(end, e["a1"] + 0.15), 3)}
        for e in phrases
    ]
    segments[1]["zoom_keys"] = [[round(phrases[1]["a0"] + 0.2, 3), 1.25]]
    source = json.loads((job / "job.json").read_text())["source"]
    edl = {"fps": 30, "size": [1080, 1920], "source": source, "segments": segments}
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    (job / "work" / "project.md").write_text("# Plan: end to end\n")
    job_tool.approve_job(job)

    code, s, _ = run_tool(capsys, "edl", "check", "--job", str(job))
    assert code == 0, s
    code, s, _ = run_tool(capsys, "render", "--preview", "--job", str(job))
    assert code == 0, s
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0 and s["checked"] == "out/preview.mp4" and s["failed"] == [], s
    code, s, _ = run_tool(capsys, "render", "--job", str(job))
    assert code == 0, s
    code, s, _ = run_tool(capsys, "verify", "--job", str(job))
    assert code == 0, s
    assert s["checked"] == "out/reel.mp4" and s["cuts"] == len(segments) - 1
    assert s["ok"] is True, (s["flagged"], s["failed"], s["info"])  # jump cuts: info only
    assert (job / "out" / "reel.mp4").stat().st_size > 10_000


def test_run_rejects_a_cut_run_without_final_render(fake, monkeypatch):
    claude, video = fake

    def preview_only(prompt, job_dir, *a, **k):
        res = claude(prompt, job_dir, *a, **k)
        if prompt.startswith(run.CUT_PROMPT):
            (job_dir / "out" / "verify.json").write_text('{"ok": true, "preview": true}')
        return res

    monkeypatch.setattr(run.agent, "run", preview_only)
    with pytest.raises(run.RunError, match="preview"):
        run.run(str(video), approve=True, say=lambda _: None)


def test_changed_brief_revokes_approval_and_stale_contract_goes(fake, monkeypatch, tmp_path):
    claude, video = fake
    lines = []
    assert run.run(str(video), approve=True, say=lines.append) == 0
    job = Path(lines[0].split("job: ")[1])
    assert job_tool.is_approved(job)
    run.open_job(job.name, None, "a different brief")
    assert not job_tool.is_approved(job)
    # prep copies the contract of the type and removes it when the type has none
    monkeypatch.setattr(run, "TEMPLATES", tmp_path / "templates")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "thought.md").write_text("30-45 s")
    run.open_job(job.name, "thought", None)
    REAL_PREP(job, steps=[])
    assert (job / "work" / "contract.md").read_text() == "30-45 s"
    run.open_job(job.name, "../thought", None)
    REAL_PREP(job, steps=[])
    assert not (job / "work" / "contract.md").exists()


def test_brand_runs_overlays_after_the_cut_and_reruns_skip_done_stages(fake, videocut_home):
    claude, video = fake
    lines = []
    code = run.run(str(video), approve=True, brand="_example", say=lines.append)
    assert code == 0, lines
    assert [c[0] for c in claude.calls] == ["plan", "cut", "overlays"]
    job = Path(lines[0].split("job: ")[1])
    data = json.loads((job / "job.json").read_text())
    assert data["brand"] == "_example"
    assert data["cut"]["segments_sha256"] == data["overlays"]["segments_sha256"]
    # nothing left to do: neither the cut nor the overlays run again
    code = run.run(job.name, say=lines.append)
    assert code == 0 and len(claude.calls) == 3
    assert any("cut: already done" in x for x in lines)
    assert any("overlays: already done" in x for x in lines)
    # an explicit --brand redoes only the overlays
    code = run.run(job.name, brand="_example", say=lines.append)
    assert code == 0 and [c[0] for c in claude.calls][3:] == ["overlays"]
    runs = [json.loads(x) for x in (job / "work" / "runs.jsonl").read_text().splitlines()]
    assert [r["phase"] for r in runs] == ["plan", "cut", "overlays", "overlays"]


def test_overlays_never_run_before_approval_or_change_the_cut(fake, videocut_home):
    claude, video = fake
    lines = []
    code = run.run(str(video), brand="_example", say=lines.append, ask=lambda _: "no")
    assert code == 3 and [c[0] for c in claude.calls] == ["plan"]
    job = Path(lines[0].split("job: ")[1])
    claude.overlay_segments = [{"in": 0, "out": 0.5}]
    with pytest.raises(run.RunError, match="changed the cut"):
        run.run(job.name, approve=True, say=lines.append)
    with pytest.raises(job_tool.ToolError, match="no brand"):
        run.run(job.name, brand="nope", say=lines.append)


def test_recut_clears_the_overlay_record_so_overlays_run_again(fake, videocut_home):
    claude, video = fake
    lines = []
    assert run.run(str(video), approve=True, brand="_example", say=lines.append) == 0
    job = Path(lines[0].split("job: ")[1])
    assert run.run(job.name, recut=True, say=lines.append) == 0
    assert [c[0] for c in claude.calls] == ["plan", "cut", "overlays", "cut", "overlays"]
    assert "cut it again (--recut)" in claude.prompts[3]
    assert "cut it again" not in claude.prompts[1]


def test_failed_overlay_verify_is_not_recorded_as_done(fake, videocut_home, monkeypatch):
    claude, video = fake
    lines = []
    real = claude.__call__

    def bad_verify(prompt, job_dir, *a, **k):
        res = real(prompt, job_dir, *a, **k)
        if prompt.startswith(run.OVERLAYS_PROMPT):
            (job_dir / "out" / "verify.json").write_text('{"ok": false, "reel": "out/reel.mp4"}')
        return res

    monkeypatch.setattr(run.agent, "run", bad_verify)
    assert run.run(str(video), approve=True, brand="_example", say=lines.append) == 4
    job = Path(lines[0].split("job: ")[1])
    assert run.run(job.name, say=lines.append) == 4
    assert [c[0] for c in claude.calls].count("overlays") == 2  # retried, not "already done"


def test_demo_prep_order_and_sync_stop(monkeypatch, tmp_path):
    job = tmp_path / "demo-job"
    (job / "work").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"id": "demo-job", "screen": "input/s.mov"}))
    calls = []

    def tool(_job, *argv):
        calls.append(list(argv))
        if argv == ("sync",):
            return {"ok": True, "method": "audio_xcorr", "confidence": 0.8, "offset_s": 1.5}
        return {"ok": True}

    monkeypatch.setattr(run, "tool", tool)
    REAL_PREP(job, say=lambda _: None)
    assert calls == run.DEMO_PREP
    assert ["sync"] in calls and calls.index(["sync"]) < calls.index(["screen"])

    def weak(_job, *argv):
        if argv == ("sync",):
            return {"ok": True, "method": "clap", "confidence": 0.1, "offset_s": 0.0}
        return {"ok": True}

    monkeypatch.setattr(run, "tool", weak)
    with pytest.raises(run.RunError, match="--offset"):
        REAL_PREP(job, say=lambda _: None)

    def failing(_job, *argv):
        if argv == ("sync",):
            raise run.RunError("videocut tool sync failed: the screen recording has no audio")
        return {"ok": True}

    monkeypatch.setattr(run, "tool", failing)
    with pytest.raises(run.RunError, match="no audio.*--offset"):
        REAL_PREP(job, say=lambda _: None)


def test_non_interactive_stop_names_the_full_commands(fake, monkeypatch):
    claude, video = fake
    monkeypatch.setattr(run.sys.stdin, "isatty", lambda: False)
    lines = []

    def plan_with_candidates(prompt, job_dir, *a, **k):
        res = claude(prompt, job_dir, *a, **k)
        box = [0, 0, 0.5, 0.1]
        cands = [{"id": 1, "kind": "email", "t0": 1.0, "t1": 2.0, "box": box},
                 {"id": 2, "kind": "name", "t0": 3.0, "t1": 4.0, "box": box}]  # fmt: skip
        (job_dir / "work" / "redaction.json").write_text(json.dumps({"candidates": cands}))
        return res

    monkeypatch.setattr(run.agent, "run", plan_with_candidates)
    assert run.run(str(video), say=lines.append) == 3
    job = Path(lines[0].split("job: ")[1]).name
    text = "\n".join(lines)
    assert "no terminal to ask" in text
    assert f"videocut run {job} --approve --demo-data" in text
    assert f"videocut tool job redaction {job} --redact-all && videocut run {job} --approve" in text
    assert f"videocut tool job redaction {job} --redact 1,2" in text


def test_format_reaches_the_prompts_and_a_changed_format_cuts_again(fake):
    claude, video = fake
    lines = []
    assert run.run(str(video), approve=True, format_="1:1", say=lines.append) == 0
    job = Path(lines[0].split("job: ")[1])
    data = json.loads((job / "job.json").read_text())
    assert data["format"] == "1:1" and data["cut"]["format"] == "1:1"
    assert all('"size" is [1080, 1080]' in p for p in claude.prompts)
    assert run.run(job.name, say=lines.append) == 0 and len(claude.calls) == 2  # cut done
    assert run.run(job.name, format_="16:9", say=lines.append) == 0
    assert [c[0] for c in claude.calls] == ["plan", "cut", "cut"]
    assert "output format changed" in claude.prompts[2] and "[1920, 1080]" in claude.prompts[2]
    assert any("made for 1:1" in x for x in lines)
    assert json.loads((job / "job.json").read_text())["cut"]["format"] == "16:9"


def test_a_cut_from_before_the_format_record_counts_as_9_16(fake):
    claude, video = fake
    lines = []
    assert run.run(str(video), approve=True, say=lines.append) == 0
    job = Path(lines[0].split("job: ")[1])
    data = json.loads((job / "job.json").read_text())
    del data["cut"]["format"]
    (job / "job.json").write_text(json.dumps(data))
    assert run.cut_done(job)
    (job / "job.json").write_text(json.dumps({**data, "screen": "input/s.mov"}))
    assert not run.cut_done(job)  # a demo job now defaults to 16:9: cut again
