"""Runs Claude headless for a job and turns its event stream into progress events.

Claude runs as the official `claude` program with:
- the Video Cut plugin (skills) from this repository,
- exactly five tools (Bash, Read, Write, Edit, Skill), no MCP servers, no web access,
- Bash limited to `videocut tool ...`, reads limited to the job folder, writes limited to
  its work/ folder (plus the ping file). job.json (it carries the human approval) and
  anything Claude Code would load as project configuration (.claude/, CLAUDE.md, .mcp.json)
  are never writable, so a prompt-injected run cannot plant settings or hooks for the next
  run; `videocut tool job approve` also refuses inside an agent run (VIDEOCUT_AGENT),
- "don't ask" permissions: anything not allowed is refused, nothing waits for a human.

Auth: "local" uses the user's own Claude Code sign-in (personal use only, see docs/AUTH.md);
"apikey" passes the key from the keychain and runs in --bare mode, which also keeps the
user's personal Claude Code settings, hooks and CLAUDE.md out of the run.
"""

import json
import os
import shutil  # noqa: F401 — tests patch agent.shutil.which (claude_cli.exe uses it)
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from . import claude_cli, settings
from .paths import ROOT, tool_env

PLUGIN = ROOT / "claude-plugin"
TOOLS = "Bash,Read,Write,Edit,Skill"
ALLOWED = [
    "Bash(videocut tool *)",
    "Read(./**)",
    "Write(./work/**)",
    "Edit(./work/**)",
    "Write(./pong.txt)",
    "Skill",
]
DENIED = [
    f"{tool}({path})"
    for path in ("./job.json", "./.claude/**", "./CLAUDE.md", "./.mcp.json")
    for tool in ("Write", "Edit")
]
EMPTY_MCP = '{"mcpServers": {}}'


class AgentError(RuntimeError):
    pass


@dataclass
class Event:
    kind: str  # start | tool | text | done | error
    text: str
    data: dict = field(default_factory=dict)


@dataclass
class Result:
    ok: bool
    text: str
    session_id: str | None
    cost_usd: float | None
    turns: int | None
    auth: str
    usage: dict | None = None  # token counts of the result event
    duration_ms: int | None = None


def command(prompt: str, resume: str | None = None, max_turns: int = 60) -> tuple[list[str], dict]:
    exe = claude_cli.exe()
    if not exe:
        raise AgentError(claude_cli.NOT_FOUND + "; see docs/AUTH.md")
    conf = settings.load()
    cmd = [
        exe,
        "-p",
        prompt,
        "--plugin-dir",
        str(PLUGIN),
        "--tools",
        TOOLS,
        "--allowedTools",
        *ALLOWED,
        "--disallowedTools",
        *DENIED,
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--mcp-config",
        EMPTY_MCP,
        "--max-turns",
        str(max_turns),
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if conf.get("model"):
        cmd += ["--model", conf["model"]]
    if resume:
        cmd += ["--resume", resume]

    env = claude_cli.fill_env(tool_env())
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{env['PATH']}{os.pathsep}{Path(exe).parent}"
    env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["VIDEOCUT_AGENT"] = "1"
    env.pop("VIDEOCUT_UI_SERVER", None)  # an agent run never inherits the app's flag
    env.pop("ANTHROPIC_API_KEY", None)
    if conf["auth"] == "apikey":
        key = settings.api_key()
        if not key:
            raise AgentError("API key mode is selected but no key is stored (videocut auth key).")
        env["ANTHROPIC_API_KEY"] = key
        cmd.append("--bare")
    else:
        cmd += ["--setting-sources", "project", *claude_cli.local_mode_flags()]
    return cmd, env


def _events(line: str) -> Iterator[Event]:
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return
    t = d.get("type")
    if t == "system" and d.get("subtype") == "init":
        yield Event(
            "start", f"Claude started ({d.get('model')})", {"session_id": d.get("session_id")}
        )
    elif t == "assistant":
        for c in d["message"].get("content", []):
            if c.get("type") == "tool_use":
                inp = c.get("input", {})
                what = inp.get("command") or inp.get("skill") or inp.get("file_path") or ""
                yield Event("tool", f"{c['name']}: {what}".strip(), {"tool": c["name"]})
            elif c.get("type") == "text" and c.get("text", "").strip():
                yield Event("text", c["text"].strip())
    elif t == "result":
        yield Event(
            "done" if not d.get("is_error") else "error",
            d.get("result", ""),
            {
                "session_id": d.get("session_id"),
                "cost_usd": d.get("total_cost_usd"),
                "turns": d.get("num_turns"),
                "subtype": d.get("subtype"),
                "usage": d.get("usage"),
                "duration_ms": d.get("duration_ms"),
            },
        )


def run(
    prompt: str,
    job_dir: Path,
    on_event: Callable[[Event], None] = lambda e: None,
    resume: str | None = None,
    log: Path | None = None,
    max_turns: int = 60,
) -> Result:
    cmd, env = command(prompt, resume, max_turns)
    auth = settings.load()["auth"]
    final: Event | None = None
    log_f = log.open("a") if log else None
    # stderr goes to a file: a pipe nobody drains while stdout is read could fill and hang
    err_f = tempfile.TemporaryFile("w+")
    proc = subprocess.Popen(
        cmd,
        cwd=job_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=err_f,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    # readline, not iteration: iterating a pipe reads ahead and delays the last events
    for line in iter(proc.stdout.readline, ""):
        if log_f:
            log_f.write(line)
        for ev in _events(line):
            on_event(ev)
            if ev.kind in ("done", "error"):
                final = ev
    proc.wait()
    if log_f:
        log_f.close()
    err_f.seek(0)
    err = err_f.read().strip()[-500:]
    err_f.close()
    if final is None:
        ev = Event("error", err or f"claude exited with code {proc.returncode}")
        on_event(ev)
        return Result(False, ev.text, None, None, None, auth)
    return Result(
        final.kind == "done",
        final.text,
        final.data.get("session_id"),
        final.data.get("cost_usd"),
        final.data.get("turns"),
        auth,
        final.data.get("usage"),
        final.data.get("duration_ms"),
    )
