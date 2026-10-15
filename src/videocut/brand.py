"""Brand profiles (BRIEF §9): load, validate and resolve `brand.json` into design tokens.

A brand lives in $VIDEOCUT_HOME/brands/<id>/ (brand.json plus its fonts and logos); the only
bundled one is brands/_example. `load(ref)` returns the RESOLVED TOKENS every scene reads:
the §9 schema with absolute asset paths and a derived `caption_colors` {text, box, highlight}
that passes WCAG AA. Nothing visible is hard-coded here: colours, fonts and logos all come
from the profile. Fonts are never fetched from the network; a family name without a file
is left to the renderer's system fonts.

Unknown fields are kept untouched (profiles grow); free-text values for enum fields
("ruhig, präzise" for motion.character) resolve to the first enum word they contain.
"""

import json
import math
import re
from pathlib import Path

from .paths import ROOT

BUNDLED_ROOT = ROOT / "brands"
EXAMPLE_ID = "_example"
BRAND_FILE = "brand.json"
BRAND_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,79}")

FONT_EXT = {".woff2", ".ttf", ".otf"}
LOGO_EXT = {".svg", ".png"}
WCAG_AA = 4.5

ENUMS = {
    "colors.accent_usage": ("sparsam", "frei"),
    "logo.placement": ("intro", "outro", "corner", "none"),
    "captions.style": ("box", "outline", "plain"),
    "captions.case": ("normal", "upper"),
    "captions.highlight": ("accent", "underline", "scale"),
    "captions.position": ("center", "lower"),
    "captions.pace": ("calm", "normal", "fast"),
    "motion.character": ("ruhig", "federnd", "knackig"),
    "motion.transitions": ("blur", "slide", "cut"),
    "voice.address": ("du", "sie"),
    "redaction.style": ("auto", "blur", "crop", "block"),
}
# Fields where the enum is only a hint: an unknown value warns instead of failing.
SOFT_ENUMS = {"icons": ("lucide", "phosphor", "none")}
DEFAULTS = {
    "colors.accent_usage": "sparsam",
    "logo.placement": "none",
    "captions.style": "box",
    "captions.case": "normal",
    "captions.highlight": "accent",
    "captions.position": "center",
    "captions.pace": "calm",
    "motion.character": "ruhig",
    "motion.transitions": "blur",
    "voice.address": "du",
    "redaction.style": "auto",
}
REQUIRED_COLORS = ("bg", "text", "accent")
OPTIONAL_COLORS = ("surface", "muted")
LOGO_KEYS = ("light", "dark", "icon_light", "icon_dark")
DEFAULT_SPRING = {"w": 13, "z": 0.8}  # BRIEF §8, also renderer/lib/spring.js
WEIGHT_RE = re.compile(r"[1-9][0-9]{0,3}")
SECTIONS = (
    "colors", "fonts", "logo", "captions", "motion", "voice", "music", "legal", "redaction",
)  # fmt: skip


