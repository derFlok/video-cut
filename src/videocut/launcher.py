"""The double-click launcher and the running server's address.

`videocut launcher` writes a tiny "Video Cut.app" on this Mac (a bundle whose executable is
a shell stub). It is made locally, so it carries no quarantine flag and Gatekeeper has
nothing to check; nothing is downloaded or signed. The icon is generated here: a neutral
grey square with a play triangle.

Double-click → `videocut open`: if the app's server already runs, open its link again;
else start `videocut ui` in the background, wait until it answers, open the link.
The server writes its port and per-start link to `$VIDEOCUT_HOME/state/server.json`
(mode 0600, removed when it stops), so a second double-click finds it.
"""

import contextlib
import json
import math
import os
import plistlib
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from .paths import ROOT

APP_NAME = "Video Cut.app"
BUNDLE_ID = "local.videocut.launcher"
STUB = "videocut-launcher"
DEFAULT_PORT = 4300
PORT_RANGE = 20  # 4300 taken by another program: try the next ones
LOGIN_ITEM = Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def _home() -> Path:
    return Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut"))


def default_port() -> int:
    """VIDEOCUT_PORT, else 4300. Taken by another program: the next free one is used."""
    try:
        return int(os.environ.get("VIDEOCUT_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def server_file() -> Path:
    return _home() / "state" / "server.json"


def log_file() -> Path:
    return _home() / "state" / "server.log"


# ------------------------------------------------------------------ server state


def write_state(port: int, token: str) -> Path:
    """Called by `videocut ui` once it knows its port: the link for the launcher."""
    f = server_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    data = {"pid": os.getpid(), "port": port, "url": f"http://127.0.0.1:{port}/?t={token}"}
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out)
    return f


def clear_state() -> None:
    """At exit: remove the file if it is still this process's."""
    f = server_file()
    with contextlib.suppress(OSError, ValueError):
        if json.loads(f.read_text()).get("pid") == os.getpid():
            f.unlink()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running(timeout: float = 2) -> dict | None:
    """The server recorded in server.json if it runs and accepts its own token."""
    try:
        data = json.loads(server_file().read_text())
        pid, port, url = int(data["pid"]), int(data["port"]), str(data["url"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not _alive(pid):
        return None
    token = url.split("?t=", 1)[-1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/session", headers={"X-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
    except (OSError, urllib.error.URLError):
        return None
    return {"pid": pid, "port": port, "url": url}


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port(start: int) -> int:
    for p in range(start, start + PORT_RANGE):
        if port_free(p):
            return p
    raise RuntimeError(f"no free port between {start} and {start + PORT_RANGE - 1}")


def open_url(url: str) -> None:
    subprocess.run(["/usr/bin/open", url], check=False)


def start_background(port: int) -> subprocess.Popen:
    """`videocut ui --no-open` detached from the launcher (it outlives the stub)."""
    log = log_file()
    log.parent.mkdir(parents=True, exist_ok=True)
    out = log.open("a")
    return subprocess.Popen(
        [sys.executable, "-m", "videocut.cli", "ui", "--no-open", "--port", str(port)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def open_app(port: int | None = None, browser: bool = True, wait_s: float = 60) -> int:
    """`videocut open`: the launcher's double-click."""
    info = running()
    if info is None:
        p = free_port(port or default_port())
        proc = start_background(p)
        deadline = time.monotonic() + wait_s
        while info is None and time.monotonic() < deadline:
            if proc.poll() is not None:
                # a second double-click lost the race for the port: the first one runs
                time.sleep(1)
                info = running()
                if info is not None:
                    break
                print(f"Video Cut stopped at start (see {log_file()}).", file=sys.stderr)
                return 1
            time.sleep(0.3)
            info = running()
        if info is None:
            print(f"Video Cut did not answer within {wait_s:.0f} s (see {log_file()}).",
                  file=sys.stderr)  # fmt: skip
            return 1
    print(f"Video Cut is running on port {info['port']}.")
    if browser:
        open_url(info["url"])
    return 0


# ------------------------------------------------------------------ the bundle

STUB_SCRIPT = """#!/bin/bash
# Video Cut launcher, made on this Mac by `videocut launcher`. Starts the app (if it is not
# running yet) and opens it in the browser. Safe to delete; `videocut launcher` makes it again.
APP={root}
export VIDEOCUT_HOME={home}
LOG="$VIDEOCUT_HOME/state/launcher.log"
alert() {{
  /usr/bin/osascript -e 'on run argv' -e 'display alert "Video Cut" message (item 1 of argv)' \\
    -e 'end run' "$1" >/dev/null 2>&1
}}
mkdir -p "$VIDEOCUT_HOME/state"
if [ ! -x "$APP/.venv/bin/python" ]; then
  alert "Video Cut is not installed in $APP. Ask your Claude Code: \\"Repair Video Cut\\".

Video Cut ist dort nicht (mehr) installiert. Frag dein Claude Code: \\"Video Cut reparieren\\"."
  exit 1
fi
cd "$APP" || exit 1
if ! "$APP/.venv/bin/python" -m videocut.cli open {port_arg}"$@" >>"$LOG" 2>&1; then
  alert "Video Cut could not start. Ask your Claude Code: \\"Video Cut does not start\\".

Video Cut konnte nicht starten. Frag dein Claude Code: \\"Video Cut startet nicht\\"."
  exit 1
fi
"""


def _q(s: str) -> str:
    """Single-quote for bash."""
    return "'" + s.replace("'", "'\\''") + "'"


def bundle(dest_dir: Path, port: int | None = None, root: Path = ROOT) -> Path:
    """Write "Video Cut.app" into dest_dir (replacing an older one) and return its path."""
    app = dest_dir / APP_NAME
    tmp = dest_dir / f".{APP_NAME}.new"
    shutil.rmtree(tmp, ignore_errors=True)
    macos = tmp / "Contents" / "MacOS"
    res = tmp / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)
    info = {
        "CFBundleName": "Video Cut",
        "CFBundleDisplayName": "Video Cut",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": STUB,
        "CFBundleIconFile": "icon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": _version(),
        "CFBundleVersion": _version(),
        "LSMinimumSystemVersion": "14.0",
        "LSUIElement": True,  # a stub: no Dock icon while it runs for a second
        "NSHighResolutionCapable": True,
    }
    with (tmp / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    stub = macos / STUB
    port_arg = f"--port {int(port)} " if port else ""
    stub.write_text(STUB_SCRIPT.format(root=_q(str(root)), home=_q(str(_home())),
                                       port_arg=port_arg))  # fmt: skip
    stub.chmod(0o755)
    (res / "icon.icns").write_bytes(icon_icns())
    old = dest_dir / f".{APP_NAME}.old"
    shutil.rmtree(old, ignore_errors=True)
    if app.exists():
        app.rename(old)  # never a moment without an app if this process dies
    tmp.rename(app)
    shutil.rmtree(old, ignore_errors=True)
    os.utime(app)  # Finder picks up the new icon
    return app


def _version() -> str:
    from . import __version__

    return __version__


def set_login_item(app: Path | None, enabled: bool, agents: Path = LOGIN_ITEM) -> Path:
    """Start the server (without opening the browser) at login. Off by default."""
    if not enabled:
        _bootout(agents)
        agents.unlink(missing_ok=True)
        return agents
    assert app is not None
    agents.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": BUNDLE_ID,
        "ProgramArguments": [str(app / "Contents" / "MacOS" / STUB), "--no-browser"],
        "RunAtLoad": True,
    }
    with agents.open("wb") as f:
        plistlib.dump(plist, f)
    return agents


def default_dirs() -> list[Path]:
    """Where `videocut launcher --remove` looks."""
    return [Path.home() / "Applications", Path("/Applications")]


def remove(dirs: list[Path] | None = None, agents: Path = LOGIN_ITEM) -> list[Path]:
    gone = []
    for d in dirs or default_dirs():
        app = d / APP_NAME
        stub = app / "Contents" / "MacOS" / STUB
        if stub.is_file():  # only our own bundle, never another "Video Cut.app"
            shutil.rmtree(app)
            gone.append(app)
    if agents.exists():
        _bootout(agents)
        agents.unlink()
        gone.append(agents)
    return gone


def _bootout(agents: Path) -> None:
    """Unload a loaded login item now (else it stays loaded until the next login)."""
    if agents.exists() and agents == LOGIN_ITEM:
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(agents)],
                       capture_output=True, check=False)  # fmt: skip


# ------------------------------------------------------------------ the icon (generated)


def _png(size: int, pixels: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    raw = b"".join(b"\x00" + pixels[y * size * 4 : (y + 1) * size * 4] for y in range(size))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def icon_png(size: int) -> bytes:
    """A neutral icon: dark grey rounded square, light grey play triangle (no brand)."""
    bg, fg = (58, 58, 58), (236, 236, 236)
    m = size * 0.1  # margin like the system's app icons
    r = size * 0.18
    # play triangle, centred a bit right (optical centre)
    cx, cy, h = size * 0.53, size * 0.5, size * 0.36
    tri = [(cx - h * 0.45, cy - h * 0.5), (cx + h * 0.55, cy), (cx - h * 0.45, cy + h * 0.5)]
    edges = []
    for (x1, y1), (x2, y2) in zip(tri, tri[1:] + tri[:1], strict=True):
        nx, ny = y2 - y1, x1 - x2  # outward normal for a clockwise (screen) triangle
        n = math.hypot(nx, ny)
        edges.append((nx / n, ny / n, -(nx * x1 + ny * y1) / n))
    out = bytearray(size * size * 4)
    lo, hi = m + r, size - m - r
    for y in range(size):
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            qx = max(lo - px, 0, px - hi)
            qy = max(lo - py, 0, py - hi)
            d_box = math.hypot(qx, qy) - r
            a_box = min(max(0.5 - d_box, 0.0), 1.0)
            if a_box <= 0:
                continue
            d_tri = max(a * px + b * py + c for a, b, c in edges)
            a_tri = min(max(0.5 - d_tri, 0.0), 1.0)
            i = (y * size + x) * 4
            for k in range(3):
                out[i + k] = round(bg[k] + (fg[k] - bg[k]) * a_tri)
            out[i + 3] = round(255 * a_box)
    return _png(size, bytes(out))


def icon_icns() -> bytes:
    """An .icns with PNG payloads (128, 256, 512 px), the format macOS reads directly."""
    parts = b""
    for kind, size in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512)):
        data = icon_png(size)
        parts += kind + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(parts) + 8) + parts
