"""Openverse (https://api.openverse.org/v1/): openly licensed images, no key needed. Sent:
the query, the aspect ratio (from the orientation), the size and the licence filter;
nothing else (no key, no locale). Images only: Openverse has no video.

Only CC0, the Public Domain Mark and CC BY are asked for: the material is cropped and
recoloured (no ND), the video may be commercial (no NC) and is not released under the same
licence (no SA). CC BY needs a credit line with title, creator, licence and link: the API's
full `attribution` sentence is kept (StockHit.attribution_full) and disclosure.md prints
it with the source link. The material is mixed in quality (Wikimedia Commons, Flickr,
museum collections): encyclopaedic rather than polished stock.

Without a key Openverse allows 20 searches per minute and 200 per day; files are
downloaded from the source sites (Wikimedia, Flickr, ...), which want a User-Agent that
names the program."""

import html
import posixpath
import re
import urllib.parse
from pathlib import Path

from ... import __version__
from . import (
    ProviderError,
    RateLimited,
    StockHit,
    StockProvider,
    download,
    get_json,
    pick_rendition,
    register_provider,
)

API = "https://api.openverse.org/v1/images/"
LICENCES = "cc0,pdm,by"  # never sa/nd/nc (see above)
ASPECT = {"portrait": "tall", "landscape": "wide", "square": "square"}
FILETYPES = {"jpg", "jpeg", "png", "webp"}
MIN_LONG_SIDE = 800
GOOD_LONG_SIDE = 1280
PAGE_SIZE = 20
TITLE_MAX = 80  # in the short credit; the full one keeps the whole title
HEADERS = {"User-Agent": f"videocut/{__version__} (https://github.com/derFlok/video-cut)"}
RATE_MSG = ("openverse: rate limit (20 searches per minute, 200 per day without a key), "
            "try again later")  # fmt: skip


class Openverse(StockProvider):
    name = "openverse"
    secret_account = None

    def available(self) -> tuple[bool, str]:
        return True, ""

    def search(self, query, kind, orientation, locale, limit):
        if kind != "image":
            return []
        params = {"q": query[:200], "license": LICENCES, "size": "large",
                  "page_size": PAGE_SIZE, "mature": "false"}  # fmt: skip
        if orientation in ASPECT:
            params["aspect_ratio"] = ASPECT[orientation]
        data = _search(params)
        hits = [h for r in data.get("results") or [] if (h := _image(r))]
        # large ones first (stable); the reported size is not always the file's, so the
        # fetch probes the download again
        hits.sort(key=lambda h: max(h.width, h.height) < GOOD_LONG_SIDE)
        return hits[:limit]

    def download(self, hit: StockHit, dest: Path, min_size: tuple[int, int] | None = None) -> Path:
        download(pick_rendition(hit, min_size), dest, HEADERS)
        return dest


def _search(params: dict) -> dict:
    """The search answer; a server error (Openverse answers 5xx now and then) is retried once."""
    for attempt in range(2):
        try:
            return get_json(API, params, HEADERS)
        except RateLimited:
            raise ProviderError(RATE_MSG) from None
        except ProviderError as e:
            if attempt or (e.status or 0) < 500:
                raise
    raise AssertionError("unreachable")


def _licence(r: dict) -> tuple[str, str] | None:
    """(licence name, short tag for the credit) of an allowed licence, None otherwise."""
    lic, ver = str(r.get("license") or "").lower(), str(r.get("license_version") or "")
    if lic == "cc0":
        return "CC0 1.0", "CC0"
    if lic == "pdm":
        return "Public Domain Mark 1.0", "Public Domain"
    if lic == "by" and ver:
        return f"CC BY {ver}", f"CC BY {ver}"
    return None


def _text(v) -> str:
    """Plain text of a title or creator: some sources send HTML ("<div class='fn'>...")."""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", str(v or ""))).split())


def _title(r: dict) -> str:
    """The title without HTML, Wikimedia's "File:" prefix and file extension, at most
    TITLE_MAX characters."""
    t = _text(r.get("title")).removeprefix("File:")
    stem, ext = posixpath.splitext(t)
    t = (stem if ext.lower().lstrip(".") in FILETYPES else t).strip()
    return t if len(t) <= TITLE_MAX else t[: TITLE_MAX - 1].rstrip() + "…"


