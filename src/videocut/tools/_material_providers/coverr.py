"""Coverr (https://api.coverr.co/docs/): stock videos under the Coverr License. Sent: the
query. Coverr has no orientation filter: the results are filtered by `is_vertical`.
A chosen download is reported to Coverr (PATCH /videos/:id/stats/downloads; required by
its API terms)."""

from . import StockHit, StockProvider, get_json, key, register_provider, request

API = "https://api.coverr.co"
LICENCE = "Coverr License"


class Coverr(StockProvider):
    name = "coverr"
    secret_account = "coverr-api-key"

    def search(self, query, kind, orientation, locale, limit):
        if kind != "video":
            return []
        auth = {"Authorization": f"Bearer {key(self.secret_account)}"}
        data = get_json(f"{API}/videos", {"query": query, "page_size": 30, "urls": "true"}, auth)
        hits = []
        for v in data.get("hits") or []:
            vertical = bool(v.get("is_vertical"))
            if (orientation == "portrait") != vertical and orientation != "square":
                continue
            urls = v.get("urls") or {}
            url = urls.get("mp4_download") or urls.get("mp4")
            if not (isinstance(url, str) and url.startswith("https://")):
                continue
            w, h = int(v.get("max_width") or 0), int(v.get("max_height") or 0)
            hits.append(StockHit(
                id=f"coverr-{v['id']}", kind="video", width=w, height=h,
                duration_s=float(v.get("duration") or 0),
                preview_url=v.get("thumbnail") or v.get("poster") or "", file_url=url,
                author="Coverr", source_url=f"https://coverr.co/videos/{v.get('slug') or v['id']}",
                licence=LICENCE, attribution="Video from Coverr", source="coverr",
            ))  # fmt: skip
            if len(hits) >= limit:
                break
        return hits

    def report_download(self, hit: StockHit) -> None:
        vid = hit.id.removeprefix("coverr-")
        auth = {"Authorization": f"Bearer {key(self.secret_account)}"}
        request("PATCH", f"{API}/videos/{vid}/stats/downloads", auth)


register_provider("coverr", Coverr)
