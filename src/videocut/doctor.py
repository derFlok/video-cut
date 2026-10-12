"""Checks that everything Video Cut needs is installed and working."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from . import manifest
from .paths import BIN, BROWSERS, HOME, MODELS, RENDERER, tool_env


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _run(*cmd: str) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, env=tool_env(), timeout=60)
    return (out.stdout or "") + (out.stderr or "")


def _binary(name: str, version_args: tuple[str, ...], expect: str) -> Check:
    path = BIN / name
    if not path.exists():
        return Check(name, False, "not installed — run ./start.sh")
    try:
        out = _run(str(path), *version_args)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Check(name, False, f"does not run: {e}")
    first = out.strip().splitlines()[0] if out.strip() else "(no output)"
    return Check(name, expect in out, first)


def check_ffmpeg_features() -> Check:
    ff = str(BIN / "ffmpeg")
    if not (BIN / "ffmpeg").exists():
        return Check("ffmpeg features", False, "ffmpeg missing")
    enc = _run(ff, "-hide_banner", "-encoders")
    flt = _run(ff, "-hide_banner", "-filters")
    need_enc = ["libx264", "prores_ks", "aac"]
    need_flt = ["loudnorm", "sidechaincompress", "silencedetect", "scdet", "ass", "overlay"]
    missing = [e for e in need_enc if f" {e} " not in enc] + [
        f for f in need_flt if f" {f} " not in flt
    ]
    return Check(
        "ffmpeg features",
        not missing,
        "libx264, ProRes 4444, loudnorm, ducking, libass"
        if not missing
        else f"missing: {', '.join(missing)}",
    )


def check_renderer() -> Check:
    if not (RENDERER / "node_modules" / "playwright-core").exists():
        return Check("renderer", False, "packages missing — run ./start.sh")
    shells = list(BROWSERS.glob("chromium_headless_shell-*"))
    if not shells:
        return Check("renderer", False, "headless Chromium missing — run ./start.sh")
    return Check("renderer", True, f"playwright-core + {shells[0].name}")


def check_models() -> list[Check]:
    checks = []
    for name, m in manifest.models().items():
        path = MODELS / (m.get("filename") or name)
        ok = path.exists()
        detail = (
            "ready"
            if ok
            else (
                "not downloaded — run ./start.sh"
                if m.get("default")
                else f"optional — videocut models pull {name}"
            )
        )
        checks.append(Check(f"model {name}", ok, detail, required=bool(m.get("default"))))
    return checks


def check_claude() -> list[Check]:
    exe = shutil.which("claude")
    if not exe:
        return [
            Check(
                "claude",
                False,
                "Claude Code not found. Install it, or set an API key (see docs/AUTH.md).",
                required=False,  # tools install fine without it; jobs need it
            )
        ]
    version = _run(exe, "--version").strip().splitlines()[0]
    checks = [Check("claude", True, version)]
    if os.environ.get("ANTHROPIC_API_KEY"):
        checks.append(Check("claude auth", True, "API key mode (ANTHROPIC_API_KEY set)"))
        return checks
    try:
        # Only the two fields below are read; account details are never shown or stored.
        status = json.loads(_run(exe, "auth", "status"))
        logged_in = bool(status.get("loggedIn"))
        method = status.get("authMethod", "unknown")
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        logged_in, method = False, "unknown"
    checks.append(
        Check(
            "claude auth",
            logged_in,
            f"local mode, signed in ({method}) — personal use only, see docs/AUTH.md"
            if logged_in
            else "not signed in: run `claude auth login`, or use an API key (docs/AUTH.md)",
            required=False,
        )
    )
    return checks


def collect() -> list[Check]:
    t = manifest.load()["tools"]
    checks = [
        Check("python", sys.version_info[:2] == (3, 12), sys.version.split()[0]),
        _binary("uv", ("--version",), t["uv"]["version"]),
        _binary("node", ("--version",), t["node"]["version"]),
        _binary("ffmpeg", ("-hide_banner", "-version"), t["ffmpeg"]["version"]),
        _binary("ffprobe", ("-hide_banner", "-version"), t["ffprobe"]["version"]),
        check_ffmpeg_features(),
        _binary("deep-filter", ("--version",), t["deep-filter"]["version"]),
        check_renderer(),
        *check_models(),
        *check_claude(),
    ]
    free_gb = shutil.disk_usage(HOME if HOME.exists() else HOME.parent).free / 1e9
    checks.append(Check("disk space", free_gb >= 8, f"{free_gb:.0f} GB free", required=False))
    return checks


def run() -> int:
    checks = collect()
    width = max(len(c.name) for c in checks)
    for c in checks:
        mark = (
            "\033[32m✓\033[0m"
            if c.ok
            else ("\033[31m✗\033[0m" if c.required else "\033[33m!\033[0m")
        )
        print(f"  {mark} {c.name.ljust(width)}  {c.detail}")
    print(f"\n  Data folder: {HOME}")
    failed = [c for c in checks if c.required and not c.ok]
    if failed:
        print(f"\n  {len(failed)} required check(s) failed.")
        return 1
    print("\n  All required checks passed.")
    return 0
