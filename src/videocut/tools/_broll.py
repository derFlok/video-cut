"""B-roll ranges of a render (EDL layout `broll`): which chosen material each range shows
and how (_layouts.BrollSeg, drawn by _layouts.compose_broll).

Only the human's choices count (tools/material.chosen: the record outside the job folder,
the file's SHA-256 checked): a broll segment whose wish has no valid choice stops the
render. The render.json "broll" list says what was shown where (output seconds), so the
AI label and the disclosure can be built from it.
"""

from pathlib import Path

from ..edl import is_broll
from . import _layouts
from . import material as material_mod
from ._color import to_sdr
from .common import ToolError


def not_chosen(n) -> str:
    return (
        f"material wish {n} has no candidate chosen by the human (videocut tool material show; "
        "a human chooses with `videocut tool material choose`)"
    )


def plan_broll(job: Path, edl, plan) -> dict:
    """{"segs": {range index: BrollSeg}, "doc": [render.json "broll" entries]}."""
    chosen = material_mod.chosen(job)
    segs, doc, f0 = {}, [], 0
    for i, r in enumerate(plan.ranges):
        if not is_broll(r.layout):
            f0 += r.frames
            continue
        n = edl.segments[r.seg].material
        c = chosen.get(n)
        if c is None:
            raise ToolError(f"segment {r.seg}: {not_chosen(n)}")
        path = (job / c["file"]).resolve()
        if not path.is_relative_to(job.resolve()) or not path.is_file():
            raise ToolError(f"segment {r.seg}: the material file {c['file']} is missing")
        kind = "video" if c.get("kind") == "video" else "image"
        sdr = ""
        if kind == "video":
            from .ingest import probe

            info = probe(path, need_audio=False)
            size = tuple(info["size"])
            sdr = to_sdr(info)
            duration = info.get("duration") or c.get("duration_s")
        else:
            size, duration = (int(c["width"]), int(c["height"])), None
            if not all(size):
                raise ToolError(f"segment {r.seg}: the size of {c['file']} is unknown")
        shown, loop = _layouts.broll_timing(r.frames, kind, duration, plan.fps)
        segs[i] = _layouts.BrollSeg(
            frames=r.frames, wish=n, candidate=c["id"], kind=kind, file=path, size=size,
            shown=shown, loop=loop, sdr=sdr, ai_generated=bool(c.get("ai_generated")),
        )  # fmt: skip
        doc.append({
            "segment": r.seg, "wish": n, "candidate": c["id"], "file": c["file"],
            "kind": kind, "ai_generated": bool(c.get("ai_generated")),
            "style": c.get("style"), "source": c.get("source"), "model": c.get("model"),
            "out_t0": round(float(f0 / plan.fps), 3),
            "out_t1": round(float((f0 + shown) / plan.fps), 3),
            "looped": loop,
        })  # fmt: skip
        f0 += r.frames
    return {"segs": segs, "doc": doc}
