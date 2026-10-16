"""User settings in ~/VideoCut/settings.json. The API key lives in the macOS keychain only."""

import json
import re
import subprocess
from pathlib import Path

from .paths import state_dir


def path() -> Path:
    """$VIDEOCUT_HOME/settings.json, read at call time (tests set VIDEOCUT_HOME per test)."""
    return state_dir().parent / "settings.json"


KEYCHAIN_SERVICE = "videocut"
KEYCHAIN_ACCOUNT = "anthropic-api-key"
DEFAULTS = {"auth": "local", "model": None}


def load() -> dict:
    f = path()
    data = json.loads(f.read_text()) if f.exists() else {}
    return {**DEFAULTS, **data}


def setup_done() -> bool:
    """The first-start screen was answered (or the mode was chosen with `videocut auth`
    before the screen existed)."""
    f = path()
    data = json.loads(f.read_text()) if f.exists() else {}
    return bool(data.get("setup_done")) or "auth" in data


def save(**changes) -> dict:
    data = {**load(), **changes}
    f = path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2))
    return data


def api_key() -> str | None:
    out = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or None if out.returncode == 0 else None


KEY_CHARS = re.compile(r"^[A-Za-z0-9_\-]+$")


def set_api_key(key: str) -> None:
    """Store the key in the keychain. The command goes to `security -i` on stdin, so the key
    never appears in a process list or in an error message."""
    if not KEY_CHARS.match(key):
        raise ValueError("an API key has only letters, digits, - and _")
    cmd = f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {KEYCHAIN_ACCOUNT} -w {key}\n"
    out = subprocess.run(["/usr/bin/security", "-i"], input=cmd, capture_output=True, text=True)
    if out.returncode != 0 or out.stderr.strip():
        raise RuntimeError("the keychain refused the key")
