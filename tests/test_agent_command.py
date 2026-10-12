from videocut import agent, settings


def _cmd(monkeypatch, auth, key=None):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(settings, "load", lambda: {"auth": auth, "model": None})
    monkeypatch.setattr(settings, "api_key", lambda: key)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-shell")
    return agent.command("do it")


def _value(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_agent_is_locked_down(monkeypatch):
    cmd, env = _cmd(monkeypatch, "local")
    assert _value(cmd, "--tools") == "Bash,Read,Write,Edit,Skill"
    assert _value(cmd, "--permission-mode") == "dontAsk"
    assert "--strict-mcp-config" in cmd
    allowed = cmd[cmd.index("--allowedTools") + 1 : cmd.index("--permission-mode")]
    assert [a for a in allowed if a.startswith("Bash")] == ["Bash(videocut tool *)"]
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"


def test_local_mode_never_passes_an_api_key(monkeypatch):
    cmd, env = _cmd(monkeypatch, "local")
    assert "ANTHROPIC_API_KEY" not in env
    assert "--bare" not in cmd
    assert _value(cmd, "--setting-sources") == "project"


def test_api_key_mode_uses_keychain_key_and_bare(monkeypatch):
    cmd, env = _cmd(monkeypatch, "apikey", key="sk-ant-from-keychain")
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-from-keychain"
    assert "--bare" in cmd


def test_api_key_mode_without_key_fails_loudly(monkeypatch):
    import pytest

    with pytest.raises(agent.AgentError):
        _cmd(monkeypatch, "apikey", key=None)


def test_stream_events_are_summarised():
    import json

    use = {"type": "tool_use", "name": "Bash", "input": {"command": "videocut tool hello"}}
    line = json.dumps({"type": "assistant", "message": {"content": [use]}})
    (ev,) = list(agent._events(line))
    assert ev.kind == "tool" and ev.text == "Bash: videocut tool hello"
