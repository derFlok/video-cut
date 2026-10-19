"""Material providers of `videocut tool material` (tools/material.py): stock libraries that
are searched (StockProvider) and image generators (ImageProvider), in one registry.

    register_provider(name, cls)   add a provider class under `name`
    get_provider(name)             an instance of it (ProviderError when unknown)
    providers(kind=None)           {name: cls}, all or only "stock" / "image" ones

Built in: `pexels`, `coverr` (stock), `gemini`, `openai` (image), and `local` (local_flux:
FLUX.2 [klein] through mflux; its module is imported on first use and registers itself
with its `register()`, so `import mflux` only happens when it is asked for).

Everything here runs on the host only: the HTTP helpers refuse inside an agent run
(VIDEOCUT_AGENT=1), and only what the contract allows leaves the machine (a stock query,
locale and orientation; a generator prompt, size, seed, palette hex values and style
words). API keys come from the macOS keychain (settings.secret) and never appear in an
error message, a log or a file.
"""

import importlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

AGENT_ENV = "VIDEOCUT_AGENT"
TIMEOUT_S = 60
RETRIES_429 = 3  # a 429 waits (Retry-After, else 2^k s) at most this many times
MAX_WAIT_S = 60
MAX_DOWNLOAD = 400 * 1024**2
USER_AGENT = "VideoCut/1 (local video editor)"


class ProviderError(RuntimeError):
    """A provider failed; the message is safe to show (never contains a key)."""


class UnknownProvider(ProviderError, KeyError):
    """No provider of that name (a KeyError too, for callers that look names up)."""

    def __str__(self) -> str:
        return RuntimeError.__str__(self)


@dataclass
class StockHit:
    id: str
    kind: str  # "video" | "image"
    width: int
    height: int
    duration_s: float | None
    preview_url: str  # a small picture (thumb)
    file_url: str  # the default file
    author: str
    source_url: str
    licence: str
    attribution: str
    renditions: list[tuple[int, int, str]] = field(default_factory=list)  # (w, h, url)
    source: str = ""


@dataclass
class GenResult:
    """What a generator made: the file and everything material.json records about it."""

    file: Path
    model: str
    revision: str
    seed: int | None
    watermark: str  # "synthid" | "c2pa" | "none"
    elapsed_s: float
    width: int = 0
    height: int = 0
    cost_usd: float | None = None
    prompt: str = ""  # the exact text sent to the model (when the provider builds its own)
    steps: int | None = None  # local models: denoising steps
    quantisation: str | None = None  # local models: e.g. "8-bit"
    extra: dict = field(default_factory=dict)  # provider specifics


class StockProvider:
    name = ""
    kind = "stock"
    secret_account: str | None = None

    def available(self) -> tuple[bool, str]:
        return _key_available(self.secret_account)

    def search(self, query: str, kind: str, orientation: str, locale: str,
               limit: int) -> list[StockHit]:  # fmt: skip
        raise NotImplementedError

    def download(self, hit: StockHit, dest: Path, min_size: tuple[int, int] | None = None) -> Path:
        """The smallest rendition whose sides reach `min_size` (else the largest) to `dest`."""
        url = pick_rendition(hit, min_size)
        download(url, dest)
        return dest


class ImageProvider:
    name = ""
    kind = "image"
    model = ""
    secret_account: str | None = None
    price_usd = 0.0  # estimated price per image (published list price)

    def available(self) -> tuple[bool, str]:
        return _key_available(self.secret_account)

    def estimate_usd(self, width: int, height: int) -> float:
        return self.price_usd

    def generate(self, prompt: str, width: int, height: int, seed: int | None,
                 palette: list[str], style: str, dest: Path) -> GenResult:  # fmt: skip
        raise NotImplementedError


_REGISTRY: dict[str, type] = {}


