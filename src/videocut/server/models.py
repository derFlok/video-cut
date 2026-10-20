"""The optional local image model (FLUX.2 [klein] 4B) from inside the app: Settings → Material.

GET    /api/models/image          state, sizes, free disk, memory, progress while it installs
POST   /api/models/image/install  start one install (409 while one runs, 400 when this Mac
                                  cannot run it or the disk is too small)
POST   /api/models/image/cancel   stop the install; finished parts stay, a new install resumes
DELETE /api/models/image          remove the model and a half-done download (409 while busy)

The install is `videocut models pull --image` as a child process in its own process group,
never in the server: the quantise step takes several GB of memory and must not take the UI
down with it. Its output goes to $VIDEOCUT_HOME/logs/model-image.log; the step comes from the
lines install_image prints, the download progress from the size of the download folder
against the model's approx_size_gb (huggingface_hub prints no line a program can read).
Nothing of this survives a server restart except the files: a half-done folder reads as
`partial`, and the next install resumes it. Never under VIDEOCUT_AGENT.
"""

import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import manifest
from ..install import AGENT_ENV, _folder_gb
from ..paths import tool_env
from ..tools._material_providers import local_flux

STATES = ("unsupported", "not_installed", "partial", "installing", "installed", "failed",
          "removing")  # fmt: skip
# install_image's step lines (install._step) -> the step the card shows
STEP_LINES = (
    ("Installing the Python packages", "packages"),
    ("Downloading model", "download"),
    ("Preparing the model", "preparing"),
)
KILL_AFTER_S = 10
LOG_TAIL = 20
SPEED_WINDOW_S = 20


def _home() -> Path:
    return Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut"))


def log_path() -> Path:
    return _home() / "logs" / "model-image.log"


def pid_path() -> Path:
    """The running child's pid and command line, so a new server finds one it left behind."""
    return _home() / "logs" / "model-image.pid"


def default_argv() -> list[str]:
    """The venv's `videocut models pull --image` (the package has no __main__)."""
    return [sys.executable, "-m", "videocut.cli", "models", "pull", "--image"]


def _model() -> dict:
    return manifest.models()[local_flux.MODEL]


def supported() -> tuple[bool, str]:
    m = _model()
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False, "local image generation needs a Mac with Apple Silicon"
    mem = local_flux.memory_gb()
    if mem < m["min_memory_gb"] - 0.5:
        return False, (f"local image generation needs at least {m['min_memory_gb']} GB memory; "
                       f"this Mac has {mem:.0f} GB")  # fmt: skip
    return True, ""


def _free_gb() -> float:
    p = local_flux.model_dir().parent
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def _part_dir() -> Path:
    """Where quantise writes the 8-bit copy until it is complete."""
    d = local_flux.model_dir()
    return d.with_name(d.name + ".part")


def need_gb() -> float:
    """Free disk the install needs from here on (install_image's formula)."""
    m = _model()
    if local_flux.model_ready():
        return 0.0
    return max(0.0, m["approx_size_gb"] + m["quantised_size_gb"] + 2
               - _folder_gb(local_flux.source_dir()))  # fmt: skip


def disk_message(free: float, need: float) -> str:
    q = _model()["quantised_size_gb"]
    return (f"needs {need:.0f} GB free disk space while it installs ({q:.0f} GB afterwards), "
            f"{free:.0f} GB are free")  # fmt: skip


def parse_step(lines: list[str]) -> str:
    """The latest install step named in the log: packages → download → preparing."""
    step = "packages"
    for line in lines:
        for marker, name in STEP_LINES:
            if marker in line:
                step = name
    return step


def _tail(path: Path, n: int = LOG_TAIL) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16384))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = [ln.rstrip() for ln in text.replace("\r", "\n").splitlines() if ln.strip()]
    return [_plain(ln) for ln in lines[-n:]]


def _plain(line: str) -> str:
    """Without the terminal colour codes install.py prints."""
    return re.sub(r"\x1b\[[0-9;]*m", "", line)[:400]


