"""`videocut tool overlays [--no-render]` (pipeline step 8): the transparent overlay track.

    work/edl.json + work/face.json + brand -> work/overlays.json
        {"size", "width", "height", "fps", "duration", "tokens", "safe": {"top", "bottom", "side"},
         "face": [{"t", "box"}], "captions": [...], "overlays": [...]}
    then  node renderer/render.js work/overlays.json --out work/overlay.mov
                                  --boxes work/overlay_boxes.json

Everything is in OUTPUT time and px. The output size is the job's format (job.json "format",
_format.py), also when the EDL's "size" says otherwise; "width"/"height" repeat it for the
renderer's scenes. `tokens` are the resolved tokens of the job's brand
(job.json "brand", default "_example"); the renderer draws only with them. `face` is the
face track of a current work/face.json mapped through the segments and the very crop
windows render uses (_geometry), so scenes can keep clear of the face. Screen layouts
(product demos, the same _layouts plan render draws): overlays.json "views" lists per
screen/pip/split stretch the screen view and the speaker area; the renderer keeps every
caption and scene off the screen view (a block placed before all items), and in those
stretches `face` is the speaker area (pip inset, the speaker's face in split), so captions
land below the screen view, outside the speaker. `safe` are the
organic safe zones (docs/BRIEF.md §8). captions/overlays are copied from the EDL.

End card (`cta_end`, a full-frame plate): it starts only after the last spoken word
(+0.2 s). An earlier one is shifted there, and when it then runs past the cut, overlays.json
gets the longer duration and render appends a held last frame with silent audio (_plates).
overlays.json "plates" = {"last_word", "tail_s", "shifted"} says what happened.

render composites work/overlay.mov when it is newer than edl.json, so run this tool again
after every EDL change. The renderer's work/overlay_boxes.json (what every scene really
drew) is what `videocut tool verify` checks against the safe zones and the face.

Trust: after a render the tool records the sha256 of overlay.mov, overlay_boxes.json and
overlays.json in $VIDEOCUT_HOME/state/<job id>/overlay_render.json, outside the job folder
(agent runs can write only below work/, like the approvals). verify flags
`overlay_boxes_untrusted` when the files no longer match that record.
"""

import hashlib
import json
import subprocess
from pathlib import Path

from .. import brand
from ..edl import EdlError, load, read_sync
from ..paths import RENDERER, state_dir, tool_env
from ..redaction import asset_boxes, same_asset
from . import _layouts, _plates
from . import _render_plan as plan_mod
from ._geometry import face_track, framed, map_box, safe_zones, sized
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
from .job import redaction_enforced

DEFAULT_BRAND = brand.EXAMPLE_ID
SPEAKER_STEP_S = 0.25
FOCUS_MAX = 0.5  # a zoom target this share of the screen view or more is not kept clear
RECORDED = ("work/overlay.mov", "work/overlay_boxes.json", "work/overlays.json")


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
        edl = load(
            edl_path, source_duration=info["duration"],
            sync=read_sync(job / "work" / "sync.json"), check_sync=True,
        )  # fmt: skip
    except EdlError as e:
        raise ToolError(
            f"{len(e.errors)} problem(s) in edl.json: " + "; ".join(e.errors[:5])
        ) from None
    src_size = (int(info["size"][0]), int(info["size"][1]))
    edl, _ = sized(job, edl)
    edl, _, face = framed(job, edl)
    layouts = [s.layout for s in edl.segments]
    use_layouts = any(lay != "face" for lay in layouts)
    plan = plan_mod.build(edl, src_size, edl.size, layouts if use_layouts else None)
    views = _views(plan, _layouts.plan_all(job, edl, plan, src_size, face, edl.size)["segs"], face,
                   src_size) if use_layouts else []  # fmt: skip
    plates = _plates.for_job(job, edl, plan.duration)
    plan = plan_mod.with_tail(plan, plates.tail_frames)
    cands, _ = redaction_enforced(job)
    track = sorted(face_track(plan, face, src_size) + _speaker_track(views, plan.fps),
                   key=lambda f: f["t"])  # fmt: skip
    doc = {
        "size": list(edl.size),
        "width": edl.size[0],
        "height": edl.size[1],
        "fps": edl.fps,
        "duration": round(plan.duration, 6),
        "tokens": brand_tokens(read_job(job).get("brand") or DEFAULT_BRAND),
        "safe": safe_zones(edl.size),
        "face": track,
        "captions": edl.captions,
        "overlays": [_redacted(job, o, cands) for o in plates.overlays],
        "plates": {
            "last_word": plates.last_word,
            "tail_s": round(float(plates.tail_frames / plan.fps), 3),
            "shifted": plates.shifted,
        },
    }
    if views:
        doc["views"] = views
    return doc


def _redacted(job: Path, item: dict, cands: list[dict]) -> dict:
    """A screenshot_frame item with the boxes of every enforced candidate of its image
    (however the path is spelled) added to props.redact (the safe default, also when the
    EDL forgot one)."""
    props = item.get("props") if isinstance(item, dict) else None
    if item.get("scene") != "screenshot_frame" or not isinstance(props, dict):
        return item
    boxes = [
        b
        for c in cands
        if "asset" in c and same_asset(job, props.get("file"), c["asset"])
        for b in asset_boxes(c)
    ]
    if not boxes:
        return item
    have = props.get("redact")
    have = have if isinstance(have, list) else ([] if have is None else have)
    if not isinstance(have, list):
        return item  # malformed: the renderer fails closed on it
    return {**item, "props": {**props, "redact": [*have, *boxes]}}


