"""End-to-end run of the local app through the browser only (Phase 3 acceptance).

Not part of pytest or CI: it starts REAL Claude runs (plan, plan feedback, cut, overlays,
revise; a few dollars, 30-60 minutes). Run it explicitly:

    VIDEOCUT_E2E=1 .tools/bin/uv run python tests/e2e/app_e2e.py [--out DIR] [--keep]

What it does:
1. makes a generated take (tests/clips.py, macOS `say`): a camera clip and a screen
   recording of the same speech; the screen shows a fake contact card (placeholder name
   and an example.com address), so the plan finds personal data;
2. starts `videocut ui --no-open` in a throwaway VIDEOCUT_HOME (models linked from the
   real one, nothing else shared) and reads the session link it prints;
3. drives the UI with app/e2e/app.e2e.mjs (the renderer's playwright-core and Chromium):
   upload by drag & drop, progress, plan, plan feedback, personal-data dialog, approve,
   result player (Range), feedback at a player position, new version, download;
   a screenshot per step into --out;
4. prints the Claude cost, turns and time per phase (work/runs.jsonl, revisions.jsonl).

    VIDEOCUT_E2E=1 ... app_e2e.py --look <job id> [--home ~/VideoCut]
opens an existing job read-only (no run is started) and takes a screenshot.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import clips  # noqa: E402

from videocut.paths import BIN, tool_env  # noqa: E402

SCRIPT = ROOT / "app" / "e2e" / "app.e2e.mjs"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_take(folder: Path) -> tuple[Path, Path]:
    """Camera clip + screen recording with a fake contact card in the detail pane."""
    cam, screen, _ = clips.screen_pair(folder)
    card = folder / "card.png"
    node(["card", str(card)])
    out = folder / "screen-with-card.mov"
    subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-y", "-i", str(screen), "-i", str(card),
         "-filter_complex", "[1:v]scale=1400:-1[c];[0:v][c]overlay=x=1100:y=420",
         "-map", "0:a?", "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )  # fmt: skip
    return cam, out


def node(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(BIN / "node"), str(SCRIPT), *argv], env=tool_env(), **{"check": True, **kw}
    )


def start_server(home: Path, port: int) -> tuple[subprocess.Popen, str]:
    env = {**tool_env(), "VIDEOCUT_HOME": str(home), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [str(BIN / "uv"), "run", "--frozen", "--quiet", "videocut", "ui", "--no-open",
         "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    end = time.monotonic() + 60
    while time.monotonic() < end:
        line = proc.stdout.readline()
        if not line:
            break
        if "not built" in line:
            raise SystemExit("the app is not built: run ./start.sh first")
        m = re.search(r"http://127\.0\.0\.1:\d+/\?t=\S+", line)
        if m:
            return proc, m.group(0)
    proc.kill()
    raise SystemExit("the server printed no session link")


def phase_table(job: Path) -> list[dict]:
    rows = []
    runs = job / "work" / "runs.jsonl"
    for line in runs.read_text().splitlines() if runs.is_file() else []:
        r = json.loads(line)
        rows.append({k: r.get(k) for k in ("phase", "cost_usd", "turns", "wall_s", "ok")})
    return rows  # the revise run is the "revise" row; revisions.jsonl adds what it changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "app" / "e2e" / "out")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway VIDEOCUT_HOME")
    ap.add_argument("--look", metavar="JOB", help="open an existing job read-only")
    ap.add_argument("--home", type=Path, default=Path.home() / "VideoCut")
    a = ap.parse_args()
    if os.environ.get("VIDEOCUT_E2E") != "1":
        print("skipped: set VIDEOCUT_E2E=1 (starts real Claude runs)")
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    port = free_port()

    if a.look:
        proc, url = start_server(a.home, port)
        try:
            node(["look", "--url", url, "--job", a.look, "--out", str(a.out)])
        finally:
            proc.terminate()
        return 0

    if not clips.available():
        raise SystemExit("needs macOS `say` with the German voice and the installed ffmpeg")
    home = Path(tempfile.mkdtemp(prefix="videocut-e2e-"))
    (home / "models").symlink_to(Path.home() / "VideoCut" / "models")
    take = home / "take"
    t0 = time.monotonic()
    cam, screen = make_take(take)
    print(f"take: {cam.name} ({clips.duration(cam):.1f} s), {screen.name}", flush=True)
    proc, url = start_server(home, port)
    try:
        res = node(
            ["run", "--url", url, "--camera", str(cam), "--screen", str(screen),
             "--out", str(a.out)],
            stdout=subprocess.PIPE, text=True, check=False,
        )  # fmt: skip
    finally:
        proc.terminate()
        proc.wait(10)
    result = json.loads(res.stdout.strip().splitlines()[-1])
    jobs = [p for p in (home / "jobs").iterdir() if (p / "job.json").is_file()]
    rows = phase_table(jobs[0]) if jobs else []
    print(json.dumps({**result, "phases": rows, "wall_s": round(time.monotonic() - t0)},
                     indent=1, ensure_ascii=False))  # fmt: skip
    print(f"screenshots: {a.out}")
    if not a.keep:
        shutil.rmtree(home, ignore_errors=True)
    else:
        print(f"home kept: {home}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