def _filetype(r: dict, url: str) -> str:
    ft = str(r.get("filetype") or "").lower()
    if not ft:  # not always filled in: the file name tells
        ft = posixpath.splitext(urllib.parse.urlsplit(url).path)[1].lstrip(".").lower()
    return ft


def _image(r: dict) -> StockHit | None:
    url = str(r.get("url") or "")
    w, h = int(r.get("width") or 0), int(r.get("height") or 0)
    lic = _licence(r)
    if (not url.startswith("https://") or _filetype(r, url) not in FILETYPES or not (w and h)
            or max(w, h) < MIN_LONG_SIDE or lic is None):  # fmt: skip
        return None
    licence, tag = lic
    creator = _text(r.get("creator")) or "unknown"
    title = _title(r)
    source = str(r.get("source") or r.get("provider") or "").strip()
    via = f"via Openverse/{source}" if source else "via Openverse"
    if r["license"].lower() == "by":
        attribution = f"{title or 'Photo'} by {creator}, {tag}, {via}"
    else:
        attribution = f"{title or 'Photo'} by {creator} ({tag}) {via}"
    return StockHit(
        id=f"openverse-{r['id']}", kind="image", width=w, height=h, duration_s=None,
        preview_url=str(r.get("thumbnail") or url), file_url=url, author=creator,
        source_url=str(r.get("foreign_landing_url") or ""), licence=licence,
        attribution=attribution, source="openverse",
        attribution_full=_text(r.get("attribution")),
        licence_url=str(r.get("license_url") or ""),
    )  # fmt: skip


register_provider("openverse", Openverse)


# --------------------------------------------------------------------------- audio (SFX)
# Sound effects for `videocut tool sfx` (_sfx_library.fetch): CC0 and the Public Domain
# Mark only (no credit is owed, so nothing can be missed in a reel; the sidecar keeps the
# credit line anyway). Sent: the search words, the licence filter and the page size.

AUDIO_API = "https://api.openverse.org/v1/audio/"
AUDIO_LICENCES = "cc0,pdm"
AUDIO_FILETYPES = {"mp3", "wav", "flac", "ogg", "oga", "m4a", "aif", "aiff"}
MAX_AUDIO_BYTES = 10 * 1024**2


def _audio(r: dict, max_s: float) -> StockHit | None:
    url = str(r.get("url") or "")
    lic = _licence(r)
    dur_ms = r.get("duration")
    dur = float(dur_ms) / 1000 if isinstance(dur_ms, int | float) and dur_ms > 0 else None
    if (not url.startswith("https://") or _filetype(r, url) not in AUDIO_FILETYPES
            or lic is None or str(r.get("license") or "").lower() not in ("cc0", "pdm")
            or dur is None or dur > max_s):  # fmt: skip
        return None
    licence, tag = lic
    creator = _text(r.get("creator")) or "unknown"
    title = _title(r) or "Sound"
    source = str(r.get("source") or r.get("provider") or "").strip()
    via = f"via Openverse/{source}" if source else "via Openverse"
    return StockHit(
        id=f"openverse-{r.get('id')}", kind="audio", width=0, height=0, duration_s=dur,
        preview_url=url, file_url=url, author=creator,
        source_url=str(r.get("foreign_landing_url") or ""), licence=licence,
        attribution=f"{title} by {creator} ({tag}) {via}", source="openverse",
        attribution_full=_text(r.get("attribution")),
        licence_url=str(r.get("license_url") or ""),
    )  # fmt: skip


def search_audio(query: str, limit: int = 20, max_s: float = 2.0) -> list[StockHit]:
    """CC0/PDM sounds for `query` no longer than `max_s` (shortest first, stable)."""
    params = {"q": query[:200], "license": AUDIO_LICENCES, "page_size": PAGE_SIZE,
              "mature": "false"}  # fmt: skip
    for attempt in range(2):
        try:
            data = get_json(AUDIO_API, params, HEADERS)
            break
        except RateLimited:
            raise ProviderError(RATE_MSG) from None
        except ProviderError as e:
            if attempt or (e.status or 0) < 500:
                raise
    hits = [h for r in data.get("results") or [] if (h := _audio(r, max_s))]
    return hits[:limit]


def download_audio(hit: StockHit, dest: Path) -> Path:
    from . import request

    raw, _ = request("GET", hit.file_url, HEADERS)
    if len(raw) > MAX_AUDIO_BYTES:
        raise ProviderError(f"openverse: {hit.id} is larger than 10 MB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(raw)
    tmp.replace(dest)
    return dest
