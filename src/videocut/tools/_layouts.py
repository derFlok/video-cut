"""Product-demo layouts of `videocut tool render`: per EDL segment "layout" (BRIEF §6).

    face    the camera, framed as before (render's original path, unchanged)
    screen  the ORIGINAL screen recording (job.json "screen", not the proxy) fills the frame
            width, vertically centred within the safe zones, on the brand's colors.bg
    pip     like "screen", plus the speaker as a rounded inset (face-centred square crop of
            the camera, PIP_SIZE of the width) below the view, so it never covers screen
            content: the view sits at the top of the safe area, the inset below it in the
            corner on the side away from the zoom targets; the strip below the inset is
            left free for the captions
    split   screen view in the top SPLIT_TOP of the frame, the speaker below (face-centred)

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
whole screen shows. Window edges are interpolated linearly in the eased parameter, so every
window edge moves monotonically between two keys and windows stay inside the frame.
A segment with one window crops it and scales it to the view; a moving window goes through
`perspective` (sense=source, eval=frame), whose corner expressions are the same piecewise
function of the frame number `in` that `window_at` computes in Python, then a fixed scale.

Sharpness: a window narrower than the view's width at the EDL size would upscale the
screen (< 1 source px per output px); such a key is widened to that width and reported
(render.json "screen_zoom_clamped").

Redaction: "redact" [{"t0", "t1": camera_s, "box"}] are solid blocks in the brand's
colors.bg drawn on the screen frames BEFORE scaling, on every output frame that overlaps
[t0, t1] plus one frame (and the drift over the segment) on each side, snapped outward to
even source px.
"""

import json
import math
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .. import brand
from .. import edl as edl_mod
from ._geometry import Window, crop_window, safe_zones
from .common import ToolError, binary, read_job, run

LAYOUTS = edl_mod.LAYOUTS
EASE_S = 0.6  # length of a zoom move
PIP_SIZE = 0.34  # inset side, fraction of the output width
PIP_RADIUS = 0.22  # inset corner radius, fraction of its side
PIP_FACE = 2.6  # inset camera crop side = face box height x this
PIP_NO_FACE = 0.8  # without a face: crop side = this x the camera's short side
SPLIT_TOP = 0.58  # split: the screen part, fraction of the output height
DEFAULT_BRAND = brand.EXAMPLE_ID


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
    redact: list[tuple[int, int, int, int, int, int]]  # (n0, n1, x, y, w, h) source px
    view: Rect
    inset: Rect | None = None
    face: Rect | None = None  # split: where the speaker goes
    camera: Window | None = None  # camera crop for the inset / the speaker part
    focus: list[list[float]] = field(default_factory=list)  # zoom targets, output px
    screen: tuple[int, int] = (0, 0)  # size of the screen recording


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


def view_rect(layout: str, out: tuple[int, int], screen: tuple[int, int]) -> Rect:
    """Where the screen view goes in an `out`-sized frame (aspect of the screen)."""
    ow, oh = out
    z = safe_zones(out)
    top, bottom = z["top"], (round(oh * SPLIT_TOP) if layout == "split" else oh - z["bottom"])
    below = _even(ow * PIP_SIZE) + _gap(out) if layout == "pip" else 0  # room for the inset
    room = bottom - top - below
    w, h = ow, _even(ow * screen[1] / screen[0])
    if h > room:  # a tall screen: fit the height instead
        w, h = _even(room * screen[0] / screen[1]), _even(room)
    y = top if layout == "pip" else top + (room - h) // 2  # pip: caption room below the inset
    return Rect((ow - w) // 2, y, w, h)


def _gap(out: tuple[int, int]) -> int:
    return safe_zones(out)["side"] // 2


def inset_rects(out: tuple[int, int], view: Rect) -> list[Rect]:
    """The pip inset's places below the pip `view` (right first), inside the safe zones."""
    ow, _ = out
    side = safe_zones(out)["side"]
    s = _even(ow * PIP_SIZE)
    y = view.y + view.h + _gap(out)
    return [Rect(ow - side - s, y, s, s), Rect(side, y, s, s)]


def window_for(
    box: list[float], screen: tuple[int, int], aspect: float, min_w: float
) -> tuple[Win, float, bool]:
    """Window (left, top, width; source px, `aspect` = w/h) around `box`, at least `min_w`
    wide, inside the frame: (window, the zoom the box asked for, whether it was clamped)."""
    sw, sh = screen
    full = min(sw, sh * aspect)
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
) -> tuple[Segment, list[dict]]:
    """The drawn form of one non-face EDL segment `seg` (edl.Segment, already framed: its
    layout, screen_zoom and redact were validated by edl.py) and its clamped zoom keys."""
    layout = seg.layout
    view = view_rect(layout, out, screen)
    sharp_w = view_rect(layout, full_out, screen).w  # >= 1 source px per output px
    aspect = screen[0] / screen[1]  # the view is this aspect up to even rounding
    sw, sh = screen
    full = min(sw, sh * aspect)
    initial: Win = ((sw - full) / 2, (sh - full / aspect) / 2, full)
    min_w = min(float(sharp_w), full)

    moves, clamped, keys = [], [], []
    ease = max(1, round(EASE_S * fps))
    for k in sorted(seg.screen_zoom, key=lambda k: k["t"]):
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

    redact = []
    for r in seg.redact:
        n0 = math.floor((r["t0"] - seg.start) * fps) - redact_slack
        n1 = math.ceil((r["t1"] - seg.start) * fps) - 1 + redact_slack
        if n1 < 0 or n0 >= frames:
            continue
        b = r["box"]
        x0, y0 = math.floor(b[0] * sw / 2) * 2, math.floor(b[1] * sh / 2) * 2
        x1, y1 = min(math.ceil(b[2] * sw / 2) * 2, sw), min(math.ceil(b[3] * sh / 2) * 2, sh)
        redact.append((max(n0, 0), min(n1, frames - 1), x0, y0, x1 - x0, y1 - y0))

    # Zoom targets in output px (for keeping the inset off them).
    focus = []
    for _, win, box in keys:
        x, y, w = win
        k = view.w / w
        focus.append([view.x + (box[0] * sw - x) * k, view.y + (box[1] * sh - y) * k,
                      view.x + (box[2] * sw - x) * k, view.y + (box[3] * sh - y) * k])  # fmt: skip

    s = Segment(layout, frames, round((seg.start + offset) * fps), initial, moves, redact, view)
    s.focus = focus
    s.screen = screen
    crop = seg.crop
    cx, cy = (crop.cx, crop.cy) if crop else (0.5, 0.5)
    if layout == "pip":
        right, left = inset_rects(out, view)
        # The side away from the zoom targets (their mean centre); right without any.
        mid = sum((b[0] + b[2]) / 2 for _, _, b in keys) / len(keys) if keys else 0
        s.inset = left if mid > 0.5 else right
        s.camera = _inset_crop(camera, cx, cy, face)
    elif layout == "split":
        top = round(out[1] * SPLIT_TOP)
        s.face = Rect(0, top, out[0], out[1] - top)
        s.camera = crop_window(camera, (s.face.w, s.face.h), cx, cy, crop.zoom if crop else 1.0)
    return s, clamped


