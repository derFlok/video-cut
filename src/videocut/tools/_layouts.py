"""Product-demo layouts of `videocut tool render`: per EDL segment "layout" (BRIEF §6).

The geometry follows the output size (the job's format, _format.py); its shape decides
(`shape`: "portrait" h/w > 1.2, "landscape" w/h > 1.2, "square" otherwise). Portrait
(9:16, 4:5):

    face    the camera, framed as before (render's original path, unchanged)
    screen  the ORIGINAL screen recording (job.json "screen", not the proxy) fills the frame
            width, vertically centred within the safe zones, on the brand's colors.bg
    pip     like "screen", plus the speaker as a rounded inset (face-centred square crop of
            the camera, PIP_SIZE of the width) below the view, so it never covers screen
            content: the view sits at the top of the safe area, the inset below it in the
            corner on the side away from the zoom targets; the strip below the inset is
            left free for the captions
    split   screen view in the top SPLIT_TOP of the frame, the speaker below (face-centred)

Landscape (16:9) and square (1:1):

    screen  the view fills the frame (landscape) or the band between the top and bottom
            safe zones (square) at that area's aspect: the screen is cropped to it, not
            letterboxed. Only a screen whose aspect differs from the area's by more than
            LETTERBOX (e.g. a portrait phone screen in 16:9) is fitted into it instead,
            with bars in colors.bg
    pip     like "screen", plus the speaker as a round bubble (PIP_BUBBLE of the frame
            height) in the bottom-right corner, just above the caption zone (bottom safe
            zone) and inside the side zone; in the bottom-left corner when that one covers
            clearly less of the zoom targets (by more than CORNER_TIE of the bubble)
    split   landscape: side by side, the screen view filling the left SPLIT_SIDE of the
            width (fitted, vertically centred, when the aspects differ by more than
            LETTERBOX_SPLIT), the speaker filling the right part (face-centred); square:
            stacked like portrait

Speaker crop (pip inset, split speaker part): centred on the face (the segment's own face
median when it has enough face frames, else the global one) and as tight as head and
shoulders need (face height x PIP_FACE), but never so tight that the part shows fewer than
SPEAKER_MIN_PX source px per output px at the EDL size; the EDL crop zoom (max 2.0) does
not limit it. Without a face: portrait pip a square of PIP_NO_FACE x the camera's short
side, otherwise the segment's crop. Every speaker crop's source px per output px is
reported (plan_all "speaker"), a warning when it upscales (< 1).

B-roll (every job type, tools/_broll.py plans it from the human's choices):

    broll        the chosen material fills the frame: centre-cropped to the output aspect
                 (cover, never letterboxed); an image gets a slow Ken Burns push-in of
                 BROLL_PUSH over the range; a video clip runs from its start, muted (the
                 camera's audio goes on), looped when it is shorter than the range by less
                 than BROLL_LOOP_S, otherwise it ends and the framed camera shows for the
                 rest of the range. HDR clips are tonemapped like the camera, everything
                 is converted to the reel's matrix

Readability (`readability`): UI text of a screen recording is taken as TEXT_PT logical px
high, ui_scale(screen) source px per logical px (HiDPI recordings, long side >= HIDPI_MIN,
count 2). Its estimated height in output px for a window w source px wide shown in a view
v px wide is TEXT_PT * ui_scale * v / w; below TEXT_MIN_PX it is flagged
`screen_text_small` (information for the cut agent, never a failure: zoom closer, or pick
a wider format).

Slides (every job type, tools/slides.py renders one opaque clip per slide id):

    slide        the slide clip fills the frame; the speaker is a round bubble
                 (`inset_side`, or the brand token pip.size) in a bottom corner above the
                 bottom safe zone (right, or pip.corner "left"); the slide's content goes
                 above the bubble or beside it, whichever lets its text be larger (the
                 renderer tries both, `areas`)
    slide_split  portrait/square: the slide in the top SPLIT_TOP, the speaker below
                 (face-centred, like split); landscape: the slide in the left SPLIT_SIDE,
                 the speaker right of it
    slide_full   the slide clip fills the frame (the voice goes on)

The content area keeps inside the safe zones and SLIDE_GAP of the short side away from the
speaker (`slide_geometry`). A stacked slide_split (portrait/square) keeps SLIDE_SEAM of the
short side free above the speaker part instead: the captions sit on the seam between the
two parts (`slide_seam`; their upper half over that empty margin, the lower half over the
top of the speaker part), so they never cover the slide's content nor the speaker's mouth.
A slide on several segments shows its clip continuously: each range starts at the clip
frame where the previous range of that slide ended (its clock runs on in output time, no
restart at the cut).

Time: segment in/out stay CAMERA time; the screen frame shown at camera time t is screen
time t + work/sync.json offset_s (+ drift_ppm applied at the segment start). Screen time 0
is the recording's first video frame (setpts=PTS-STARTPTS, like the proxy and sync). The
screen is put on the output frame grid once (fps, start_time=0: sample and hold, so a
variable-frame-rate recording with long static gaps keeps its timing) and cut by frame
number; before the recording starts the view is
the background colour, after it ends the last frame is held.

Zoom: "screen_zoom" keyframes [{"t": camera_s, "box": [x0, y0, x1, y1]}] (normalised
screen coords). Each key's box becomes a window of the view's aspect around the box centre
(kept inside the frame); the view ARRIVES at it at t, easing (smoothstep) from the previous
window over the EASE_S before t (shorter when keys are closer). Before the first key the
widest window of the view's aspect shows ("no zoom", `full_width`): the whole screen when
the view has the screen's aspect; in a filled view of another aspect the full width (or
height) of the screen, placed around the segment's first zoom target (centred without
one), so the move towards it only zooms. Zoom factors are relative to that window. Window
edges are interpolated linearly in the eased parameter, so every window edge moves
monotonically between two keys and windows stay inside the frame.
A segment with one window crops it and scales it to the view; a moving window goes through
`perspective` (sense=source, eval=frame), whose corner expressions are the same piecewise
function of the frame number `in` that `window_at` computes in Python, then a fixed scale.

Sharpness: a window narrower than the view's width at the EDL size would upscale the
screen (< 1 source px per output px); such a key is widened to that width and reported
(render.json "screen_zoom_clamped").

Redaction: "redact" [{"t0", "t1": camera_s, "box"}] are drawn on the screen frames BEFORE
scaling, on every output frame that overlaps [t0, t1] plus one frame (and the drift over
the segment) on each side, snapped outward to even source px, in the job's redact style
(_redact_style.py): "block" a solid block in the brand's colors.bg; "blur" the box
pixelated (cells of `blur_cell` source px, at least the UI text height) and blurred
(gaussian, sigma half a cell), so it keeps its colours and no text can be read; "crop" a
band along a screen edge (`band_edge`: touching it within EDGE_TOL, spanning FULL_SPAN of
the other side; "auto" only top/bottom bands) is left out of the screen for the whole
segment: the segment's screen is the rest of the recording (Segment.origin, Segment.screen)
and the view is laid out for that, so zoom windows, targets and the other blocks are in
its coordinates. A crop that would remove more than CROP_MAX of the screen's height (or
width) blurs those bands instead (noted per block in Segment.redaction, render.json).
"""

import json
import math
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

from .. import brand
from .. import edl as edl_mod
from . import _redact_style
from ._format import safe_zones
from ._geometry import Window, crop_window, overlap
from .common import ToolError, binary, read_job, run
from .face import segment_box