def register_provider(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def get_provider(name: str):
    _load_builtin()
    cls = _REGISTRY.get(name)
    if cls is None:
        raise UnknownProvider(f"unknown material provider {name!r}")
    return cls()


def providers(kind: str | None = None) -> dict[str, type]:
    _load_builtin()
    return {n: c for n, c in _REGISTRY.items() if kind is None or c.kind == kind}


BUILTIN = ("pexels", "coverr", "gemini", "openai_image", "local_flux")


def _load_builtin() -> None:
    for mod in BUILTIN:
        try:
            m = importlib.import_module(f"{__name__}.{mod}")
        except ModuleNotFoundError as e:
            if e.name != f"{__name__}.{mod}":  # a missing dependency inside it: say so
                raise
            continue
        if hasattr(m, "register"):  # registers on request, not on import (local_flux)
            m.register()


# --------------------------------------------------------------------------- helpers


def _key_available(account: str | None) -> tuple[bool, str]:
    if account is None:
        return True, ""
    from ... import settings

    try:
        ok = settings.has_secret(account)
    except (OSError, ValueError):
        ok = False
    return (True, "") if ok else (False, f"no key stored ({account})")


def key(account: str) -> str:
    from ... import settings

    k = settings.secret(account)
    if not k:
        raise ProviderError(f"no API key stored for {account.removesuffix('-api-key')}")
    return k


def host_only() -> None:
    if os.environ.get(AGENT_ENV):
        raise ProviderError("no network inside an agent run")


def pick_rendition(hit: StockHit, min_size: tuple[int, int] | None) -> str:
    rends = sorted(hit.renditions, key=lambda r: r[0] * r[1])
    if not rends:
        return hit.file_url
    if min_size:
        need = min(min_size)
        for w, h, url in rends:
            if min(w, h) >= need:
                return url
    return rends[-1][2]


def _safe_url(url: str) -> str:
    """The URL without its query (a key may sit there) for messages."""
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def request(method: str, url: str, headers: dict | None = None, body: bytes | None = None,
            sleep=time.sleep) -> tuple[bytes, dict]:  # fmt: skip
    """(body, headers) of an HTTPS request; 429 waits and retries (RETRIES_429)."""
    host_only()
    if not url.startswith("https://"):
        raise ProviderError(f"refusing a non-HTTPS URL: {_safe_url(url)}")
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(RETRIES_429 + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (https only)
                return r.read(MAX_DOWNLOAD + 1), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < RETRIES_429:
                wait = e.headers.get("Retry-After") if e.headers else None
                try:
                    wait_s = min(float(wait), MAX_WAIT_S) if wait else 2.0 ** (attempt + 1)
                except ValueError:
                    wait_s = 2.0 ** (attempt + 1)
                sleep(wait_s)
                continue
            detail = ""
            try:
                detail = e.read(400).decode("utf-8", "replace")
            except OSError:
                pass
            raise ProviderError(
                f"{_safe_url(url)}: HTTP {e.code}" + (f" ({_scrub(detail)})" if detail else "")
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderError(f"{_safe_url(url)}: {getattr(e, 'reason', e)}") from None
    raise ProviderError(f"{_safe_url(url)}: rate limited (HTTP 429) {RETRIES_429 + 1} times")


def _scrub(text: str) -> str:
    """An error body without anything that looks like a key."""
    import re

    return re.sub(r"[A-Za-z0-9_\-]{24,}", "…", " ".join(text.split()))[:200]


def get_json(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    raw, _ = request("GET", url, headers)
    try:
        return json.loads(raw)
    except ValueError:
        raise ProviderError(f"{_safe_url(url)}: the answer is not JSON") from None


def post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    raw, _ = request(
        "POST", url, {"Content-Type": "application/json", **(headers or {})},
        json.dumps(payload).encode(),
    )  # fmt: skip
    try:
        return json.loads(raw)
    except ValueError:
        raise ProviderError(f"{_safe_url(url)}: the answer is not JSON") from None


def download(url: str, dest: Path, headers: dict | None = None) -> Path:
    raw, _ = request("GET", url, headers)
    if len(raw) > MAX_DOWNLOAD:
        raise ProviderError(f"{_safe_url(url)}: larger than {MAX_DOWNLOAD // 1024**2} MB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(raw)
    tmp.replace(dest)
    return dest


def aspect_label(width: int, height: int, allowed: list[str]) -> str:
    """The allowed "w:h" ratio nearest to width/height."""
    r = width / height

    def dist(a: str) -> float:
        x, y = (int(v) for v in a.split(":"))
        return abs(x / y - r)

    return min(allowed, key=dist)


STYLE_WORDS = {
    "photo": "photorealistic, natural light, shallow depth of field",
    "illustration": "flat vector illustration, clean shapes, minimal",
}


NO_TEXT = "no text, no letters, no logos"  # the end of every generator prompt
_HUES = ((15, "red"), (40, "orange"), (52, "amber"), (68, "yellow"), (160, "green"),
         (195, "teal"), (250, "blue"), (290, "violet"), (330, "magenta"), (346, "pink"),
         (360, "red"))  # fmt: skip


def colour_word(hex_colour: str) -> str | None:
    """A plain colour name for "#rrggbb" ("deep navy blue", "warm amber"), None when it is
    not one. Generators get these words, never the hex string: a model draws "#F2A33A" as
    literal text into the picture."""
    h = hex_colour.strip().lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        return None
    import colorsys

    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    if sat < 0.15 or light < 0.06 or light > 0.95:
        greys = ((0.12, "black"), (0.35, "charcoal grey"), (0.7, "grey"), (0.92, "light grey"))
        return next((n for limit, n in greys if light < limit), "white")
    deg = hue * 360
    name = next(n for limit, n in _HUES if deg < limit)
    if name == "blue" and light < 0.3:
        name = "navy blue"
    tone = ("deep" if light < 0.3 else "pale" if light > 0.8 else "light" if light > 0.65
            else "warm" if name in ("red", "orange", "amber", "yellow") else "")  # fmt: skip
    return f"{tone} {name}".strip()


def colour_words(palette: list, limit: int = 5) -> str:
    """The palette as one phrase: "deep navy blue and warm amber" ("" for none)."""
    words: list[str] = []
    for c in palette:
        w = colour_word(c) if isinstance(c, str) else None
        if w and w not in words:
            words.append(w)
    words = words[:limit]
    return " and ".join(words) if len(words) < 3 else ", ".join(words[:-1]) + " and " + words[-1]


def full_prompt(prompt: str, palette: list[str], style: str) -> str:
    """The generator prompt: the plan's sentence, the style words, the brand palette as
    colour names (never hex) and, last, NO_TEXT."""
    parts = [prompt.strip().rstrip(".") + ".", STYLE_WORDS.get(style, "") + "."]
    if colours := colour_words(palette):
        parts.append(f"Colours: {colours}.")
    parts.append(NO_TEXT[0].upper() + NO_TEXT[1:] + ".")
    return " ".join(p for p in parts if p != ".")
