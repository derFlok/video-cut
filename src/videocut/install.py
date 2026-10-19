"""Install pinned tools into .tools/ and models into ~/VideoCut/models.

Idempotent and resumable: a finished step is recorded in .tools/installed.json and skipped;
an interrupted download continues from its part file (download.py, .tools/downloads/).
`./start.sh` prints steps 1-3 (this Mac, uv, Python); this module prints steps 4-7."""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from . import manifest
from .download import fetch
from .paths import BIN, BROWSERS, MODELS, RENDERER, ROOT, TOOLS, tool_env

STATE = TOOLS / "installed.json"


def _state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _mark(key: str, value: str) -> None:
    s = _state()
    s[key] = value
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, sort_keys=True))


def _done(key: str, value: str, *paths: Path) -> bool:
    return _state().get(key) == value and all(p.exists() for p in paths)


def _make_exec(p: Path) -> None:
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


TOTAL_STEPS = 7
DOWNLOADS = TOOLS / "downloads"  # archives while they download (resumable part files)


def _step(msg: str) -> None:
    print(f"  \033[1m▸\033[0m {msg}", flush=True)


def _minutes(mb: float, mbit: int = 100) -> str:
    """Rough download time on a typical line: 1.6 GB on 100 Mbit/s -> "about 2 minutes"."""
    s = mb * 8 / mbit
    return (
        "under a minute"
        if s < 60
        else f"about {round(s / 60)} minute" + ("s" if round(s / 60) != 1 else "")
    )


def headline(n: int, what: str, mb: float | None = None, done: bool = False) -> None:
    size = f", {_size(mb)}, {_minutes(mb)} on 100 Mbit/s" if mb and not done else ""
    tail = " (already done)" if done else ""
    print(f"\033[1mStep {n}/{TOTAL_STEPS}: {what}{size}{tail}\033[0m", flush=True)


def _size(mb: float) -> str:
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"


def install_node() -> None:
    t = manifest.tool("node")
    target = TOOLS / f"node-{t['version']}"
    if _done("node", t["sha256"], target, BIN / "node"):
        return
    _step(f"Installing Node {t['version']}")
    with tempfile.TemporaryDirectory(dir=TOOLS) as tmp:
        archive = fetch(t["url"], t["sha256"], DOWNLOADS / f"node-{t['version']}.tar.gz", "node")
        with tarfile.open(archive) as tf:
            tf.extractall(tmp, filter="tar")
        archive.unlink()
        extracted = next(p for p in Path(tmp).iterdir() if p.is_dir())
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(extracted, target)
    for name in ("node", "npm", "npx"):
        link = BIN / name
        link.unlink(missing_ok=True)
        link.symlink_to(target / "bin" / name)
    _mark("node", t["sha256"])


def install_zipped_binary(name: str) -> None:
    t = manifest.tool(name)
    dest = BIN / name
    if _done(name, t["sha256"], dest):
        return
    _step(f"Installing {name} {t['version']}")
    with tempfile.TemporaryDirectory(dir=TOOLS) as tmp:
        archive = fetch(t["url"], t["sha256"], DOWNLOADS / f"{name}-{t['version']}.zip", name)
        member = t.get("member", name)  # the binary's path inside the zip
        with zipfile.ZipFile(archive) as zf:
            zf.extract(member, tmp)
        archive.unlink()
        shutil.move(Path(tmp) / member, dest)
    _make_exec(dest)
    _mark(name, t["sha256"])


def install_plain_binary(name: str) -> None:
    t = manifest.tool(name)
    dest = BIN / name
    if _done(name, t["sha256"], dest):
        return
    _step(f"Installing {name} {t['version']}")
    fetch(t["url"], t["sha256"], dest, name)
    _make_exec(dest)
    _mark(name, t["sha256"])


def install_renderer() -> None:
    lock = RENDERER / "package-lock.json"
    from .download import sha256_of

    key = sha256_of(lock)
    if not _done("renderer", key, RENDERER / "node_modules"):
        _step("Installing renderer packages")
        subprocess.run(
            [str(BIN / "npm"), "ci", "--no-audit", "--no-fund", "--silent"],
            cwd=RENDERER,
            env=tool_env(),
            check=True,
        )
        _mark("renderer", key)
    install_chromium()


