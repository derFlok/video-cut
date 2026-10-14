"""`videocut tool plan check [file]`: validate work/plan.json, the structured twin of
work/project.md that the plan run writes for the app.

    {"title": "...", "format": "16:9",
     "hook": {"text": "...", "src": [t0, t1]},
     "core_message": "...",                               # optional
     "change_note": "...",                 # optional: after plan feedback, what changed
     "target_s": 45,                                      # optional
     "keep_list": [{"n": 1, "src": [t0, t1], "text": "...", "why": "..."}],
     "views": [{"n": 1, "src": [t0, t1], "layout": "screen",
                "zoom_box": [x0, y0, x1, y1], "zoom_t": t,  # optional, box 0..1 of the screen
                "why": "...", "still": "work/plan_stills/view_01.jpg"}],
     "overlays": [{"n": 1, "src": [t0, t1], "scene": "keyword", "text": "...", "why": "..."}],
     "personal_data": [{"id": 1, "kind": "email", "t0": t0, "t1": t1, "box": [...],
                        "reason": "...", "still": "work/plan_stills/pd_01.jpg"}],
     "risks": ["..."]}

All times are source (camera) seconds. `views` is empty for a talking-head job.
`personal_data` repeats work/redaction.json (the same ids, kinds, times/assets and boxes,
after `videocut tool redact measure`); a screenshot candidate has "asset" instead of t0/t1.
Every e-mail, phone or name candidate names its "text" and every candidate with text is
measured ("measured"), otherwise the check fails. `still`, `still_t` (camera s) and
`still_boxes` (the boxes drawn on it: what will be covered) are filled in by
`videocut tool sheet plan`. Prints one line per problem with its JSON path.
"""

import json
from pathlib import Path

from .. import redaction
from ..edl import LAYOUTS
from . import _format
from .brand import SAMPLES
from .common import ToolError, job_parent, read_job, resolve_job, run_tool

FILE = "work/plan.json"
STILLS = "work/plan_stills"
SLACK_S = 0.5  # a range may end this much after the probed source duration


def _num(v) -> bool:
    return redaction._num(v)


