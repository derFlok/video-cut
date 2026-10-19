"""Verify: the AI label (`ai_label_missing`).

Every AI-generated material item the cut shows that must carry the visible label
(_ai_label.labelled) needs "ai_badge" boxes in work/overlay_boxes.json (what the renderer
really drew) whose drawn times cover the item's whole output span, give or take
SLACK_FRAMES frames. No boxes file at all (no overlay rendered) counts as not labelled.
The drawn badge spans are returned for the overlay picture sheet (one tile each)."""

from pathlib import Path

from ..edl import Edl
from . import _ai_label

SLACK_FRAMES = 1.5


def _covered(t0: float, t1: float, drawn: list[tuple[float, float]], slack: float) -> bool:
    """Whether the union of the `drawn` intervals covers [t0, t1]."""
    cur = t0
    for a, b in sorted(drawn):
        if a - slack > cur:
            break
        cur = max(cur, b)
        if cur + slack >= t1:
            return True
    return cur + slack >= t1


def check(job: Path, edl: Edl, raw: dict, boxes: list[dict] | None, fps: float) -> dict:
    """{"items": [{"n", "kind", "t0", "t1", "model", "style", "labelled", "drawn"}],
    "flagged": [overlay_flagged entries], "badges": [{"scene", "t0", "t1"} drawn]}."""
    items = [i for i in _ai_label.on_screen(job, edl, raw) if _ai_label.is_ai(i)]
    drawn = []
    for b in boxes or []:
        if b.get("scene") != _ai_label.SCENE:
            continue
        x0, y0, x1, y1 = b["box"]
        if x1 <= x0 or y1 <= y0:
            continue
        d = b.get("drawn") if isinstance(b.get("drawn"), list) and len(b["drawn"]) == 2 else None
        drawn.append((float(d[0]), float(d[1])) if d else (float(b["t0"]), float(b["t1"])))
    frame = 1 / fps if fps else 0.04
    docs, flagged = [], []
    for i in items:
        need = _ai_label.labelled(i)
        # the last frame shown starts one frame before the segment's end
        ok = not need or _covered(i["t0"], i["t1"] - frame, drawn, SLACK_FRAMES * frame)
        docs.append({
            "n": i["n"], "kind": i["kind"], "t0": round(i["t0"], 3), "t1": round(i["t1"], 3),
            "model": i["candidate"].get("model"), "style": _ai_label.style(i),
            "labelled": need, "drawn": ok if need else None,
        })  # fmt: skip
        if not ok:
            flagged.append({"scene": _ai_label.SCENE, "t0": round(i["t0"], 3),
                            "t1": round(i["t1"], 3), "flags": ["ai_label_missing"],
                            "material": i["n"]})  # fmt: skip
    badges = [{"scene": _ai_label.SCENE, "t0": a, "t1": b} for a, b in sorted(set(drawn))]
    return {"items": docs, "flagged": flagged, "badges": badges}


def unchecked(job: Path, duration: float) -> dict:
    """The EDL cannot be read or is stale: fail closed when any chosen candidate is
    AI-generated (flag `ai_label_unchecked` over the whole reel)."""
    mat = _ai_label.read_material(job)
    ai = [
        w["n"] for w in _ai_label.chosen_wishes(mat) if _ai_label.is_ai({"candidate": w["_chosen"]})
    ]
    flagged = [{"scene": _ai_label.SCENE, "t0": 0.0, "t1": round(duration, 3),
                "flags": ["ai_label_unchecked"], "material": ai[0]}] if ai else []  # fmt: skip
    return {"items": [], "flagged": flagged, "badges": []}
