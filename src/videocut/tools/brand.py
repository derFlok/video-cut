"""`videocut tool brand list|check|show|sheet`: inspect brand profiles (BRIEF §9).

`check` is the gate before a brand is used: it prints the contrast table and advisory
warnings and exits 1 when brand.json has errors. `show` prints the resolved tokens the
scenes receive. `sheet` renders the brand's preview sheet: every available scene with
neutral sample content, in the brand's look, at an output format (`--format`, default 9:16;
another format writes preview-<format>.jpg, e.g. preview-16x9.jpg, next to preview.jpg).
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .. import brand
from ..paths import RENDERER
from . import _format, _motion_sheet, vocab
from ._geometry import safe_zones
from .common import ToolError, binary, run_tool, write_json
from .job import AGENT_ENV

SHEET_FORMAT = _format.PORTRAIT
SHEET_SIZE = _format.FORMATS[SHEET_FORMAT]
SHEET_FPS = 30
SLOT_S = 2.0  # one scene per slot; the sheet tile is taken SHEET_AT_S into it
SHEET_AT_S = 1.1
# Neutral sample content per scene (no industry copy): the preview sheet shows the look,
# never a message. A scene without an entry is shown with empty props.
SAMPLE_IMAGE = "sample-screen.png"
SAMPLES = {
    "ai_badge": {"text": "AI-generated"},
    "keyword": {"text": "Keyword"},
    "number_card": {"value": "42 %", "label": "Sample label"},
    "list_item": {"index": 2, "text": "Second point of a list", "total": 3},
    "chat_bubble": {"text": "How does this work?"},
    "screenshot_frame": {
        "file": SAMPLE_IMAGE,
        "focus": [0.05, 0.05, 0.95, 0.55],
        "redact": [[0.1, 0.7, 0.6, 0.76]],
    },
    "split_compare": {
        "left": {"title": "Before", "text": "Sample text"},
        "right": {"title": "After", "text": "Sample text"},
    },
    "bar_chart": {
        "bars": [{"label": "A", "value": 3}, {"label": "B", "value": 5}, {"label": "C", "value": 8}]
    },
    "lower_third": {"title": "First Last", "subtitle": "Role or topic"},
    "cta_end": {"text": "Share this with someone", "logo": True},
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


def sample_screen(dst: Path) -> None:
    """A neutral, made-up app screen (grey bars on white) for screenshot_frame."""
    bars = "".join(
        f",drawbox=x=60:y={y}:w={w}:h={h}:color=gray@{a}:t=fill"
        for y, w, h, a in (
            (60, 780, 120, 0.9), (260, 600, 40, 0.5), (330, 700, 40, 0.5), (400, 520, 40, 0.5),
            (520, 780, 420, 0.3), (1020, 640, 40, 0.5), (1090, 700, 40, 0.5), (1400, 780, 120, 0.8),
        )
    )  # fmt: skip
    subprocess.run(
        [binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=900x1600",
         "-vf", bars[1:], "-frames:v", "1", str(dst)],
        check=True, capture_output=True,
    )  # fmt: skip


def scene_names() -> list[str]:
    """Scenes the installed renderer has (renderer/scenes/*.js)."""
    return sorted(p.stem for p in (RENDERER / "scenes").glob("*.js"))


def sheet_spec(tokens: dict, scenes: list[str], size: tuple[int, int] = SHEET_SIZE) -> dict:
    """overlays.json for the preview sheet at `size`: one SLOT_S slot per scene, each with a sample
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
        "size": list(size),
        "width": size[0],
        "height": size[1],
        "fps": SHEET_FPS,
        "duration": len(slots) * SLOT_S,
        "tokens": tokens,
        "safe": safe_zones(size),
        "face": [],
        "captions": captions,
        "overlays": overlays,
        # every tile over a neutral generated picture (renderer lib/glass.js backdrop), so
        # shadows and the glass material show as they would over video
        "sheet": {
            "times": times,
            "labels": slots,
            "background": tokens["colors"]["bg"],
            "backdrop": True,
        },  # fmt: skip
    }


def sheet_name(fmt: str) -> str:
    """preview.jpg for the default format, else e.g. preview-16x9.jpg."""
    return "preview.jpg" if fmt == SHEET_FORMAT else f"preview-{fmt.replace(':', 'x')}.jpg"


def sheet_cmd(args) -> dict:
    from .overlays import run_renderer

    if os.environ.get(AGENT_ENV):
        raise ToolError("brand sheet is for humans (not available inside an agent run)")
    tokens, errors, _ = _tokens(args.brand)
    if errors:
        raise ToolError(f"{args.brand}: " + "; ".join(errors))
    try:
        fmt = _format.parse_format(args.format)
    except _format.FormatError as e:
        raise ToolError(str(e)) from None
    scenes = scene_names()
    if not scenes:
        raise ToolError("renderer/scenes is empty (overlay renderer not installed)")
    out = (
        Path(args.out).expanduser().resolve()
        if args.out
        else brand.brands_root() / tokens["id"] / sheet_name(fmt)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = sheet_spec(tokens, scenes, _format.FORMATS[fmt])
    with tempfile.TemporaryDirectory(prefix="videocut-sheet-") as tmp:
        doc = Path(tmp) / "overlays.json"
        write_json(doc, spec)
        sample_screen(Path(tmp) / SAMPLE_IMAGE)
        # A brand given as a folder path may take its logo from there.
        extra = {"VIDEOCUT_BRAND_DIR": tokens["dir"]}
        result = run_renderer([str(doc), "--sheet", str(out)], Path(tmp), extra)
    if not out.is_file():
        raise ToolError(f"renderer reported success but wrote no {out}")
    motion = motion_row(tokens, out)
    shown = spec["sheet"]["labels"]
    print(f"{out}: {len(shown)} tiles ({', '.join(shown)})")
    return {
        "brand": tokens["id"],
        "sheet": str(out),
        "format": fmt,
        "scenes": shown,
        "no_sample": [s for s in shown if s != "caption" and s not in SAMPLES],
        "fonts": result.get("fonts"),
        "skipped_scenes": result.get("skipped_scenes", []),
        "motion": motion,
    }


def motion_row(tokens: dict, sheet: Path) -> dict:
    """Append a row of motion-scene stills (renderer/motion/sheet_scene.js) to the sheet, so
    the person sees their motion style: theme, depth, radius, energy, type scale."""
    src = _motion_sheet.SHEET_SCENE.read_text()
    moments = int(re.search(r"export const MOMENTS = (\d+)", src).group(1))
    slot = float(re.search(r"export const SLOT = ([0-9.]+)", src).group(1))
    size = SHEET_SIZE
    w, h = size
    region = (round(w * 0.04), round(h * 0.1), round(w * 0.96), round(h * 0.38))
    spec = _motion_sheet.motion_spec(tokens, size=size, region=region, duration=moments * slot)
    times = [round(i * slot + 1.6, 3) for i in range(moments)]
    with tempfile.TemporaryDirectory(prefix="videocut-motion-sheet-") as tmp:
        # over the generated backdrop, so the material (shadows, glass) shows
        pngs = _motion_sheet.stills(spec, src, Path(tmp) / "run", times, ["--backdrop"])
        width = _image_width(sheet)
        row = _motion_sheet.contact_sheet(
            pngs, Path(tmp) / "row.jpg", cols=moments, background=tokens["colors"]["bg"],
            width=max(64, (width - 12 * (moments + 1)) // moments),
        )  # fmt: skip
        joined = Path(tmp) / "joined.jpg"
        subprocess.run(
            [binary("ffmpeg"), "-v", "error", "-y", "-i", str(sheet), "-i", str(row),
             "-filter_complex", f"[1:v]scale={width}:-2[r];[0:v][r]vstack",
             "-frames:v", "1", "-q:v", "3", str(joined)],
            check=True, capture_output=True,
        )  # fmt: skip
        joined.replace(sheet)
    m = tokens.get("motion", {})
    keys = ("theme", "theme_resolved", "depth", "energy", "radius", "type_scale")
    return {k: m.get(k) for k in keys}


def _image_width(path: Path) -> int:
    r = subprocess.run(
        [binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    return int(r.stdout.strip().split()[0])


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
    sh.add_argument(
        "--format",
        default=SHEET_FORMAT,
        help=f"output format: {', '.join(_format.FORMATS)} (default {SHEET_FORMAT})",
    )
    sh.set_defaults(handler=run_tool("brand sheet", sheet_cmd))
    vocab.register_brand(sub)
