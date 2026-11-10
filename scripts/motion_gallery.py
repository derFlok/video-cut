"""Render the motion component gallery (renderer/test/motion/gallery_full.js) in a brand's
look: one still per component slot and a contact sheet.

    python scripts/motion_gallery.py <brand> <out-dir> [--gpu swiftshader|metal] [--clip]

<brand> is a brand id or folder (VIDEOCUT_HOME decides where ids are looked up). Writes
<out-dir>/gallery.jpg (+ gallery.mov and events.json with --clip). Nothing is written into
the repository.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from videocut import brand
from videocut.tools import _motion_sheet as ms

SIZE = (1080, 1920)
REGION = (40, 200, 1040, 900)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("brand")
    ap.add_argument("out", type=Path)
    ap.add_argument("--gpu", default="swiftshader")
    ap.add_argument("--clip", action="store_true", help="also render the whole gallery clip")
    a = ap.parse_args()
    tokens = brand.load(a.brand)
    src = ms.GALLERY_SCENE.read_text()
    slot = float(re.search(r"export const SLOT = ([0-9.]+)", src).group(1))
    n = len(re.findall(r"^\s*// \d+: ", src, re.M))
    spec = ms.motion_spec(tokens, size=SIZE, region=REGION, duration=round(n * slot, 3))
    times = [round(i * slot + 2.2, 3) for i in range(n)]
    extra = ["--gpu", a.gpu, "--events", str(a.out / "events.json")]
    if a.clip:
        extra += ["--out", str(a.out / "gallery.mov")]
    run = a.out / "run"
    res = ms.run_motion(
        spec, src, run,
        ["--region-only", "--stills", str(run / "stills"), ",".join(map(str, times)), *extra],
    )  # fmt: skip
    pngs = [Path(p) for p in res["stills"]]
    bg = tokens["colors"]["bg"]
    sheet = ms.contact_sheet(pngs, a.out / "gallery.jpg", cols=4, background=bg)
    keep = {k: res.get(k) for k in ("frames", "render_seconds", "ms_per_frame", "style", "cues")}
    print(json.dumps({"ok": True, "sheet": str(sheet), "slots": n, **keep}))


if __name__ == "__main__":
    main()