def _area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _views(plan, segs: dict, face: dict | None, src: tuple[int, int]) -> list[dict]:
    """Per non-face range (output time and px): `screen`, the screen view to keep clear,
    `focus`, the zoom targets to keep clear ([{"t0", "t1", "box"}]: from the frame a target
    is reached to the next one), and `speaker`, where the speaker is (pip: the inset; split:
    the face box inside the speaker part, else that part's upper half; screen: none). A view
    that fills the frame (_layouts.fills: landscape screen/pip) has `screen` None: the
    screen is the picture, overlays avoid only its targets and the speaker. A target that
    covers FOCUS_MAX of the view or more (a screen-shaped zoom box: the zoom only enlarges
    the whole picture) leaves nothing specific to keep clear and is not listed."""
    out, f0 = [], 0
    median = ((face or {}).get("median") or {}).get("box")
    for i, r in enumerate(plan.ranges):
        seg = segs.get(i)
        if seg is not None:
            speaker = None
            if seg.layout == "pip":
                speaker = seg.inset.box()
            elif seg.layout == "split":
                part = seg.face
                speaker = [part.x, part.y, part.x + part.w, part.y + part.h // 2]
                box = map_box(median, src, seg.camera, (part.w, part.h)) if median else None
                if box:
                    speaker = [box[0] + part.x, box[1] + part.y, box[2] + part.x,
                               box[3] + part.y]  # fmt: skip
            t0, t1 = round(float(f0 / plan.fps), 6), round(float((f0 + r.frames) / plan.fps), 6)
            ends = [*seg.focus_at[1:], r.frames][: len(seg.focus_at)]
            focus = [
                {"t0": round(float((f0 + a) / plan.fps), 6),
                 "t1": round(float((f0 + b) / plan.fps), 6), "box": [round(v) for v in box]}
                for a, b, box in zip(seg.focus_at, ends, seg.focus, strict=True)
                if b > a and _area(box) < FOCUS_MAX * seg.view.w * seg.view.h
            ]  # fmt: skip
            fills = _layouts.fills(seg.view, plan.out_size)
            out.append(
                {
                    "t0": t0,
                    "t1": t1,
                    "layout": seg.layout,
                    "screen": None if fills else seg.view.box(),
                    **({"focus": focus} if fills else {}),
                    "speaker": speaker,
                }
            )
        f0 += r.frames
    return out


def _speaker_track(views: list[dict], fps) -> list[dict]:
    """Face-track samples (every SPEAKER_STEP_S and at both ends) with each view's speaker
    box: in pip/split the speaker area is the face to keep clear."""
    out = []
    for v in views:
        if v["speaker"] is None:
            continue
        t, last = v["t0"], v["t1"] - float(1 / fps)
        while t < last:
            out.append({"t": round(t, 6), "box": v["speaker"]})
            t += SPEAKER_STEP_S
        out.append({"t": round(max(last, v["t0"]), 6), "box": v["speaker"]})
    return out


def record_path(job: Path) -> Path:
    """The overlay record of a job folder (outside it; keyed like the approvals)."""
    return state_dir() / job.resolve().name / "overlay_render.json"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def file_hashes(job: Path) -> dict:
    """sha256 of the recorded files (None for a missing one)."""
    return {rel: _sha256(job / rel) for rel in RECORDED}


def write_record(job: Path) -> None:
    rec = record_path(job)
    rec.parent.mkdir(parents=True, exist_ok=True)
    write_json(rec, {"job": str(job.resolve()), "sha256": file_hashes(job)})


def check_record(job: Path) -> str | None:
    """None when work/overlay.mov, overlay_boxes.json and overlays.json are exactly what
    the last `videocut tool overlays` run produced, else what is wrong."""
    try:
        rec = json.loads(record_path(job).read_text())
    except (OSError, ValueError):
        return "no record of an overlay render by `videocut tool overlays`"
    if not isinstance(rec, dict) or rec.get("job") != str(job.resolve()):
        return "the overlay record belongs to another job folder"
    want = rec.get("sha256") if isinstance(rec.get("sha256"), dict) else {}
    have = file_hashes(job)
    changed = [rel for rel in RECORDED if want.get(rel) is None or want.get(rel) != have[rel]]
    if changed:
        return "changed since `videocut tool overlays` wrote them: " + ", ".join(changed)
    return None


def run_renderer(argv: list[str], job: Path, extra_env: dict | None = None) -> dict:
    """Run renderer/render.js with `argv` for the job folder `job` (the only place scene
    images may come from, VIDEOCUT_JOB_DIR); returns its JSON summary line, raises on
    failure."""
    env = {**tool_env(), "VIDEOCUT_JOB_DIR": str(job), **(extra_env or {})}
    r = subprocess.run(renderer_cmd() + argv, capture_output=True, text=True, env=env, cwd=job)
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
    if doc["plates"]["shifted"] or doc["plates"]["tail_s"]:
        fields["plates"] = doc["plates"]
        for m in doc["plates"]["shifted"]:
            print(f"{m['scene']} moved from {m['from']}s to {m['to']}s (after the last word)")
        if doc["plates"]["tail_s"]:
            print(f"the reel is extended by a held tail of {doc['plates']['tail_s']}s")
    record_path(job).unlink(missing_ok=True)  # the old record is void from here on
    if args.no_render:
        return {**fields, "rendered": False}
    result = render_overlay(job, dst)
    write_record(job)
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
