"""Shared helpers for `videocut tool ...`: job resolution, summaries, ffmpeg calls.

Contract for every tool:
- it works on one job folder, resolved from `--job <path|id>` or the current directory
  (Claude runs inside the job folder, possibly in a subfolder);
- it prints whatever it wants for humans, then exactly one JSON line as the last line
  of stdout (`{"tool": ..., "ok": ..., ...}`) so Claude can read the result without
  opening files;
- it exits 0 on success and 1 on failure.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from ..paths import BIN, tool_env

JOB_FILE = "job.json"
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


class ToolError(Exception):
    """A failure with a message meant for the reader of the summary line."""


def home() -> Path:
    """User data root, read at call time so tests can point it elsewhere."""
    return Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut"))


def jobs_root() -> Path:
    return home() / "jobs"


def job_parent() -> argparse.ArgumentParser:
    """Parent parser that adds `--job` to a tool subcommand."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--job", help="job folder or job id (default: the job containing the current directory)"
    )
    return p


def resolve_job(ref: str | None = None) -> Path:
    """Job folder from a path, an id under $VIDEOCUT_HOME/jobs, or the current directory."""
    if ref:
        p = Path(ref).expanduser()
        if (p / JOB_FILE).is_file():
            return p.resolve()
        if ID_RE.fullmatch(ref) and (jobs_root() / ref / JOB_FILE).is_file():
            return (jobs_root() / ref).resolve()
        raise ToolError(f"no job found at {ref!r} (neither a job folder nor a job id)")
    cwd = Path.cwd().resolve()
    for d in (cwd, *cwd.parents):
        if (d / JOB_FILE).is_file():
            return d
    raise ToolError("not inside a job folder; pass --job <path|id>")


def read_job(job: Path) -> dict:
    return json.loads((job / JOB_FILE).read_text())


def write_json(path: Path, data) -> None:
    """Write JSON atomically (temp file + rename), so readers never see half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def update_job(job: Path, **fields) -> dict:
    data = read_job(job)
    data.update(fields)
    write_json(job / JOB_FILE, data)
    return data


def source_path(job: Path) -> Path:
    src = read_job(job).get("source")
    if not src:
        raise ToolError("job.json has no source")
    p = (job / src).resolve()
    if not p.is_relative_to(job) or not p.is_file():
        raise ToolError(f"source {src!r} is missing or outside the job folder")
    return p


def summary(tool: str, ok: bool = True, **fields) -> None:
    """The machine-readable last line of every tool run."""
    print(json.dumps({"tool": tool, "ok": ok, **fields}, ensure_ascii=False), flush=True)


def run_tool(name: str, fn: Callable[[argparse.Namespace], dict]) -> Callable:
    """Wrap a tool body: its returned dict becomes the summary; ToolError a failed one."""

    def handler(args: argparse.Namespace) -> int:
        try:
            fields = fn(args) or {}
        except ToolError as e:
            print(f"error: {e}", file=sys.stderr)
            summary(name, ok=False, error=str(e))
            return 1
        summary(name, **fields)
        return 0

    return handler


def not_implemented(name: str) -> Callable:
    def body(args: argparse.Namespace) -> dict:
        raise ToolError(f"{name}: not implemented yet")

    return run_tool(name, body)


def is_fresh(outputs: list[Path], inputs: list[Path]) -> bool:
    """True when every output exists and is newer than every input."""
    if not all(o.is_file() for o in outputs):
        return False
    newest_in = max(i.stat().st_mtime for i in inputs)
    return min(o.stat().st_mtime for o in outputs) > newest_in


def binary(name: str) -> str:
    """Installed tool from .tools/bin, else from PATH (CI without the installer)."""
    p = BIN / name
    if p.is_file():
        return str(p)
    from shutil import which

    found = which(name)
    if not found:
        raise ToolError(f"{name} not found; run ./start.sh")
    return found


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    out = subprocess.run(cmd, capture_output=True, text=True, env=tool_env())
    if out.returncode != 0:
        tail = (out.stderr or out.stdout).strip()[-600:]
        raise ToolError(f"{Path(cmd[0]).name} failed: {tail}")
    return out


def ffmpeg(*args: str) -> None:
    run([binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args])


def ffmpeg_to(dst: Path, *args: str) -> None:
    """ffmpeg with `dst` as last argument, written via a temp file so a failed or
    interrupted run never leaves a fresh-looking partial output behind."""
    part = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    try:
        ffmpeg(*args, str(part))
        part.replace(dst)
    finally:
        part.unlink(missing_ok=True)
