"""B-roll checks of `videocut tool verify` (render.json "broll": the stretches that show
chosen material full frame, tools/_broll.py).

Per b-roll stretch the middle frame of the reel is read in grey at a quarter of its size:

    broll_black   the frame is mostly black (BLACK_SHARE of its pixels below BLACK_LUMA):
                  a dark clip, a failed decode
    broll_aspect  letterbox or pillarbox bars: both bands along two opposite edges
                  (BAR_SHARE of the frame) are flat (std < BAR_STD) and dark (mean <
                  BAR_LUMA) while the middle is not: the material was not cropped to fill
"""

import subprocess
from pathlib import Path

import numpy as np

from ..paths import tool_env
from .common import binary

BLACK_LUMA = 28  # limited range: 16 is black
BLACK_SHARE = 0.95
BAR_SHARE = 0.06
BAR_STD = 3.0
BAR_LUMA = 40


def frame(reel: Path, t: float, size: tuple[int, int]) -> np.ndarray | None:
    """The reel's frame at output time `t` in grey at `size` (w, h); None when unreadable."""
    w, h = size
    out = subprocess.run(
        [binary("ffmpeg"), "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(reel),
         "-frames:v", "1", "-an", "-vf", f"scale={w}:{h}:flags=area,format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, env=tool_env(), check=False,
    )  # fmt: skip
    if out.returncode != 0 or len(out.stdout) < w * h:
        return None
    return np.frombuffer(out.stdout[: w * h], dtype=np.uint8).reshape(h, w).astype(np.float32)


def flags_of(img: np.ndarray) -> list[str]:
    flags = []
    if float((img < BLACK_LUMA).mean()) >= BLACK_SHARE:
        return ["broll_black"]
    h, w = img.shape
    for a, b, mid in (
        (img[: max(1, int(h * BAR_SHARE))], img[h - max(1, int(h * BAR_SHARE)) :],
         img[int(h * 0.3) : int(h * 0.7)]),
        (img[:, : max(1, int(w * BAR_SHARE))], img[:, w - max(1, int(w * BAR_SHARE)) :],
         img[:, int(w * 0.3) : int(w * 0.7)]),
    ):  # fmt: skip
        flat = all(float(x.std()) < BAR_STD and float(x.mean()) < BAR_LUMA for x in (a, b))
        if flat and float(mid.std()) >= BAR_STD:
            flags.append("broll_aspect")
            break
    return flags


def check(render: dict, reel: Path, size: tuple[int, int]) -> list[dict]:
    """One entry per render.json "broll" stretch: {segment, wish, candidate, t0, t1, t,
    flags}."""
    out = []
    small = (max(16, size[0] // 4 // 2 * 2), max(16, size[1] // 4 // 2 * 2))
    for b in render.get("broll") or []:
        try:
            t0, t1 = float(b["out_t0"]), float(b["out_t1"])
        except (KeyError, TypeError, ValueError):
            continue
        t = round((t0 + t1) / 2, 3)
        img = frame(reel, t, small)
        flags = ["broll_unreadable"] if img is None else flags_of(img)
        out.append({"segment": b.get("segment"), "wish": b.get("wish"),
                    "candidate": b.get("candidate"), "t0": t0, "t1": t1, "t": t,
                    "flags": flags})  # fmt: skip
    return out
