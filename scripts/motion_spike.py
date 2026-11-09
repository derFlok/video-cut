"""Motion scene spike: build work/motion/motion.json for a span of a finished job.

    python scripts/motion_spike.py <job> --from <s> --dur <s> [--beats work/motion/beats.json]
        [--scene work/motion/scene.js] [--top 230] [--forehead 0.2]

Reads work/overlays.json (output size, fps, resolved brand tokens, caption words in output
time, the face track in output px). The stage region spans the frame width inside the side
safe zone, from --top down to the highest face top over the span plus --forehead of the
face height (hair and forehead may be covered, eyes may not). Words are shifted to scene
time (0 = --from). The beat sheet (JSON {id: {word, n, edge, offset}}) is copied in as is;
the renderer resolves it on the words. Writes nothing outside work/motion/.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(
    job: Path, t0: float, dur: float, beats: Path, scene: str, top: int, forehead: float
) -> dict:
    ov = json.loads((job / "work" / "overlays.json").read_text())
    w, h = ov["size"]
    side = int(ov.get("safe", {}).get("side", round(w * 0.06)))
    words = [
        {"w": x["w"], "t0": round(x["t0"] - t0, 3), "t1": round(x["t1"] - t0, 3)}
        for c in ov.get("captions", [])
        for x in c.get("words", [])
        if t0 <= x["t0"] < t0 + dur
    ]
    faces = [f["box"] for f in ov.get("face", []) if t0 <= f["t"] <= t0 + dur]
    limit = min((b[1] + forehead * (b[3] - b[1]) for b in faces), default=h * 0.33)
    region = [side, top, w - side, int(limit)]
    return {
        "mode": "motion",
        "size": [w, h],
        "fps": ov["fps"],
        "duration": dur,
        "unit": min(w, h),
        "region": region,
        "scene": scene,
        "seed": 1,
        "span": {"from": t0, "to": t0 + dur},
        "words": words,
        "beats": json.loads(beats.read_text()) if beats.is_file() else {},
        "tokens": ov["tokens"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", type=Path)
    ap.add_argument("--from", dest="t0", type=float, required=True)
    ap.add_argument("--dur", type=float, required=True)
    ap.add_argument("--beats", default="work/motion/beats.json")
    ap.add_argument("--scene", default="work/motion/scene.js")
    ap.add_argument("--top", type=int, default=230)
    ap.add_argument("--forehead", type=float, default=0.2)
    a = ap.parse_args()
    spec = build(a.job, a.t0, a.dur, a.job / a.beats, a.scene, a.top, a.forehead)
    out = a.job / "work" / "motion" / "motion.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n")
    print(
        json.dumps(
            {"ok": True, "out": str(out), "region": spec["region"], "words": len(spec["words"])}
        )
    )


if __name__ == "__main__":
    main()
