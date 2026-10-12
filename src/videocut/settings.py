"""User settings in ~/VideoCut/settings.json. The API key lives in the macOS keychain only."""

import json
import subprocess

from .paths import HOME

FILE = HOME / "settings.json"
KEYCHAIN_SERVICE = "videocut"
KEYCHAIN_ACCOUNT = "anthropic-api-key"
DEFAULTS = {"auth": "local", "model": None}


def load() -> dict:
    data = json.loads(FILE.read_text()) if FILE.exists() else {}
    return {**DEFAULTS, **data}


def save(**changes) -> dict:
    data = {**load(), **changes}
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data, indent=2))
    return data


def api_key() -> str | None:
    out = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or None if out.returncode == 0 else None


def set_api_key(key: str) -> None:
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
            "-w",
            key,
        ],
        check=True,
        capture_output=True,
    )
