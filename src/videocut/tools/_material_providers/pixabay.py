"""Pixabay (https://pixabay.com/api/docs/): stock videos and photos under the Pixabay
Content License. Sent: the query, the language, orientation (photos only), a minimum size
and safesearch. Attribution per item ("Video by <user> on Pixabay"); the app and
disclosure.md link to Pixabay, the one condition of the free API.

The video endpoint has no orientation filter: the hits are filtered by the size of their
renditions (large/medium/small share one aspect ratio; portrait h > w, landscape w > h,
square within 10 %). Photos come as `largeImageURL` (long side at most 1280 px)
unless the key has full API access (`fullHDURL`, `imageURL`). Pixabay asks for search
answers to be cached 24 h: they are kept in $VIDEOCUT_HOME/cache/pixabay/ (keyed by the
SHA-256 of the parameters without the key; the key is never written)."""

import hashlib
import json
import time

from . import ProviderError, RateLimited, StockHit, StockProvider, get_json, key, register_provider

API = "https://pixabay.com/api/"
LICENCE = "Pixabay Content License"
CACHE_TTL_S = 24 * 3600
LARGE = 1280  # long side of largeImageURL
FULL_HD = 1920  # long side of fullHDURL
MIN_LONG_SIDE = 1280
SQUARE_TOL = 0.10
# the search languages Pixabay accepts (`lang`); anything else searches in English
LANGS = {"cs", "da", "de", "en", "es", "fr", "id", "it", "hu", "nl", "no", "pl", "pt", "ro",
         "sk", "fi", "sv", "tr", "vi", "th", "bg", "ru", "el", "ja", "ko", "zh"}  # fmt: skip
RATE_MSG = "pixabay: rate limit (100 requests per minute), try again in a minute"


class Pixabay(StockProvider):
    name = "pixabay"
    secret_account = "pixabay-api-key"

    def search(self, query, kind, orientation, locale, limit):
        lang = (locale or "en").split("-")[0].lower()
        params = {"q": query[:100], "lang": lang if lang in LANGS else "en",
                  "safesearch": "true", "per_page": 50}  # fmt: skip
        if orientation in ("portrait", "landscape"):  # HD and more
            params["min_height" if orientation == "portrait" else "min_width"] = MIN_LONG_SIDE
        if kind == "video":
            data = _search(f"{API}videos/", params, self.secret_account)
            hits = [h for v in data.get("hits") or [] if (h := _video(v))]
        else:
            params["image_type"] = "photo"
            params["orientation"] = {"portrait": "vertical", "landscape": "horizontal"}.get(
                orientation, "all"
            )
            data = _search(API, params, self.secret_account)
            hits = [h for p in data.get("hits") or [] if (h := _photo(p))]
        return [h for h in hits if fits(h.width, h.height, orientation)][:limit]


def fits(w: int, h: int, orientation: str) -> bool:
    if orientation == "square":
        return abs(w / h - 1) <= SQUARE_TOL
    return h > w if orientation == "portrait" else w > h


def _search(url: str, params: dict, account: str) -> dict:
    """The search answer, from the 24 h cache when there is one."""
    from ...paths import state_dir

    ident = json.dumps({"url": url, **params}, sort_keys=True)
    digest = hashlib.sha256(ident.encode()).hexdigest()
    f = state_dir().parent / "cache" / "pixabay" / f"{digest}.json"
    try:
        if time.time() - f.stat().st_mtime < CACHE_TTL_S:
            return json.loads(f.read_text())
    except (OSError, ValueError):
        pass
    try:
        data = get_json(url, {"key": key(account), **params})
    except RateLimited:
        raise ProviderError(RATE_MSG) from None
    f.parent.mkdir(parents=True, exist_ok=True)
    for old in f.parent.glob("*.json"):  # expired answers are never read again
        try:
            if time.time() - old.stat().st_mtime >= CACHE_TTL_S:
                old.unlink()
        except OSError:
            pass
    tmp = f.with_name(f.name + ".part")
    tmp.write_text(json.dumps(data))
    tmp.replace(f)
    return data


def _video(v: dict) -> StockHit | None:
    vids = v.get("videos") or {}
    rends, thumbs = [], []
    for q in ("small", "medium", "large"):  # tiny (480 px) is never enough
        r = vids.get(q) or {}
        url, w, h = str(r.get("url") or ""), int(r.get("width") or 0), int(r.get("height") or 0)
        if url.startswith("https://") and w and h and r.get("size"):
            rends.append((w, h, url))
            thumbs.append((q, r.get("thumbnail") or ""))
    if not rends:
        return None
    thumb = dict(thumbs).get("medium") or thumbs[-1][1]
    name = v.get("user") or "unknown"
    w, h, url = max(rends, key=lambda r: r[0] * r[1])
    return StockHit(
        id=f"pixabay-video-{v['id']}", kind="video", width=w, height=h,
        duration_s=float(v.get("duration") or 0), preview_url=thumb, file_url=url,
        author=name, source_url=v.get("pageURL") or "", licence=LICENCE,
        attribution=f"Video by {name} on Pixabay", renditions=rends, source="pixabay",
    )  # fmt: skip


def _photo(p: dict) -> StockHit | None:
    w, h = int(p.get("imageWidth") or 0), int(p.get("imageHeight") or 0)
    if not (w and h):
        return None
    rends = []
    for field, cap in (("largeImageURL", LARGE), ("fullHDURL", FULL_HD), ("imageURL", None)):
        url = str(p.get(field) or "")
        if url.startswith("https://"):
            k = min(1.0, cap / max(w, h)) if cap else 1.0
            rends.append((round(w * k), round(h * k), url))
    if not rends:
        return None
    rends = sorted(set(rends), key=lambda r: r[0] * r[1])
    name = p.get("user") or "unknown"
    best = rends[-1]
    return StockHit(
        id=f"pixabay-photo-{p['id']}", kind="image", width=best[0], height=best[1],
        duration_s=None, preview_url=p.get("webformatURL") or p.get("previewURL") or best[2],
        file_url=best[2], author=name, source_url=p.get("pageURL") or "", licence=LICENCE,
        attribution=f"Photo by {name} on Pixabay", renditions=rends, source="pixabay",
    )  # fmt: skip


register_provider("pixabay", Pixabay)
