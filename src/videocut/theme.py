"""The app's own look from a brand (the default brand, or the one being edited).

`build(tokens, asset_url)` maps resolved brand tokens (brand.load) onto the CSS custom
properties of app/src/styles.css and returns the stylesheet the app injects as
<style id="brand-theme"> (served inline in index.html, so the first paint is already themed).

Mapping: colors.bg → --bg, surface → --surface (--surface-2, --line, --line-strong are mixed
from surface towards text), text → --text, muted → --muted, accent → --primary and --focus
(--primary-text is black or white, whichever reads better). fonts.display → --font-display,
fonts.body → --font; font files are served through the brand asset endpoint, a family name
alone falls back to that family, then the system font.

Dark mode: a dark brand (bg luminance < 0.18) keeps its palette in both schemes. A light
brand keeps the neutral dark tokens in the dark scheme and only brings its accent (when it
stands out from the dark background) and its fonts; the header shows logo.dark there.

Contrast guard (WCAG 2.x, brand.contrast): text and muted need 4.5:1 on bg and surface, the
accent 3:1 on bg (a UI component). A failing token falls back to the neutral one, and
`notes` says which, for the plain-language hint on the Brands screen. Status colours (ok,
warn, error, redact, change) stay neutral: the light or dark set, following the brand bg.
"""

import re
from collections.abc import Callable
from pathlib import Path

from .brand import WCAG_AA, contrast, luminance, parse_hex, to_hex

UI_CONTRAST = 3.0  # WCAG 1.4.11 non-text contrast (buttons, focus ring)
DARK_BG = 0.18  # the same split brand._lint uses for "the background is dark"

# The neutral tokens of app/src/styles.css (:root and its dark block); a test keeps them equal.
NEUTRAL_LIGHT = {
    "bg": "#f6f6f5", "surface": "#ffffff", "surface-2": "#f0f0ee", "text": "#1b1b1a",
    "muted": "#64645f", "line": "#dededa", "line-strong": "#c4c4bf", "primary": "#1f1f1e",
    "primary-text": "#ffffff", "focus": "#2b6fd6", "ok": "#1d7a46", "ok-bg": "#e7f4ec",
    "warn": "#8a5a00", "warn-bg": "#fbf2dc", "error": "#b42318", "error-bg": "#fdecea",
    "info-bg": "#edf2fa", "change-bg": "#fff5d6", "change": "#8a6400", "redact": "#d92d20",
    "shadow": "0 1px 2px rgb(0 0 0 / 6%), 0 4px 16px rgb(0 0 0 / 5%)",
}  # fmt: skip
NEUTRAL_DARK = {
    "bg": "#141414", "surface": "#1d1d1c", "surface-2": "#262625", "text": "#ececea",
    "muted": "#a3a39e", "line": "#333331", "line-strong": "#4a4a47", "primary": "#ececea",
    "primary-text": "#141414", "focus": "#7aa7f0", "ok": "#6fd49b", "ok-bg": "#16291e",
    "warn": "#f0c060", "warn-bg": "#2d2513", "error": "#ff8a80", "error-bg": "#34191a",
    "info-bg": "#1a2230", "change-bg": "#2f2812", "change": "#f0cc6a", "redact": "#ff6b5e",
    "shadow": "0 1px 2px rgb(0 0 0 / 40%)",
}  # fmt: skip
STATUS = ("ok", "ok-bg", "warn", "warn-bg", "error", "error-bg", "info-bg", "change-bg",
          "change", "redact", "shadow")  # fmt: skip
SYSTEM_FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
GENERIC_FAMILIES = {"system-ui", "sans-serif", "serif", "monospace", "cursive", "ui-sans-serif",
                    "ui-serif", "ui-monospace", "ui-rounded"}  # fmt: skip
FONT_FORMAT = {".woff2": "woff2", ".ttf": "truetype", ".otf": "opentype"}


def _mix(a: str, b: str, k: float) -> str:
    """`a` moved the share `k` towards `b`."""
    ra, rb = parse_hex(a), parse_hex(b)
    return to_hex(tuple(round(x + (y - x) * k) for x, y in zip(ra, rb, strict=True)))  # type: ignore[arg-type]


def is_dark(color: str) -> bool:
    return luminance(parse_hex(color)) < DARK_BG


def _ink(on: str) -> str:
    """Black or white, whichever has more contrast on `on` (always ≥ 4.5:1)."""
    return "#000000" if contrast("#000000", on) >= contrast("#FFFFFF", on) else "#FFFFFF"


def _worst(fg: str, *bgs: str) -> float:
    return min(contrast(fg, b) for b in bgs)


def _note(code: str, scheme: str, fg: str, bg: str) -> dict:
    return {"code": code, "scheme": scheme, "ratio": round(contrast(fg, bg), 2)}


def _pick(candidates: tuple[str, ...], score, minimum: float) -> str:
    """The first candidate whose score reaches `minimum`, else the best-scoring one."""
    return next((c for c in candidates if score(c) >= minimum), None) or max(candidates, key=score)


