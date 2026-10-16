"""The official `claude` program: where it is, whether it is signed in, a cheap test call.

Video Cut only ever *runs* the official program and reads what it prints. It never opens
the program's configuration or credential files, and it never stores what it reads here
except the sign-in method (the account line is shown, never saved).

A launcher started from Finder has a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin), so the
usual install locations are searched as well.
"""

import json
import os
import pwd
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

# Where the official program is installed (searched in this order after PATH):
# the native installer (~/.local/bin), the older local install (~/.claude/local), Homebrew
# and npm global installs, and last the copy the Claude desktop app keeps for itself (a
# signed macOS bundle per version, the newest is used).
KNOWN = (
    "~/.local/bin/claude",
    "~/.claude/local/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    "~/.npm-global/bin/claude",
)
DESKTOP = "~/Library/Application Support/Claude/claude-code"
NOT_FOUND = (
    "the claude program was not found (looked in PATH, ~/.local/bin, ~/.claude/local, "
    "/opt/homebrew/bin, /usr/local/bin, ~/.npm-global/bin and the Claude desktop app)"
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
    return _desktop_copy()


def _version_key(name: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in name.split("."))


def _desktop_copy() -> str | None:
    """The desktop app's own program: <DESKTOP>/<version>/<id>/claude.app/Contents/MacOS/claude."""
    root = Path(DESKTOP).expanduser()
    try:
        versions = sorted((d for d in root.iterdir() if d.is_dir()),
                          key=lambda d: _version_key(d.name), reverse=True)  # fmt: skip
    except OSError:
        return None
    for v in versions:
        for exe in sorted(v.glob("*/claude.app/Contents/MacOS/claude")):
            if os.access(exe, os.X_OK):
                return str(exe)
    return None


_HELP: dict[str, str] = {}


def supports(flag: str) -> bool:
    """Whether the installed program's --help lists a flag (asked once per process)."""
    path = exe()
    if not path:
        return False
    if path not in _HELP:
        try:
            out = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=20,
                                 env=_env())  # fmt: skip
            _HELP[path] = out.stdout
        except (OSError, subprocess.TimeoutExpired):
            _HELP[path] = ""
    return flag in _HELP[path].split()


def fill_env(env: dict[str, str]) -> dict[str, str]:
    """The program finds its own sign-in by the user name: a minimal environment (env -i,
    some launchers) lacks USER, and it then reports "not signed in"."""
    if not env.get("USER") or not env.get("LOGNAME"):
        name = pwd.getpwuid(os.getuid()).pw_name
        env.setdefault("USER", name)
        env.setdefault("LOGNAME", name)
    return env


def _env() -> dict[str, str]:
    env = fill_env(dict(os.environ))
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


NO_BARE = "--no-bare"


def local_mode_flags() -> list[str]:
    """Local mode needs the program's own sign-in, which --bare ignores. Claude Code
    2.1.301 runs `-p` non-bare by default and has no opt-out flag; if a later version
    makes --bare the default and offers the opt-out, pass it."""
    return [NO_BARE] if supports(NO_BARE) else []


def test_call(api_key: str | None, timeout: float = 90) -> dict:
    """One tiny headless call through the official program (no tools, one turn), the
    same way a job run connects: an API key runs in --bare mode, the own sign-in with the
    project's settings only. Returns {ok, message, seconds, cost_usd}."""
    path = exe()
    if not path:
        return {"ok": False, "message": NOT_FOUND, "seconds": 0}
    cmd = [path, "-p", TEST_PROMPT, "--max-turns", "1", "--tools", "", "--output-format", "json"]
    cmd += ["--model", TEST_MODEL]
    cmd += ["--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}']
    env = _env()
    env.pop("ANTHROPIC_API_KEY", None)
    env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
        cmd.append("--bare")
    else:
        cmd += ["--setting-sources", "project", *local_mode_flags()]
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