LAYOUTS = edl_mod.LAYOUTS
EASE_S = 0.6  # length of a zoom move
PIP_SIZE = 0.34  # portrait: inset side, fraction of the output width
PIP_RADIUS = 0.22  # portrait: inset corner radius, fraction of its side
PIP_BUBBLE = 0.26  # landscape/square: inset side, fraction of the output height
BUBBLE_RADIUS = 0.5  # landscape/square: a circle
CORNER_TIE = 0.05  # landscape/square: corners covering zoom targets within this share of
# the bubble's area of each other are a tie (a centred target: the bubble stays right)
PIP_FACE = 2.6  # speaker crop height = face box height x this (head and shoulders)
PIP_NO_FACE = 0.8  # without a face: crop side = this x the camera's short side
SPEAKER_MIN_PX = 0.75  # speaker crop: at least this many source px per output px
SPLIT_TOP = 0.58  # portrait/square split: the screen part, fraction of the output height
SPLIT_SIDE = 0.64  # landscape split: the screen part, fraction of the output width
TEXT_PT = 13  # typical UI text height, logical px
TEXT_MIN_PX = 18  # UI text lower than this on the output is hard to read
VIEW_FILLS = 0.8  # a screen view this share of the frame's width and height fills it
LETTERBOX = 0.25  # landscape/square: a screen whose aspect differs from the view area's by
# more than this (larger / smaller - 1) is fitted with bars instead of filling the area
LETTERBOX_SPLIT = 0.5  # the same for the narrower screen part of a landscape split
HIDPI_MIN = 2700  # recordings with a long side this large count as 2x (HiDPI) screens
EDGE_TOL = 0.01  # a redact box this close to a screen edge touches it (crop)
FULL_SPAN = 0.9  # ... and is a band when it spans this share of the other side
CROP_MAX = 0.2  # a segment's crop removes at most this share of the screen height (width)
BLUR_DIV = 4  # blur cells are at least the box's short side / this (and the text height),
BLUR_MAX_TEXT = 3  # ... that part at most this many text heights (a panel stays a blur)
DEFAULT_BRAND = brand.EXAMPLE_ID
SLIDE_LAYOUTS = edl_mod.SLIDE_LAYOUTS
SLIDE_GAP = 0.03  # slide content keeps this share of the short side off the speaker
# Stacked slide_split: the free margin above the seam (captions there), share of the short
# side in tall frames (h/w >= 1.6, renderer style.frame `tall`: larger captions) / others.
SLIDE_SEAM = (0.12, 0.09)
PIP_TOKEN_SIZE = (0.15, 0.45)  # brand pip.size is clamped to this range
BROLL_PUSH = 0.05  # Ken Burns: an image grows by this share over its range
BROLL_LOOP_S = 1.0  # a clip shorter than its range by less than this loops, else it ends
BROLL_UPSCALE = 2  # the image is scaled this much larger than the frame before the push


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def box(self) -> list[float]:
        return [self.x, self.y, self.x + self.w, self.y + self.h]


# A screen window: left, top, width in source px (height = width / view aspect).
Win = tuple[float, float, float]


@dataclass
class Move:
    start: int  # local output frame where the move starts
    end: int  # frame where it arrives (window `win` from here on)
    win: Win


@dataclass
class Segment:
    """How one non-face segment is drawn; frames are local to the segment."""

    layout: str
    frames: int
    screen_frame: int  # screen frame (on the output grid) of local frame 0; may be < 0
    initial: Win
    moves: list[Move]
    redact: list[tuple[int, int, int, int, int, int]]  # solid blocks (n0, n1, x, y, w, h)
    view: Rect
    inset: Rect | None = None
    face: Rect | None = None  # split: where the speaker goes
    camera: Window | None = None  # camera crop for the inset / the speaker part
    radius: float = PIP_RADIUS  # inset corner radius, fraction of its side
    speaker_px: float | None = None  # camera source px per output px of the speaker part
    focus: list[list[float]] = field(default_factory=list)  # zoom targets, output px
    focus_at: list[int] = field(default_factory=list)  # local frame each target is reached
    screen: tuple[int, int] = (0, 0)  # size of the screen shown (the recording minus a crop)
    origin: tuple[int, int] = (0, 0)  # where that screen starts in the recording (crop)
    full_screen: tuple[int, int] | None = None  # size of the recording (None: `screen`)
    blur: list[tuple[int, int, int, int, int, int, int]] = field(default_factory=list)
    # ^ blurred boxes (n0, n1, x, y, w, h, cell): source px of `screen`, like `redact`
    redaction: list[dict] = field(default_factory=list)  # per redact block: style used
    planned: list[tuple[int, int, int, int, int, int]] = field(default_factory=list)
    # ^ the boxes before _redact_grow (n0, n1, x, y, w, h) of the blocked and blurred ones


@dataclass
class SlideGeo:
    """Where a slide layout puts the slide clip and the speaker in an output frame."""

    layout: str
    rect: Rect  # the slide clip's place (its size is the clip size)
    area: list[int]  # the slide's content box, px of the frame (the largest candidate)
    speaker: Rect | None = None  # slide: the bubble; slide_split: the speaker part
    round: bool = False  # the speaker is a circle (the bubble)
    areas: list[list[int]] = field(default_factory=list)  # every candidate content box


@dataclass
class SlideSeg:
    """How one slide range is drawn; frames are local to the range."""

    layout: str
    frames: int
    slide: str  # slide id
    clip_frame: int  # frame of the slide clip shown on local frame 0
    geo: SlideGeo
    camera: Window | None = None  # camera crop for the bubble / the speaker part
    speaker_px: float | None = None  # camera source px per output px of the speaker


@dataclass
class BrollSeg:
    """How one b-roll range is drawn; frames are local to the range."""

    frames: int
    wish: int
    candidate: str
    kind: str  # "video" | "image"
    file: Path
    size: tuple[int, int]  # the material's display size
    shown: int  # frames that show the material (the rest: the framed camera)
    loop: bool = False  # the clip is looped to fill the range
    sdr: str = ""  # tonemap chain of an HDR clip
    ai_generated: bool = False


# --- job inputs ---------------------------------------------------------------------------


def screen_source(job: Path) -> Path:
    rel = read_job(job).get("screen")
    if not isinstance(rel, str) or not rel:
        raise ToolError('the EDL uses screen layouts but job.json has no "screen" recording')
    p = (job / rel).resolve()
    if not (p.is_relative_to(job) and p.is_file()):
        raise ToolError(f"screen recording {rel!r} is missing or outside the job folder")
    return p


def sync(job: Path) -> dict:
    """The valid work/sync.json of the job (edl.read_sync); screen layouts need it."""
    data = edl_mod.read_sync(job / "work" / "sync.json")
    if data is None:
        raise ToolError(
            "a valid work/sync.json is needed for screen layouts; "
            "run `videocut tool sync` (or `tool sync --offset <s>`) first"
        )
    return data


def background(job: Path) -> str:
    """colors.bg of the job's brand (default _example) as an ffmpeg colour 0xRRGGBB."""
    ref = read_job(job).get("brand") or DEFAULT_BRAND
    if not isinstance(ref, str):
        raise ToolError('job.json "brand" must be a brand id')
    try:
        tokens = brand.load(ref)
    except brand.BrandError as e:
        raise ToolError(str(e)) from None
    return "0x" + tokens["colors"]["bg"].lstrip("#").upper()


