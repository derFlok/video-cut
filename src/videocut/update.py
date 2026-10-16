"""`videocut update`: get the newest Video Cut and run ./start.sh again.

Two ways, chosen automatically:
- a git checkout on a Mac with the command line tools: `git pull --ff-only` (a checkout
  with local changes is left alone);
- otherwise (the usual install by download): the newest release tag of the repository,
  or `main` while there is none, as a GitHub tarball. The new tree replaces the code;
  the installed tools (.tools, .venv, node_modules, the built app) are moved over, so only
  what changed is installed again. The previous code is kept as `app.old-<time>` until
  start.sh succeeded.

User data in ~/VideoCut is never touched. A running app is stopped first (not while a job
run is active, unless --force).
"""

import json
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, launcher
from .paths import ROOT

REPO = "derFlok/video-cut"
KEEP = (".tools", ".venv", "renderer/node_modules", "app/node_modules", "src/videocut/web/dist")
SOURCE_FILE = ".videocut-source.json"  # which ref a downloaded install came from


class UpdateError(RuntimeError):
    pass


def _get(url: str, timeout: float = 60):
    req = urllib.request.Request(
        url, headers={"User-Agent": f"videocut/{__version__}", "Accept": "application/json"}
    )
    return urllib.request.urlopen(req, timeout=timeout)


def latest_ref() -> str:
    """The newest release tag, or `main` while the repository has no release."""
    try:
        with _get(f"https://api.github.com/repos/{REPO}/releases/latest", 20) as r:
            tag = json.load(r).get("tag_name")
            if isinstance(tag, str) and tag:
                return tag
    except (OSError, urllib.error.URLError, ValueError):
        pass
    return "main"


def has_git() -> bool:
    """/usr/bin/git without the command line tools only opens an install dialog."""
    if not (ROOT / ".git").exists():
        return False
    try:
        return subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True).returncode == 0
    except OSError:
        return False


def stop_server(force: bool) -> bool:
    """Stop the running app (POST /api/shutdown with its own token). True if one ran."""
    info = launcher.running()
    if info is None:
        return False
    token = info["url"].split("?t=", 1)[-1]
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/api/shutdown",
        data=json.dumps({"force": force}).encode(),
        headers={"X-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except urllib.error.HTTPError as e:
        if e.code == 409:
            raise UpdateError(
                "Video Cut is working on a video right now. Wait until it is done, or run "
                "`videocut update --force` to stop it."
            ) from None
        raise
    for _ in range(50):
        if launcher.running(timeout=0.5) is None:
            return True
        time.sleep(0.2)
    raise UpdateError("the running app did not stop; quit it and try again")


def git_update(root: Path = ROOT) -> str:
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    if dirty:
        raise UpdateError("this checkout has local changes; commit or stash them first")
    subprocess.run(["git", "-C", str(root), "pull", "--ff-only"], check=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


def download_tree(ref: str, into: Path) -> Path:
    """Fetch and unpack the repository at `ref`; return the unpacked top folder."""
    into.mkdir(parents=True, exist_ok=True)
    archive = into / "source.tar.gz"
    with (
        _get(f"https://codeload.github.com/{REPO}/tar.gz/{ref}", 120) as r,
        archive.open("wb") as out,
    ):
        shutil.copyfileobj(r, out)
    with tarfile.open(archive) as tf:
        tf.extractall(into, filter="data")
    archive.unlink()
    tops = [p for p in into.iterdir() if p.is_dir()]
    if len(tops) != 1 or not (tops[0] / "start.sh").is_file():
        raise UpdateError("the download does not look like Video Cut (no start.sh)")
    return tops[0]


def swap(new: Path, root: Path = ROOT) -> Path:
    """Move the installed tools into `new`, put `new` in place of `root`. Returns the old
    code's folder (deleted by the caller once start.sh succeeded)."""
    moved = []
    try:
        for rel in KEEP:
            src, dst = root / rel, new / rel
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
                moved.append(rel)
        old = root.with_name(f"{root.name}.old-{time.strftime('%Y%m%d-%H%M%S')}")
        root.rename(old)
    except OSError:
        for rel in moved:  # put the tools back where they were
            (new / rel).rename(root / rel)
        raise
    try:
        new.rename(root)
    except OSError:
        old.rename(root)
        for rel in moved:
            (new / rel).rename(root / rel)
        raise
    return old


def run(force: bool = False, ref: str | None = None, root: Path = ROOT) -> int:
    old = None
    if has_git():
        stopped = stop_server(force)
        print(f"Updating the git checkout in {root} …", flush=True)
        what = git_update(root)
    elif (root / ".git").exists():
        # a developer checkout without the command line tools: never overwrite it
        raise UpdateError(
            f"{root} is a git checkout; update it with git (needs the command line tools)"
        )
    else:
        what = ref or latest_ref()
        print(f"Downloading Video Cut ({what}) …", flush=True)
        staging = root.with_name(f".{root.name}-update")
        shutil.rmtree(staging, ignore_errors=True)
        new = download_tree(what, staging)  # before stopping: no network, app keeps running
        (new / SOURCE_FILE).write_text(json.dumps({"ref": what, "updated": time.time()}))
        stopped = stop_server(force)
        old = swap(new, root)
        shutil.rmtree(staging, ignore_errors=True)
    print(f"Now at {what}. Running ./start.sh …", flush=True)
    code = subprocess.run([str(root / "start.sh")], cwd=root).returncode
    if code != 0:
        hint = f" The previous code (without its tools) is in {old}." if old else ""
        print(f"start.sh failed (exit {code}).{hint} Run `videocut doctor`.", flush=True)
        return code
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
    if stopped:
        launcher.open_app()
    return 0
