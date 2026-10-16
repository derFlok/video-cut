"""The official `claude` program: where it is, whether it is signed in, a cheap test call.

Video Cut only ever *runs* the official program and reads what it prints. It never opens
the program's configuration or credential files, and it never stores what it reads here
except the sign-in method (the account line is shown, never saved).

A launcher started from Finder has a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin), so the
usual install locations are searched as well.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

# The official installer, npm global installs and Homebrew put the program here.
KNOWN = (
    "~/.local/bin/claude",
    "~/.claude/local/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    "~/.npm-global/bin/claude",
)
CONSOLE_URL = "https://console.anthropic.com/settings/keys"
TEST_PROMPT = "Reply with the single word OK."
TEST_MODEL = "haiku"  # the cheapest alias the program knows; the check only needs an answer


def exe() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for p in KNOWN:
        path = Path(p).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _env() -> dict[str, str]:
    env = dict(os.environ)
    found = exe()
    if found:  # the program may need its own folder on PATH (node shims)
        env["PATH"] = f"{Path(found).parent}{os.pathsep}{env.get('PATH', '')}"
    return env


def status(timeout: float = 20) -> dict:
    """Ask the official program: installed? version? signed in, and how?

    `account` is the e-mail address only if the program prints one; it is for display and
    is never written anywhere."""
    path = exe()
    out: dict = {
        "installed": bool(path),
        "path": path,
        "version": None,
        "signed_in": False,
        "method": None,
        "account": None,
        "subscription": None,
        "error": None,
    }
    if not path:
        return out
    try:
        v = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=timeout, env=_env()
        )
        out["version"] = (v.stdout.strip().splitlines() or [None])[0]
        s = subprocess.run(
            [path, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env(),
        )
        data = json.loads(s.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired) as e:
        out["error"] = f"the claude program did not answer: {e}"
        return out
    except json.JSONDecodeError:
        out["error"] = "the claude program printed no sign-in status"
        return out
    out["signed_in"] = bool(data.get("loggedIn"))
    out["method"] = data.get("authMethod") if out["signed_in"] else None
    if out["signed_in"]:
        account = data.get("email")
        out["account"] = account if isinstance(account, str) and "@" in account else None
        sub = data.get("subscriptionType")
        out["subscription"] = sub if isinstance(sub, str) else None
    return out


def test_call(api_key: str | None, timeout: float = 90) -> dict:
    """One tiny headless call through the official program (no tools, one turn), the
    same way a job run connects: an API key runs in --bare mode, the own sign-in with the
    project's settings only. Returns {ok, message, seconds, cost_usd}."""
    path = exe()
    if not path:
        return {"ok": False, "message": "the claude program is not installed", "seconds": 0}
    cmd = [path, "-p", TEST_PROMPT, "--max-turns", "1", "--tools", "", "--output-format", "json"]
    cmd += ["--model", TEST_MODEL]
    cmd += ["--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}']
    env = _env()
    env.pop("ANTHROPIC_API_KEY", None)
    env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
        cmd.append("--bare")
    else:
        cmd += ["--setting-sources", "project"]
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run(
                cmd, cwd=tmp, capture_output=True, text=True, timeout=timeout, env=env
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "message": str(e), "seconds": round(time.monotonic() - t0, 1)}
    seconds = round(time.monotonic() - t0, 1)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        tail = (r.stderr or r.stdout).strip()[-300:]
        return {"ok": False, "message": tail or f"exit code {r.returncode}", "seconds": seconds}
    ok = not d.get("is_error") and r.returncode == 0
    msg = str(d.get("result") or "")[:300]
    return {"ok": ok, "message": msg, "seconds": seconds, "cost_usd": d.get("total_cost_usd")}
