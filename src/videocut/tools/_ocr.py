"""Local text recognition (macOS Vision, VNRecognizeTextRequest) for personal-data redaction.

Vision is part of macOS; it is called through the Objective-C runtime with ctypes, so there
is no extra download, no compiler and no Python package. Everything runs on this machine:
frames are handed over as pixels in memory, nothing is written or sent anywhere.

    recognize(rgb)          [{"text", "box", "conf"}] text lines of an [h, w, 3] uint8 image,
                            boxes [x0, y0, x1, y1] normalised to the image, top-left origin
    find(rgb, needle)       [{"text", "box", "conf"}] where `needle` (a candidate's text) is
                            shown: the matched part of each line (Vision's sub-range box),
                            tightened to the ink inside it (find_many: several needles,
                            one recognition pass)
    matches(line, needle)   the (start, end) character span of `needle` in `line`, tolerant
                            to OCR noise (case, spacing, a few wrong characters)

    pmap(fn, items)         fn over items on WORKERS threads (recognition releases the GIL)

`available()` is False off macOS (Linux CI): callers fail closed (verify) or say so.
"""

import ctypes
import ctypes.util
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import cache

import numpy as np

LANGUAGES = ("de-DE", "en-US")
INK_LEVEL = 40  # grey levels a text pixel differs from its line's background
MIN_CONF = 0.3
WORKERS = 4  # frames read at once (pmap): Vision runs outside the GIL; more do not help


class OcrError(RuntimeError):
    pass


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                ("w", ctypes.c_double), ("h", ctypes.c_double)]  # fmt: skip


class _Range(ctypes.Structure):
    _fields_ = [("loc", ctypes.c_ulong), ("len", ctypes.c_ulong)]


@cache
def _runtime():
    if sys.platform != "darwin":
        raise OcrError("text recognition needs macOS (Vision)")
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Vision.framework/Vision")
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
    except OSError as e:
        raise OcrError(f"Vision is not available: {e}") from None
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    cg.CGColorSpaceCreateDeviceRGB.restype = ctypes.c_void_p
    cg.CGDataProviderCreateWithData.restype = ctypes.c_void_p
    cg.CGDataProviderCreateWithData.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.c_size_t, ctypes.c_void_p]  # fmt: skip
    cg.CGImageCreate.restype = ctypes.c_void_p
    cg.CGImageCreate.argtypes = [ctypes.c_size_t] * 5 + [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_bool, ctypes.c_int,
    ]  # fmt: skip
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    if not objc.objc_getClass(b"VNRecognizeTextRequest"):
        raise OcrError("this macOS has no VNRecognizeTextRequest (needs macOS 10.15 or newer)")
    return objc, cg, cf


def pmap(fn, items: list) -> list:
    """fn over items, WORKERS at a time (frame reads + recognition), results in order."""
    if len(items) < 2:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(fn, items))


def available() -> bool:
    try:
        _runtime()
    except OcrError:
        return False
    return True


def _send(obj, sel: bytes, *args, restype=ctypes.c_void_p, argtypes=()):
    objc = _runtime()[0]
    fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
        ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    )
    return fn(obj, objc.sel_registerName(sel), *args)


def _cls(name: bytes):
    return _runtime()[0].objc_getClass(name)


def _nsstring(s: str):
    return _send(_cls(b"NSString"), b"stringWithUTF8String:", s.encode(),
                 argtypes=(ctypes.c_char_p,))  # fmt: skip


def _pystring(ns) -> str:
    raw = _send(ns, b"UTF8String", restype=ctypes.c_char_p)
    return raw.decode("utf-8", "replace") if raw else ""


def _norm_box(r: _Rect) -> list[float]:
    """Vision's normalised rect (bottom-left origin) -> [x0, y0, x1, y1] top-left origin."""
    x0, x1 = max(0.0, r.x), min(1.0, r.x + r.w)
    y0, y1 = max(0.0, 1.0 - (r.y + r.h)), min(1.0, 1.0 - r.y)
    return [x0, y0, x1, y1]


