"""Local PDF rasterisation (macOS CoreGraphics) for uploaded slides.

CoreGraphics is part of macOS (the same PDF engine Preview uses); it is called through
ctypes, so there is no download, no compiler and no Python package. Nothing leaves the
machine. An uploaded PDF is untrusted input: `rasterise()` parses it in a child process
(`python -I -m videocut.tools._pdf`) with a time limit, so a malformed file can neither
hang nor crash the caller, and the child only writes PNG files into the folder it is given.

    page_count(pdf)                     pages of a PDF (child process)
    rasterise(pdf, out_dir, long_edge)  page_01.png ... at `long_edge` px on the long side,
                                        white background, the page's crop box and rotation

`available()` is False off macOS (Linux CI): callers say so.
"""

import ctypes
import json
import subprocess
import sys
from functools import cache
from pathlib import Path

TIMEOUT_S = 120  # one whole document
MAX_PAGES = 60
MAX_PIXELS = 4096 * 4096


class PdfError(RuntimeError):
    pass


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                ("w", ctypes.c_double), ("h", ctypes.c_double)]  # fmt: skip


class _Affine(ctypes.Structure):
    _fields_ = [(k, ctypes.c_double) for k in ("a", "b", "c", "d", "tx", "ty")]


CROP_BOX = 1  # kCGPDFCropBox
NONE_SKIP_LAST = 5  # kCGImageAlphaNoneSkipLast
UTF8 = 0x08000100  # kCFStringEncodingUTF8


@cache
def _libs():
    if sys.platform != "darwin":
        raise PdfError("reading PDF slides needs macOS (CoreGraphics)")
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        io = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ImageIO.framework/ImageIO")
    except OSError as e:
        raise PdfError(f"CoreGraphics is not available: {e}") from None
    vp, sz, db = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_double
    sig = {
        (cf, "CFURLCreateFromFileSystemRepresentation"): (
            vp,
            [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool],
        ),  # noqa: E501
        (cf, "CFStringCreateWithCString"): (vp, [vp, ctypes.c_char_p, ctypes.c_uint32]),
        (cf, "CFRelease"): (None, [vp]),
        (cg, "CGPDFDocumentCreateWithURL"): (vp, [vp]),
        (cg, "CGPDFDocumentRelease"): (None, [vp]),
        (cg, "CGPDFDocumentIsEncrypted"): (ctypes.c_bool, [vp]),
        (cg, "CGPDFDocumentIsUnlocked"): (ctypes.c_bool, [vp]),
        (cg, "CGPDFDocumentGetNumberOfPages"): (sz, [vp]),
        (cg, "CGPDFDocumentGetPage"): (vp, [vp, sz]),
        (cg, "CGPDFPageGetBoxRect"): (_Rect, [vp, ctypes.c_int]),
        (cg, "CGPDFPageGetRotationAngle"): (ctypes.c_int, [vp]),
        (cg, "CGPDFPageGetDrawingTransform"): (
            _Affine,
            [vp, ctypes.c_int, _Rect, ctypes.c_int, ctypes.c_bool],
        ),  # noqa: E501
        (cg, "CGColorSpaceCreateDeviceRGB"): (vp, []),
        (cg, "CGColorSpaceRelease"): (None, [vp]),
        (cg, "CGBitmapContextCreate"): (vp, [vp, sz, sz, sz, sz, vp, ctypes.c_uint32]),
        (cg, "CGContextRelease"): (None, [vp]),
        (cg, "CGContextSetRGBFillColor"): (None, [vp, db, db, db, db]),
        (cg, "CGContextFillRect"): (None, [vp, _Rect]),
        (cg, "CGContextScaleCTM"): (None, [vp, db, db]),
        (cg, "CGContextConcatCTM"): (None, [vp, _Affine]),
        (cg, "CGContextSetInterpolationQuality"): (None, [vp, ctypes.c_int]),
        (cg, "CGContextDrawPDFPage"): (None, [vp, vp]),
        (cg, "CGBitmapContextCreateImage"): (vp, [vp]),
        (cg, "CGImageRelease"): (None, [vp]),
        (io, "CGImageDestinationCreateWithURL"): (vp, [vp, vp, sz, vp]),
        (io, "CGImageDestinationAddImage"): (None, [vp, vp, vp]),
        (io, "CGImageDestinationFinalize"): (ctypes.c_bool, [vp]),
    }
    for (lib, name), (res, args) in sig.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = res, args
    return cg, cf, io


def available() -> bool:
    try:
        _libs()
    except PdfError:
        return False
    return True


def _url(path: Path):
    _, cf, _ = _libs()
    raw = str(path).encode()
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    if not url:
        raise PdfError(f"{path.name}: invalid path")
    return url


def _open(pdf: Path):
    cg, cf, _ = _libs()
    url = _url(pdf)
    try:
        doc = cg.CGPDFDocumentCreateWithURL(url)
    finally:
        cf.CFRelease(url)
    if not doc:
        raise PdfError(f"{pdf.name}: not a readable PDF")
    if cg.CGPDFDocumentIsEncrypted(doc) and not cg.CGPDFDocumentIsUnlocked(doc):
        cg.CGPDFDocumentRelease(doc)
        raise PdfError(f"{pdf.name}: the PDF is password protected; export it without one")
    return doc


