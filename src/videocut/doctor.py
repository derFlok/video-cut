"""Checks that everything Video Cut needs is installed and working.

Every failed check carries a concrete fix. `videocut doctor --json` prints the same as data
for a helper (the person's own Claude Code follows the `fix` lines, see INSTALL.md)."""

import json
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass

from . import claude_cli, launcher, manifest, settings
from .paths import BIN, HOME, MODELS, RENDERER, ROOT, tool_env

START = f"cd {ROOT} && ./start.sh"
RESTART = f"run `{START}` again (finished steps are skipped)"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True
    fix: str = ""  # what to do when the check fails; empty when ok


def _run(*cmd: str) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, env=tool_env(), timeout=60)
    return (out.stdout or "") + (out.stderr or "")


def _binary(name: str, version_args: tuple[str, ...], expect: str) -> Check:
    path = BIN / name
    if not path.exists():
        return Check(name, False, "not installed", fix=RESTART)
    try:
        out = _run(str(path), *version_args)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Check(name, False, f"does not run: {e}",
                     fix=f"delete {path} and {RESTART}")  # fmt: skip
    first = out.strip().splitlines()[0] if out.strip() else "(no output)"
    ok = expect in out
    return Check(name, ok, first, fix="" if ok else f"wrong version: delete {path} and {RESTART}")


def check_platform() -> Check:
    mac = sys.platform == "darwin"
    arm = platform.machine() == "arm64"
    ver = platform.mac_ver()[0] if mac else ""
    major = int(ver.split(".")[0]) if ver else 0
    ok = mac and arm and major >= 14
    detail = f"macOS {ver or '?'} on {platform.machine()}"
    fix = ""
    if not (mac and arm):
        fix = "Video Cut needs a Mac with Apple Silicon (M1 or newer); this machine cannot run it"
    elif major < 14:
        fix = "update macOS to 14 (Sonoma) or newer in System Settings → General → Software Update"
    return Check("this Mac", ok, detail, fix=fix)


def check_ffmpeg_features() -> Check:
    ff = str(BIN / "ffmpeg")
    if not (BIN / "ffmpeg").exists():
        return Check("ffmpeg features", False, "ffmpeg missing", fix=RESTART)
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
        fix="" if not missing else f"delete {BIN / 'ffmpeg'} and {RESTART}",
    )


def check_c2patool(want: str) -> Check:
    """Optional: without it videos are delivered without a C2PA manifest (render.json
    "c2pa": "skipped")."""
    c = _binary("c2patool", ("--version",), want)
    c.required = False
    c.detail = (c.detail + " (C2PA provenance for finished videos)") if c.ok else (
        f"{c.detail}: finished videos get no C2PA manifest")  # fmt: skip
    return c


def check_renderer() -> Check:
    from . import install

    if not (RENDERER / "node_modules" / "playwright-core").exists():
        return Check("renderer", False, "packages missing", fix=RESTART)
    t = manifest.tool("chromium-headless-shell")
    shell = install.chromium_dir()
    if not (shell / "INSTALLATION_COMPLETE").exists():
        return Check("renderer", False, "headless Chromium missing", fix=RESTART)
    if install._state().get("chromium-shell") != t["sha256"]:
        return Check("renderer", False, "headless Chromium is not the pinned, verified build",
                     fix=RESTART)  # fmt: skip
    want = install.playwright_revision()
    if want and want != t["revision"]:
        detail = f"playwright-core wants revision {want}, manifest pins {t['revision']}"
        return Check("renderer", False, detail, fix="update manifest/tools.json")
    return Check("renderer", True, f"playwright-core + headless Chromium {t['version']}")


def check_app() -> Check:
    from .install import APP_DIST

    ok = (APP_DIST / "index.html").is_file()
    return Check("app", ok, "built" if ok else "not built", fix="" if ok else RESTART)


def check_models() -> list[Check]:
    checks = []
    for name, m in manifest.models().items():
        path = MODELS / (m.get("filename") or name)
        ok = path.exists()
        default = bool(m.get("default"))
        detail = "ready" if ok else ("not downloaded" if default else "optional, not downloaded")
        fix = "" if ok else (RESTART if default else f"optional: `videocut models pull {name}`")
        checks.append(Check(f"model {name}", ok, detail, required=default, fix=fix))
    return checks


SIGN_IN = (
    "sign in once: run `claude auth login` in Terminal (opens the browser), or choose "
    '"API key" in Video Cut\'s settings'
)
INSTALL_CLAUDE = (
    "install Anthropic's official claude program (https://code.claude.com/docs, native "
    'installer, no sudo), or choose "API key" in Video Cut\'s settings'
)