def _observations(rgb: np.ndarray, needles: list[str]):
    """Raw lines [(text, box, conf, {needle: sub_boxes})] of the image; sub_boxes are the
    boxes of the needle's matches in that line (Vision's boundingBoxForRange)."""
    _, cg, cf = _runtime()
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise OcrError("recognize() needs an [h, w, 3] uint8 image")
    h, w = rgb.shape[:2]
    rgbx = np.empty((h, w, 4), np.uint8)
    rgbx[..., :3], rgbx[..., 3] = rgb, 255
    buf = np.ascontiguousarray(rgbx)
    pool = _send(_send(_cls(b"NSAutoreleasePool"), b"alloc"), b"init")
    space = cg.CGColorSpaceCreateDeviceRGB()
    provider = cg.CGDataProviderCreateWithData(None, buf.ctypes.data, buf.nbytes, None)
    image = None
    out = []
    try:
        # 8 bits per component, 32 per pixel, kCGImageAlphaNoneSkipLast (5)
        image = cg.CGImageCreate(w, h, 8, 32, w * 4, space, 5, provider, None, False, 0)
        if not image:
            raise OcrError("cannot hand the frame to Vision")
        handler = _send(_send(_cls(b"VNImageRequestHandler"), b"alloc"),
                        b"initWithCGImage:options:", image,
                        _send(_cls(b"NSDictionary"), b"dictionary"),
                        argtypes=(ctypes.c_void_p, ctypes.c_void_p))  # fmt: skip
        req = _send(_send(_cls(b"VNRecognizeTextRequest"), b"alloc"), b"init")
        _send(req, b"setRecognitionLevel:", 0, restype=None, argtypes=(ctypes.c_long,))
        _send(req, b"setUsesLanguageCorrection:", False, restype=None,
              argtypes=(ctypes.c_bool,))  # fmt: skip
        langs = _send(_cls(b"NSMutableArray"), b"array")
        for lang in LANGUAGES:
            _send(langs, b"addObject:", _nsstring(lang), restype=None,
                  argtypes=(ctypes.c_void_p,))  # fmt: skip
        _send(req, b"setRecognitionLanguages:", langs, restype=None,
              argtypes=(ctypes.c_void_p,))  # fmt: skip
        reqs = _send(_cls(b"NSArray"), b"arrayWithObject:", req, argtypes=(ctypes.c_void_p,))
        ok = _send(handler, b"performRequests:error:", reqs, None, restype=ctypes.c_bool,
                   argtypes=(ctypes.c_void_p, ctypes.c_void_p))  # fmt: skip
        if not ok:
            _send(req, b"release", restype=None)
            _send(handler, b"release", restype=None)
            raise OcrError("Vision could not read the frame")
        res = _send(req, b"results")
        n = _send(res, b"count", restype=ctypes.c_ulong) if res else 0
        for i in range(n):
            obs = _send(res, b"objectAtIndex:", i, argtypes=(ctypes.c_ulong,))
            top = _send(obs, b"topCandidates:", 1, argtypes=(ctypes.c_ulong,))
            if not top or not _send(top, b"count", restype=ctypes.c_ulong):
                continue
            cand = _send(top, b"objectAtIndex:", 0, argtypes=(ctypes.c_ulong,))
            nstext = _send(cand, b"string")
            text = _pystring(nstext)
            conf = float(_send(cand, b"confidence", restype=ctypes.c_float))
            box = _norm_box(_send(obs, b"boundingBox", restype=_Rect))
            subs = {}
            for needle in needles:
                found = subs.setdefault(needle, [])
                for a, b in matches(text, needle):
                    # NSString ranges count UTF-16 units
                    loc = len(text[:a].encode("utf-16-le")) // 2
                    ln = len(text[a:b].encode("utf-16-le")) // 2
                    sub = _send(cand, b"boundingBoxForRange:error:", _Range(loc, ln), None,
                                argtypes=(_Range, ctypes.c_void_p))  # fmt: skip
                    sb = _norm_box(_send(sub, b"boundingBox", restype=_Rect)) if sub else None
                    found.append(sb if sb and sb[2] > sb[0] and sb[3] > sb[1] else box)
            out.append((text, box, conf, subs))
        _send(req, b"release", restype=None)
        _send(handler, b"release", restype=None)
    finally:
        if image:
            cf.CFRelease(image)
        cf.CFRelease(provider)
        cf.CFRelease(space)
        _send(pool, b"drain", restype=None)
    del buf
    return out


def recognize(rgb: np.ndarray) -> list[dict]:
    """Text lines of the image: [{"text", "box", "conf"}], box normalised, top-left origin."""
    return [
        {"text": t, "box": [round(v, 5) for v in b], "conf": round(c, 3)}
        for t, b, c, _ in _observations(rgb, [])
        if c >= MIN_CONF and t.strip()
    ]


