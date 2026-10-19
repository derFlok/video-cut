"""Pexels (https://www.pexels.com/api/): stock videos and photos under the Pexels License.
Sent: the query, orientation, locale and a minimum size. Attribution per item
("Video by <name> on Pexels"); the app and disclosure.md link to Pexels."""

from . import StockHit, StockProvider, get_json, key, register_provider

API = "https://api.pexels.com/v1"
LICENCE = "Pexels License"


class Pexels(StockProvider):
    name = "pexels"
    secret_account = "pexels-api-key"

    def search(self, query, kind, orientation, locale, limit):
        params = {"query": query, "orientation": orientation, "per_page": max(1, min(limit, 80))}
        if locale:
            params["locale"] = locale
        auth = {"Authorization": key(self.secret_account)}
        if kind == "video":
            params["size"] = "medium"  # Full HD and more
            data = get_json(f"{API}/videos/search", params, auth)
            return [h for v in data.get("videos") or [] if (h := _video(v))][:limit]
        params["size"] = "medium"
        data = get_json(f"{API}/search", params, auth)
        return [h for p in data.get("photos") or [] if (h := _photo(p))][:limit]


def _video(v: dict) -> StockHit | None:
    files = [
        (int(f["width"]), int(f["height"]), f["link"])
        for f in v.get("video_files") or []
        if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")
        and str(f.get("link", "")).startswith("https://")
    ]  # fmt: skip
    if not files:
        return None
    user = v.get("user") or {}
    name = user.get("name") or "unknown"
    return StockHit(
        id=f"pexels-video-{v['id']}", kind="video", width=int(v.get("width") or files[-1][0]),
        height=int(v.get("height") or files[-1][1]), duration_s=float(v.get("duration") or 0),
        preview_url=v.get("image") or "", file_url=max(files, key=lambda f: f[0] * f[1])[2],
        author=name, source_url=v.get("url") or "", licence=LICENCE,
        attribution=f"Video by {name} on Pexels", renditions=files, source="pexels",
    )  # fmt: skip


def _photo(p: dict) -> StockHit | None:
    src = p.get("src") or {}
    w, h = int(p.get("width") or 0), int(p.get("height") or 0)
    if not (w and h and src.get("original")):
        return None
    base = src["original"]
    # Pexels resizes on request (?w=); three renditions by the long side, plus the original
    rends = []
    for long_side in (1280, 1920, 2560):
        if long_side < max(w, h):
            k = long_side / max(w, h)
            rends.append((round(w * k), round(h * k), f"{base}?auto=compress&cs=tinysrgb&"
                          + (f"w={long_side}" if w >= h else f"h={long_side}")))  # fmt: skip
    rends.append((w, h, base))
    name = p.get("photographer") or "unknown"
    return StockHit(
        id=f"pexels-photo-{p['id']}", kind="image", width=w, height=h, duration_s=None,
        preview_url=src.get("medium") or src.get("small") or base, file_url=base,
        author=name, source_url=p.get("url") or "", licence=LICENCE,
        attribution=f"Photo by {name} on Pexels", renditions=rends, source="pexels",
    )  # fmt: skip


register_provider("pexels", Pexels)
