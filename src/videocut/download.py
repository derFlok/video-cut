"""Checksum-verified downloads with a simple progress line. Resumable: an interrupted
download keeps its `<dest>.part` file and continues with an HTTP Range request."""

import hashlib
import http.client
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__

RETRIES = 4  # attempts per file; each one resumes the part file


class ChecksumError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str, part: Path, label: str) -> None:
    """One attempt: append to the part file from where it stopped."""
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": f"videocut/{__version__}"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:  # the part file is already complete
            return
        raise
    with resp:
        if have and getattr(resp, "status", 200) != 206:  # the server ignored the range: start over
            have = 0
        total = int(resp.headers.get("Content-Length") or 0) + have
        done = have
        with part.open("ab" if have else "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total and sys.stdout.isatty():
                    pct = done * 100 // total
                    print(f"\r  {label}: {pct:3d} % of {total / 1e6:.0f} MB", end="", flush=True)
        if total and sys.stdout.isatty():
            print()
        if total and done < total:
            raise OSError(f"{label}: connection closed after {done} of {total} bytes")


def fetch(url: str, sha256: str, dest: Path, label: str, retries: int = RETRIES) -> Path:
    """Download url to dest, verify sha256, move into place atomically. Network errors are
    retried (resuming); a checksum mismatch deletes the part file and fails."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        try:
            _get(url, part, label)
            break
        except (OSError, urllib.error.URLError, http.client.HTTPException) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500:
                part.unlink(missing_ok=True)
                raise
            if attempt == retries:
                raise OSError(
                    f"{label}: download failed after {retries} attempts ({e}). Check the "
                    "internet connection and run ./start.sh again: it continues where it stopped."
                ) from e
            print(f"  {label}: {e}; retrying ({attempt}/{retries - 1})", flush=True)
            time.sleep(min(2**attempt, 10))
    actual = sha256_of(part)
    if actual != sha256:
        part.unlink(missing_ok=True)
        raise ChecksumError(f"{label}: expected sha256 {sha256}, got {actual}")
    shutil.move(part, dest)
    return dest
