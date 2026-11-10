"""The motion checks of `videocut tool verify` (tools/motion.py spans).

Per span of an approved motion moment:

    motion_dropped          the moment could not become a span (words cut, on a slide, no
                            room above the face); the reason is listed
    motion_missing          no scene module or no clip (run the overlays skill /
                            `videocut tool motion render`)
    motion_untrusted        clip, events or report differ from what `motion render` recorded
                            (outside the job folder); never edit them, render again
    motion_stale            the clip was made from another spec, scene or renderer
    motion_not_composited   the reel's render.json does not list the span
    motion_static           a stretch longer than STATIC_MAX_S without visible change inside
                            the span (ffmpeg freezedetect at STATIC_NOISE over the clip laid
                            on grey); the rule is an event at least every 0.5 s
    motion_repeats_caption  the band's text (read locally from the clip, macOS Vision) shares
                            REPEAT_MIN_TOKENS or more words, and REPEAT_SHARE of its words,
                            with the captions on screen around it: the band must SHOW what is
                            meant (number, list, picture, chart), never repeat what is said
    motion_invented         band text read locally (OCR) holds words or numbers that are neither
                            in the transcript around the span (± INVENT_WINDOW_S), nor in the
                            approved moment (`shows`, neutral `labels`), nor in the motion wish,
                            in INVENT_MIN_SAMPLES or more samples: the band must never invent a
                            rating, a place, a claim or a button text nobody said
    overlay_on_face         what the band really drew (alpha bounding box per sample, output
                            px) covers the face below the band limit: the eye line minus
                            motion.EYE_CLEAR of the face height (> FACE_OVERLAP_MAX of the face
                            box; hair and the upper forehead may be covered)
    motion_caption_overlap  a caption box and the band's drawn box on screen together share
                            more than BLOCK_OVERLAP_MAX of the smaller one

Samples: SAMPLE_FPS frames per second of each clip, decoded as RGBA. Without text
recognition (not macOS) the repeat check is reported as unchecked (a warning), not passed.
"""

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from . import _ocr
from . import motion as motion_mod
from ._geometry import overlap
from .common import binary

SAMPLE_FPS = 4
ALPHA_MIN = 24  # a pixel with more alpha than this counts as drawn
STATIC_NOISE = "-50dB"
STATIC_MAX_S = 0.6
EDGE_S = 0.3  # the first and last this-many seconds of a span may hold still (enter, leave)
REPEAT_WINDOW_S = 1.5
REPEAT_MIN_TOKENS = 3
REPEAT_SHARE = 0.6
MIN_TOKEN = 3  # shorter words (articles, "3", "%") are not counted as repeated text
FACE_OVERLAP_MAX = 0.02
FOREHEAD = motion_mod.BAND_FACE_SHARE  # eye line - EYE_CLEAR (as the band itself)
BLOCK_OVERLAP_MAX = 0.1
INVENT_WINDOW_S = 3.0
INVENT_MIN_SAMPLES = 2  # one odd OCR read is noise; the same invented word twice is not
INVENT_PREFIX = 5  # "Schritte" matches "Schritten"; OCR and inflection tolerance


def tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"\w+", text.casefold()) if len(t) >= MIN_TOKEN and not t.isdigit()
    }


def said_tokens(text: str) -> set[str]:
    """Words of 3+ letters and numbers of 2+ digits (a rating "4,8", a year, a price)."""
    return {
        t for t in re.findall(r"\w+", text.casefold())
        if (t.isdigit() and len(t) >= 2) or (not t.isdigit() and len(t) >= MIN_TOKEN)
    }  # fmt: skip


def invented(band: set[str], allowed: set[str]) -> set[str]:
    """Band tokens without an allowed token equal to it or sharing INVENT_PREFIX letters."""
    pre = {a[:INVENT_PREFIX] for a in allowed if len(a) >= INVENT_PREFIX and not a.isdigit()}
    return {
        b for b in band
        if b not in allowed and not (len(b) >= INVENT_PREFIX and b[:INVENT_PREFIX] in pre)
    }  # fmt: skip


