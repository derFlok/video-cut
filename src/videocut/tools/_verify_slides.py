"""The slide checks of `videocut tool verify` (EDL segments with a slide layout).

Per slide the EDL shows, from work/slides/<id>.json (the layout report `slides render`
wrote; believed only while it, the clip and their record outside the job folder match,
and while its spec is the one the current slides.json, EDL and brand give):

    slide_text_overflow    the text does not fit its content box even at 60 % (the
                           renderer never shrinks further), or a text box leaves the box
    slide_reading_time     on screen (all its segments) shorter than
                           slides.READ_S_PER_WORD per word + slides.READ_EXTRA_S
    slide_bubble_over_text the speaker (the round bubble of `slide`, the speaker part of
                           `slide_split`) covers a text box of the slide
    slide_missing          no clip or report; `slide_untrusted` changed since `slides
                           render` wrote them; `slide_stale` made for another spec
    slide_unchecked        work/slides.json is missing or invalid

Also the spans verify needs for its frozen check: a `slide_full` stretch is a still
picture on purpose; in `slide` / `slide_split` the camera is judged inside the speaker
area only (`bubbles`). `times`: a moment near the end of every slide stretch (its final
state) for the slide sheet.
"""

import json
from pathlib import Path

from .. import edl as edl_mod
from . import _layouts
from . import slides as slides_mod
from ._geometry import overlap, sized
from .common import ToolError, read_job
from .job import job_lang

EDGE_TOL = 1.0  # px a text box may stand out of its content box (rounding)


def _circle_hits(box: list[float], c: list[float]) -> bool:
    """Whether the rect `box` meets the circle inscribed in the square `c`."""
    cx, cy, r = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2, (c[2] - c[0]) / 2
    nx, ny = min(max(cx, box[0]), box[2]), min(max(cy, box[1]), box[3])
    return (nx - cx) ** 2 + (ny - cy) ** 2 < r * r


def text_flags(report: dict, geo: dict) -> list[str]:
    """Flags of one slide's layout report against its geometry (full output px)."""
    flags = []
    rx, ry = geo["rect"][0], geo["rect"][1]
    # the content box the renderer chose (clip px), else the geometry's largest one
    ra = report.get("area")
    ok = isinstance(ra, list) and len(ra) == 4
    area = [ra[0] + rx, ra[1] + ry, ra[2] + rx, ra[3] + ry] if ok else geo["area"]
    texts = [[t["box"][0] + rx, t["box"][1] + ry, t["box"][2] + rx, t["box"][3] + ry]
             for t in report.get("texts") or [] if isinstance(t, dict)]  # fmt: skip
    outside = any(
        b[0] < area[0] - EDGE_TOL or b[1] < area[1] - EDGE_TOL
        or b[2] > area[2] + EDGE_TOL or b[3] > area[3] + EDGE_TOL
        for b in texts
    )  # fmt: skip
    if not report.get("fits") or outside or report.get("missing"):
        flags.append("slide_text_overflow")
    sp = geo.get("speaker")
    if sp and any((_circle_hits(b, sp) if geo.get("round") else overlap(b, sp) > 0) for b in texts):
        flags.append("slide_bubble_over_text")
    return flags


def check(job: Path, edl: edl_mod.Edl, reel_size: tuple[int, int]) -> dict:
    """{"slides": [{"id", "layout", "spans", "seconds", "need", "scale", "flags"}],
    "still": [(t0, t1)], "bubbles": [(t0, t1, box at reel size)], "times": [s]};
    {} when the EDL shows no slide."""
    if not any(edl_mod.is_slide(s.layout) for s in edl.segments):
        return {}
    edl = sized(job, edl)[0]
    try:
        doc = slides_mod.load_doc(job)
    except ToolError as e:
        return {"slides": [{"id": None, "flags": ["slide_unchecked"], "error": str(e)}]}
    slides = slides_mod.by_id(doc)
    places = slides_mod.placements(edl)
    tokens, lang = slides_mod.brand_tokens(job), job_lang(read_job(job))
    cands, shot_style = slides_mod.redaction(job)  # image slides burn these boxes in
    out, fps = tuple(edl.size), edl.fps
    docs, still, bubbles, times = [], [], [], []
    for sid, pl in places.items():
        spans = [(r["out_frame"] / fps, (r["out_frame"] + r["frames"]) / fps) for r in pl["runs"]]
        geo_reel = _layouts.slide_geometry(pl["layout"], reel_size, tokens)
        for a, b in spans:
            if pl["layout"] == "slide_full":
                still.append((a, b))
            elif geo_reel.speaker is not None:
                bubbles.append((a, b, [int(v) for v in geo_reel.speaker.box()]))
        times.append(round(max(spans[-1][0], spans[-1][1] - 0.25), 3))
        entry = {"id": sid, "layout": pl["layout"],
                 "spans": [[round(a, 3), round(b, 3)] for a, b in spans],
                 "seconds": pl["seconds"]}  # fmt: skip
        s = slides.get(sid)
        if s is None:
            docs.append({**entry, "flags": ["slide_missing"], "error": "not in slides.json"})
            continue
        need = slides_mod.reading_s(s)
        entry["need"] = need
        flags = [] if pl["seconds"] + 1e-6 >= need else ["slide_reading_time"]
        rel = [f"work/slides/{sid}.mov", f"work/slides/{sid}.json"]
        try:
            report = json.loads((job / rel[1]).read_text())
        except (OSError, ValueError):
            report = None
        if report is None or not (job / rel[0]).is_file():
            docs.append({**entry, "flags": [*flags, "slide_missing"]})
            continue
        untrusted = slides_mod.check_record(job, rel)
        if untrusted:
            docs.append({**entry, "flags": [*flags, "slide_untrusted"], "error": untrusted})
            continue
        want = slides_mod.spec(s, pl["layout"], out, fps, pl["seconds"], pl["reveal"], tokens,
                               lang, redact=slides_mod.page_boxes(job, s, cands),
                               redact_style=shot_style)  # fmt: skip
        if report.get("spec_sha256") != slides_mod.spec_sha(want, job):
            flags.append("slide_stale")
        geo = slides_mod.geometry_doc(pl["layout"], out, tokens)
        flags += text_flags(report, geo)
        docs.append({**entry, "scale": report.get("scale"), "words": report.get("words"),
                     "flags": flags})  # fmt: skip
    return {"slides": docs, "still": still, "bubbles": bubbles, "times": times}
