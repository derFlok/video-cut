"""Personal data in product demos: candidates (plan), the human decision, enforcement.

    work/redaction.json   written by the plan run: every place that may show personal or
                          customer data
        {"candidates": [
            {"id": 1, "kind": "email", "t0": 18.0, "t1": 24.1, "box": [x0, y0, x1, y1],
             "reason": "..."},                       # on the screen recording: CAMERA time,
                                                     # box normalised to the screen
            {"id": 2, "kind": "name", "asset": "input/shot.png", "box": [...],
             "reason": "..."}                        # in a screenshot (job.json assets)
        ]}
        kind: email | phone | name | customer | other

    Measured positions (`videocut tool redact measure`, never estimated by eye): a screen
    candidate with "text" (the exact text, as `videocut tool redact text` lists it) gets
        "range": [t0, t1]   the time the plan named (kept; t0/t1 become the measured hull)
        "track": [{"t0", "t1", "box", "text_box", "source"}]   camera time pieces: source
                            "ocr": box = the measured text (text_box) plus a margin, the
                            union over the piece; "area": the text was not found there,
                            box = the whole named area plus AREA_PAD (fail closed)
        "measured": "ok" | "partial" | "failed", "note": what the human should know
    "box" stays the area the plan named (the panel the item is in). Blocks are drawn from
    the track when there is one, from the box otherwise (pieces()). A screenshot candidate
    with "text" gets "track": [{"box", "text_box", "source"}] (no times).

    $VIDEOCUT_HOME/state/<job>/redaction_decision.json   the HUMAN decision (`videocut tool
                          job redaction`, or the approval prompt of `videocut run`), outside
                          the agent-writable job folder: {"job", "at", "method",
                          "candidates_sha256", "candidates" (a copy), "redact": [ids],
                          "demo": [ids]}

Safe default: without a valid decision (none, or work/redaction.json changed since) every
candidate is redacted. What a decision once marked "redact" stays redacted even when the
candidate list changes later (the record keeps its own copy), and so does every candidate
the approval record saw: an agent cannot un-redact anything by editing the list.

`enforced(job)` is the list render, overlays and verify act on; `missing(job, data)` lists
decided-redact candidates the EDL does not cover (flag `redaction_missing`).
"""

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from .paths import state_dir

FILE = "work/redaction.json"
KINDS = ("email", "phone", "name", "customer", "other")
EPS = 1e-3  # seconds
BOX_TOL = 0.005  # a redact box may be this much smaller than the candidate on each side
MEASURED = ("ok", "partial", "failed")
SOURCES = ("ocr", "area")


class RedactionError(ValueError):
    pass


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _box(b) -> bool:
    return (
        isinstance(b, list)
        and len(b) == 4
        and all(_num(v) and 0 <= v <= 1 for v in b)
        and b[2] > b[0]
        and b[3] > b[1]
    )


def validate(data) -> list[str]:
    """Problems of a redaction.json document (JSON paths), empty when valid."""
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        return ['$: must be {"candidates": [...]}']
    errs, seen = [], set()
    for i, c in enumerate(data["candidates"]):
        p = f"$.candidates[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{p}: must be an object")
            continue
        cid = c.get("id")
        if not (isinstance(cid, int) and not isinstance(cid, bool) and cid >= 1) or cid in seen:
            errs.append(f"{p}.id: must be a unique integer >= 1")
        seen.add(cid)
        if c.get("kind") not in KINDS:
            errs.append(f"{p}.kind: must be one of {', '.join(KINDS)}")
        if not _box(c.get("box")):
            errs.append(f"{p}.box: must be [x0, y0, x1, y1] in 0..1 with x0 < x1, y0 < y1")
        if "asset" in c:
            a = c["asset"]
            if not (isinstance(a, str) and a and not a.startswith("/") and ".." not in a):
                errs.append(f"{p}.asset: must be a job-relative image path (job.json assets)")
        elif not (_num(c.get("t0")) and _num(c.get("t1")) and c["t0"] < c["t1"]):
            errs.append(f"{p}: needs camera seconds t0 < t1 (or an asset)")
        errs += _measure_errors(c, p)
    return errs


def _measure_errors(c: dict, p: str) -> list[str]:
    errs = []
    if "text" in c and not (isinstance(c["text"], str) and c["text"].strip()):
        errs.append(f"{p}.text: must be the non-empty text shown on the screen")
    if "measured" in c and c["measured"] not in MEASURED:
        errs.append(f"{p}.measured: must be one of {', '.join(MEASURED)}")
    if "note" in c and not isinstance(c["note"], str):
        errs.append(f"{p}.note: must be a string")
    r = c.get("range")
    if "range" in c and not (
        isinstance(r, list) and len(r) == 2 and _num(r[0]) and _num(r[1]) and r[0] < r[1]
    ):
        errs.append(f"{p}.range: must be [t0, t1] camera seconds")
    if "track" not in c:
        return errs
    tr = c["track"]
    if not isinstance(tr, list) or not tr:
        return [*errs, f"{p}.track: must be a non-empty list"]
    timed = "asset" not in c
    for j, k in enumerate(tr):
        q = f"{p}.track[{j}]"
        if not isinstance(k, dict) or not _box(k.get("box")):
            errs.append(f"{q}.box: must be [x0, y0, x1, y1] in 0..1")
            continue
        if "text_box" in k and not _box(k["text_box"]):
            errs.append(f"{q}.text_box: must be [x0, y0, x1, y1] in 0..1")
        if k.get("source") not in SOURCES:
            errs.append(f"{q}.source: must be one of {', '.join(SOURCES)}")
        if timed and not (_num(k.get("t0")) and _num(k.get("t1")) and k["t0"] < k["t1"]):
            errs.append(f"{q}: needs camera seconds t0 < t1")
    return errs


