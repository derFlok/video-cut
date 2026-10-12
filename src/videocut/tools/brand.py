"""`videocut tool brand list|check|show|sheet`: inspect brand profiles (BRIEF §9).

`check` is the gate before a brand is used: it prints the contrast table and advisory
warnings and exits 1 when brand.json has errors. `show` prints the resolved tokens the
scenes receive. `sheet` renders the brand's preview sheet: every available scene with
neutral sample content, in the brand's look.
"""

import json

from .. import brand
from .common import ToolError, run_tool


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
