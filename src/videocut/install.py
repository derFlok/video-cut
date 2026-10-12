"""Install pinned tools into .tools/ and models into ~/VideoCut/models. Idempotent."""

import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from . import manifest
from .download import fetch
from .paths import BIN, MODELS, RENDERER, TOOLS, tool_env

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


def _step(msg: str) -> None:
    print(f"\033[1m▸ {msg}\033[0m", flush=True)


def install_node() -> None:
    t = manifest.tool("node")
    target = TOOLS / f"node-{t['version']}"
    if _done("node", t["sha256"], target, BIN / "node"):
        return
    _step(f"Installing Node {t['version']}")
    with tempfile.TemporaryDirectory(dir=TOOLS) as tmp:
        archive = fetch(t["url"], t["sha256"], Path(tmp) / "node.tar.gz", "node")
        with tarfile.open(archive) as tf:
            tf.extractall(tmp, filter="tar")
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
        archive = fetch(t["url"], t["sha256"], Path(tmp) / f"{name}.zip", name)
        with zipfile.ZipFile(archive) as zf:
            zf.extract(name, tmp)
        shutil.move(Path(tmp) / name, dest)
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
    if not _done("chromium", key):
        _step("Installing headless Chromium for the renderer")
        subprocess.run(
            [
                str(BIN / "node"),
                "node_modules/playwright-core/cli.js",
                "install",
                "--only-shell",
                "chromium",
            ],
            cwd=RENDERER,
            env=tool_env(),
            check=True,
        )
        _mark("chromium", key)


def install_model(name: str) -> None:
    m = manifest.models()[name]
    if m["kind"] == "huggingface":
        dest = MODELS / name
        if _done(f"model:{name}", m["revision"], dest):
            return
        _step(f"Downloading model {name} (~{m['approx_size_gb']} GB)")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=m["repo"], revision=m["revision"], local_dir=dest)
        _mark(f"model:{name}", m["revision"])
    else:
        dest = MODELS / m["filename"]
        if _done(f"model:{name}", m["sha256"], dest):
            return
        _step(f"Downloading model {name}")
        fetch(m["url"], m["sha256"], dest, name)
        _mark(f"model:{name}", m["sha256"])


def run(models: bool = True) -> None:
    BIN.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    install_node()
    install_zipped_binary("ffmpeg")
    install_zipped_binary("ffprobe")
    install_plain_binary("deep-filter")
    install_renderer()
    if models:
        for name, m in manifest.models().items():
            if m.get("default"):
                install_model(name)
