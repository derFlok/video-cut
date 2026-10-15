"""How personal data is hidden on the screen: job.json "redact_style".

    blur   the box is pixelated and blurred (it keeps its colours, so it reads as part of
           the UI; text recognition reads nothing there)
    crop   a box that is a band along an edge of the screen (touches it, spans almost
           the whole other side: browser tabs, address and bookmark bars, a sidebar) is
           cut off: the screen view leaves the band out for the whole segment. Other
           boxes are blurred
    block  a solid block in the brand's colors.bg (the style before the others existed)
    auto   crop for full-width bands at the top or bottom edge, blur for everything else

Absent, the brand's "redaction": {"style": ...} decides (default "auto"). Screenshots
(screenshot_frame props.redact) know only blur and block: crop means blur there. Only a
human sets it (`videocut tool job redact-style`, `videocut run --redact-style`, the app).
"""

from .. import brand
from .common import ToolError

AUTO = "auto"
STYLES = ("auto", "blur", "crop", "block")
HELP = (
    "auto (cut off full-width bars at the top/bottom of the screen, blur the rest), blur, "
    "crop (cut off bands along an edge, blur the rest) or block (solid colour)"
)


def parse(value) -> str:
    """A style name (STYLES); ToolError otherwise."""
    v = str(value).strip().lower() if isinstance(value, str) else None
    if v not in STYLES:
        raise ToolError(f"redact style {value!r} is not one of {'|'.join(STYLES)}")
    return v


def brand_default(ref) -> str:
    """The brand's redaction style ("auto" for the bundled example and invalid brands:
    render reports a broken brand elsewhere)."""
    try:
        tokens = brand.load(ref if isinstance(ref, str) and ref else brand.EXAMPLE_ID)
    except brand.BrandError:
        return AUTO
    sec = tokens.get("redaction")
    return sec.get("style", AUTO) if isinstance(sec, dict) else AUTO


def resolved(data: dict) -> str:
    """The style of job.json `data`: its "redact_style", else the brand's default.
    ToolError for an invalid "redact_style" (fail closed: nothing renders on a guess)."""
    if data.get("redact_style") is not None:
        try:
            return parse(data["redact_style"])
        except ToolError as e:
            raise ToolError(f'job.json "redact_style": {e}') from None
    return brand_default(data.get("brand"))


def screenshot(style: str) -> str:
    """The style screenshot_frame items get: "block" or "blur" (crop and auto blur)."""
    return "block" if style == "block" else "blur"