def _text(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _src_errors(v, p: str, duration: float | None) -> list[str]:
    if not (isinstance(v, list) and len(v) == 2 and all(_num(x) for x in v)):
        return [f"{p}: must be [t0, t1] in source seconds"]
    if not 0 <= v[0] < v[1]:
        return [f"{p}: need 0 <= t0 < t1, got {v}"]
    if duration is not None and v[1] > duration + SLACK_S:
        return [f"{p}: {v[1]} is beyond the source duration {duration:.2f}s"]
    return []


def _items(data: dict, key: str, required: bool = False) -> tuple[list, list[str]]:
    v = data.get(key)
    if v is None and not required:
        return [], []
    if not isinstance(v, list) or (required and not v):
        return [], [f"$.{key}: must be a {'non-empty ' if required else ''}list"]
    return v, []


def _numbered(items: list, key: str) -> list[str]:
    ns = [it.get("n") for it in items if isinstance(it, dict)]
    errs = [f"$.{key}[{i}].n: must be an integer" for i, n in enumerate(ns) if type(n) is not int]
    if not errs and len(set(ns)) != len(ns):
        errs.append(f"$.{key}: numbers n must be unique")
    return errs


def validate(
    data, fmt: str | None = None, duration: float | None = None, has_screen: bool = True,
    candidates: list[dict] | None = None,
) -> list[str]:  # fmt: skip
    """Problems in a plan.json dict; empty = valid. `fmt`: the job's output format;
    `candidates`: work/redaction.json's list (None: no file) that personal_data must repeat."""
    if not isinstance(data, dict):
        return ["$: must be an object"]
    errs: list[str] = []
    if not _text(data.get("title")):
        errs.append("$.title: must be a non-empty string")
    if data.get("format") not in _format.FORMATS:
        errs.append(f"$.format: must be one of {', '.join(_format.FORMATS)}")
    elif fmt is not None and data["format"] != fmt:
        errs.append(f"$.format: {data['format']} is not the job's format {fmt}")
    hook = data.get("hook")
    if not (isinstance(hook, dict) and _text(hook.get("text"))):
        errs.append('$.hook: must be {"text": "...", "src": [t0, t1]}')
    elif "src" in hook:
        errs += _src_errors(hook["src"], "$.hook.src", duration)
    for key in ("core_message", "change_note", "why"):
        if key in data and not isinstance(data[key], str):
            errs.append(f"$.{key}: must be a string")
    if "target_s" in data and not (_num(data["target_s"]) and data["target_s"] > 0):
        errs.append("$.target_s: must be a positive number")

    keep, e = _items(data, "keep_list", required=True)
    errs += e + _numbered(keep, "keep_list")
    for i, it in enumerate(keep):
        p = f"$.keep_list[{i}]"
        if not isinstance(it, dict):
            errs.append(f"{p}: must be an object")
            continue
        errs += _src_errors(it.get("src"), f"{p}.src", duration)
        errs += [f"{p}.{k}: must be a string" for k in ("text", "why") if not _text(it.get(k))]

    views, e = _items(data, "views")
    errs += e + _numbered(views, "views")
    for i, it in enumerate(views):
        p = f"$.views[{i}]"
        if not isinstance(it, dict):
            errs.append(f"{p}: must be an object")
            continue
        errs += _src_errors(it.get("src"), f"{p}.src", duration)
        layout = it.get("layout")
        if layout not in LAYOUTS:
            errs.append(f"{p}.layout: must be one of {', '.join(LAYOUTS)}")
        elif layout != "face" and not has_screen:
            errs.append(f"{p}.layout: {layout} needs a screen recording (job.json has none)")
        if it.get("zoom_box") is not None:
            if not redaction._box(it["zoom_box"]):
                errs.append(f"{p}.zoom_box: must be [x0, y0, x1, y1] in 0..1 with x0<x1, y0<y1")
            elif layout == "face":
                errs.append(f"{p}.zoom_box: a face view has no screen to zoom into")
        zt, src = it.get("zoom_t"), it.get("src")
        if zt is not None and not (
            _num(zt) and isinstance(src, list) and len(src) == 2 and src[0] <= zt <= src[1]
        ):
            errs.append(f"{p}.zoom_t: must be a source time inside src")
        if "still" in it and not isinstance(it["still"], str | None):
            errs.append(f"{p}.still: must be a path")

    ovs, e = _items(data, "overlays")
    errs += e + _numbered(ovs, "overlays")
    for i, it in enumerate(ovs):
        p = f"$.overlays[{i}]"
        if not isinstance(it, dict):
            errs.append(f"{p}: must be an object")
            continue
        errs += _src_errors(it.get("src"), f"{p}.src", duration)
        if it.get("scene") not in SAMPLES:
            errs.append(f"{p}.scene: must be one of {', '.join(SAMPLES)}")
        errs += [f"{p}.{k}: must be a string" for k in ("text", "why") if not _text(it.get(k))]

    pd, e = _items(data, "personal_data")
    errs += e
    for i, it in enumerate(pd):
        p = f"$.personal_data[{i}]"
        if not isinstance(it, dict):
            errs.append(f"{p}: must be an object")
            continue
        c = {k: v for k, v in it.items() if k not in ("still", "still_t", "still_boxes")}
        errs += [m.replace("$.candidates[0]", p) for m in redaction.validate({"candidates": [c]})]
    if not any(m.startswith("$.personal_data[") for m in errs):
        errs += _same_candidates(pd, candidates)
    errs += _unmeasured(candidates or [])

    risks, e = _items(data, "risks")
    errs += e + [f"$.risks[{i}]: must be a string" for i, r in enumerate(risks) if not _text(r)]
    return errs


def _key(c: dict) -> tuple:
    where = (c.get("asset"),) if "asset" in c else (round(c["t0"], 2), round(c["t1"], 2))
    return (c["id"], c["kind"], *where, tuple(round(v, 3) for v in c["box"]))


def _unmeasured(candidates: list[dict]) -> list[str]:
    """Personal data is placed by measurement, never by eye: an e-mail, phone or name
    candidate names its exact text, and every candidate with text has been measured."""
    errs = []
    for c in candidates:
        cid = c.get("id")
        if "text" not in c and c.get("kind") in redaction.TEXT_KINDS:
            errs.append(
                f'{redaction.FILE} candidate {cid}: needs "text" (the exact text on the '
                "screen, see `videocut tool redact text`) so its position can be measured"
            )
        elif "text" in c and "measured" not in c:
            errs.append(
                f"{redaction.FILE} candidate {cid}: not measured; run "
                "`videocut tool redact measure`, then copy the candidates into personal_data"
            )
    return errs


def _same_candidates(pd: list[dict], candidates: list[dict] | None) -> list[str]:
    if not pd and not candidates:
        return []
    if candidates is None:
        return [f"$.personal_data: lists {len(pd)} item(s) but {redaction.FILE} is missing"]
    mine, theirs = sorted(map(_key, pd)), sorted(map(_key, candidates))
    if mine != theirs:
        return [
            f"$.personal_data: must repeat {redaction.FILE} exactly (ids, kinds, times or "
            "assets, boxes)"
        ]
    return []


def check_job(job: Path, path: Path | None = None) -> tuple[dict, list[str]]:
    """(plan.json data or {}, problems) for the job's plan file."""
    path = path or job / FILE
    if not path.is_file():
        return {}, [f"{path.name} not found"]
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        return {}, [f"$: invalid JSON: {e}"]
    meta = read_job(job)
    try:
        fmt = _format.job_format(meta)
    except _format.FormatError:
        fmt = None
    try:
        duration = json.loads((job / "work" / "probe.json").read_text())["duration"]
    except (OSError, ValueError, KeyError, TypeError):
        duration = None
    try:
        cands = redaction.load(job)
    except redaction.RedactionError as e:
        return data, [str(e)]
    errs = validate(data, fmt, duration, bool(meta.get("screen")), cands)
    return data, errs


def check(args) -> dict:
    job = resolve_job(args.job)
    job = job.resolve()
    path = (job / (args.file or FILE)).resolve()
    if not path.is_relative_to(job):
        raise ToolError(f"{args.file} is outside the job folder")
    data, errs = check_job(job, path)
    for err in errs:
        print(err)
    if errs:
        raise ToolError(f"{len(errs)} problem(s) in {path.name}: " + "; ".join(errs[:5]))
    print(f"{path.relative_to(job)}: ok")
    return {
        "file": str(path.relative_to(job)),
        "keep": len(data["keep_list"]),
        "views": len(data.get("views") or []),
        "overlays": len(data.get("overlays") or []),
        "personal_data": len(data.get("personal_data") or []),
        "stills": sum(
            1 for it in (data.get("views") or []) + (data.get("personal_data") or [])
            if isinstance(it, dict) and it.get("still")
        ),
    }  # fmt: skip


def register(subparsers) -> None:
    p = subparsers.add_parser("plan", help="work with the structured plan (work/plan.json)")
    sub = p.add_subparsers(dest="plan_cmd", required=True)
    c = sub.add_parser("check", help="validate work/plan.json", parents=[job_parent()])
    c.add_argument("file", nargs="?", help="plan file inside the job (default: work/plan.json)")
    c.set_defaults(handler=run_tool("plan check", check))