def check_claude() -> list[Check]:
    st = claude_cli.status()
    conf = settings.load()
    mode = conf.get("auth")
    checks = []
    if not st["installed"]:
        checks.append(Check("claude", False, claude_cli.NOT_FOUND,
                            required=False, fix=INSTALL_CLAUDE))  # fmt: skip
    else:
        checks.append(Check("claude", True, st["version"] or st["path"]))
    if mode == "apikey":
        has_key = bool(_api_key())
        checks.append(
            Check(
                "claude auth",
                has_key,
                "API key mode (key in the keychain)" if has_key else "API key mode, no key stored",
                required=False,
                fix=""
                if has_key
                else "store a key with `videocut auth key` or in the app settings",
            )
        )
    elif st["installed"]:
        ok = st["signed_in"]
        checks.append(
            Check(
                "claude auth",
                ok,
                f"own sign-in ({st['method']}), personal use only, see docs/AUTH.md"
                if ok
                else (st["error"] or "the claude program is not signed in"),
                required=False,
                fix="" if ok else SIGN_IN,
            )
        )
    return checks


def _api_key() -> str | None:
    try:
        return settings.api_key()
    except Exception:  # noqa: BLE001 — no keychain: no key
        return None


def check_launcher() -> Check:
    found = [d / launcher.APP_NAME for d in launcher.default_dirs()
             if (d / launcher.APP_NAME).exists()]  # fmt: skip
    return Check(
        "launcher",
        bool(found),
        str(found[0]) if found else "no Video Cut.app",
        required=False,
        fix="" if found else "`videocut launcher` creates ~/Applications/Video Cut.app",
    )


def check_default_brand() -> Check:
    """Informational: the brand new videos preselect and the app's look follows."""
    from . import brand

    conf = settings.load()
    ref = conf.get("default_brand")
    if not ref:
        return Check("default brand", True, "none (neutral)", required=False)
    if settings.default_brand() is None:
        fix = "choose another default brand under Brands, or none"
        return Check("default brand", False, f"{ref} (folder missing)", required=False, fix=fix)
    _, errors, _ = brand.inspect(ref)
    follows = (
        "app look follows it"
        if conf.get("theme_follows_brand") is not False
        else "app look neutral"
    )
    if errors:
        detail = f"{ref} has {len(errors)} problem(s); app look neutral"
        fix = f"`videocut tool brand check {ref}` lists them"
        return Check("default brand", False, detail, required=False, fix=fix)
    return Check("default brand", True, f"{ref} ({follows})", required=False)


def collect() -> list[Check]:
    t = manifest.load()["tools"]
    checks = [
        check_platform(),
        Check(
            "python",
            sys.version.split()[0] == t["python"]["version"],
            sys.version.split()[0],
            fix=f"pinned is {t['python']['version']}: delete {ROOT / '.venv'} and {RESTART}",
        ),
        _binary("uv", ("--version",), t["uv"]["version"]),
        _binary("node", ("--version",), t["node"]["version"]),
        _binary("ffmpeg", ("-hide_banner", "-version"), t["ffmpeg"]["version"]),
        _binary("ffprobe", ("-hide_banner", "-version"), t["ffprobe"]["version"]),
        check_ffmpeg_features(),
        _binary("deep-filter", ("--version",), t["deep-filter"]["version"]),
        check_c2patool(t["c2patool"]["version"]),
        check_renderer(),
        check_app(),
        *check_models(),
        *check_claude(),
        check_launcher(),
        check_default_brand(),
    ]
    free_gb = shutil.disk_usage(HOME if HOME.exists() else HOME.parent).free / 1e9
    checks.append(
        Check(
            "disk space",
            free_gb >= 8,
            f"{free_gb:.0f} GB free",
            required=False,
            fix="" if free_gb >= 8 else "free at least 8 GB (a video job needs several GB)",
        )
    )
    for c in checks:
        if c.ok:
            c.fix = ""
    return checks


def report(checks: list[Check]) -> dict:
    failed = [c for c in checks if c.required and not c.ok]
    return {
        "ok": not failed,
        "root": str(ROOT),
        "data": str(HOME),
        "auth": settings.load().get("auth"),
        "default_brand": settings.default_brand(),
        "checks": [asdict(c) for c in checks],
        "failed": [c.name for c in failed],
        "warnings": [c.name for c in checks if not c.required and not c.ok],
    }


def run(as_json: bool = False) -> int:
    checks = collect()
    if as_json:
        print(json.dumps(report(checks), indent=2))
        return 0 if report(checks)["ok"] else 1
    width = max(len(c.name) for c in checks)
    for c in checks:
        mark = (
            "\033[32m✓\033[0m"
            if c.ok
            else ("\033[31m✗\033[0m" if c.required else "\033[33m!\033[0m")
        )
        print(f"  {mark} {c.name.ljust(width)}  {c.detail}")
        if c.fix:
            print(f"    {' ' * width}  → {c.fix}")
    print(f"\n  Data folder: {HOME}")
    failed = [c for c in checks if c.required and not c.ok]
    if failed:
        print(f"\n  {len(failed)} required check(s) failed.")
        return 1
    print("\n  All required checks passed.")
    return 0
