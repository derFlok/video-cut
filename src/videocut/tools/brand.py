"""`videocut tool brand list|check|show|sheet`: inspect brand profiles (BRIEF §9).

`check` is the gate before a brand is used: it prints the contrast table and advisory
warnings and exits 1 when brand.json has errors. `show` prints the resolved tokens the
scenes receive. `sheet` renders the brand's preview sheet: every available scene with
neutral sample content, in the brand's look.
"""

import json
import tempfile
from pathlib import Path

from .. import brand
from ..paths import RENDERER
from ._geometry import safe_zones
from .common import ToolError, run_tool, write_json

SHEET_SIZE = (1080, 1920)
SHEET_FPS = 30
SLOT_S = 2.0  # one scene per slot; the sheet tile is taken SHEET_AT_S into it
SHEET_AT_S = 1.1
# Neutral sample content per scene (no industry copy): the preview sheet shows the look,
# never a message. A scene without an entry is shown with empty props.
SAMPLES = {
    "keyword": {"text": "Keyword"},
    "number_card": {"value": "42 %", "label": "Sample label"},
    "list_item": {"n": 2, "text": "Second point of a list", "total": 3},
    "chat_bubble": {"text": "How does this work?", "from": "Question"},
    "screenshot_frame": {},
    "split_compare": {"left": "Before", "right": "After"},
    "bar_chart": {"bars": [{"label": "A", "value": 3}, {"label": "B", "value": 5}]},
    "lower_third": {"title": "First Last", "subtitle": "Role or topic"},
    "cta_end": {"text": "Share this with someone who needs it"},
}
SAMPLE_CAPTION = ["Sample", "caption", "words"]


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


def scene_names() -> list[str]:
    """Scenes the installed renderer has (renderer/scenes/*.js)."""
    return sorted(p.stem for p in (RENDERER / "scenes").glob("*.js"))


def sheet_spec(tokens: dict, scenes: list[str]) -> dict:
    """overlays.json for the preview sheet: one SLOT_S slot per scene, each with a sample
    caption group under it (the first slot shows the caption alone), and one tile per slot."""
    slots = ["caption"] + [s for s in scenes if s != "caption"]
    overlays, captions, times = [], [], []
    for i, scene in enumerate(slots):
        t0 = i * SLOT_S
        if scene != "caption":
            props = json.loads(json.dumps(SAMPLES.get(scene, {})))
            overlays.append(
                {"scene": scene, "t0": t0 + 0.1, "t1": t0 + SLOT_S - 0.1, "props": props}
            )
        words = [
            {"w": w, "t0": round(t0 + 0.2 + k * 0.4, 3), "t1": round(t0 + 0.55 + k * 0.4, 3),
             "emph": k == 2}
            for k, w in enumerate(SAMPLE_CAPTION)
        ]  # fmt: skip
        captions.append({"t0": t0 + 0.1, "t1": t0 + SLOT_S - 0.1, "words": words})
        times.append(round(t0 + SHEET_AT_S, 3))
    return {
        "size": list(SHEET_SIZE),
        "fps": SHEET_FPS,
        "duration": len(slots) * SLOT_S,
        "tokens": tokens,
        "safe": safe_zones(SHEET_SIZE),
        "face": [],
        "captions": captions,
        "overlays": overlays,
        "sheet": {"times": times, "labels": slots, "background": tokens["colors"]["bg"]},
    }


def sheet_cmd(args) -> dict:
    from .overlays import run_renderer

    tokens, errors, _ = _tokens(args.brand)
    if errors:
        raise ToolError(f"{args.brand}: " + "; ".join(errors))
    scenes = scene_names()
    if not scenes:
        raise ToolError("renderer/scenes is empty (overlay renderer not installed)")
    out = (
        Path(args.out).expanduser().resolve()
        if args.out
        else brand.brands_root() / tokens["id"] / "preview.jpg"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = sheet_spec(tokens, scenes)
    with tempfile.TemporaryDirectory(prefix="videocut-sheet-") as tmp:
        doc = Path(tmp) / "overlays.json"
        write_json(doc, spec)
        result = run_renderer([str(doc), "--sheet", str(out)], Path(tmp))
    if not out.is_file():
        raise ToolError(f"renderer reported success but wrote no {out}")
    shown = spec["sheet"]["labels"]
    print(f"{out}: {len(shown)} tiles ({', '.join(shown)})")
    return {
        "brand": tokens["id"],
        "sheet": str(out),
        "scenes": shown,
        "no_sample": [s for s in shown if s != "caption" and s not in SAMPLES],
        "fonts": result.get("fonts"),
        "skipped_scenes": result.get("skipped_scenes", []),
    }


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
    sh = sub.add_parser("sheet", help="preview sheet: every scene in the brand's look")
    sh.add_argument("brand", help="brand id (or a brand folder)")
    sh.add_argument("--out", help="JPEG to write (default: <brands>/<id>/preview.jpg)")
    sh.set_defaults(handler=run_tool("brand sheet", sheet_cmd))