def pieces(c: dict) -> list[dict]:
    """What is drawn for candidate `c`: its measured track, else {t0, t1, box} (a screen
    candidate) or {box} (a screenshot) from the candidate itself."""
    if c.get("track"):
        keys = ("box",) if "asset" in c else ("t0", "t1", "box")
        return [{k: p[k] for k in keys} for p in c["track"]]
    return [{"box": c["box"]}] if "asset" in c else [{k: c[k] for k in ("t0", "t1", "box")}]


def load(job: Path) -> list[dict] | None:
    """The candidates of work/redaction.json; None without the file. An invalid file raises
    RedactionError (fail closed: nothing may render past a list nobody can read)."""
    path = job / FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise RedactionError(f"{FILE} is not valid JSON: {e}") from None
    errs = validate(data)
    if errs:
        raise RedactionError(f"{FILE}: " + "; ".join(errs[:5]))
    return data["candidates"]


def file_sha(job: Path) -> str | None:
    path = job / FILE
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def decision_path(job: Path) -> Path:
    return state_dir() / job.resolve().name / "redaction_decision.json"


def read_decision(job: Path) -> dict | None:
    """The human decision for this job folder (any, even a stale one), or None."""
    try:
        rec = json.loads(decision_path(job).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("job") != str(job.resolve()):
        return None
    return rec


def valid_decision(job: Path) -> dict | None:
    """The decision when it was made on exactly the current work/redaction.json."""
    rec = read_decision(job)
    sha = file_sha(job)
    return (
        rec if rec is not None and sha is not None and rec.get("candidates_sha256") == sha else None
    )


def decide(job: Path, redact: list[int] | None, method: str) -> dict:
    """Record the human decision: `redact` = the ids to redact (None = all); the rest are
    demo data. Only ever called by human-facing commands (never inside an agent run)."""
    cands = load(job)
    if not cands:
        raise RedactionError(f"{FILE} lists no candidates; nothing to decide")
    ids = [c["id"] for c in cands]
    chosen = ids if redact is None else sorted(set(redact))
    unknown = [i for i in chosen if i not in ids]
    if unknown:
        raise RedactionError(f"unknown candidate number(s) {unknown}; the list has {ids}")
    rec = {
        "job": str(job.resolve()),
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": method,
        "candidates_sha256": file_sha(job),
        "candidates": cands,
        "redact": chosen,
        "demo": [i for i in ids if i not in chosen],
    }
    path = decision_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)
    return rec


def asset_file(job: Path, ref) -> Path | None:
    """The file an asset reference names, resolved like the renderer does (a job-relative
    path or file:// URL, symlinks followed); None when it is not a file reference."""
    if not isinstance(ref, str) or not ref.strip() or "\0" in ref:
        return None
    ref = ref.strip()
    if ref.startswith("file://"):
        ref = unquote(urlparse(ref).path)
    elif re.match(r"[a-z][a-z0-9+.-]*:", ref, re.I):  # another scheme: not a file
        return None
    try:
        return (job / ref).resolve()
    except (OSError, ValueError):
        return None


def same_asset(job: Path, ref, asset: str) -> bool:
    """Whether the overlay file reference `ref` shows the candidate's `asset`: the same
    resolved file, or a file with the same bytes (a copy under another name)."""
    a, b = asset_file(job, ref), asset_file(job, asset)
    if a is None or b is None:
        return False
    if a == b:
        return True
    try:
        return (
            a.is_file()
            and b.is_file()
            and a.stat().st_size == b.stat().st_size
            and (hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest())
        )
    except OSError:
        return False