class Installer:
    """At most one install child at a time, its log, and the download speed it measured."""

    def __init__(self, argv: Callable[[], list[str]] = default_argv):
        self.argv = argv
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.error: str | None = None  # the last install's failure, until the next action
        self.removing = False
        self.samples: deque[tuple[float, float]] = deque()
        self.stop_orphan()

    # ------------------------------------------------------------------ state

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def view(self) -> dict:
        m = _model()
        ok, why = supported()
        version = local_flux.mflux_version()
        ready = local_flux.model_ready()
        src = local_flux.source_dir()
        partial_gb = _folder_gb(src) + _folder_gb(_part_dir())
        installed_gb = _folder_gb(local_flux.model_dir()) if ready else 0.0
        log = _tail(log_path())
        progress = None
        with self.lock:
            busy, removing, error = self.running(), self.removing, self.error
        if removing:
            state, reason = "removing", ""
        elif busy:
            state, reason = "installing", ""
            progress = self._progress(log, _folder_gb(src), m["approx_size_gb"])
        elif ready and version:
            state, reason = "installed", ""
        elif error:
            state, reason = "failed", ""
        elif not ok:
            state, reason = "unsupported", why
        elif partial_gb > 0:  # the packages alone (they stay after a removal) are not partial
            state = "partial"
            reason = f"{partial_gb:.1f} GB of {m['approx_size_gb']:.0f} GB downloaded"
        elif ready:
            state, reason = "partial", "the model is there, its packages are missing"
        else:
            state, reason = "not_installed", ""
        free, need = _free_gb(), need_gb()
        if state in ("not_installed", "partial") and free < need:
            reason = disk_message(free, need)
        return {
            "state": state,
            "reason": reason,
            "download_gb": m["approx_size_gb"],
            "disk_gb": m["quantised_size_gb"],
            "need_gb": round(need, 1),
            "free_gb": round(free, 1),
            "disk_ok": free >= need,
            "memory_gb": round(local_flux.memory_gb(), 1),
            "min_memory_gb": m["min_memory_gb"],
            "installed_gb": round(installed_gb, 1),
            "partial_gb": round(partial_gb, 1),
            "mflux_version": version,
            "progress": progress,
            "error": error if state == "failed" else None,
            "log_tail": log if state in ("failed", "installing") else [],
        }

    def _progress(self, log: list[str], got: float, total: float) -> dict:
        step = parse_step(log)
        now = time.monotonic()
        out: dict = {"step": step, "downloaded_gb": round(got, 2), "pct": None}
        if step == "download":
            with self.lock:  # samples of the download only: the packages step would slow it
                self.samples.append((now, got))
                while len(self.samples) > 2 and now - self.samples[0][0] > SPEED_WINDOW_S:
                    self.samples.popleft()
                (t0, g0), (t1, g1) = self.samples[0], self.samples[-1]
            out["pct"] = round(min(99.0, got / total * 100), 1) if total else None
            if t1 - t0 >= 2 and g1 > g0:
                rate = (g1 - g0) / (t1 - t0)  # GB/s
                out["speed_mbps"] = round(rate * 8000, 1)
                out["eta_s"] = round(max(0.0, total - got) / rate)
        elif step == "preparing":
            out["pct"] = 100.0  # the download is complete; the 8-bit copy takes about a minute
        return out

    # --------------------------------------------------------------- actions

    def start(self) -> None:
        if os.environ.get(AGENT_ENV):
            raise HTTPException(403, "models are downloaded by the app, not inside an agent run")
        ok, why = supported()
        if not ok:
            raise HTTPException(400, why)
        free, need = _free_gb(), need_gb()
        if free < need:
            raise HTTPException(400, disk_message(free, need))
        with self.lock:
            if self.running() or self.removing:
                raise HTTPException(409, "the image model is being installed or removed")
            log = log_path()
            log.parent.mkdir(parents=True, exist_ok=True)
            env = {**tool_env(), "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1"}
            env.pop(AGENT_ENV, None)
            with log.open("wb") as out:
                self.proc = subprocess.Popen(
                    self.argv(), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                    env=env, start_new_session=True,  # its own group: cancel stops its children
                )  # fmt: skip
            self.cancelled, self.error = False, None
            self.samples.clear()
            pid_path().write_text(f"{self.proc.pid}\n{' '.join(self.argv()[1:])}\n")
            threading.Thread(target=self._watch, args=(self.proc,), daemon=True).start()

    def _watch(self, proc: subprocess.Popen) -> None:
        code = proc.wait()
        with self.lock:
            if proc is self.proc:
                pid_path().unlink(missing_ok=True)
            if code != 0 and not self.cancelled and proc is self.proc:
                last = [ln for ln in _tail(log_path()) if "✗" in ln or "rror" in ln]
                self.error = (last[-1].lstrip("✗ ").strip() if last
                              else f"the install stopped (exit code {code})")  # fmt: skip

    def cancel(self) -> None:
        with self.lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                raise HTTPException(409, "no install is running")
            self.cancelled = True
        self._kill(proc)

    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(KILL_AFTER_S)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(5)
        except ProcessLookupError:
            pass

    def stop(self) -> None:
        """At server exit (lifespan): an install without the app that watches it is stopped."""
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self.cancelled = True
            self._kill(proc)

    def stop_orphan(self) -> None:
        """At server start: a child an earlier server left running (it was killed before its
        shutdown ran) is stopped, so two installs never write the same folder. Only when the
        process still runs the recorded command (a reused pid is left alone)."""
        try:
            pid_s, cmd = pid_path().read_text().split("\n")[:2]
            pid = int(pid_s)
        except (OSError, ValueError):
            return
        pid_path().unlink(missing_ok=True)
        try:
            now = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=5).stdout  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return
        if not cmd or cmd not in now:
            return
        for sig, wait in ((signal.SIGTERM, KILL_AFTER_S), (signal.SIGKILL, 5)):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            end = time.monotonic() + wait
            while time.monotonic() < end:
                try:
                    os.killpg(pid, 0)
                except ProcessLookupError:
                    return
                time.sleep(0.1)

    def remove(self) -> None:
        with self.lock:
            if self.running() or self.removing:
                raise HTTPException(409, "the image model is being installed or removed")
            self.removing, self.error = True, None
        try:
            d = local_flux.model_dir()
            for p in (d, _part_dir(), local_flux.source_dir().parent):
                shutil.rmtree(p, ignore_errors=True)
        finally:
            with self.lock:
                self.removing = False


def router(argv: Callable[[], list[str]] = default_argv) -> APIRouter:
    r = APIRouter(prefix="/api/models/image")
    inst = Installer(argv)
    r.installer = inst  # type: ignore[attr-defined] — tests reach the child

    @r.get("")
    def status() -> dict:
        return inst.view()

    @r.post("/install")
    def install() -> dict:
        inst.start()
        return inst.view()

    @r.post("/cancel")
    def cancel() -> dict:
        inst.cancel()
        return inst.view()

    @r.delete("")
    def remove() -> dict:
        inst.remove()
        return inst.view()

    return r