def playwright_revision() -> str | None:
    """The headless shell revision the installed playwright-core expects."""
    f = RENDERER / "node_modules" / "playwright-core" / "browsers.json"
    try:
        browsers = json.loads(f.read_text())["browsers"]
    except (OSError, ValueError, KeyError):
        return None
    return next((b["revision"] for b in browsers if b["name"] == "chromium-headless-shell"), None)


def chromium_dir() -> Path:
    rev = manifest.tool("chromium-headless-shell")["revision"]
    return BROWSERS / f"chromium_headless_shell-{rev}"


def install_chromium() -> None:
    """The renderer's headless Chromium, pinned and checksum-verified like every other tool
    (playwright's own installer does not verify), unpacked where playwright looks for it."""
    t = manifest.tool("chromium-headless-shell")
    want = playwright_revision()
    if want and want != t["revision"]:
        raise RuntimeError(
            f"playwright-core expects headless shell revision {want}, manifest/tools.json pins "
            f"{t['revision']}: update the chromium-headless-shell entry (url, sha256) together"
        )
    dest = chromium_dir()
    if _done("chromium-shell", t["sha256"], dest / "INSTALLATION_COMPLETE"):
        return
    _step(f"Installing headless Chromium {t['version']}")
    archive = fetch(t["url"], t["sha256"], DOWNLOADS / f"chromium-{t['version']}.zip",
                    "chromium")  # fmt: skip
    new, old = dest.with_name(dest.name + ".new"), dest.with_name(dest.name + ".old")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    # /usr/bin/unzip keeps the executable bits (zipfile does not)
    subprocess.run(["/usr/bin/unzip", "-q", "-o", str(archive), "-d", str(new)], check=True)
    (new / "DEPENDENCIES_VALIDATED").touch()
    (new / "INSTALLATION_COMPLETE").touch()
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)  # a running renderer keeps its open files
    new.rename(dest)
    shutil.rmtree(old, ignore_errors=True)
    archive.unlink()
    _mark("chromium-shell", t["sha256"])


APP = ROOT / "app"  # the UI source (React + TypeScript + Vite)
APP_DIST = ROOT / "src" / "videocut" / "web" / "dist"  # served by `videocut ui`


def build_app() -> None:
    """Build the UI with the bundled node when its source is present (a no-op otherwise).
    Rebuilt when the lock file or any source file changed since the last build."""
    if not (APP / "package.json").is_file():
        return
    from .download import sha256_of

    lock = APP / "package-lock.json"
    if not lock.is_file():
        raise RuntimeError(f"{lock} is missing (app packages are installed pinned, npm ci)")
    deps = sha256_of(lock)
    if not _done("app-deps", deps, APP / "node_modules"):
        _step("Installing app packages")
        subprocess.run(
            [str(BIN / "npm"), "ci", "--no-audit", "--no-fund", "--silent"],
            cwd=APP,
            env=tool_env(),
            check=True,
        )
        _mark("app-deps", deps)
    newest = max(
        (
            p.stat().st_mtime
            for p in APP.rglob("*")
            if p.is_file() and "node_modules" not in p.parts and "dist" not in p.parts
        ),
        default=0,
    )
    built = APP_DIST / "index.html"
    if built.is_file() and built.stat().st_mtime >= newest:
        return
    _step("Building the app")
    subprocess.run(
        [str(BIN / "npm"), "run", "build", "--silent"], cwd=APP, env=tool_env(), check=True
    )
    if not built.is_file():
        raise RuntimeError(f"the app build wrote no {built}")


HF_MARKER = ".videocut-revision"  # in a Hugging Face model's folder once it is complete
AGENT_ENV = "VIDEOCUT_AGENT"