def state_sha(cands: list[dict]) -> str | None:
    """Fingerprint of the enforced candidates (job.json "cut" record: a changed decision
    means the reel must be rendered again); None without any."""
    if not cands:
        return None
    blob = json.dumps(sorted(cands, key=_key), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _key(c: dict) -> tuple:
    where = ("asset", c["asset"]) if "asset" in c else ("t", round(c["t0"], 3), round(c["t1"], 3))
    drawn = tuple(
        (round(p.get("t0", 0), 3), round(p.get("t1", 0), 3), *(round(v, 4) for v in p["box"]))
        for p in pieces(c)
    )
    return (*where, tuple(round(v, 4) for v in c["box"]), drawn)


def enforced(job: Path, approved: list[dict] | None = None) -> tuple[list[dict], str]:
    """(candidates to redact, source). source: "decision" (a valid human decision),
    "default" (candidates without one: all redacted) or "none" (no candidates). Also
    redacted: what an older decision marked "redact" and `approved` (the candidates the
    approval record saw), so a later edit of the list cannot drop a redaction."""
    current = load(job) or []
    rec = valid_decision(job)
    if rec is not None:
        demo = set(rec.get("demo") or [])
        out = [c for c in current if c["id"] not in demo]
        source = "decision"
    else:
        out = list(current)
        source = "default" if current else "none"
    old = read_decision(job)
    keep = []
    if old is not None:
        red = set(old.get("redact") or [])
        keep += [c for c in old.get("candidates") or [] if c.get("id") in red]
    if rec is None:
        keep += approved or []
    seen = {_key(c) for c in out}
    for c in keep:
        if isinstance(c, dict) and not validate({"candidates": [c]}) and _key(c) not in seen:
            out.append(c)
            seen.add(_key(c))
            source = source if source != "none" else "default"
    return out, source


def _covers(r_box, c_box) -> bool:
    return (
        r_box[0] <= c_box[0] + BOX_TOL
        and r_box[1] <= c_box[1] + BOX_TOL
        and r_box[2] >= c_box[2] - BOX_TOL
        and r_box[3] >= c_box[3] - BOX_TOL
    )


def screen_blocks(cands: list[dict], start: float, end: float) -> list[dict]:
    """Redact blocks {t0, t1, box} (camera time) of the screen candidates that overlap the
    segment [start, end], clipped to it."""
    out = []
    for c in cands:
        if "asset" in c:
            continue
        for p in pieces(c):
            t0, t1 = max(p["t0"], start), min(p["t1"], end)
            if t1 - t0 > EPS:
                out.append({"t0": round(t0, 4), "t1": round(t1, 4), "box": list(p["box"])})
    return out


def asset_boxes(c: dict) -> list[list[float]]:
    """The redact boxes of a screenshot candidate (measured, or its own box)."""
    return [list(p["box"]) for p in pieces(c)]


def covered(block: dict, reds: list) -> bool:
    """Whether redact block `block` {t0, t1, box} lies inside one of `reds`."""
    return any(
        isinstance(r, dict)
        and _num(r.get("t0")) and _num(r.get("t1"))
        and r["t0"] <= block["t0"] + EPS and r["t1"] >= block["t1"] - EPS
        and _box(r.get("box")) and _covers(r["box"], block["box"])
        for r in reds
    )  # fmt: skip


def missing(cands: list[dict], data: dict, job: Path) -> list[dict]:
    """Candidates to redact that the raw EDL `data` does not cover: a non-face segment
    showing the screen during the candidate's time without a redact block over its box,
    or a screenshot_frame of its asset (any spelling of the path, or a copy of the file)
    without such a redact box. [{"id", "where", "need"}]: `need` is the block (screen) or
    box (screenshot) to add."""
    segs = data.get("segments") if isinstance(data, dict) else None
    ovs = data.get("overlays") if isinstance(data, dict) else None
    out = []
    for c in cands:
        if "asset" in c:
            for j, o in enumerate(ovs if isinstance(ovs, list) else []):
                props = o.get("props") if isinstance(o, dict) else None
                if not (o.get("scene") == "screenshot_frame" and isinstance(props, dict)):
                    continue
                if not same_asset(job, props.get("file"), c["asset"]):
                    continue
                boxes = props.get("redact") if isinstance(props.get("redact"), list) else []
                for need in asset_boxes(c):
                    if not any(_box(b) and _covers(b, need) for b in boxes):
                        out.append({"id": c["id"], "where": f"$.overlays[{j}].props.redact",
                                    "need": need})  # fmt: skip
            continue
        for i, s in enumerate(segs if isinstance(segs, list) else []):
            if not isinstance(s, dict) or s.get("layout", "face") == "face":
                continue
            a, b = s.get("in"), s.get("out")
            if not (_num(a) and _num(b)):
                continue
            reds = s.get("redact") if isinstance(s.get("redact"), list) else []
            for need in screen_blocks([c], a, b):
                if not covered(need, reds):
                    out.append({"id": c["id"], "where": f"$.segments[{i}].redact",
                                "need": need})  # fmt: skip
    return out


def summary(job: Path) -> dict | None:
    """For `job show`: candidates, whether a valid decision exists, ids to redact / demo."""
    try:
        cands = load(job)
    except RedactionError as e:
        return {"error": str(e)}
    if cands is None:
        return None
    rec = valid_decision(job)
    demo = set(rec.get("demo") or []) if rec else set()
    return {
        "candidates": len(cands),
        "decided": rec is not None,
        "method": rec.get("method") if rec else "default (all redacted)",
        "redact": [c["id"] for c in cands if c["id"] not in demo],
        "demo": sorted(demo),
    }
