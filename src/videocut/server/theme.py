"""The app's look: the default brand setting, the theme it gives the UI (videocut/theme.py),
the live preview of an edited brand, and the brand's fonts and logos for the app shell.

GET /api/theme and PUT /api/settings/brand answer with the same view:
{brand_id, name, css, logo: {light, dark}, notes, settings: {default_brand,
theme_follows_brand}}; brand_id is null (and css empty) while the UI is neutral."""

import json
import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import brand, settings, theme
from .brands import _dir, _read

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)
MEDIA = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}
STYLE_ID = "brand-theme"


class BrandSettings(BaseModel):
    """Only the fields sent change; default_brand null is neutral."""

    default_brand: str | None = None
    theme_follows_brand: bool | None = None


def _asset_url(brand_id: str, d: Path):
    root = d.resolve()

    def url(p: Path) -> str:
        """ "" for a file outside the brand folder (an absolute path in brand.json)."""
        p = p.resolve()
        if not p.is_relative_to(root):
            return ""
        rel = p.relative_to(root).as_posix()
        v = int(p.stat().st_mtime) if p.is_file() else 0
        return f"/api/brands/{quote(brand_id)}/assets/{quote(rel)}?v={v}"

    return url


def _theme_of(brand_id: str, tokens: dict) -> dict:
    return theme.build(tokens, _asset_url(brand_id, brand.brand_dir(brand_id)))


def _settings() -> dict:
    conf = settings.load()
    return {
        "default_brand": settings.default_brand(),
        "theme_follows_brand": conf.get("theme_follows_brand") is not False,
    }


def current() -> dict:
    """The theme view; neutral when no default brand is set, the switch is off or the
    brand has errors (then `invalid` is true, so the Brands screen can say why)."""
    s = _settings()
    view = {"brand_id": None, "name": None, "css": "", "logo": {"light": None, "dark": None},
            "notes": [], "invalid": False, "settings": s}  # fmt: skip
    ref = s["default_brand"]
    if ref is None or not s["theme_follows_brand"]:
        return view
    tokens, errors, _ = brand.inspect(ref)
    if errors:
        return {**view, "invalid": True}
    return {**view, **_theme_of(ref, tokens), "brand_id": ref, "name": tokens.get("name")}


def safe_current() -> dict:
    """current(), or the neutral view when settings.json or the brand folder cannot be read:
    the page must always load, so the setting can be fixed from the app."""
    try:
        return current()
    except Exception:  # noqa: BLE001 — a broken settings.json, an unreadable brand file
        log.warning("brand theme unavailable", exc_info=True)
        return {"brand_id": None, "name": None, "css": "", "logo": {"light": None, "dark": None},
                "notes": [], "invalid": True, "settings": None}  # fmt: skip


@router.get("/theme")
def get_theme() -> dict:
    return safe_current()


@router.put("/settings/brand")
def put_brand_settings(body: BrandSettings) -> dict:
    changes = body.model_dump(exclude_unset=True)
    ref = changes.get("default_brand")
    if ref is not None and (not brand.BRAND_ID_RE.fullmatch(ref) or ref not in brand.list_ids()):
        raise HTTPException(404, f"no brand {ref!r}")
    if changes.get("theme_follows_brand", True) is None:
        changes.pop("theme_follows_brand")
    settings.save(**changes)
    return current()


@router.post("/brands/{brand_id}/theme")
async def preview_theme(brand_id: str, request: Request) -> dict:
    """The theme of an edited, unsaved brand.json (the editor's live preview and contrast
    hints); 422 with the brand check's errors while it cannot be resolved."""
    d = _dir(brand_id)
    try:
        data = await request.json()
    except ValueError:
        data = _read(d)
    if not isinstance(data, dict):
        raise HTTPException(400, "body must be a JSON object (brand.json)")
    data.pop("vocabulary", None)  # not part of the look; a half-typed entry must not block it
    try:
        tokens, errors, warnings = brand._validate(json.loads(json.dumps(data)), d)
    except (TypeError, ValueError, AttributeError) as e:
        tokens, errors, warnings = {}, [f"$: {e}"], []
    if errors:
        raise HTTPException(
            422, {"message": "the brand has problems", "errors": errors, "warnings": warnings}
        )
    return {**_theme_of(brand_id, tokens), "brand_id": brand_id, "name": tokens.get("name")}


@router.get("/brands/{brand_id}/assets/{name:path}")
def brand_asset(brand_id: str, name: str) -> FileResponse:
    """A logo or font file of the brand folder, for the app header and
    @font-face. Uploaded SVGs are untrusted: served with a CSP that runs nothing."""
    d = _dir(brand_id).resolve()
    p = (d / name).resolve()
    if not p.is_relative_to(d) or p.suffix.lower() not in MEDIA or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(
        p,
        media_type=MEDIA[p.suffix.lower()],
        headers={
            "Cache-Control": "private, max-age=86400",  # the URL carries the file's mtime
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            "img-src data:; font-src data:; form-action 'none'; sandbox",
        },
    )


def inject(page: str) -> str:
    """index.html with the current theme inline (stylesheet + view as JSON data), so the
    first paint already has the brand's look and logo."""
    view = safe_current()
    data = json.dumps(view).replace("<", "\\u003c")
    head = (
        f'<style id="{STYLE_ID}">{view["css"]}</style>'
        f'<script type="application/json" id="{STYLE_ID}-data">{data}</script>'
    )
    return page.replace("</head>", head + "</head>", 1)