def install_model(name: str) -> None:
    if os.environ.get(AGENT_ENV):
        raise RuntimeError("models are downloaded by the app, not inside an agent run")
    m = manifest.models()[name]
    if m.get("extra"):  # a model with its own packages and preparation
        install_image()
        return
    if m["kind"] == "huggingface":
        if _model_done(name):
            return
        _hf_download(name, m, MODELS / name)
    else:
        dest = MODELS / m["filename"]
        if _done(f"model:{name}", m["sha256"], dest):
            return
        _step(f"Downloading model {name}")
        fetch(m["url"], m["sha256"], dest, name)
        _mark(f"model:{name}", m["sha256"])


def _hf_download(name: str, m: dict, dest: Path) -> None:
    _step(f"Downloading model {name} (~{m['approx_size_gb']} GB)")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    from huggingface_hub import snapshot_download

    # resumable: an interrupted run continues from the partial files in dest/.cache
    snapshot_download(repo_id=m["repo"], revision=m["revision"], local_dir=dest,
                      allow_patterns=m.get("allow_patterns"))  # fmt: skip
    (dest / HF_MARKER).write_text(m["revision"] + "\n")


def uv_env() -> dict[str, str]:
    """The environment start.sh gives uv, for a sync started from Python (the app, `videocut
    models pull`): the pinned Python only, our cache, this venv. Developer tools only where
    they are installed already (UV_NO_DEV, like start.sh for end users)."""
    env = dict(os.environ)
    env.setdefault("UV_PYTHON_INSTALL_DIR", str(TOOLS / "python"))
    env.setdefault("UV_PYTHON_PREFERENCE", "only-managed")
    env.setdefault("UV_CACHE_DIR", str(TOOLS / "uv-cache"))
    env.setdefault("UV_PROJECT_ENVIRONMENT", sys.prefix)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    env["UV_NO_PROGRESS"] = "1"
    if "UV_NO_DEV" not in env and importlib.util.find_spec("pytest") is None:
        env["UV_NO_DEV"] = "1"
    return env


def install_extra(extra: str) -> None:
    """Add an optional package set from uv.lock (pyproject `[project.optional-dependencies]`)
    to the venv. --inexact keeps what is there; start.sh keeps the extra on later syncs."""
    _step(f"Installing the Python packages for {extra}")
    uv = str(BIN / "uv")
    subprocess.run([uv, "sync", "--frozen", "--inexact", "--quiet", "--extra", extra],
                   cwd=ROOT, env=uv_env(), check=True)  # fmt: skip
    # like start.sh: the venv holds its own copies, drop what a re-sync can download again
    subprocess.run([uv, "cache", "prune", "--ci", "--quiet"], cwd=ROOT,
                   env={**uv_env(), "UV_LOCK_TIMEOUT": "5"}, capture_output=True)  # fmt: skip


def install_image() -> None:
    """`videocut models pull --image` / `videocut setup --image`: the local image generator
    (FLUX.2 [klein] 4B through mflux). Says the size first, refuses a Mac it cannot run on.
    Downloads the pinned revision into models/.download/, saves the 8-bit copy the app loads
    into models/flux2-klein-4b/ and deletes the download; each step resumes."""
    from .tools._material_providers import local_flux

    if os.environ.get(AGENT_ENV):
        raise RuntimeError("models are downloaded by the app, not inside an agent run")
    name = local_flux.MODEL
    m = manifest.models()[name]
    mem = local_flux.memory_gb()
    if mem < m["min_memory_gb"] - 0.5:
        raise RuntimeError(
            f"local image generation needs at least {m['min_memory_gb']} GB memory; this Mac "
            f"has {mem:.0f} GB. Use an image service in the settings instead."
        )
    done = _model_done(name) and local_flux.mflux_version() is not None
    mb = m["approx_size_gb"] * 1000
    print(f"\033[1mLocal image generation: FLUX.2 [klein] 4B ({m['license']}), "
          + (f"{_size(mb)} download ({_minutes(mb)} on 100 Mbit/s), "
             f"{m['quantised_size_gb']:.0f} GB on disk in {MODELS / name}"
             if not done else "already done") + "\033[0m", flush=True)  # fmt: skip
    if done:
        return
    src = local_flux.source_dir()
    MODELS.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(MODELS).free / 1e9
    need_gb = m["approx_size_gb"] + m["quantised_size_gb"] + 2 - _folder_gb(src)
    if not local_flux.model_ready() and free_gb < need_gb:  # else only the packages are missing
        raise RuntimeError(f"needs {need_gb:.0f} GB free disk space while it installs "
                           f"({m['quantised_size_gb']:.0f} GB afterwards), "
                           f"{free_gb:.0f} GB are free")  # fmt: skip
    install_extra(m["extra"])
    if not local_flux.model_ready():
        try:
            downloaded = (src / HF_MARKER).read_text().strip() == m["revision"]
        except OSError:
            downloaded = False
        if not downloaded:
            _hf_download(name, m, src)
        _step(f"Preparing the model for this Mac ({local_flux.QUANTIZE}-bit copy, about a minute)")
        local_flux.quantise(src)
    shutil.rmtree(src.parent, ignore_errors=True)  # the 16 GB download is not needed any more