def with_redaction(edl, cands: list[dict]):
    """`edl` (edl.Edl) whose non-face segments also carry a redact block for every screen
    candidate of redaction.enforced that overlaps them (the safe default: render, verify)."""
    from dataclasses import replace

    from .. import redaction

    segs = []
    for s in edl.segments:
        extra = redaction.screen_blocks(cands, s.start, s.end) if s.layout != "face" else []
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


def plan_all(job: Path, edl, plan, camera: tuple[int, int], face: dict | None) -> dict:
    """Every non-face range of `plan` (a _render_plan.Plan of the framed `edl`) drawn:
    {"segs": {range index: Segment}, "clamped": [{"segment", "t", "zoom", "max_zoom"}],
    "screen": the recording, "info": probe_screen, "sync": work/sync.json}. Shared by
    render, overlays (where the screen view and the speaker are) and verify."""
    src = screen_source(job)
    sync_doc = sync(job)
    info = probe_screen(src)
    screen_frames = math.floor(info["duration"] * plan.fps)
    segs, clamped = {}, []
    for i, r in enumerate(plan.ranges):
        if r.layout == "face":
            continue
        seg_in = edl.segments[r.seg]
        offset, slack = segment_offset(sync_doc, seg_in.start, seg_in.duration, plan.fps)
        seg, cl = plan_segment(
            seg_in, r.frames, plan.fps, offset=offset, screen=tuple(info["size"]),
            camera=camera, out=plan.out_size, full_out=tuple(edl.size), face=face,
            redact_slack=slack,
        )  # fmt: skip
        if seg.screen_frame + seg.frames <= 0 or seg.screen_frame >= screen_frames:
            raise ToolError(
                f"segment {r.seg} ({r.layout}) lies outside the screen recording "
                f"(offset_s {offset})"
            )
        segs[i] = seg
        clamped += [{"segment": r.seg, **c} for c in cl]
    return {"segs": segs, "clamped": clamped, "screen": src, "info": info, "sync": sync_doc}


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
    view: cut by frame, padded/held, HDR -> SDR, redacted, zoomed, `view`-sized."""
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


def mask(side: int, fps: Fraction, frames: int) -> str:
    """Source filter (no labels) of a rounded-square alpha mask, anti-aliased."""
    c, r = side / 2, side * PIP_RADIUS
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
        f"[{screen_label}]" + ",".join(screen_view(seg, fps, bg, matrix, screen_sdr)) + f"[sv{i}];",
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
            mask(r.w, fps, n) + f"[pm{i}];",
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


def video_chain(
    plan,
    segs: dict[int, Segment],
    face_filters,
    video_in: str,
    screen_in: str,
    *,
    bg: str,
    matrix: str,
    cam_sdr: str,
    screen_sdr: str,
) -> list[str]:
    """Video lines of a render with layouts, joined as [v] like _render_plan.video_chain.
    `segs` maps a range index of `plan` to its drawn segment (ranges not in it are face
    ranges, cut by `face_filters(range)`); HDR camera frames are tonemapped per branch."""
    fps = plan.fps
    cams = [i for i, _ in enumerate(plan.ranges) if i not in segs or segs[i].layout != "screen"]
    if cams:
        lines = [f"[{video_in}]split={len(cams)}" + "".join(f"[c{i}]" for i in cams) + ";"]
    else:  # screen only: the camera gives just the audio
        lines = [f"[{video_in}]nullsink;"]
    if segs:
        lines.append(
            f"[{screen_in}]setpts=PTS-STARTPTS,fps={fps}:start_time=0,split={len(segs)}"
            + "".join(f"[s{i}]" for i in sorted(segs))
            + ";"
        )
    for i, r in enumerate(plan.ranges):
        if i in segs:
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