def find(rgb: np.ndarray, needle: str) -> list[dict]:
    """Every place the image shows `needle`: [{"text" (the whole line), "box" (the matched
    part, tightened to its ink), "conf"}], normalised, top-left origin."""
    return find_many(rgb, [needle])[needle]


def find_many(rgb: np.ndarray, needles: list[str]) -> dict[str, list[dict]]:
    """find() for several needles with one recognition pass: {needle: [...]}."""
    out = {n: [] for n in needles}
    for text, _, conf, subs in _observations(rgb, list(needles)):
        for needle, boxes in subs.items():
            for sb in boxes:
                out[needle].append({"text": text, "box": [round(v, 5) for v in ink_box(rgb, sb)],
                                    "conf": round(conf, 3)})  # fmt: skip
    return out


def ink_box(rgb: np.ndarray, box: list[float]) -> list[float]:
    """`box` (normalised) shrunk to the text pixels inside it: the pixels that differ from
    the box's dominant (background) grey by more than INK_LEVEL. Vision's line boxes carry
    line spacing; the ink is where the text really is. Unchanged when no ink is found."""
    h, w = rgb.shape[:2]
    x0, y0 = int(box[0] * w), int(box[1] * h)
    x1, y1 = max(x0 + 1, int(np.ceil(box[2] * w))), max(y0 + 1, int(np.ceil(box[3] * h)))
    patch = rgb[y0:y1, x0:x1].astype(float) @ np.array([0.299, 0.587, 0.114])
    if patch.size == 0:
        return box
    hist = np.bincount(np.clip(patch, 0, 255).astype(int).ravel(), minlength=256)
    bg = float(np.argmax(hist))
    ink = np.abs(patch - bg) > INK_LEVEL
    rows, cols = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
    if not len(rows) or not len(cols):
        return box
    return [(x0 + cols[0]) / w, (y0 + rows[0]) / h, (x0 + cols[-1] + 1) / w,
            (y0 + rows[-1] + 1) / h]  # fmt: skip


# --- matching -----------------------------------------------------------------------------

_KEEP = re.compile(r"[\w@.+\-]", re.UNICODE)


def _fold(s: str) -> tuple[str, list[int]]:
    """`s` casefolded, without spaces and most punctuation, and the index in `s` of every
    kept character (to map a match back)."""
    chars, idx = [], []
    for i, ch in enumerate(s):
        for c in unicodedata.normalize("NFKC", ch).casefold():
            if _KEEP.match(c):
                chars.append(c)
                idx.append(i)
    return "".join(chars), idx


def _allowed(n: int) -> int:
    """Wrong characters tolerated in a needle of n kept characters."""
    return 0 if n < 5 else 1 if n < 12 else 2 if n < 24 else 3


def matches(line: str, needle: str) -> list[tuple[int, int]]:
    """Character spans [(start, end)] of `needle` in `line`: an approximate substring match
    (Sellers) on the folded strings, at most _allowed(len) edits, non-overlapping."""
    hay, idx = _fold(line)
    pat, _ = _fold(needle)
    m, n = len(pat), len(hay)
    if not m or not n:
        return []
    k = _allowed(m)
    if n < m - k:
        return []
    # prev[j] = edit distance of pat[:i] matching a substring of hay ending at j
    prev = [0] * (n + 1)
    start = list(range(n + 1))
    for i in range(1, m + 1):
        cur, cs = [i] + [0] * n, [0] * (n + 1)
        for j in range(1, n + 1):
            sub = prev[j - 1] + (pat[i - 1] != hay[j - 1])
            dele, ins = prev[j] + 1, cur[j - 1] + 1
            best = min(sub, dele, ins)
            cur[j] = best
            cs[j] = start[j - 1] if best == sub else start[j] if best == dele else cs[j - 1]
        prev, start = cur, cs
    spans, j = [], n
    hits = sorted((prev[j], -j) for j in range(1, n + 1) if prev[j] <= k)
    used = []
    for _, neg in hits:
        end = -neg
        s = start[end]
        if s >= end or any(s < b and end > a for a, b in used):
            continue
        used.append((s, end))
    for s, end in sorted(used):
        spans.append((idx[s], idx[end - 1] + 1))
    return spans