def _folder_gb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e9 if p.exists() else 0


TOOL_NAMES = ("node", "ffmpeg", "ffprobe", "deep-filter", "c2patool")
# Measured download sizes (MB) of the parts that are not single pinned files.
RENDERER_MB = 120  # renderer npm packages + headless Chromium
APP_MB = 60  # app npm packages (the build itself is local)


def _tools_done() -> bool:
    for name in TOOL_NAMES:
        t = manifest.tool(name)
        dest = TOOLS / f"node-{t['version']}" if name == "node" else BIN / name
        if not _done(name, t["sha256"], dest):
            return False
    return True


def run(models: bool = True) -> None:
    BIN.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    tools_mb = sum(manifest.tool(n).get("approx_size_mb", 0) for n in TOOL_NAMES)
    headline(4, "tools: Node, FFmpeg, FFprobe, DeepFilterNet, c2patool", tools_mb, _tools_done())
    install_node()
    install_zipped_binary("ffmpeg")
    install_zipped_binary("ffprobe")
    install_plain_binary("deep-filter")
    try:  # optional: without it a render is delivered without a C2PA manifest
        install_zipped_binary("c2patool")
    except Exception as e:  # noqa: BLE001 — a failed optional download must not stop setup
        print(f"   c2patool not installed ({e}); videos get no C2PA manifest until it is")
    from .download import sha256_of

    lock = sha256_of(RENDERER / "package-lock.json")
    shell = manifest.tool("chromium-headless-shell")["sha256"]
    r_done = _done("renderer", lock, RENDERER / "node_modules") and _done(
        "chromium-shell", shell, chromium_dir() / "INSTALLATION_COMPLETE"
    )
    headline(5, "overlay renderer and headless Chromium", RENDERER_MB, r_done)
    install_renderer()
    headline(6, "the app (packages, then a local build)", APP_MB)
    build_app()
    wanted = {n: m for n, m in manifest.models().items() if m.get("default")} if models else {}
    if not wanted:
        headline(7, "models (skipped: --no-models)", done=False)
        return
    mb = sum(m.get("approx_size_gb", 0) * 1000 for m in wanted.values())
    all_done = all(_model_done(n) for n in wanted)
    headline(7, f"models: speech recognition and face tracking, into {MODELS}", mb, all_done)
    for name in wanted:
        install_model(name)


def _model_done(name: str) -> bool:
    m = manifest.models()[name]
    if m.get("extra"):
        from .tools._material_providers import local_flux

        return local_flux.model_ready()
    if m["kind"] == "huggingface":
        # the marker next to the model (since the image model); older installs recorded it
        # in .tools/installed.json only
        try:
            if (MODELS / name / HF_MARKER).read_text().strip() == m["revision"]:
                return True
        except OSError:
            pass
        return _done(f"model:{name}", m["revision"], MODELS / name)
    return _done(f"model:{name}", m["sha256"], MODELS / m["filename"])
