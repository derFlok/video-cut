"""Checksum-verified downloads with a simple progress line."""

import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from . import __version__


class ChecksumError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, sha256: str, dest: Path, label: str) -> Path:
    """Download url to dest, verify sha256, move into place atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        req = urllib.request.Request(url, headers={"User-Agent": f"videocut/{__version__}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                tmp.write(chunk)
                done += len(chunk)
                if total and sys.stdout.isatty():
                    pct = done * 100 // total
                    print(f"\r  {label}: {pct:3d} % of {total / 1e6:.0f} MB", end="", flush=True)
        if total and sys.stdout.isatty():
            print()
    actual = sha256_of(tmp_path)
    if actual != sha256:
        tmp_path.unlink(missing_ok=True)
        raise ChecksumError(f"{label}: expected sha256 {sha256}, got {actual}")
    shutil.move(tmp_path, dest)
    return dest
