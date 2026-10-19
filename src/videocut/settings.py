"""User settings in ~/VideoCut/settings.json. API keys live in the macOS keychain only
(service "videocut", one account per key: SECRET_ACCOUNTS)."""

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
# default_brand: the brand id the new-job form preselects and the app's look follows
# (theme_follows_brand); None is neutral. ai_label_illustrations: AI-generated illustrations
# get the visible AI label too (realistic AI material always does: EU AI Act Art. 50).
# Material (tools/material.py): the new-job form's default mode, the image generator
# (image_provider: a name from tools/_material_providers, "none" for no generated stills),
# the stock sources and the cost limit per job for generated images.
DEFAULTS = {
    "auth": "local",
    "model": None,
    "default_brand": None,
    "theme_follows_brand": True,
    "material_default_mode": "none",
    "image_provider": "gemini",
    "stock_providers": ["pixabay", "pexels", "coverr"],
    "material_budget_usd": 1.0,
    "ai_label_illustrations": True,
}
# Keychain accounts a key may be stored under (the Anthropic key and the material providers').
SECRET_ACCOUNTS = (
    KEYCHAIN_ACCOUNT,
    "pixabay-api-key",
    "pexels-api-key",
    "coverr-api-key",
    "gemini-api-key",
    "openai-api-key",
)


def load() -> dict:
    f = path()
    data = json.loads(f.read_text()) if f.exists() else {}
    return {**DEFAULTS, **data}


def default_brand() -> str | None:
    """The default brand id, or None when unset or the brand folder is gone (a brand
    removed outside the app reads as neutral)."""
    from . import brand

    ref = load().get("default_brand")
    return ref if isinstance(ref, str) and ref in brand.list_ids() else None


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


def _account(account: str) -> str:
    if account not in SECRET_ACCOUNTS:
        raise ValueError(f"unknown keychain account {account!r}")
    return account


def secret(account: str) -> str | None:
    """The key stored under `account` (SECRET_ACCOUNTS), None when there is none."""
    out = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            _account(account),
            "-w",
        ],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or None if out.returncode == 0 else None


def has_secret(account: str) -> bool:
    """Whether a key is stored under `account`, without reading it."""
    out = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", _account(account)],
        capture_output=True,
        text=True,
    )
    return out.returncode == 0


KEY_CHARS = re.compile(r"^[A-Za-z0-9_\-]+$")


def set_secret(account: str, key: str) -> None:
    """Store `key` under `account` in the keychain; an empty key removes it. The command
    goes to `security -i` on stdin, so the key never appears in a process list or in an
    error message."""
    account = _account(account)
    if not key:
        subprocess.run(
            ["/usr/bin/security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
            capture_output=True,
            text=True,
        )
        return
    if not KEY_CHARS.match(key):
        raise ValueError("an API key has only letters, digits, - and _")
    cmd = f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {account} -w {key}\n"
    out = subprocess.run(["/usr/bin/security", "-i"], input=cmd, capture_output=True, text=True)
    if out.returncode != 0 or out.stderr.strip():
        raise RuntimeError("the keychain refused the key")


def api_key() -> str | None:
    """The Anthropic API key (API-key mode)."""
    return secret(KEYCHAIN_ACCOUNT)


def set_api_key(key: str) -> None:
    if not key:
        raise ValueError("an API key has only letters, digits, - and _")
    set_secret(KEYCHAIN_ACCOUNT, key)
