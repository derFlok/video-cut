"""/api/brands: list, read, edit (validated like `videocut tool brand check`), create from
the neutral example, upload a logo or font, and the preview sheet (rendered once per
brand state and format, then cached)."""

import json
import re
import shutil
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import brand
from .. import run as pipeline
from ..tools import _format
from ..tools.brand import sheet_name
from ..tools.common import write_json
from .uploads import UploadError, read_form

router = APIRouter(prefix="/api/brands")
_sheet_lock = threading.Lock()  # one renderer at a time
MAX_BRAND_FILE = 20 * 1024**2
SLUG_RE = re.compile(r"[^a-z0-9]+")
# Magic bytes of the accepted brand files (the extension alone is not trusted).
MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".woff2": (b"wOF2",),
    ".ttf": (b"\x00\x01\x00\x00", b"true"),
    ".otf": (b"OTTO",),
}


def _dir(brand_id: str) -> Path:
    if not brand.BRAND_ID_RE.fullmatch(brand_id) or brand_id not in brand.list_ids():
        raise HTTPException(404, f"no brand {brand_id!r}")
    return brand.brand_dir(brand_id)


def _editable(brand_id: str) -> Path:
    d = _dir(brand_id)
    if brand_id == brand.EXAMPLE_ID:
        raise HTTPException(409, "the bundled example brand is read-only; create a new brand")
    return d


def _read(d: Path) -> dict:
    try:
        data = json.loads((d / brand.BRAND_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _files(d: Path) -> list[str]:
    return sorted(
        str(p.relative_to(d))
        for p in d.rglob("*")
        if p.is_file() and not p.name.startswith(("preview", ".")) and p.name != brand.BRAND_FILE
    )


@router.get("")
def list_brands() -> list[dict]:
    out = []
    for i in brand.list_ids():
        name = _read(brand.brand_dir(i)).get("name")
        out.append(
            {
                "id": i,
                "name": name if isinstance(name, str) else i,
                "preview_url": f"/api/brands/{i}/sheet",
            }
        )
    return out


@router.get("/{brand_id}")
def get_brand(brand_id: str) -> dict:
    d = _dir(brand_id)
    _, errors, warnings = brand.inspect(brand_id)
    return {
        **_read(d),
        "files": _files(d),
        "id": brand_id,
        "editable": brand_id != brand.EXAMPLE_ID,
        "errors": errors,
        "warnings": warnings,
    }


@router.put("/{brand_id}")
async def put_brand(brand_id: str, request: Request) -> dict:
    d = _editable(brand_id)
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "body must be a JSON object (brand.json)")
    for k in ("files", "id", "editable", "errors", "warnings"):  # what GET adds
        data.pop(k, None)
    try:
        _, errors, warnings = brand._validate(json.loads(json.dumps(data)), d)
    except (TypeError, ValueError, AttributeError) as e:
        errors, warnings = [f"$: {e}"], []
    if errors:
        raise HTTPException(422, {"errors": errors, "warnings": warnings})
    write_json(d / brand.BRAND_FILE, data)
    _, _, warnings = brand.inspect(brand_id)
    return {"ok": True, "id": brand_id, "warnings": warnings}


@router.post("")
async def new_brand(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    name = str(body.get("name") or "").strip()[:80] or "New brand"
    brand_id = str(body.get("id") or SLUG_RE.sub("-", name.lower()).strip("-") or "brand")
    if not brand.BRAND_ID_RE.fullmatch(brand_id) or brand_id == brand.EXAMPLE_ID:
        raise HTTPException(400, f"invalid brand id {brand_id!r}")
    d = brand.brands_root() / brand_id
    if d.exists():
        raise HTTPException(409, f"brand {brand_id} already exists")
    data = _read(brand.BUNDLED_ROOT / brand.EXAMPLE_ID)
    data.pop("$comment", None)
    data["name"] = name
    d.mkdir(parents=True)
    write_json(d / brand.BRAND_FILE, data)
    return {"id": brand_id}


def _magic_ok(path: Path) -> bool:
    head = path.read_bytes()[:512] if path.stat().st_size else b""
    ext = path.suffix.lower()
    if ext == ".svg":
        text = head.decode("utf-8", "replace").lstrip("﻿ \t\r\n").lower()
        return text.startswith("<") and "<svg" in path.read_text(errors="replace")[:4096].lower()
    return any(head.startswith(m) for m in MAGIC.get(ext, ()))


@router.post("/{brand_id}/files")
async def upload_brand_file(brand_id: str, request: Request) -> dict:
    """Multipart field `logo` (.svg/.png) or `font` (.woff2/.ttf/.otf); saved as
    logo/<name> or fonts/<name>, the path the brand.json tokens then refer to."""
    d = _editable(brand_id)
    try:
        form = await read_form(
            request,
            {"logo": set(brand.LOGO_EXT), "font": set(brand.FONT_EXT)},
            set(),
            max_file_bytes=MAX_BRAND_FILE,
        )
    except UploadError as e:
        raise HTTPException(e.status, str(e)) from None
    try:
        got = [(k, p) for k, ps in form.files.items() for p in ps]
        if len(got) != 1:
            raise HTTPException(400, "send exactly one file (field logo or font)")
        kind, src = got[0]
        if not _magic_ok(src):
            raise HTTPException(415, f"{src.name} is not a valid {src.suffix} file")
        sub = "logo" if kind == "logo" else "fonts"
        (d / sub).mkdir(exist_ok=True)
        dst = d / sub / src.name
        shutil.move(src, dst)
    finally:
        shutil.rmtree(form.folder, ignore_errors=True)
    return {"path": f"{sub}/{dst.name}", "files": _files(d)}


@router.get("/{brand_id}/sheet")
def brand_sheet(brand_id: str, format: str = _format.PORTRAIT) -> FileResponse:
    d = _dir(brand_id)
    try:
        fmt = _format.parse_format(format)
    except _format.FormatError as e:
        raise HTTPException(400, str(e)) from None
    out = brand.brands_root() / d.name / sheet_name(fmt)
    newest = max(
        (
            p.stat().st_mtime
            for p in d.rglob("*")
            if p.is_file() and not (p.parent == d and p.name.startswith("preview"))
        ),
        default=0,
    )  # the brand's files, not its cached sheets
    with _sheet_lock:
        if not (out.is_file() and out.stat().st_mtime >= newest):
            from ..cli import main

            with pipeline.capture_stdout() as buf:
                code = pipeline._main(main, ["tool", "brand", "sheet", brand_id, "--format", fmt])
            if code != 0 or not out.is_file():
                lines = buf.getvalue().strip().splitlines()
                try:
                    err = json.loads(lines[-1]).get("error") if lines else None
                except ValueError:
                    err = None
                raise HTTPException(500, f"preview sheet failed: {err or code}")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
