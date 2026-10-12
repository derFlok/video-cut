"""`videocut run`: agent command per phase, orchestration with a fake Claude, and an
end-to-end prep -> hand-written EDL -> render -> verify on a generated clip (no Claude)."""

import json
import os
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


@pytest.mark.parametrize("prompt", [run.PLAN_PROMPT, run.CUT_PROMPT])
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
    assert (
        agent.PLUGIN / "skills" / ("plan" if "plan" in prompt else "cut") / "SKILL.md"
    ).is_file()
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


# ---------------------------------------------------------------- orchestration


class FakeClaude:
    """Stands in for agent.run: plan writes project.md, cut writes the outputs."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt, job_dir, on_event, resume=None, log=None, max_turns=60):
        phase = "plan" if prompt == run.PLAN_PROMPT else "cut"
        self.calls.append((phase, resume, max_turns))
        if phase == "plan":
            (job_dir / "work" / "project.md").write_text("# Plan: test\n")
        else:
            assert job_tool.is_approved(job_dir)
            (job_dir / "out" / "reel.mp4").write_bytes(b"x")
            (job_dir / "out" / "verify.json").write_text('{"ok": true, "reel": "out/reel.mp4"}')
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
    if have_model:
        (videocut_home / "models").symlink_to(REAL_MODELS)
        steps = [ingest, ["transcribe"], ["sheet", "contact"]]
    else:  # CI: exact word times instead of a model
        doc = {"model": "layout", "language": "de", "duration": layout[-1]["t1"]}
        (job / "work" / "words.json").write_text(
            json.dumps({**doc, "words": _layout_words(layout)})
        )
        steps = [ingest, ["candidates"], ["sheet", "contact"]]
    run.prep(job, steps, say=lambda _: None)
    for f in ("work/takes_packed.md", "work/candidates.json", "work/sheets/contact_01.jpg"):
        assert (job / f).is_file(), f

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
        if prompt == run.CUT_PROMPT:
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