def _size(page, long_edge: int) -> tuple[int, int, float, _Rect]:
    """Output size (w, h) of a page at `long_edge`, the scale and its rotated box."""
    cg = _libs()[0]
    box = cg.CGPDFPageGetBoxRect(page, CROP_BOX)
    w, h = abs(box.w), abs(box.h)
    if cg.CGPDFPageGetRotationAngle(page) % 180:
        w, h = h, w
    if w < 1 or h < 1:
        raise PdfError("a page has no size")
    scale = long_edge / max(w, h)
    ow, oh = max(2, round(w * scale)), max(2, round(h * scale))
    if ow * oh > MAX_PIXELS:
        raise PdfError(f"a page is too large ({ow}x{oh} px)")
    return ow, oh, scale, _Rect(0, 0, w, h)


def _render_page(page, out: Path, long_edge: int) -> list[int]:
    cg, cf, io = _libs()
    ow, oh, scale, rect = _size(page, long_edge)
    cs = cg.CGColorSpaceCreateDeviceRGB()
    ctx = cg.CGBitmapContextCreate(None, ow, oh, 8, 0, cs, NONE_SKIP_LAST)
    cg.CGColorSpaceRelease(cs)
    if not ctx:
        raise PdfError("cannot allocate the page bitmap")
    try:
        cg.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)  # slides without a background
        cg.CGContextFillRect(ctx, _Rect(0, 0, ow, oh))
        cg.CGContextSetInterpolationQuality(ctx, 3)  # kCGInterpolationHigh
        cg.CGContextScaleCTM(ctx, scale, scale)
        # rotation and crop box into the unscaled rect (the transform never scales up)
        cg.CGContextConcatCTM(ctx, cg.CGPDFPageGetDrawingTransform(page, CROP_BOX, rect, 0, True))
        cg.CGContextDrawPDFPage(ctx, page)
        img = cg.CGBitmapContextCreateImage(ctx)
    finally:
        cg.CGContextRelease(ctx)
    if not img:
        raise PdfError("cannot draw the page")
    url, kind = _url(out), cf.CFStringCreateWithCString(None, b"public.png", UTF8)
    try:
        dest = io.CGImageDestinationCreateWithURL(url, kind, 1, None)
        if not dest:
            raise PdfError(f"cannot write {out.name}")
        io.CGImageDestinationAddImage(dest, img, None)
        ok = io.CGImageDestinationFinalize(dest)
        cf.CFRelease(dest)
    finally:
        cf.CFRelease(url)
        cf.CFRelease(kind)
        cg.CGImageRelease(img)
    if not ok:
        raise PdfError(f"cannot write {out.name}")
    return [ow, oh]


def _child(cmd: str, pdf: Path, out_dir: Path | None, long_edge: int) -> dict:
    """What the child process does (`python -m videocut.tools._pdf`)."""
    cg = _libs()[0]
    doc = _open(pdf)
    try:
        n = int(cg.CGPDFDocumentGetNumberOfPages(doc))
        if cmd == "count":
            return {"pages": n}
        if n < 1:
            raise PdfError(f"{pdf.name}: the PDF has no pages")
        if n > MAX_PAGES:
            raise PdfError(f"{pdf.name}: {n} pages; at most {MAX_PAGES}")
        sizes = []
        for k in range(1, n + 1):
            page = cg.CGPDFDocumentGetPage(doc, k)
            if not page:
                raise PdfError(f"{pdf.name}: page {k} cannot be read")
            sizes.append(_render_page(page, out_dir / f"page_{k:02d}.png", long_edge))
        return {"pages": n, "sizes": sizes}
    finally:
        cg.CGPDFDocumentRelease(doc)


def _run(cmd: str, pdf: Path, out_dir: Path | None = None, long_edge: int = 1920) -> dict:
    if not available():
        raise PdfError("reading PDF slides needs macOS (CoreGraphics); export the pages as images")
    argv = [sys.executable, "-I", "-m", "videocut.tools._pdf", cmd, str(pdf.resolve())]
    argv += [str(out_dir.resolve()) if out_dir else "-", str(long_edge)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise PdfError(f"{pdf.name}: reading the PDF took longer than {TIMEOUT_S} s") from None
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        res = {"error": (r.stderr.strip().splitlines() or [f"exit code {r.returncode}"])[-1]}
    if r.returncode != 0 or "error" in res:
        raise PdfError(res.get("error") or f"{pdf.name}: cannot be read")
    return res


def page_count(pdf: Path) -> int:
    return int(_run("count", pdf)["pages"])


def rasterise(pdf: Path, out_dir: Path, long_edge: int = 1920) -> list[Path]:
    """Every page of `pdf` as out_dir/page_NN.png (NN from 01), `long_edge` px long side."""
    res = _run("render", pdf, out_dir, long_edge)
    return [out_dir / f"page_{k:02d}.png" for k in range(1, res["pages"] + 1)]


def main(argv: list[str]) -> int:
    cmd, pdf, out, long_edge = argv
    try:
        res = _child(cmd, Path(pdf), None if out == "-" else Path(out), int(long_edge))
    except PdfError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