def _brand_palette(c: dict, scheme: str, notes: list[dict]) -> dict:
    """CSS tokens for a scheme that uses the brand's own colours, with the contrast guard."""
    base = NEUTRAL_DARK if is_dark(c["bg"]) else NEUTRAL_LIGHT
    bg, surface, text, muted = c["bg"], c["surface"], c["text"], c["muted"]
    if _worst(text, bg, surface) < WCAG_AA:
        notes.append(_note("text", scheme, text, bg if contrast(text, bg) < WCAG_AA else surface))
        # the neutral text, or pure black/white for a mid-tone background
        cands = (base["text"], NEUTRAL_LIGHT["text"], NEUTRAL_DARK["text"], "#000000", "#FFFFFF")
        text = _pick(cands, lambda t: _worst(t, bg, surface), WCAG_AA)
        if _worst(text, bg, surface) < WCAG_AA:  # bg and surface too far apart for one text
            text = _ink(bg)
            notes.append(_note("surface", scheme, text, surface))
            surface = _mix(bg, "#FFFFFF" if text == "#000000" else "#000000", 0.04)
    if _worst(muted, bg, surface) < WCAG_AA:
        notes.append(_note("muted", scheme, muted, bg))
        muted = _pick((base["muted"], text), lambda m: _worst(m, bg, surface), WCAG_AA)
    out = {k: base[k] for k in STATUS}
    out.update(bg=bg, surface=surface, text=text, muted=muted)
    out["surface-2"] = _mix(surface, text, 0.05)
    out["line"] = _mix(surface, text, 0.14)
    out["line-strong"] = _mix(surface, text, 0.26)
    out.update(_accent(c["accent"], bg, base, scheme, notes))
    return out


def _accent(accent: str, bg: str, base: dict, scheme: str, notes: list[dict]) -> dict:
    if contrast(accent, bg) >= UI_CONTRAST:
        primary = focus = accent
    else:
        notes.append(_note("accent", scheme, accent, bg))
        cands = (base["primary"], NEUTRAL_LIGHT["primary"], NEUTRAL_DARK["primary"],
                 "#000000", "#FFFFFF")  # fmt: skip
        primary = _pick(cands, lambda p: contrast(p, bg), UI_CONTRAST)
        focus = base["focus"] if contrast(base["focus"], bg) >= UI_CONTRAST else primary
    return {"primary": primary, "primary-text": _ink(primary), "focus": focus}


def _family(value) -> str | None:
    """A font family name safe inside CSS (quoted unless a generic keyword), or None."""
    if not isinstance(value, str) or "/" in value or Path(value).suffix.lower() in FONT_FORMAT:
        return None
    name = " ".join(re.sub(r"[^\w \-]", "", value).split())[:80]
    if not name:
        return None
    return name if name.lower() in GENERIC_FAMILIES else f'"{name}"'


def _fonts(tokens: dict, asset_url: Callable[[Path], str]) -> tuple[list[str], dict]:
    """(@font-face rules, {"--font": …, "--font-display": …})."""
    fonts = tokens.get("fonts") or {}
    faces: list[str] = []

    def face(family: str, file: str, weight: str) -> bool:
        p = Path(file)
        url = asset_url(p)
        if not url:
            return False
        fmt = FONT_FORMAT.get(p.suffix.lower(), "woff2")
        faces.append(
            f'@font-face{{font-family:"{family}";src:url("{url}") format("{fmt}");'
            f"font-weight:{weight};font-display:block}}"
        )
        return True

    stacks = {}
    for role, family in (("body", "vc-brand-body"), ("display", "vc-brand-display")):
        file = fonts.get(f"{role}_file")
        if file and face(family, file, "100 1000"):
            stacks[role] = f'"{family}"'
        elif role == "body" and fonts.get("files"):
            served = [face(family, f, w) for w, f in sorted(fonts["files"].items())]
            stacks[role] = f'"{family}"' if any(served) else _family(fonts.get(role))
        else:
            stacks[role] = _family(fonts.get(role))
    body = f"{stacks['body']}, {SYSTEM_FONT}" if stacks["body"] else SYSTEM_FONT
    display = f"{stacks['display']}, {body}" if stacks["display"] else body
    return faces, {"font": body, "font-display": display}


def _block(selector: str, tokens: dict, scheme: str) -> str:
    body = "".join(f"--{k}:{v};" for k, v in tokens.items())
    return f"{selector}{{{body}color-scheme:{scheme}}}"


def build(tokens: dict, asset_url: Callable[[Path], str]) -> dict:
    """{"css", "logo": {"light", "dark"}, "notes"} for resolved brand `tokens`.
    `asset_url` turns an absolute font or logo path of the brand into a URL ("" when it
    cannot be served)."""
    c = {k: to_hex(parse_hex(v)) for k, v in tokens["colors"].items() if k in
         ("bg", "surface", "text", "muted", "accent")}  # fmt: skip
    notes: list[dict] = []
    light = _brand_palette(c, "light", notes)
    if is_dark(c["bg"]):
        dark = light
    else:
        dark = dict(NEUTRAL_DARK)
        dark.update(_accent(c["accent"], NEUTRAL_DARK["bg"], NEUTRAL_DARK, "dark", notes))
    faces, fonts = _fonts(tokens, asset_url)
    light_scheme = "dark" if is_dark(c["bg"]) else "light"
    css = "".join(
        [
            *faces,
            # html:root outranks styles.css's :root, and :not() its dark block as well
            _block("html:root", {**light, **fonts}, light_scheme),
            "@media (prefers-color-scheme: dark){"
            + _block('html:root:not([data-theme="light"])', {**dark, **fonts}, "dark")
            + "}",
        ]
    )
    if "<" in css:  # served inside <style>; nothing in it may close the element
        raise ValueError("unexpected '<' in the theme stylesheet")
    # logo.light is the variant for a light background, logo.dark for a dark one
    logo = tokens.get("logo") or {}
    on_dark = logo.get("dark") or logo.get("light")
    on_light = on_dark if is_dark(c["bg"]) else logo.get("light") or logo.get("dark")
    return {
        "css": css,
        "logo": {  # by the colour scheme the app shows, not by the variant's name
            "light": (asset_url(Path(on_light)) or None) if on_light else None,
            "dark": (asset_url(Path(on_dark)) or None) if on_dark else None,
        },
        "notes": notes,
    }