def _frames(clip: Path, size: tuple[int, int]) -> np.ndarray:
    w, h = size
    r = subprocess.run(
        [binary("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(clip),
         "-vf", f"fps={SAMPLE_FPS}", "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
        capture_output=True, check=False,
    )  # fmt: skip
    if r.returncode != 0:
        return np.zeros((0, h, w, 4), np.uint8)
    n = len(r.stdout) // (w * h * 4)
    return np.frombuffer(r.stdout[: n * w * h * 4], np.uint8).reshape(n, h, w, 4)


def static_stretches(clip: Path, size: tuple[int, int], fps, duration: float) -> list[dict]:
    """Freezes longer than STATIC_MAX_S inside [EDGE_S, duration - EDGE_S] (clip seconds)."""
    w, h = size
    g = (f"color=c=gray:s={w}x{h}:r={fps}[bg];[bg][0:v]overlay=shortest=1,"
         f"freezedetect=n={STATIC_NOISE}:d={STATIC_MAX_S}")  # fmt: skip
    r = subprocess.run(
        [binary("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(clip), "-filter_complex", g,
         "-f", "null", "-"], capture_output=True, text=True, check=False,
    )  # fmt: skip
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", r.stderr)]
    out = []
    for i, a in enumerate(starts):
        b = ends[i] if i < len(ends) else duration
        a2, b2 = max(a, EDGE_S), min(b, duration - EDGE_S)
        if b2 - a2 > STATIC_MAX_S:
            out.append({"t0": round(a, 2), "t1": round(b, 2), "seconds": round(b - a, 2)})
    return out


def _bbox(alpha: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(alpha > ALPHA_MIN)
    if not len(xs):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def _caption_words(captions: list[dict], t0: float, t1: float) -> set[str]:
    return {
        t
        for c in captions
        for w in c.get("words", []) or []
        if isinstance(w, dict) and isinstance(w.get("t0"), int | float) and t0 <= w["t0"] <= t1
        for t in tokens(str(w.get("w", "")))
    }


def _face_core(face: list[dict], t: float) -> list[float] | None:
    best = min(face, key=lambda f: abs(f["t"] - t), default=None)
    if not best or abs(best["t"] - t) > 0.25:
        return None
    x0, y0, x1, y1 = best["box"]
    return [x0, y0 + FOREHEAD * (y1 - y0), x1, y1]


def check(job: Path, render: dict, captions: list[dict], boxes: list[dict] | None,
          face: list[dict]) -> dict:  # fmt: skip
    """{"spans": [per span doc], "flagged": [...], "warnings": [...]}."""
    from . import _job_motion
    from .common import read_job

    if _job_motion.stored(read_job(job))["mode"] == "off":
        return {"spans": [], "flagged": [], "warnings": []}
    ctx = motion_mod._edl_ctx(job)
    spans, dropped, meta = motion_mod.build_spans(job, ctx=ctx)
    # the real face track (as the band was built from), not overlays.json "face": during a
    # span that one is cut at CAPTION_FACE_SHARE for the captions, which would put the eye
    # line too high (a false overlay_on_face: 3.1 % in the e2e, drawn box above the band)
    from ._geometry import face_track

    _, rplan, src_size, face_doc = ctx
    face = face_track(rplan, face_doc, src_size) or face
    flagged = [{"id": d["id"], "flags": ["motion_dropped"], "why": d["why"]} for d in dropped]
    warnings: list[str] = []
    if not spans:
        return {"spans": [], "flagged": flagged, "warnings": warnings}
    tokens_of = motion_mod.brand_tokens(job)
    rec = motion_mod.read_record(job)
    shown = {m.get("id") for m in render.get("motion") or [] if isinstance(m, dict)}
    caps = [b for b in boxes or [] if b.get("scene") == "caption"]
    wish = _job_motion.stored(read_job(job)).get("wish") or ""
    all_words = [w for c in captions for w in c.get("words", []) or [] if isinstance(w, dict)]
    ocr_ok = _ocr.available()
    if not ocr_ok:
        warnings.append("motion: no local text recognition here; band text vs captions unchecked")
    docs = []
    for s in spans:
        doc = {"id": s["id"], "t0": s["t0"], "t1": s["t1"], "region": s["region"]}
        flags: list[str] = []
        st = motion_mod.status(job, s, meta, tokens_of, rec)
        doc["status"] = st
        if st in ("no_scene", "missing"):
            flags.append("motion_missing")
        elif st == "untrusted":
            flags.append("motion_untrusted")
        elif st == "stale":
            flags.append("motion_stale")
        if st != "ok":
            docs.append(doc | {"flags": flags})
            continue
        if s["id"] not in shown:
            flags.append("motion_not_composited")
        d = job / motion_mod.DIR / s["id"]
        rep = json.loads((d / "clip.json").read_text())
        size = tuple(rep["out_size"])
        px, py = rep["place_at"]
        dur = s["t1"] - s["t0"]
        static = static_stretches(d / "clip.mov", size, meta["fps"], dur)
        if static:
            flags.append("motion_static")
            doc["static"] = static
        frames = _frames(d / "clip.mov", size)
        worst_face, repeats, cap_hits, drawn = 0.0, [], [], None
        allowed = said_tokens(" ".join([s.get("shows") or "", *s.get("labels", []), wish]))
        allowed |= said_tokens(" ".join(str(w.get("w", "")) for w in s.get("words", [])))
        allowed |= said_tokens(" ".join(
            str(w.get("w", "")) for w in all_words
            if isinstance(w.get("t0"), int | float)
            and s["t0"] - INVENT_WINDOW_S <= w["t0"] <= s["t1"] + INVENT_WINDOW_S
        ))  # fmt: skip
        new_words: dict[str, int] = {}
        for i, fr in enumerate(frames):
            tc = i / SAMPLE_FPS
            t = s["t0"] + tc
            bb = _bbox(fr[:, :, 3])
            if bb is None:
                continue
            box = [bb[0] + px, bb[1] + py, bb[2] + px, bb[3] + py]
            if drawn is None:
                drawn = list(box)
            else:
                lo, hi = np.minimum(drawn[:2], box[:2]), np.maximum(drawn[2:], box[2:])
                drawn = [*map(int, lo), *map(int, hi)]
            core = _face_core(face, t)
            if core:
                full = (core[2] - core[0]) * (core[3] - core[1]) / (1 - FOREHEAD)
                if full > 0:
                    worst_face = max(worst_face, overlap(box, core) / full)
            for c in caps:
                if c["t0"] <= t <= c["t1"]:
                    a = min(_area(box), _area(c["box"]))
                    if a > 0 and overlap(box, c["box"]) > BLOCK_OVERLAP_MAX * a:
                        cap_hits.append(round(t, 2))
            if ocr_ok and i % 2 == 0:  # text twice per second
                rgb = _on_grey(fr)
                text = " ".join(x["text"] for x in _ocr.recognize(rgb))
                band = tokens(text)
                for x in invented(said_tokens(text), allowed):
                    new_words[x] = new_words.get(x, 0) + 1
                if len(band) >= REPEAT_MIN_TOKENS:
                    said = _caption_words(captions, t - REPEAT_WINDOW_S, t + REPEAT_WINDOW_S)
                    same = band & said
                    if len(same) >= REPEAT_MIN_TOKENS and len(same) / len(band) >= REPEAT_SHARE:
                        repeats.append({"t": round(t, 2), "words": sorted(same)})
        made_up = sorted(w for w, n in new_words.items() if n >= INVENT_MIN_SAMPLES)
        if made_up:
            flags.append("motion_invented")
            doc["invented"] = made_up[:12]
        doc["drawn"] = drawn
        doc["face_overlap"] = round(worst_face, 3)
        if worst_face > FACE_OVERLAP_MAX:
            flags.append("overlay_on_face")
        if cap_hits:
            flags.append("motion_caption_overlap")
            doc["caption_overlap_at"] = cap_hits[:10]
        if repeats:
            flags.append("motion_repeats_caption")
            doc["repeats"] = repeats[:5]
        try:
            ev = json.loads((d / "events.json").read_text())
            doc["events"] = len(ev) if isinstance(ev, list) else 0
        except (OSError, ValueError):
            doc["events"] = 0
        docs.append(doc | {"flags": flags})
    flagged += [
        {k: d[k] for k in ("id", "t0", "t1", "static", "repeats", "caption_overlap_at",
                           "face_overlap", "invented") if k in d} | {"flags": d["flags"]}
        for d in docs if d["flags"]
    ]  # fmt: skip
    return {"spans": docs, "flagged": flagged, "warnings": warnings}


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _on_grey(rgba: np.ndarray) -> np.ndarray:
    a = rgba[:, :, 3:4].astype(np.float32) / 255
    return (rgba[:, :, :3] * a + 128 * (1 - a)).astype(np.uint8)
