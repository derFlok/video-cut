"""`videocut tool brand list|check|show|scaffold-test-brands`: inspect brand profiles (BRIEF §9).

`check` is the gate before a brand is used: it prints the contrast table and advisory
warnings and exits 1 when brand.json has errors. `show` prints the resolved tokens the
scenes receive. `scaffold-test-brands` writes two invented brands (brand-a, brand-b) into
$VIDEOCUT_HOME/brands so look differences can be tried without a real brand; their fonts
are referenced from the macOS system font folders, nothing is downloaded or copied.
"""

import json
from pathlib import Path

from .. import brand
from .common import ToolError, run_tool, write_json

SYSTEM_FONT_DIRS = [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
# First existing file wins; only ttf/otf, so the renderer can register them directly.
TEST_FONTS = {
    "brand-a": ["SFNS.ttf", "Impact.ttf", "Arial Black.ttf"],
    "brand-b": ["SFNSRounded.ttf", "Arial Rounded Bold.ttf", "Comic Sans MS.ttf"],
}


def _tokens(ref: str):
    try:
        return brand.inspect(ref)
    except brand.BrandError as e:
        raise ToolError(str(e)) from None


def list_cmd(args) -> dict:
    rows = []
    for bid in brand.list_ids():
        tokens, errors, _ = brand.inspect(bid)
        name = tokens.get("name") or "?"
        print(f"{bid}\t{name}\t{'ok' if not errors else 'INVALID'}")
        rows.append({"id": bid, "name": name, "valid": not errors})
    return {"brands": rows, "root": str(brand.brands_root())}


def check_cmd(args) -> dict:
    tokens, errors, warnings = _tokens(args.brand)
    for e in errors:
        print(f"error: {e}")
    if errors:
        raise ToolError(f"{args.brand}: {len(errors)} error(s): " + "; ".join(errors))
    table = brand.contrast_table(tokens)
    for r in table:
        print(f"{r['pair']:<26}{r['fg']} on {r['bg']}  {r['ratio']:>6.2f}:1  "
              f"{'AA' if r['aa'] else 'below AA'}")  # fmt: skip
    for w in warnings:
        print(f"warning: {w}")
    return {
        "brand": args.brand,
        "name": tokens["name"],
        "caption_colors": tokens["caption_colors"],
        "derived": tokens["derived"],
        "warnings": warnings,
        "contrast": table,
    }


def show_cmd(args) -> dict:
    tokens, errors, _ = _tokens(args.brand)
    if errors:
        raise ToolError(f"{args.brand}: " + "; ".join(errors))
    print(json.dumps(tokens, ensure_ascii=False, indent=2))
    return {"brand": args.brand, "tokens": tokens}


def _find_font(names: list[str]) -> Path:
    for name in names:
        for d in SYSTEM_FONT_DIRS:
            if (d / name).is_file():
                return d / name
    raise ToolError(f"none of {names} found in {', '.join(map(str, SYSTEM_FONT_DIRS))}")


def _logo_svg(letter: str, shape: str, fill: str, ink: str) -> str:
    glyph = {
        "circle": '<circle cx="50" cy="50" r="46" fill="{f}"/>',
        "square": '<rect x="6" y="6" width="88" height="88" rx="14" fill="{f}"/>',
    }[shape].format(f=fill)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="400" height="400">'
        f'{glyph}<text x="50" y="68" font-size="56" font-weight="700" text-anchor="middle" '
        f'font-family="sans-serif" fill="{ink}">{letter}</text></svg>\n'
    )


def _test_brand(bid: str, font: Path) -> dict:
    a = bid == "brand-a"
    return {
        "name": "Brand A" if a else "Brand B",
        "$comment": "Invented test brand; fonts are system fonts referenced by path.",
        "colors": {
            "bg": "#0B0B10" if a else "#FFF4E5",
            "surface": "#1A1A24" if a else "#FFFFFF",
            "text": "#F5F5F7" if a else "#2A1B3D",
            "muted": "#9A9AA8" if a else "#7A6A8A",
            "accent": "#FF1F5A" if a else "#FF6B2C",
            "accent2": None if a else "#2CB5FF",
            "accent_usage": "frei",
        },
        "fonts": {
            "display": "Brand A Display" if a else "Brand B Display",
            "body": "Brand A Text" if a else "Brand B Text",
            "weights": [400, 800] if a else [400, 700],
            "files": {"400": str(font), "800" if a else "700": str(font)},
        },
        "logo": {
            "light": "logo-light.svg",
            "dark": "logo-dark.svg",
            "placement": "corner" if a else "outro",
        },
        "captions": {
            "style": "plain" if a else "box",
            "case": "upper" if a else "normal",
            "highlight": "scale" if a else "accent",
            "position": "lower" if a else "center",
        },
        "motion": {
            "character": "knackig" if a else "federnd",
            "spring": {"w": 22, "z": 0.95} if a else {"w": 11, "z": 0.45},
            "transitions": "cut" if a else "slide",
        },
        "icons": "none",
        "voice": {"address": "du", "tone": "direct" if a else "playful",
                  "forbidden_words": [], "cta": "Follow for more" if a else "Say hi"},
        "music": {"mood": "driving" if a else "bouncy", "max_bpm": 140 if a else 120},
        "legal": {"footer": ""},
    }  # fmt: skip


def scaffold_cmd(args) -> dict:
    root = brand.brands_root()
    made = []
    for bid, names in TEST_FONTS.items():
        d = root / bid
        if d.exists() and not args.force:
            print(f"{bid}: exists, kept (use --force to rewrite)")
            continue
        font = _find_font(names)
        data = _test_brand(bid, font)
        data["colors"] = {k: v for k, v in data["colors"].items() if v is not None}
        d.mkdir(parents=True, exist_ok=True)
        c = data["colors"]
        letter, shape = ("A", "circle") if bid == "brand-a" else ("B", "square")
        (d / "logo-light.svg").write_text(_logo_svg(letter, shape, c["text"], c["bg"]))
        (d / "logo-dark.svg").write_text(_logo_svg(letter, shape, c["accent"], c["text"]))
        write_json(d / "brand.json", data)
        print(f"{bid}: written to {d} (font {font})")
        made.append({"id": bid, "font": str(font)})
    return {"created": made, "root": str(root)}


def register(subparsers) -> None:
    p = subparsers.add_parser("brand", help="list, check or show brand profiles")
    sub = p.add_subparsers(dest="brand_cmd", required=True)
    sub.add_parser("list", help="brands available").set_defaults(
        handler=run_tool("brand list", list_cmd)
    )
    for name, fn, text in (
        ("check", check_cmd, "validate a brand; contrast table and warnings, exit 1 on errors"),
        ("show", show_cmd, "print the resolved tokens scenes receive"),
    ):
        c = sub.add_parser(name, help=text)
        c.add_argument("brand", help="brand id (or a brand folder)")
        c.set_defaults(handler=run_tool(f"brand {name}", fn))
    s = sub.add_parser(
        "scaffold-test-brands", help="write the invented brand-a and brand-b into the brands folder"
    )
    s.add_argument("--force", action="store_true", help="overwrite existing test brands")
    s.set_defaults(handler=run_tool("brand scaffold-test-brands", scaffold_cmd))