def probe_screen(path: Path) -> dict:
    """Display size, duration, fps and colour tags of the screen recording (a recording
    without a microphone has no audio, so ingest.probe does not fit)."""
    out = run(
        [binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_streams"]
        + ["-show_format", "-of", "json", str(path)]
    )
    info = json.loads(out.stdout)
    v = (info.get("streams") or [None])[0]
    if not v:
        raise ToolError("the screen recording has no video stream")
    rot = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"]))) % 360
    w, h = int(v["width"]), int(v["height"])
    if rot in (90, 270):
        w, h = h, w
    return {
        "size": [w, h],
        "duration": float(info.get("format", {}).get("duration") or v.get("duration") or 0),
        "video_start": float(v.get("start_time") or 0),  # screen time 0 = this pts
        "color_transfer": v.get("color_transfer"),
        "color_primaries": v.get("color_primaries"),
        "color_space": v.get("color_space"),
    }


# --- geometry -----------------------------------------------------------------------------


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def shape(out: tuple[int, int]) -> str:
    """ "portrait" (h/w > 1.2), "landscape" (w/h > 1.2) or "square"."""
    w, h = out
    return "portrait" if h / w > 1.2 else "landscape" if w / h > 1.2 else "square"


def _stacked(layout: str, out: tuple[int, int]) -> bool:
    """Whether `layout` stacks the view above the speaker / inset (portrait rules)."""
    form = shape(out)
    return form == "portrait" or (form == "square" and layout == "split")


def _fit(area: Rect, screen: tuple[int, int]) -> Rect:
    """The largest even-sized rect of the screen's aspect centred in `area`."""
    w, h = area.w // 2 * 2, _even(area.w * screen[1] / screen[0])
    if h > area.h:
        w, h = min(_even(area.h * screen[0] / screen[1]), area.w // 2 * 2), area.h // 2 * 2
    return Rect(area.x + (area.w - w) // 2, area.y + (area.h - h) // 2, w, h)


def _area(layout: str, out: tuple[int, int]) -> Rect:
    """Landscape/square (not stacked): the area the screen view fills or is fitted into."""
    ow, oh = out
    z = safe_zones(out)
    if layout == "split":  # landscape: the left part
        return Rect(0, 0, _even(ow * SPLIT_SIDE), oh)
    if shape(out) == "square":
        return Rect(0, z["top"], ow, oh - z["top"] - z["bottom"])
    return Rect(0, 0, ow, oh)


def filled(layout: str, out: tuple[int, int], screen: tuple[int, int]) -> bool:
    """Whether the `layout` view fills its area at the area's aspect (landscape/square,
    the screen's aspect within LETTERBOX / LETTERBOX_SPLIT of it) rather than showing the
    screen at its own aspect."""
    if _stacked(layout, out):
        return False
    area = _area(layout, out)
    a, b = area.w / area.h, screen[0] / screen[1]
    return max(a, b) / min(a, b) - 1 <= (LETTERBOX_SPLIT if layout == "split" else LETTERBOX)


def view_rect(layout: str, out: tuple[int, int], screen: tuple[int, int]) -> Rect:
    """Where the screen view goes in an `out`-sized frame (aspect of the screen, or of the
    area it fills: `filled`)."""
    ow, oh = out
    z = safe_zones(out)
    if not _stacked(layout, out):
        area = _area(layout, out)
        if filled(layout, out, screen):
            return Rect(area.x, area.y, area.w // 2 * 2, area.h // 2 * 2)
        return _fit(area, screen)
    top, bottom = z["top"], (round(oh * SPLIT_TOP) if layout == "split" else oh - z["bottom"])
    below = _even(ow * PIP_SIZE) + _gap(out) if layout == "pip" else 0  # room for the inset
    room = bottom - top - below
    w, h = ow, _even(ow * screen[1] / screen[0])
    if h > room:  # a tall screen: fit the height instead
        w, h = _even(room * screen[0] / screen[1]), room // 2 * 2
    y = top if layout == "pip" else top + (room - h) // 2  # pip: caption room below the inset
    return Rect((ow - w) // 2, y, w, h)


def _gap(out: tuple[int, int]) -> int:
    return safe_zones(out)["side"] // 2


def fills(view: Rect, out: tuple[int, int]) -> bool:
    """Whether the screen view (nearly) fills the frame (VIEW_FILLS of both sides): then
    the screen is the picture itself and overlays keep off its zoom targets, not all of it."""
    return view.w >= VIEW_FILLS * out[0] and view.h >= VIEW_FILLS * out[1]


def inset_side(out: tuple[int, int]) -> int:
    """Side of the pip inset in px."""
    return _even(out[0] * PIP_SIZE) if _stacked("pip", out) else _even(out[1] * PIP_BUBBLE)


def inset_rects(out: tuple[int, int], view: Rect) -> list[Rect]:
    """The pip inset's places (right first), inside the safe zones: portrait below the pip
    `view`, landscape/square in the bottom corners above the caption zone."""
    ow, oh = out
    z = safe_zones(out)
    s = inset_side(out)
    y = view.y + view.h + _gap(out) if _stacked("pip", out) else oh - z["bottom"] - s
    return [Rect(ow - z["side"] - s, y, s, s), Rect(z["side"], y, s, s)]


def speaker_rect(out: tuple[int, int]) -> Rect:
    """Split: where the speaker goes (below the screen part, or right of it)."""
    ow, oh = out
    if _stacked("split", out):
        top = round(oh * SPLIT_TOP)
        return Rect(0, top, ow, oh - top)
    left = _even(ow * SPLIT_SIDE)
    return Rect(left, 0, ow - left, oh)


def _pip_token(tokens: dict | None) -> tuple[str, float | None]:
    """(corner "right"|"left", size fraction or None) from the brand's optional `pip`
    token {"corner", "size"} (size: of the width in portrait, of the height otherwise)."""
    pip = (tokens or {}).get("pip")
    pip = pip if isinstance(pip, dict) else {}
    corner = "left" if str(pip.get("corner", "")).lower().startswith("left") else "right"
    size = pip.get("size")
    ok = isinstance(size, int | float) and not isinstance(size, bool) and math.isfinite(size)
    return corner, (min(max(float(size), PIP_TOKEN_SIZE[0]), PIP_TOKEN_SIZE[1]) if ok else None)


def slide_geometry(layout: str, out: tuple[int, int], tokens: dict | None = None) -> SlideGeo:
    """The SlideGeo of a slide `layout` in an `out`-sized frame (brand `tokens` for pip)."""
    ow, oh = out
    z = safe_zones(out)
    gap = _even(min(out) * SLIDE_GAP)
    full = Rect(0, 0, ow, oh)
    if layout == "slide_full":
        return SlideGeo(layout, full, [z["side"], z["top"], ow - z["side"], oh - z["bottom"]])
    if layout == "slide":
        corner, size = _pip_token(tokens)
        if size is None:
            side = inset_side(out)
        else:
            side = _even((ow if _stacked("pip", out) else oh) * size)
        y = oh - z["bottom"] - side
        right = corner == "right"
        x = ow - z["side"] - side if right else z["side"]
        above = [z["side"], z["top"], ow - z["side"], y - gap]
        beside = (
            [z["side"], z["top"], x - gap, oh - z["bottom"]]
            if right
            else [x + side + gap, z["top"], ow - z["side"], oh - z["bottom"]]
        )
        areas = sorted((above, beside), key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
        return SlideGeo(layout, full, areas[0], Rect(x, y, side, side), True, areas)
    if layout != "slide_split":
        raise ValueError(f"not a slide layout: {layout}")
    part = speaker_rect(out)
    if _stacked("split", out):
        rect = Rect(0, 0, ow, part.y)
        seam = SLIDE_SEAM[0] if oh / ow >= 1.6 else SLIDE_SEAM[1]
        area = [z["side"], z["top"], ow - z["side"], part.y - _even(min(out) * seam)]
    else:
        rect = Rect(0, 0, part.x, oh)
        area = [z["side"], z["top"], part.x - gap, oh - z["bottom"]]
    return SlideGeo(layout, rect, area, part)


def slide_seam(layout: str, out: tuple[int, int]) -> int | None:
    """Output y of the seam between the slide part and the speaker part of a stacked
    slide_split (where its captions go), else None."""
    if layout != "slide_split" or not _stacked("split", out):
        return None
    return speaker_rect(out).y


def view_aspect(layout: str, out: tuple[int, int], screen: tuple[int, int]) -> float:
    """w / h of the screen windows of a `layout` view: the view's own aspect when it fills
    its area, else the screen's (the fitted view has it up to even rounding)."""
    if filled(layout, out, screen):
        v = view_rect(layout, out, screen)
        return v.w / v.h
    return screen[0] / screen[1]


def full_width(screen: tuple[int, int], aspect: float) -> float:
    """Width in source px of the widest window of `aspect` inside the screen (no zoom)."""
    return min(screen[0], screen[1] * aspect)


def window_for(
    box: list[float], screen: tuple[int, int], aspect: float, min_w: float
) -> tuple[Win, float, bool]:
    """Window (left, top, width; source px, `aspect` = w/h) around `box`, at least `min_w`
    wide, inside the frame: (window, the zoom the box asked for, whether it was clamped)."""
    sw, sh = screen
    full = full_width(screen, aspect)
    bw, bh = (box[2] - box[0]) * sw, (box[3] - box[1]) * sh
    want = min(max(bw, bh * aspect), full)
    w = min(max(want, min_w), full)
    h = w / aspect
    cx, cy = (box[0] + box[2]) / 2 * sw, (box[1] + box[3]) / 2 * sh
    x = min(max(cx - w / 2, 0.0), sw - w)
    y = min(max(cy - h / 2, 0.0), sh - h)
    return (x, y, w), full / want, want < min_w - 1e-6


def _smooth(u: float) -> float:
    return u * u * (3 - 2 * u)


def window_at(seg: Segment, n: int) -> Win:
    """Screen window of local frame n (the reference the ffmpeg expressions implement)."""
    v = seg.initial
    for m in seg.moves:
        if n >= m.end:
            v = m.win
        elif n > m.start:
            k = _smooth((n - m.start) / (m.end - m.start))
            return tuple(a + (b - a) * k for a, b in zip(v, m.win, strict=True))
        else:
            break
    return v


def _num(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def window_expr(seg: Segment, i: int, frame: str) -> str:
    """ffmpeg expression of component i (0 left, 1 top, 2 width) of window_at(seg, frame)."""
    prev = seg.initial[i]
    parts = []
    for m in seg.moves:
        target = m.win[i]
        if m.end > m.start:
            u = f"(({frame})-{m.start})/{m.end - m.start}"
            ease = f"{_num(prev)}+({_num(target - prev)})*{u}*{u}*(3-2*{u})"
            parts.append((m.start, _num(prev)))
            parts.append((m.end, ease))
        else:
            parts.append((m.end, _num(prev)))
        prev = target
    expr = _num(prev)
    for limit, value in reversed(parts):
        expr = f"if(lt({frame},{limit}),{value},{expr})"
    return expr


# --- planning -----------------------------------------------------------------------------


def band_edge(box: list[float], style: str) -> str | None:
    """The screen edge a redact `box` (normalised) is a band along, for redact `style`:
    "top"/"bottom" (touches it, spans FULL_SPAN of the width) for auto and crop,
    "left"/"right" for crop only; None when it is not cut off but blurred or blocked."""
    if style not in ("auto", "crop"):
        return None
    x0, y0, x1, y1 = box
    if x1 - x0 >= FULL_SPAN:
        if y0 <= EDGE_TOL:
            return "top"
        if y1 >= 1 - EDGE_TOL:
            return "bottom"
    if style == "crop" and y1 - y0 >= FULL_SPAN:
        if x0 <= EDGE_TOL:
            return "left"
        if x1 >= 1 - EDGE_TOL:
            return "right"
    return None


def crop_plan(
    boxes: list[list[float]], screen: tuple[int, int], style: str
) -> tuple[tuple[int, int, int, int], dict[int, str], dict[int, str]]:
    """((x, y, w, h) of the recording a segment shows, {box index: edge} of the boxes cut
    off, {box index: note} of bands blurred instead because the crop would remove more
    than CROP_MAX of the screen's height or width). The kept area is snapped inward to
    even px, so every band is gone completely."""
    cut = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    edges = {}
    for j, b in enumerate(boxes):
        e = band_edge(b, style)
        if e:
            edges[j] = e
            far = {"top": b[3], "bottom": 1 - b[1], "left": b[2], "right": 1 - b[0]}[e]
            cut[e] = max(cut[e], far)
    notes = {}
    for side, pair in (("height", ("top", "bottom")), ("width", ("left", "right"))):
        share = cut[pair[0]] + cut[pair[1]]
        if share > CROP_MAX:
            for j in [j for j, e in edges.items() if e in pair]:
                notes[j] = (
                    f"cropping would remove {share:.0%} of the screen {side} "
                    f"(more than {CROP_MAX:.0%}): blurred instead"
                )
                del edges[j]
            cut[pair[0]] = cut[pair[1]] = 0.0
    sw, sh = screen
    x0, y0 = math.ceil(cut["left"] * sw / 2) * 2, math.ceil(cut["top"] * sh / 2) * 2
    x1 = math.floor((1 - cut["right"]) * sw / 2) * 2 if cut["right"] else sw
    y1 = math.floor((1 - cut["bottom"]) * sh / 2) * 2 if cut["bottom"] else sh
    return (x0, y0, x1 - x0, y1 - y0), edges, notes


def blur_cell(w: int, h: int, screen: tuple[int, int]) -> int:
    """Pixelation cell (source px, even) of a blurred w x h box: the UI text height of the
    recording (TEXT_PT x ui_scale), or the box's short side / BLUR_DIV up to BLUR_MAX_TEXT
    text heights when that is more (large text in a box sized to it). Render's calibration
    makes it larger where text recognition still reads something."""
    text = TEXT_PT * ui_scale(screen)
    cell = max(text, min(min(w, h) / BLUR_DIV, BLUR_MAX_TEXT * text))
    return max(2, math.ceil(cell / 2) * 2)


def plan_segment(
    seg,
    frames: int,
    fps: Fraction,
    *,
    offset: float,
    screen: tuple[int, int],
    camera: tuple[int, int],
    out: tuple[int, int],
    full_out: tuple[int, int],
    face: dict | None,
    redact_slack: int = 1,
    redact_style: str = "block",
) -> tuple[Segment, list[dict]]:
    """The drawn form of one non-face EDL segment `seg` (edl.Segment, already framed: its
    layout, screen_zoom and redact were validated by edl.py) and its clamped zoom keys.
    `redact_style`: how its redact blocks are drawn (_redact_style.STYLES)."""
    layout = seg.layout
    blocks = []  # (n0, n1, block) of the redact blocks shown in this segment
    for r in seg.redact:
        n0 = math.floor((r["t0"] - seg.start) * fps) - redact_slack
        n1 = math.ceil((r["t1"] - seg.start) * fps) - 1 + redact_slack
        if n1 >= 0 and n0 < frames:
            blocks.append((max(n0, 0), min(n1, frames - 1), r))
    full_screen = screen
    kept, edges, notes = crop_plan([r["box"] for _, _, r in blocks], screen, redact_style)
    ox, oy, sw, sh = kept
    screen = (sw, sh)

    def inside(b: list[float]) -> list[float]:
        """Normalised box of the recording -> of the screen shown (clamped, not empty)."""
        out = []
        for a, b2, o, n, size in ((b[0], b[2], ox, sw, full_screen[0]),
                                  (b[1], b[3], oy, sh, full_screen[1])):  # fmt: skip
            lo, hi = (min(max((v * size - o) / n, 0.0), 1.0) for v in (a, b2))
            if hi - lo < 1e-3:  # wholly in the cropped band: a sliver at its edge
                lo = min(lo, 1 - 1e-3)
                hi = lo + 1e-3
            out.append((lo, hi))
        return [out[0][0], out[1][0], out[0][1], out[1][1]]

    zoom = [{**k, "box": inside(k["box"])} for k in seg.screen_zoom] if edges else seg.screen_zoom
    view = view_rect(layout, out, screen)
    sharp_w = view_rect(layout, full_out, screen).w  # >= 1 source px per output px
    aspect = view_aspect(layout, out, screen)  # the view is this aspect up to even rounding
    sw, sh = screen
    full = full_width(screen, aspect)
    initial: Win = ((sw - full) / 2, (sh - full / aspect) / 2, full)
    if zoom and filled(layout, out, screen):  # no zoom, around the first target
        first = min(zoom, key=lambda k: k["t"])
        initial = window_for(first["box"], screen, aspect, full)[0]
    min_w = min(float(sharp_w), full)

    moves, clamped, keys = [], [], []
    ease = max(1, round(EASE_S * fps))
    for k in sorted(zoom, key=lambda k: k["t"]):
        win, zoom, was = window_for(k["box"], screen, aspect, min_w)
        if was:
            clamped.append(
                {"t": k["t"], "zoom": round(zoom, 2), "max_zoom": round(full / min_w, 2)}
            )
        f = min(max(round((k["t"] - seg.start) * fps), 0), frames)
        keys.append((f, win, k["box"]))
    prev_end = 0
    for f, win, _ in keys:
        if f <= 0:
            initial, moves = win, []
            continue
        start = max(prev_end, f - ease)
        moves.append(Move(start, f, win))
        prev_end = f

    redact, blur, log, planned = [], [], [], []
    for j, (n0, n1, r) in enumerate(blocks):
        b = r["box"]
        entry = {"t0": r["t0"], "t1": r["t1"], "box": list(b),
                 **{k: r[k] for k in ("planned", "grown") if k in r}}  # fmt: skip
        if j in edges:
            log.append({**entry, "style": "crop", "edge": edges[j]})
            continue
        x0, y0, x1, y1 = _shown_px(b, full_screen, (ox, oy), screen)
        if x1 <= x0 or y1 <= y0:
            log.append({**entry, "style": "crop", "note": "inside the cropped band"})
            continue
        p0, q0, p1, q1 = _shown_px(r.get("planned", b), full_screen, (ox, oy), screen)
        if p1 > p0 and q1 > q0:
            planned.append((n0, n1, p0, q0, p1 - p0, q1 - q0))
        if redact_style == "block":
            redact.append((n0, n1, x0, y0, x1 - x0, y1 - y0))
            log.append({**entry, "style": "block"})
            continue
        cell = blur_cell(x1 - x0, y1 - y0, full_screen)
        blur.append((n0, n1, x0, y0, x1 - x0, y1 - y0, cell))
        log.append(
            {**entry, "style": "blur", "cell": cell, **({"note": notes[j]} if j in notes else {})}
        )

    # Zoom targets in output px (for keeping the inset off them).
    focus = []
    for _, win, box in keys:
        x, y, w = win
        k = view.w / w
        focus.append([view.x + (box[0] * sw - x) * k, view.y + (box[1] * sh - y) * k,
                      view.x + (box[2] * sw - x) * k, view.y + (box[3] * sh - y) * k])  # fmt: skip

    s = Segment(layout, frames, round((seg.start + offset) * fps), initial, moves, redact, view)
    s.focus = focus
    # from the start of the move towards each target (the box is on its way there)
    s.focus_at = [max(f - ease, 0) for f, _, _ in keys]
    s.screen, s.origin, s.blur, s.redaction = screen, (ox, oy), blur, log
    s.full_screen, s.planned = full_screen, planned
    crop = seg.crop
    cx, cy = (crop.cx, crop.cy) if crop else (0.5, 0.5)
    box = segment_box(face, seg.start, seg.end) if face else None
    if layout == "pip":
        right, left = inset_rects(out, view)
        if _stacked(layout, out):
            # The side away from the zoom targets (their mean centre); right without any.
            mid = sum((b[0] + b[2]) / 2 for _, _, b in keys) / len(keys) if keys else 0
            s.inset = left if mid > 0.5 else right
        else:  # the corner that covers clearly less of the zoom targets; right on a tie
            hidden = [sum(overlap(r.box(), f) for f in focus) for r in (right, left)]
            s.inset = left if hidden[1] < hidden[0] - CORNER_TIE * right.w * right.h else right
            s.radius = BUBBLE_RADIUS
        side = inset_side(full_out)
        if box is None and _stacked(layout, out):
            s.camera = _inset_crop(camera, cx, cy, face)
        else:
            s.camera = speaker_crop(camera, (side, side), box, cx, cy, crop.zoom if crop else 1.0)
        s.speaker_px = round(s.camera.h / side, 2)
    elif layout == "split":
        s.face = speaker_rect(out)
        part = speaker_rect(full_out)
        zoom = crop.zoom if crop else 1.0
        s.camera = speaker_crop(camera, (part.w, part.h), box, cx, cy, zoom)
        s.speaker_px = round(s.camera.h / part.h, 2)
    return s, clamped


def speaker_crop(
    camera: tuple[int, int],
    part: tuple[int, int],
    box: list[float] | None,
    cx: float,
    cy: float,
    zoom: float,
) -> Window:
    """Camera crop of the aspect of `part` (the speaker part at the EDL size) centred on the
    face `box` (normalised), face height x PIP_FACE high, at least SPEAKER_MIN_PX source
    px per output px; without a face the segment crop (cx, cy, zoom) as before."""
    if not box:
        return crop_window(camera, part, cx, cy, zoom)
    largest = crop_window(camera, part, 0.5, 0.5, 1.0)
    want = (box[3] - box[1]) * camera[1] * PIP_FACE
    h = min(max(want, part[1] * SPEAKER_MIN_PX), largest.h)
    fx, fy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return crop_window(camera, part, fx, fy, largest.h / h)


def _shown_px(b, full_screen, origin, screen) -> tuple[int, int, int, int]:
    """Normalised box of the recording -> [x0, y0, x1, y1] px of the screen shown: snapped
    outward on the recording, then into the screen shown (a crop's offset is even). Empty
    (x1 <= x0 or y1 <= y0) when it lies wholly inside a cropped band."""
    (fw, fh), (ox, oy), (sw, sh) = full_screen, origin, screen
    x0, y0 = math.floor(b[0] * fw / 2) * 2 - ox, math.floor(b[1] * fh / 2) * 2 - oy
    x1 = min(math.ceil(b[2] * fw / 2) * 2, fw) - ox
    y1 = min(math.ceil(b[3] * fh / 2) * 2, fh) - oy
    return max(x0, 0), max(y0, 0), min(x1, sw), min(y1, sh)


def grown_redaction(job: Path, edl, plan, sync_doc: dict, src: Path, info: dict) -> dict:
    """{EDL segment index: its redact list with every box grown by _redact_grow} for the
    non-face segments of `plan` that have redact blocks."""
    from . import _redact_grow

    todo = []  # (segment index, block index, block with offset)
    for si in sorted({r.seg for r in plan.ranges if edl_mod.is_screen(r.layout)}):
        seg = edl.segments[si]
        off, _ = segment_offset(sync_doc, seg.start, seg.duration, plan.fps)
        todo += [(si, j, {**r, "offset": off}) for j, r in enumerate(seg.redact)]
    grown = _redact_grow.apply(job, [b for _, _, b in todo], src, info, float(plan.fps))
    out: dict[int, list] = {}
    for (si, j, _), g in zip(todo, grown, strict=True):
        reds = out.setdefault(si, [dict(r) for r in edl.segments[si].redact])
        reds[j] = {**reds[j], **g}
    return out


def with_redaction(edl, cands: list[dict]):
    """`edl` (edl.Edl) whose screen segments also carry a redact block for every screen
    candidate of redaction.enforced that overlaps them (the safe default: render, verify).
    A slide segment shows no screen content, so it needs none."""
    from .. import redaction

    segs = []
    for s in edl.segments:
        extra = (
            redaction.screen_blocks(cands, s.start, s.end) if edl_mod.is_screen(s.layout) else []
        )
        extra = [b for b in extra if not redaction.covered(b, s.redact)]
        segs.append(replace(s, redact=[*s.redact, *extra]) if extra else s)
    return replace(edl, segments=segs)


def segment_offset(sync_doc: dict, start: float, duration: float, fps) -> tuple[float, int]:
    """(offset_s for a segment starting at camera time `start`, redaction slack in frames).
    The measured drift is applied at the segment start (offset_s is exact at drift_ref);
    redact ranges grow by one frame on each side (the screen frame grid is rounded) plus
    what the drift adds over the segment."""
    off = sync_doc["offset_s"]
    drift = sync_doc.get("drift_ppm") or 0.0
    ref = sync_doc.get("drift_ref")
    if drift and ref is not None:
        off += drift * 1e-6 * (start - ref)
    return off, 1 + math.ceil(abs(drift) * 1e-6 * duration * float(fps))


def plan_all(
    job: Path,
    edl,
    plan,
    camera: tuple[int, int],
    face: dict | None,
    full_out: tuple[int, int] | None = None,
) -> dict:
    """Every screen range of `plan` (a _render_plan.Plan of the framed `edl`) drawn:
    {"segs": {range index: Segment}, "clamped": [{"segment", "t", "zoom", "max_zoom"}],
    "speaker": [{"segment", "layout", "px_ratio"}], "warnings": [str] (upscaled speaker
    crops), "screen": the recording, "info": probe_screen, "sync": work/sync.json,
    "redact_style": the job's, "redaction": [{"segment", "t0", "t1", "box", "style",
    ...}] how each redact block is drawn}. Shared
    by render, overlays (where the screen view and the speaker are) and verify. `full_out`
    is the job's output size (the geometry follows the job format; `plan` may be a preview
    at a smaller size); default edl.size."""
    full_out = tuple(full_out or edl.size)
    style = _redact_style.resolved(read_job(job))
    src = screen_source(job)
    sync_doc = sync(job)
    info = probe_screen(src)
    screen_frames = math.floor(info["duration"] * plan.fps)
    segs, clamped, speaker, warnings, redaction = {}, [], [], [], []
    grown = grown_redaction(job, edl, plan, sync_doc, src, info)
    for i, r in enumerate(plan.ranges):
        if not edl_mod.is_screen(r.layout):
            continue
        seg_in = edl.segments[r.seg]
        if r.seg in grown:
            seg_in = replace(seg_in, redact=grown[r.seg])
        offset, slack = segment_offset(sync_doc, seg_in.start, seg_in.duration, plan.fps)
        seg, cl = plan_segment(
            seg_in, r.frames, plan.fps, offset=offset, screen=tuple(info["size"]),
            camera=camera, out=plan.out_size, full_out=full_out, face=face,
            redact_slack=slack, redact_style=style,
        )  # fmt: skip
        if seg.screen_frame + seg.frames <= 0 or seg.screen_frame >= screen_frames:
            raise ToolError(
                f"segment {r.seg} ({r.layout}) lies outside the screen recording "
                f"(offset_s {offset})"
            )
        segs[i] = seg
        for b in seg.redaction:  # the same dicts: render's blur calibration updates them
            b["segment"] = r.seg
            redaction.append(b)
        clamped += [{"segment": r.seg, **c} for c in cl]
        if seg.speaker_px is not None:
            speaker.append({"segment": r.seg, "layout": r.layout, "px_ratio": seg.speaker_px})
            if seg.speaker_px < 1:
                warnings.append(
                    f"segment {r.seg} ({r.layout}): the speaker is upscaled "
                    f"({seg.speaker_px} camera px per output px)"
                )
    return {
        "segs": segs, "clamped": clamped, "speaker": speaker, "warnings": warnings,
        "screen": src, "info": info, "sync": sync_doc,
        "redact_style": style, "redaction": redaction,
    }  # fmt: skip


def slide_runs(edl, plan) -> dict[str, list[dict]]:
    """Per slide id, in output order, every range of `plan` that shows it: {"range",
    "segment", "layout", "out_frame" (first output frame), "frames", "clip_frame" (the
    slide clip's frame shown first: the frames of that slide shown before)}."""
    runs: dict[str, list[dict]] = {}
    f0 = 0
    for i, r in enumerate(plan.ranges):
        if edl_mod.is_slide(r.layout):
            sid = edl.segments[r.seg].slide
            have = runs.setdefault(sid, [])
            clip = sum(x["frames"] for x in have)
            have.append({"range": i, "segment": r.seg, "layout": r.layout, "out_frame": f0,
                         "frames": r.frames, "clip_frame": clip})  # fmt: skip
        f0 += r.frames
    return runs


def plan_slides(
    edl,
    plan,
    camera: tuple[int, int],
    face: dict | None,
    full_out: tuple[int, int] | None = None,
    tokens: dict | None = None,
) -> dict:
    """Every slide range of `plan` drawn: {"segs": {range index: SlideSeg}, "speaker":
    [{"segment", "layout", "px_ratio"}], "warnings": [str]}. The speaker (bubble, split
    part) is cropped like pip/split: face-centred, head and shoulders, at least
    SPEAKER_MIN_PX camera px per output px at the job size `full_out`."""
    full_out = tuple(full_out or edl.size)
    segs, speaker, warnings = {}, [], []
    for sid, runs in slide_runs(edl, plan).items():
        for rn in runs:
            i, seg = rn["range"], edl.segments[rn["segment"]]
            geo = slide_geometry(rn["layout"], plan.out_size, tokens)
            s = SlideSeg(rn["layout"], rn["frames"], sid, rn["clip_frame"], geo)
            if geo.speaker is not None:
                part = slide_geometry(rn["layout"], full_out, tokens).speaker
                crop = seg.crop
                cx, cy = (crop.cx, crop.cy) if crop else (0.5, 0.5)
                box = segment_box(face, seg.start, seg.end) if face else None
                zoom = crop.zoom if crop else 1.0
                s.camera = speaker_crop(camera, (part.w, part.h), box, cx, cy, zoom)
                s.speaker_px = round(s.camera.h / part.h, 2)
                speaker.append(
                    {"segment": rn["segment"], "layout": rn["layout"], "px_ratio": s.speaker_px}
                )
                if s.speaker_px < 1:
                    warnings.append(
                        f"segment {rn['segment']} ({rn['layout']}): the speaker is upscaled "
                        f"({s.speaker_px} camera px per output px)"
                    )
            segs[i] = s
    return {"segs": segs, "speaker": speaker, "warnings": warnings}


def ui_scale(screen: tuple[int, int]) -> int:
    """Source px per logical px of a screen recording (heuristic: HiDPI = 2)."""
    return 2 if max(screen) >= HIDPI_MIN else 1


def text_px(screen: tuple[int, int], view_w: float, win_w: float) -> float:
    """Estimated UI text height in output px for a window `win_w` source px wide shown
    `view_w` px wide."""
    return TEXT_PT * ui_scale(screen) * view_w / win_w


def readable_zoom(layout: str, out: tuple[int, int], screen: tuple[int, int]) -> float:
    """Zoom (relative to the no-zoom window, `full_width`) at which UI text reaches
    TEXT_MIN_PX."""
    view = view_rect(layout, out, screen)
    full = full_width(screen, view_aspect(layout, out, screen))
    return TEXT_MIN_PX / text_px(screen, view.w, full)


def max_zoom(layout: str, out: tuple[int, int], screen: tuple[int, int]) -> float:
    """Largest zoom (relative to the no-zoom window) that keeps >= 1 source px per output px
    in the `layout` view of an `out`-sized output; rounded down to 0.01."""
    view = view_rect(layout, out, screen)
    full = full_width(screen, view_aspect(layout, out, screen))
    return math.floor(full / view.w * 100 + 1e-6) / 100


def readability(seg, screen: tuple[int, int], out: tuple[int, int]) -> list[dict]:
    """Per window of the non-face EDL segment `seg` (the whole screen until its first key,
    then each screen_zoom key, sharpness-clamped as render draws it) at the EDL size `out`:
    [{"t", "zoom", "text_px", "readable_zoom", "flags"}]; flag `screen_text_small` when the
    text is lower than TEXT_MIN_PX."""
    view = view_rect(seg.layout, out, screen)
    aspect = view_aspect(seg.layout, out, screen)
    full = full_width(screen, aspect)
    keys = sorted(seg.screen_zoom, key=lambda k: k["t"])
    wins = [] if keys and keys[0]["t"] <= seg.start else [(seg.start, full)]
    for k in keys:
        wins.append((k["t"], window_for(k["box"], screen, aspect, min(view.w, full))[0][2]))
    need = round(readable_zoom(seg.layout, out, screen), 2)
    out_docs = []
    for t, w in wins:
        px = round(text_px(screen, view.w, w), 1)
        flags = ["screen_text_small"] if px < TEXT_MIN_PX else []
        out_docs.append({"t": round(t, 3), "zoom": round(full / w, 2), "text_px": px,
                         "readable_zoom": need, "flags": flags})  # fmt: skip
    return out_docs


def _inset_crop(camera: tuple[int, int], cx: float, cy: float, face: dict | None) -> Window:
    cw, ch = camera
    short = min(cw, ch)
    box = ((face or {}).get("median") or {}).get("box")
    side = (box[3] - box[1]) * ch * PIP_FACE if box else short * PIP_NO_FACE
    side = min(max(_even(side), 64), short - short % 2)
    x = min(max(int(round(cx * cw - side / 2)), 0), cw - side)
    y = min(max(int(round(cy * ch - side / 2)), 0), ch - side)
    return Window(side, side, x, y)


# --- filter graph -------------------------------------------------------------------------


def _t(v: float) -> str:
    return f"{v:.6f}"


def screen_view(seg: Segment, fps: Fraction, bg: str, matrix: str, to_sdr: str) -> list[str]:
    """Filters (no labels) that turn the grid-aligned screen stream into this segment's
    view: cut by frame, padded/held, HDR -> SDR, cropped, redacted with solid blocks,
    zoomed, `view`-sized. Blurred boxes need a branch: `screen_lines`."""
    if seg.blur:
        raise ValueError("a segment with blurred boxes is drawn by screen_lines")
    return [*_screen_in(seg, fps, bg, to_sdr), *_screen_out(seg, fps, bg, matrix)]


def blur_chain(w: int, h: int, cell: int) -> str:
    """Filters (no labels) that pixelate and blur a w x h picture: area-average into cells
    of `cell` px, back to full size without smoothing, then a gaussian of half a cell."""
    cw, ch = max(1, math.ceil(w / cell)), max(1, math.ceil(h / cell))
    return (
        f"scale={cw}:{ch}:flags=area,scale={w}:{h}:flags=neighbor,"
        f"gblur=sigma={_num(cell / 2)}:steps=2"
    )


def screen_lines(
    i: int, seg: Segment, fps: Fraction, bg: str, matrix: str, to_sdr: str, label: str
) -> list[str]:
    """Filter lines that draw the screen view of segment i from [label] as [sv{i}]: like
    screen_view, with every blurred box (`seg.blur`) cut out, pixelated, blurred and put
    back over its frames before the blocks and the zoom."""
    pre, post = _screen_in(seg, fps, bg, to_sdr), _screen_out(seg, fps, bg, matrix)
    if not seg.blur:
        return [f"[{label}]" + ",".join([*pre, *post]) + f"[sv{i}];"]
    k = len(seg.blur)
    lines = [
        f"[{label}]" + ",".join(pre) + f",split={k + 1}[rm{i}]"
        + "".join(f"[rc{i}_{j}]" for j in range(k)) + ";"
    ]  # fmt: skip
    cur = f"rm{i}"
    for j, (n0, n1, x, y, w, h, cell) in enumerate(seg.blur):
        a, b = (n0 - 0.5) / fps, (n1 + 0.5) / fps
        lines += [
            f"[rc{i}_{j}]crop={w}:{h}:{x}:{y},{blur_chain(w, h, cell)}[rb{i}_{j}];",
            f"[{cur}][rb{i}_{j}]overlay=x={x}:y={y}:format=auto"
            f":enable='between(t,{_t(a)},{_t(b)})'[ro{i}_{j}];",
        ]
        cur = f"ro{i}_{j}"
    return [*lines, f"[{cur}]" + ",".join(post) + f"[sv{i}];"]


def _screen_in(seg: Segment, fps: Fraction, bg: str, to_sdr: str) -> list[str]:
    """Cut by frame, padded/held, HDR -> SDR, cropped to the screen shown."""
    f0, n = seg.screen_frame, seg.frames
    lead = min(max(-f0, 0), n)
    first = max(f0, 0)
    f = [
        f"trim=start_frame={first}:end_frame={first + n - lead}",
        "setpts=PTS-STARTPTS",
    ]
    if lead:
        f.append(f"tpad=start={lead}:start_mode=add:color={bg}")
    f += ["tpad=stop_mode=clone:stop=-1", f"trim=end_frame={n}"]
    if to_sdr:
        f.append(to_sdr)
    if seg.full_screen not in (None, seg.screen):  # a band cut off (crop style)
        f.append(f"crop={seg.screen[0]}:{seg.screen[1]}:{seg.origin[0]}:{seg.origin[1]}")
    return f


def _screen_out(seg: Segment, fps: Fraction, bg: str, matrix: str) -> list[str]:
    """Solid blocks, then the zoom window scaled to the view."""
    f = []
    for n0, n1, x, y, w, h in seg.redact:
        a, b = (n0 - 0.5) / fps, (n1 + 0.5) / fps
        f.append(
            f"drawbox=x={x}:y={y}:w={w}:h={h}:color={bg}@1:t=fill"
            f":enable='between(t,{_t(a)},{_t(b)})'"
        )
    vw, vh = seg.view.w, seg.view.h
    scale = f"scale={vw}:{vh}:flags=lanczos:out_color_matrix={matrix}:out_range=tv"
    if not seg.moves:  # one window for the whole segment: a plain crop
        sw, sh = seg.screen
        x, y = (max(0, round(v)) for v in seg.initial[:2])
        w, h = min(int(seg.initial[2]), sw - x), min(int(seg.initial[2] * vh / vw), sh - y)
        return [*f, f"crop={w}:{h}:{x}:{y}", scale, "setsar=1"]
    # Moving window: perspective maps the window's corners (source px, per input frame)
    # onto the whole frame with sub-pixel precision, then one fixed-size scale. Its `in`
    # counts from 1 (the first frame is in=1), the local frame n is in-1.
    # (A scale with eval=frame followed by crop cannot work: crop clamps x/y to the
    # size of its input link, fixed when the graph is configured.)
    x, y, w = (f"({window_expr(seg, i, '(in-1)')})" for i in range(3))
    h = f"({w}*{vh}/{vw})"
    corners = [(x, y), (f"{x}+{w}", y), (x, f"{y}+{h}"), (f"{x}+{w}", f"{y}+{h}")]
    args = ":".join(f"x{k}='{cx}':y{k}='{cy}'" for k, (cx, cy) in enumerate(corners))
    f += [f"perspective={args}:interpolation=cubic:sense=source:eval=frame", scale, "setsar=1"]
    return f


def mask(side: int, fps: Fraction, frames: int, radius: float = PIP_RADIUS) -> str:
    """Source filter (no labels) of a rounded-square alpha mask (corner `radius` of the
    side; 0.5 = a circle), anti-aliased."""
    c, r = side / 2, side * radius
    edge = f"hypot(max(abs(X+0.5-{c})-{c - r},0),max(abs(Y+0.5-{c})-{c - r},0))"
    return (
        f"color=c=black:s={side}x{side}:r={fps},trim=end_frame={frames},format=gray,"
        f"geq=lum='255*clip({r}+0.5-{edge},0,1)'"
    )


def camera_cut(src_in: float, frames: int, fps: Fraction) -> list[str]:
    """Filters that cut `frames` output frames of the camera from source time src_in (the
    same cut render's face ranges use)."""
    end = src_in + float(frames / fps)
    return [
        f"trim=start={_t(src_in)}:end={_t(end)}",
        "setpts=PTS-STARTPTS",
        f"fps={fps}",
        "tpad=stop_mode=clone:stop=-1",
        f"trim=end_frame={frames}",
    ]


def compose(
    i: int,
    seg: Segment,
    src_in: float,
    fps: Fraction,
    out: tuple[int, int],
    *,
    screen_label: str,
    camera_label: str | None,
    bg: str,
    matrix: str,
    cam_sdr: str,
    screen_sdr: str,
) -> list[str]:
    """Filter lines that draw segment i as [vc{i}] (out-sized)."""
    ow, oh = out
    n = seg.frames
    lines = [
        f"color=c={bg}:s={ow}x{oh}:r={fps},trim=end_frame={n},format=rgb24,"
        f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p[bg{i}];",
        *screen_lines(i, seg, fps, bg, matrix, screen_sdr, screen_label),
    ]
    v = seg.view
    base = f"[bg{i}][sv{i}]overlay=x={v.x}:y={v.y}"
    cam = ",".join(f for f in [*camera_cut(src_in, n, fps), cam_sdr] if f)
    if seg.layout == "screen":
        lines.append(base + f",setsar=1[vc{i}];")
    elif seg.layout == "pip":
        r, w = seg.inset, seg.camera
        lines += [
            f"[{camera_label}]{cam},crop={w.w}:{w.h}:{w.x}:{w.y},"
            f"scale={r.w}:{r.h}:flags=lanczos,format=yuva420p[pc{i}];",
            mask(r.w, fps, n, seg.radius) + f"[pm{i}];",
            f"[pc{i}][pm{i}]alphamerge[pi{i}];",
            base + f"[ps{i}];",
            f"[ps{i}][pi{i}]overlay=x={r.x}:y={r.y},setsar=1[vc{i}];",
        ]
    else:  # split
        r, w = seg.face, seg.camera
        lines += [
            f"[{camera_label}]{cam},crop={w.w}:{w.h}:{w.x}:{w.y},"
            f"scale={r.w}:{r.h}:flags=lanczos,setsar=1[pc{i}];",
            base + f"[ps{i}];",
            f"[ps{i}][pc{i}]overlay=x={r.x}:y={r.y},setsar=1[vc{i}];",
        ]
    return lines


def compose_slide(
    i: int,
    seg: SlideSeg,
    src_in: float,
    fps: Fraction,
    out: tuple[int, int],
    *,
    slide_label: str,
    camera_label: str | None,
    bg: str,
    matrix: str,
    cam_sdr: str,
) -> list[str]:
    """Filter lines that draw slide range i as [vc{i}] (out-sized): the slide clip from
    its frame `clip_frame` on (BT.709 -> the reel's matrix, scaled to its rect), on the
    brand background, plus the speaker (a round bubble, or the split part)."""
    ow, oh = out
    n, g = seg.frames, seg.geo
    r = g.rect
    lines = [
        f"[{slide_label}]trim=start_frame={seg.clip_frame}:end_frame={seg.clip_frame + n},"
        f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop=-1,trim=end_frame={n},"
        f"scale={r.w}:{r.h}:flags=lanczos:in_color_matrix=bt709:in_range=tv"
        f":out_color_matrix={matrix}:out_range=tv,format=yuv420p,setsar=1[sl{i}];"
    ]
    base = f"sl{i}"
    if (r.x, r.y, r.w, r.h) != (0, 0, ow, oh):
        lines += [
            f"color=c={bg}:s={ow}x{oh}:r={fps},trim=end_frame={n},format=rgb24,"
            f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p[sb{i}];",
            f"[sb{i}][sl{i}]overlay=x={r.x}:y={r.y}[sp{i}];",
        ]
        base = f"sp{i}"
    if g.speaker is None:
        return [*lines, f"[{base}]setsar=1[vc{i}];"]
    p, w = g.speaker, seg.camera
    cam = ",".join(f for f in [*camera_cut(src_in, n, fps), cam_sdr] if f)
    if g.round:
        return [
            *lines,
            f"[{camera_label}]{cam},crop={w.w}:{w.h}:{w.x}:{w.y},"
            f"scale={p.w}:{p.h}:flags=lanczos,format=yuva420p[pc{i}];",
            mask(p.w, fps, n, BUBBLE_RADIUS) + f"[pm{i}];",
            f"[pc{i}][pm{i}]alphamerge[pi{i}];",
            f"[{base}][pi{i}]overlay=x={p.x}:y={p.y},setsar=1[vc{i}];",
        ]
    return [
        *lines,
        f"[{camera_label}]{cam},crop={w.w}:{w.h}:{w.x}:{w.y},"
        f"scale={p.w}:{p.h}:flags=lanczos,setsar=1[pc{i}];",
        f"[{base}][pc{i}]overlay=x={p.x}:y={p.y},setsar=1[vc{i}];",
    ]


def broll_crop(src: tuple[int, int], out: tuple[int, int]) -> Window:
    """The centred window of `src` with the aspect of `out` (cover: fills, never bars)."""
    sw, sh = src
    k = min(sw / out[0], sh / out[1])
    w, h = min(sw, _even(out[0] * k)), min(sh, _even(out[1] * k))
    return Window(w, h, (sw - w) // 2, (sh - h) // 2)


def broll_timing(frames: int, kind: str, duration_s: float | None, fps) -> tuple[int, bool]:
    """(frames that show the material, looped) for a range of `frames` frames."""
    if kind != "video":
        return frames, False
    have = int(math.floor((duration_s or 0) * float(fps) + 1e-6))
    if have >= frames:
        return frames, False
    if (frames - have) / float(fps) < BROLL_LOOP_S and have > 0:
        return frames, True
    return max(0, have), False


def broll_inputs(seg: BrollSeg, fps) -> list[str]:
    """ffmpeg input options + -i for the material of one b-roll range."""
    if seg.kind == "image":
        dur = float((seg.frames + 2) / Fraction(fps))
        return ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.4f}", "-i", str(seg.file)]
    return [*(["-stream_loop", "-1"] if seg.loop else []), "-i", str(seg.file)]


def compose_broll(
    i: int,
    seg: BrollSeg,
    fps: Fraction,
    out: tuple[int, int],
    *,
    material_label: str,
    camera: str | None,
    matrix: str,
) -> list[str]:
    """Filter lines that draw b-roll range i as [vc{i}]: the material cropped to fill the
    frame (an image with a push-in), in the reel's matrix; `camera` (a filter line source
    "[c{i}]...," already framed, without label) runs under a clip that ends early."""
    ow, oh = out
    n = seg.frames
    w = broll_crop(seg.size, out)
    to_reel = f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p,setsar=1"
    if seg.kind == "image":
        uw, uh = ow * BROLL_UPSCALE, oh * BROLL_UPSCALE
        z = f"1+{BROLL_PUSH}*on/{max(n - 1, 1)}"
        return [
            f"[{material_label}]format=rgb24,crop={w.w}:{w.h}:{w.x}:{w.y},"
            f"scale={uw}:{uh}:flags=lanczos,"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={ow}x{oh}"
            f":fps={fps},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop=-1,"
            f"trim=end_frame={n},{to_reel}[vc{i}];"
        ]
    clip = ",".join(
        f for f in [
            "setpts=PTS-STARTPTS", f"fps={fps}", seg.sdr,
            f"crop={w.w}:{w.h}:{w.x}:{w.y}",
            f"scale={ow}:{oh}:flags=lanczos:out_color_matrix={matrix}:out_range=tv",
            "format=yuv420p", "setsar=1",
        ] if f
    )  # fmt: skip
    if seg.shown >= n:
        return [f"[{material_label}]{clip},tpad=stop_mode=clone:stop=-1,trim=end_frame={n}[vc{i}];"]
    return [
        f"[{material_label}]{clip},trim=end_frame={seg.shown}[bm{i}];",
        f"{camera}[cb{i}];",
        f"[cb{i}][bm{i}]overlay=0:0:eof_action=pass:format=yuv420,setsar=1[vc{i}];",
    ]


def video_chain(
    plan,
    segs: dict[int, Segment],
    face_filters,
    video_in: str,
    screen_in: str | None,
    *,
    bg: str,
    matrix: str,
    cam_sdr: str,
    screen_sdr: str,
    slides: dict[int, SlideSeg] | None = None,
    slide_in: dict[int, str] | None = None,
    broll: dict[int, BrollSeg] | None = None,
    broll_in: dict[int, str] | None = None,
) -> list[str]:
    """Video lines of a render with layouts, joined as [v] like _render_plan.video_chain.
    `segs` maps a range index of `plan` to its drawn screen segment, `slides` to its
    slide range (its clip is input `slide_in[range]`); the other ranges are face ranges,
    cut by `face_filters(range)`. HDR camera frames are tonemapped per branch."""
    fps = plan.fps
    slides, slide_in = slides or {}, slide_in or {}
    broll, broll_in = broll or {}, broll_in or {}

    def needs_camera(i: int) -> bool:
        if i in broll:  # under a clip that ends before its range
            return broll[i].shown < broll[i].frames
        if i in segs:
            return segs[i].layout != "screen"
        if i in slides:
            return slides[i].geo.speaker is not None
        return True

    cams = [i for i, _ in enumerate(plan.ranges) if needs_camera(i)]
    if cams:
        lines = [  # PTS-STARTPTS: video time, as in _render_plan.video_chain
            f"[{video_in}]setpts=PTS-STARTPTS,split={len(cams)}"
            + "".join(f"[c{i}]" for i in cams)
            + ";"
        ]
    else:  # screen / slides only: the camera gives just the audio
        lines = [f"[{video_in}]nullsink;"]
    if segs:
        lines.append(
            f"[{screen_in}]setpts=PTS-STARTPTS,fps={fps}:start_time=0,split={len(segs)}"
            + "".join(f"[s{i}]" for i in sorted(segs))
            + ";"
        )
    for i, r in enumerate(plan.ranges):
        if i in broll:
            f = [*face_filters(r), cam_sdr] if cam_sdr else face_filters(r)
            lines += compose_broll(
                i, broll[i], fps, plan.out_size, material_label=broll_in[i],
                camera=f"[c{i}]" + ",".join(f) if i in cams else None, matrix=matrix,
            )  # fmt: skip
        elif i in slides:
            lines += compose_slide(
                i, slides[i], r.src_in, fps, plan.out_size, slide_label=slide_in[i],
                camera_label=f"c{i}" if i in cams else None, bg=bg, matrix=matrix,
                cam_sdr=cam_sdr,
            )  # fmt: skip
        elif i in segs:
            lines += compose(
                i, segs[i], r.src_in, fps, plan.out_size,
                screen_label=f"s{i}", camera_label=f"c{i}" if i in cams else None,
                bg=bg, matrix=matrix, cam_sdr=cam_sdr, screen_sdr=screen_sdr,
            )  # fmt: skip
        else:
            f = [*face_filters(r), cam_sdr] if cam_sdr else face_filters(r)
            lines.append(f"[c{i}]" + ",".join(f) + f"[vc{i}];")
    n = len(plan.ranges)
    tail = "format=yuv420p"
    if plan.tail:
        tail = f"tpad=stop_mode=clone:stop={plan.tail},{tail}"
    return [*lines, "".join(f"[vc{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,{tail}[v]"]
