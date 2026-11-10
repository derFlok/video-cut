"""Stills and contact sheets of motion scenes (renderer/motion.js) for people: the motion
row of `brand sheet` and the component gallery (scripts/motion_gallery.py).

A motion scene module must live inside the folder the renderer treats as the job folder, so
the scene source is written into a temporary folder together with its motion.json.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..paths import RENDERER, tool_env
from .common import ToolError, binary, write_json

SHEET_SCENE = RENDERER / "motion" / "sheet_scene.js"
GALLERY_SCENE = RENDERER / "test" / "motion" / "gallery_full.js"


def motion_spec(tokens: dict, *, size, region, duration: float, fps: int = 30,
                words: list | None = None, beats: dict | None = None) -> dict:  # fmt: skip
    return {
        "mode": "motion",
        "size": list(size),
        "fps": fps,
        "duration": duration,
        "unit": min(size),
        "region": list(region),
        "scene": "scene.js",
        "seed": 1,
        "words": words or [],
        "beats": beats or {},
        "tokens": tokens,
    }


def run_motion(spec: dict, scene_src: str, folder: Path, args: list[str]) -> dict:
    """Write scene.js + motion.json into `folder` and run motion.js with `args`; returns
    its JSON summary, raises ToolError on failure."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "scene.js").write_text(scene_src)
    write_json(folder / "motion.json", spec)
    cmd = [binary("node"), str(RENDERER / "motion.js"), str(folder / "motion.json"), *args]
    env = {**tool_env(), "VIDEOCUT_JOB_DIR": str(folder)}
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=folder)
    lines = r.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        result = {}
    if r.returncode != 0 or not result.get("ok"):
        raise ToolError(f"motion renderer failed: {result.get('error') or r.stderr[-600:]}")
    return result


def stills(spec: dict, scene_src: str, folder: Path, times: list[float],
           extra: list[str] | None = None) -> list[Path]:  # fmt: skip
    """PNG stills (region only) of the scene at `times`, in that order."""
    res = run_motion(
        spec, scene_src, folder,
        ["--region-only", "--stills", str(folder / "stills"), ",".join(f"{t:.3f}" for t in times),
         *(extra or [])],
    )  # fmt: skip
    return [Path(p) for p in res["stills"]]


def contact_sheet(pngs: list[Path], out: Path, *, cols: int, background: str,
                  width: int = 540, pad: int = 12) -> Path:  # fmt: skip
    """The stills on `background` (a hex colour), scaled to `width`, `cols` per row."""
    if not pngs:
        raise ToolError("no stills for the contact sheet")
    tmp = out.parent / (out.stem + "-frames")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for i, p in enumerate(pngs):
        shutil.copyfile(p, tmp / f"f{i:03d}.png")
    rows = -(-len(pngs) // cols)
    bg = "0x" + background.lstrip("#")
    probe = subprocess.run(
        [binary("ffprobe"), "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(tmp / "f000.png")],
        check=True, capture_output=True, text=True,
    ).stdout.strip().split(",")  # fmt: skip
    w0, h0 = int(probe[0]), int(probe[1])
    height = round(h0 * width / w0 / 2) * 2
    graph = (
        f"[0:v]format=rgba,scale={width}:{height}[f];"
        f"color=c={bg}:s={width}x{height}:r=1,format=rgba[c];"
        f"[c][f]overlay=shortest=1:eof_action=endall,"
        f"tile={cols}x{rows}:padding={pad}:margin={pad}:color={bg}"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [binary("ffmpeg"), "-v", "error", "-y", "-framerate", "1", "-i", str(tmp / "f%03d.png"),
         "-filter_complex", graph, "-frames:v", "1", "-q:v", "3", str(out)],
        check=True, capture_output=True,
    )  # fmt: skip
    shutil.rmtree(tmp, ignore_errors=True)
    return out
