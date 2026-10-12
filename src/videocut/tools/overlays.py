"""`videocut tool overlays [--no-render]` (pipeline step 8): the transparent overlay track.

    work/edl.json + work/face.json + brand -> work/overlays.json
        {"size", "fps", "duration", "tokens", "safe": {"top", "bottom", "side"},
         "face": [{"t", "box"}], "captions": [...], "overlays": [...]}
    then  node renderer/render.js work/overlays.json --out work/overlay.mov
                                  --boxes work/overlay_boxes.json

Everything is in OUTPUT time and px. `tokens` are the resolved tokens of the job's brand
(job.json "brand", default "_example"); the renderer draws only with them. `face` is the
face track of a current work/face.json mapped through the segments and the very crop
windows render uses (_geometry), so scenes can keep clear of the face. `safe` are the
organic safe zones (docs/BRIEF.md §8). captions/overlays are copied from the EDL.

render composites work/overlay.mov when it is newer than edl.json, so run this tool again
after every EDL change. The renderer's work/overlay_boxes.json (what every scene really
drew) is what `videocut tool verify` checks against the safe zones and the face.
"""

import json
import subprocess
from pathlib import Path

from .. import brand
from ..edl import EdlError, load
from ..paths import RENDERER, tool_env
from . import _render_plan as plan_mod
from ._geometry import face_track, framed, safe_zones
from .common import (
    ToolError,
    binary,
    job_parent,
    read_job,
    resolve_job,
    run_tool,
    write_json,
)
from .ingest import probe

DEFAULT_BRAND = brand.EXAMPLE_ID


def brand_tokens(ref) -> dict:
    """Resolved design tokens of brand `ref` (videocut.brand.load)."""
    if not isinstance(ref, str):
        raise ToolError('job.json "brand" must be a brand id')
    try:
        return brand.load(ref)
    except brand.BrandError as e:
        raise ToolError(str(e)) from None


def renderer_cmd() -> list[str]:
    script = RENDERER / "render.js"
    if not script.is_file():
        raise ToolError("renderer/render.js is missing (overlay renderer not installed)")
    return [binary("node"), str(script)]


def build(job: Path) -> dict:
    """The overlays.json document for the job's current EDL."""
    edl_path = job / "work" / "edl.json"
    if not edl_path.is_file():
        raise ToolError("work/edl.json is missing")
    try:
        rel = json.loads(edl_path.read_text()).get("source")
    except (ValueError, AttributeError):
        rel = None
    src = (job / rel).resolve() if isinstance(rel, str) and rel else None
    if src is None or not src.is_relative_to(job) or not src.is_file():
        raise ToolError(f"EDL source {rel!r} is missing or outside the job folder")
    info = probe(src)
    try:
        edl = load(edl_path, source_duration=info["duration"])
    except EdlError as e:
        raise ToolError(
            f"{len(e.errors)} problem(s) in edl.json: " + "; ".join(e.errors[:5])
        ) from None
    src_size = (int(info["size"][0]), int(info["size"][1]))
    edl, _, face = framed(job, edl)
    plan = plan_mod.build(edl, src_size, edl.size)
    return {
        "size": list(edl.size),
        "fps": edl.fps,
        "duration": round(plan.duration, 6),
        "tokens": brand_tokens(read_job(job).get("brand") or DEFAULT_BRAND),
        "safe": safe_zones(edl.size),
        "face": face_track(plan, face, src_size),
        "captions": edl.captions,
        "overlays": edl.overlays,
    }


def run_renderer(argv: list[str], cwd: Path) -> dict:
    """Run renderer/render.js with `argv`; returns its JSON summary line, raises on failure."""
    r = subprocess.run(
        renderer_cmd() + argv, capture_output=True, text=True, env=tool_env(), cwd=cwd
    )
    lines = r.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        result = {}
    if r.returncode != 0:
        tail = (result.get("error") if isinstance(result, dict) else None) or (
            r.stderr or r.stdout
        ).strip()[-600:]
        raise ToolError(f"renderer failed: {tail}")
    return result if isinstance(result, dict) else {}


def render_overlay(job: Path, doc_path: Path) -> dict:
    """Render work/overlay.mov + work/overlay_boxes.json; returns the renderer summary."""
    work = job / "work"
    result = run_renderer(
        [
            str(doc_path),
            "--out",
            str(work / "overlay.mov"),
            "--boxes",
            str(work / "overlay_boxes.json"),
        ],
        job,
    )
    for name in ("overlay.mov", "overlay_boxes.json"):
        if not (work / name).is_file():
            raise ToolError(f"renderer reported success but wrote no work/{name}")
    return result


def overlays(args) -> dict:
    job = resolve_job(args.job)
    doc = build(job)
    dst = job / "work" / "overlays.json"
    write_json(dst, doc)
    print(
        f"work/overlays.json: {len(doc['captions'])} caption groups, "
        f"{len(doc['overlays'])} overlays, {len(doc['face'])} face samples, "
        f"{doc['duration']:.2f}s"
    )
    fields = {
        "job": str(job),
        "file": "work/overlays.json",
        "captions": len(doc["captions"]),
        "overlays": len(doc["overlays"]),
        "face_samples": len(doc["face"]),
        "duration": round(doc["duration"], 3),
    }
    if args.no_render:
        return {**fields, "rendered": False}
    result = render_overlay(job, dst)
    boxes = json.loads((job / "work" / "overlay_boxes.json").read_text())
    return {
        **fields,
        "rendered": True,
        "overlay": "work/overlay.mov",
        "boxes": "work/overlay_boxes.json",
        "box_count": len(boxes) if isinstance(boxes, list) else None,
        "renderer": result,
    }


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "overlays",
        help="work/overlays.json + transparent work/overlay.mov",
        parents=[job_parent()],
    )
    p.add_argument("--no-render", action="store_true", help="only write work/overlays.json")
    p.set_defaults(handler=run_tool("overlays", overlays))