class BrandError(Exception):
    """A brand that cannot be used; `errors` lists every problem with its JSON path."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]


# --- colour maths (WCAG 2.x) -------------------------------------------------------


def parse_hex(value: str) -> tuple[int, int, int]:
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(value)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def luminance(rgb: tuple[int, int, int]) -> float:
    def lin(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(parse_hex(a)), luminance(parse_hex(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mix(rgb: tuple[int, int, int], target: int, k: float) -> tuple[int, int, int]:
    return tuple(round(c + (target - c) * k) for c in rgb)  # type: ignore[return-value]


def ensure_contrast(color: str, against: str, minimum: float = WCAG_AA) -> tuple[str, bool]:
    """`color` itself when it reaches `minimum` against `against`, else the closest
    darker or lighter variant of it that does. Returns (hex, derived)."""
    if contrast(color, against) >= minimum:
        return to_hex(parse_hex(color)), False
    rgb = parse_hex(color)
    # Go whichever way (towards black or white) can reach more contrast with the backdrop.
    target = 0 if contrast("#000000", against) >= contrast("#FFFFFF", against) else 255
    for step in range(1, 101):
        cand = to_hex(_mix(rgb, target, step / 100))
        if contrast(cand, against) >= minimum:
            return cand, True
    return ("#000000" if target == 0 else "#FFFFFF"), True


def derive_caption_colors(colors: dict, style: str) -> tuple[dict, list[str]]:
    """{text, box, highlight} for captions, each ≥ 4.5:1 against what it sits on, plus a
    note per colour that had to be derived. The box is the surface colour for box captions,
    else the background (plain/outline captions sit directly on the video, so the brand
    background is the best stand-in)."""
    box = colors["surface"] if style == "box" else colors["bg"]
    notes = []
    out = {"box": to_hex(parse_hex(box))}
    for key, source in (("text", colors["text"]), ("highlight", colors["accent"])):
        out[key], derived = ensure_contrast(source, box)
        if derived:
            notes.append(
                f"caption {key}: {to_hex(parse_hex(source))} has only "
                f"{contrast(source, box):.2f}:1 on {out['box']}; derived {out[key]} "
                f"({contrast(out[key], box):.2f}:1)"
            )
    return out, notes


# --- validation --------------------------------------------------------------------


def _enum_words(value, allowed: tuple[str, ...]) -> list[str]:
    """Allowed enum words contained in `value`, in order of appearance (free text such as
    "ruhig, präzise" or "accent_underline")."""
    if not isinstance(value, str):
        return []
    found = [w for w in re.findall(r"[a-zäöüß]+", value.lower()) if w in allowed]
    return list(dict.fromkeys(found))


def _resolve_file(brand_dir: Path, value: str) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = brand_dir / p
    return p


def _check_asset(path: str, value, brand_dir: Path, allowed: set[str], errors: list[str]):
    """Absolute path of an asset reference, or None after recording an error."""
    if not isinstance(value, str) or not value:
        errors.append(f"{path}: expected a file path, got {value!r}")
        return None
    p = _resolve_file(brand_dir, value)
    if not Path(value).is_absolute() and not p.resolve().is_relative_to(brand_dir.resolve()):
        errors.append(f"{path}: {value!r} points outside the brand folder")
        return None
    if p.suffix.lower() not in allowed:
        errors.append(f"{path}: {value!r} must be one of {', '.join(sorted(allowed))}")
        return None
    if not p.is_file():
        errors.append(f"{path}: file not found: {p}")
        return None
    return p.resolve()


def _section(data: dict, key: str) -> dict:
    v = data.get(key)
    return v if isinstance(v, dict) else {}


def _validate(data, brand_dir: Path) -> tuple[dict, list[str], list[str]]:
    """(resolved tokens, errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return {}, ["$: brand.json must be a JSON object"], []
    tokens = json.loads(json.dumps(data))  # deep copy; extra fields survive
    # A section of the wrong type is an error; the tokens get an empty one, so validation
    # goes on and reports every other problem too.
    for sec in SECTIONS:
        if sec in data and not isinstance(data[sec], dict):
            errors.append(f"$.{sec}: expected an object, got {type(data[sec]).__name__}")
            tokens[sec] = {}

    if not isinstance(data.get("name"), str) or not data["name"].strip():
        errors.append("$.name: required, a non-empty string")

    # colours
    colors = _section(data, "colors")
    if "colors" not in data:
        errors.append("$.colors: required")
    hexes: dict[str, str] = {}
    for key in (*REQUIRED_COLORS, *OPTIONAL_COLORS):
        v = colors.get(key)
        if v is None:
            if key in REQUIRED_COLORS and "colors" in data:
                errors.append(f"$.colors.{key}: required")
            continue
        try:
            hexes[key] = to_hex(parse_hex(v))
        except (ValueError, AttributeError):
            errors.append(f"$.colors.{key}: {v!r} is not a hex colour like #RRGGBB")
    for key, fallback in (("surface", "bg"), ("muted", "text")):
        if key not in hexes and fallback in hexes:
            hexes[key] = hexes[fallback]
    tokens.setdefault("colors", {})
    tokens["colors"].update({k: v for k, v in hexes.items()})

    # enums
    resolved_enums: dict[str, str] = {}
    for dotted, allowed in ENUMS.items():
        sec, key = dotted.split(".")
        raw = (data.get(sec) or {}).get(key) if isinstance(data.get(sec), dict) else None
        if raw is None:
            resolved_enums[dotted] = DEFAULTS[dotted]
            continue
        hits = _enum_words(raw, allowed)
        if not hits:
            errors.append(f"$.{sec}.{key}: {raw!r} is not one of {'|'.join(allowed)}")
            continue
        resolved_enums[dotted] = hits[0]
        if len(hits) > 1:
            warnings.append(f"$.{sec}.{key}: {raw!r} names {'+'.join(hits)}; using {hits[0]!r}")
    if "icons" in data:
        if not _enum_words(data["icons"], SOFT_ENUMS["icons"]):
            warnings.append(f"$.icons: {data['icons']!r} is not one of lucide|phosphor|none")

    # fonts
    fonts = _section(data, "fonts")
    tokens.setdefault("fonts", {})
    font_files: dict[str, str] = {}
    for role in ("display", "body"):
        v = fonts.get(role)
        tokens["fonts"][f"{role}_file"] = None
        if v is None:
            continue
        if not isinstance(v, str) or not v.strip():
            errors.append(f"$.fonts.{role}: expected a family name or a font file")
            continue
        if Path(v).suffix.lower() in FONT_EXT or "/" in v:
            f = _check_asset(f"$.fonts.{role}", v, brand_dir, FONT_EXT, errors)
            tokens["fonts"][f"{role}_file"] = str(f) if f else None
    files = fonts.get("files")
    if files is not None and not isinstance(files, dict):
        errors.append("$.fonts.files: expected an object {weight: file}")
    elif files:
        for weight, v in files.items():
            if not WEIGHT_RE.fullmatch(str(weight)) or not 1 <= int(weight) <= 1000:
                errors.append(f"$.fonts.files.{weight}: weight must be 1..1000, such as 400")
                continue
            f = _check_asset(f"$.fonts.files.{weight}", v, brand_dir, FONT_EXT, errors)
            if f:
                font_files[str(weight)] = str(f)
    weights = fonts.get("weights")
    if weights is not None and not (
        isinstance(weights, list) and all(type(w) is int and 1 <= w <= 1000 for w in weights)
    ):
        errors.append("$.fonts.weights: expected a list of weights 1..1000 such as [400, 700]")
        weights = None
    if not font_files:
        # Only display/body files (the §9 form): the renderer registers fonts per weight,
        # so body takes the lightest declared weight and display the strongest.
        ws = sorted(weights) if weights else [400, 700]
        for role, w in (("body", ws[0]), ("display", ws[-1])):
            if tokens["fonts"][f"{role}_file"]:
                font_files[str(w)] = tokens["fonts"][f"{role}_file"]
    tokens["fonts"]["files"] = font_files
    if font_files and isinstance(weights, list):
        for w in weights:
            if str(w) not in font_files:
                warnings.append(f"$.fonts.files: no file for declared weight {w}")

    # logo
    logo = _section(data, "logo")
    tokens["logo"] = {**logo}
    for key in LOGO_KEYS:
        v = logo.get(key)
        tokens["logo"][key] = None
        if v is not None:
            f = _check_asset(f"$.logo.{key}", v, brand_dir, LOGO_EXT, errors)
            tokens["logo"][key] = str(f) if f else None
    tokens["logo"]["placement"] = resolved_enums["logo.placement"]

    # numbers
    spring = (
        (data.get("motion") or {}).get("spring") if isinstance(data.get("motion"), dict) else None
    )
    if spring is not None:
        ok = isinstance(spring, dict) and all(
            type(spring.get(k)) in (int, float) and 0 < spring[k] < 1000 for k in ("w", "z")
        )
        if not ok:
            errors.append('$.motion.spring: expected {"w": number > 0, "z": number > 0}')
    else:
        tokens.setdefault("motion", {})["spring"] = dict(DEFAULT_SPRING)
    voice = _section(data, "voice")
    words = voice.get("forbidden_words")
    if words is not None and not (
        isinstance(words, list) and all(isinstance(w, str) for w in words)
    ):
        errors.append("$.voice.forbidden_words: expected a list of words")

    # resolved enums go into canonical places; the raw text stays under "<key>_text"
    for dotted, word in resolved_enums.items():
        sec, key = dotted.split(".")
        tokens.setdefault(sec, {})
        raw = tokens[sec].get(key)
        if isinstance(raw, str) and raw != word:
            tokens[sec][f"{key}_text"] = raw
        tokens[sec][key] = word

    if not errors:
        caps, notes = derive_caption_colors(tokens["colors"], tokens["captions"]["style"])
        tokens["caption_colors"] = caps
        tokens["derived"] = notes
    return tokens, errors, warnings


# --- public API --------------------------------------------------------------------


def brands_root() -> Path:
    from .tools.common import home

    return home() / "brands"


def brand_dir(ref: str) -> Path:
    """Folder of brand `ref`: the bundled example, a user brand id, or a folder path."""
    if ref == EXAMPLE_ID:
        return BUNDLED_ROOT / EXAMPLE_ID
    if BRAND_ID_RE.fullmatch(ref) and (brands_root() / ref / BRAND_FILE).is_file():
        return brands_root() / ref
    p = Path(ref).expanduser()
    if (p / BRAND_FILE).is_file():
        return p
    raise BrandError(f"no brand {ref!r} (looked in {brands_root()} and the bundled brands)")


def list_ids() -> list[str]:
    ids = [EXAMPLE_ID] if (BUNDLED_ROOT / EXAMPLE_ID / BRAND_FILE).is_file() else []
    root = brands_root()
    if root.is_dir():
        ids += sorted(
            p.name
            for p in root.iterdir()
            if (p / BRAND_FILE).is_file() and p.name != EXAMPLE_ID and BRAND_ID_RE.fullmatch(p.name)
        )
    return ids


def inspect(ref: str) -> tuple[dict, list[str], list[str]]:
    """(tokens, errors, warnings) without raising on invalid content; the tokens are only
    complete when errors is empty."""
    d = brand_dir(ref)
    try:
        data = json.loads(
            (d / BRAND_FILE).read_text(), parse_constant=_no_constant, parse_float=_finite
        )
    except (OSError, ValueError) as e:
        return {}, [f"$: cannot read {BRAND_FILE}: {e}"], []
    tokens, errors, warnings = _validate(data, d)
    if not errors:
        tokens["id"] = d.name
        tokens["dir"] = str(d.resolve())
        warnings += _lint(tokens)
    return tokens, errors, warnings


def _no_constant(name: str):
    raise ValueError(f"{name} is not a valid JSON number")


def _finite(text: str) -> float:
    v = float(text)
    if not math.isfinite(v):
        raise ValueError(f"{text} is out of range")
    return v


def load(ref: str) -> dict:
    """Resolved tokens of brand `ref`; raises BrandError listing every problem."""
    tokens, errors, _ = inspect(ref)
    if errors:
        raise BrandError(f"brand {ref!r} is invalid: " + "; ".join(errors), errors)
    return tokens


def contrast_table(tokens: dict) -> list[dict]:
    """Raw and caption-derived colour pairs with their WCAG ratio."""
    c, cc = tokens["colors"], tokens["caption_colors"]
    pairs = [
        ("text on bg", c["text"], c["bg"]),
        ("text on surface", c["text"], c["surface"]),
        ("muted on bg", c["muted"], c["bg"]),
        ("accent on bg", c["accent"], c["bg"]),
        ("accent on surface", c["accent"], c["surface"]),
        ("caption text on box", cc["text"], cc["box"]),
        ("caption highlight on box", cc["highlight"], cc["box"]),
    ]
    return [
        {"pair": n, "fg": fg, "bg": bg, "ratio": round(contrast(fg, bg), 2),
         "aa": contrast(fg, bg) >= WCAG_AA}
        for n, fg, bg in pairs
    ]  # fmt: skip


def _lint(tokens: dict) -> list[str]:
    """Advisory findings for `brand check` (logo rules, accent usage, missing assets)."""
    w: list[str] = []
    logo, caps = tokens["logo"], tokens["captions"]
    if logo["placement"] != "none" and not any(logo[k] for k in LOGO_KEYS):
        w.append(f"$.logo: placement is {logo['placement']!r} but no logo file is set")
    bg_dark = luminance(parse_hex(tokens["colors"]["bg"])) < 0.18
    wanted = "dark" if bg_dark else "light"
    if (
        any(logo[k] for k in LOGO_KEYS)
        and not logo.get(wanted)
        and logo.get("light") != logo.get("dark")
    ):
        w.append(f"$.logo.{wanted}: background is {'dark' if bg_dark else 'light'} "
                 f"but only the other logo variant is set")  # fmt: skip
    if logo.get("rules"):
        w.append(f"logo rules to respect (not machine-checked): {logo['rules']}")
    if tokens["colors"]["accent_usage"] == "sparsam" and caps["highlight"] == "accent":
        w.append("accent_usage is 'sparsam': use the accent only for the active word, lines "
                 "and small shapes, never as a large fill")  # fmt: skip
    for n in tokens.get("derived", []):
        w.append(f"derived: {n}")
    raw = tokens["colors"]
    if contrast(raw["text"], raw["bg"]) < WCAG_AA:
        w.append(f"colors.text has {contrast(raw['text'], raw['bg']):.2f}:1 on colors.bg (< 4.5)")
    return w
