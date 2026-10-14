"""Pure planning for `videocut tool render`: frame-snapped ranges, crop windows, filter graph.

No I/O here, so the timing and crop maths can be tested without ffmpeg.

Timing: every segment boundary and zoom key is snapped to the output frame grid
(round(t_out * fps)). Video gets an exact frame count per range and audio the same
snapped duration (in samples), so audio and video never drift apart however many cuts
there are.

Audio transitions: the video is a hard cut on the frame grid, the audio crossfades at
every cut (equal power, `xfade_s` or the segment's "xfade_ms"). The crossfade is centred on
the cut: the outgoing segment's audio runs on for half of it past its `out`, the incoming
one starts half of it before its `in` (the slack snapping leaves), so the total duration is
unchanged. It is shortened where a segment is too short or the source ends, and to the
quiet room around the two cut points (`xfade_caps`, from _snap.xfade_caps: a cut that has
only 20 ms of quiet before the next word gets a short crossfade, never under
MIN_XFADE_S). Two segments
that continue each other in the source (a join) get a JOIN_XFADE_S linear crossfade
(the same audio on both sides). The reel's first and last samples fade over EDGE_FADE_S.
The legacy per-segment "fade_ms" is ignored.
"""

import math
from dataclasses import dataclass, replace
from fractions import Fraction

from ..edl import Edl
from ._geometry import Window, crop_window

SHARP_MIN_WIDTH = 608  # crop narrower than this is visibly soft at 1080 wide (BRIEF §10)
SHARP_REF_WIDTH = 1080
TARGET_I, TARGET_TP, TARGET_LRA = -14.0, -1.0, 11.0
# loudnorm aims this much below TARGET_TP: AAC encoding overshoots the true peak by a few
# tenths of a dB, and the finished file must still stay at or below TARGET_TP.
TP_HEADROOM = 0.5
MEASURED_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
RATE = 48000
EDGE_FADE_S = 0.02
JOIN_XFADE_S = 0.01
MIN_XFADE_S = 0.01  # the shortest crossfade at a cut (shorter ones click)
JOIN_S = 0.001  # out == next in within this: a join, not a cut


def sharp_min_width(out_size: tuple[int, int]) -> int:
    """Narrowest face crop (source px) that still looks sharp at `out_size`: SHARP_MIN_WIDTH
    at 1080 wide, the same source px per output px at any other output width."""
    return math.ceil(SHARP_MIN_WIDTH * out_size[0] / SHARP_REF_WIDTH)


@dataclass
class Range:
    """A stretch of output frames that shows one source range with one crop."""

    src_in: float  # source seconds of the first frame
    frames: int
    zoom: float
    window: Window
    layout: str = "face"  # EDL segment layout; non-face segments are one range (_layouts)
    seg: int = 0  # index of the EDL segment


@dataclass
class AudioPart:
    src_in: float
    dur: float  # snapped output duration of the segment
    start: int = 0  # first source sample read (crossfade lead-in included)
    samples: int = 0  # samples read: output samples + both crossfade halves
    xfade: int = 0  # samples crossfaded with the previous part (0: the first part)
    join: bool = False  # the previous part continues into this one in the source
    fade_in: float = 0.0  # seconds (reel start only)
    fade_out: float = 0.0  # seconds (reel end only)


@dataclass
class Plan:
    fps: Fraction
    out_size: tuple[int, int]
    ranges: list[Range]
    audio: list[AudioPart]
    frames: int  # all output frames, the held tail included
    tail: int = 0  # held frames after the cut (the last frame repeats, silent audio)

    @property
    def duration(self) -> float:
        return float(self.frames / self.fps)

    def min_crop_width(self) -> int:
        face = [r for r in self.ranges if r.layout == "face"] or self.ranges
        return min(r.window.w for r in face)


def build(
    edl: Edl,
    src_size: tuple[int, int],
    out_size: tuple[int, int],
    layouts: list[str] | None = None,
    xfade_s: float = 0.0,
    source_duration: float | None = None,
    xfade_caps: dict[int, float] | None = None,
) -> Plan:
    """`layouts`: per segment its EDL layout (default all "face"); a non-face segment
    becomes one range without zoom steps (_layouts draws it). `xfade_s`: the audio
    crossfade at every cut (the cut style's; a segment's "xfade_ms" overrides it)."""
    fps = Fraction(edl.fps).limit_denominator(1001)
    ranges: list[Range] = []
    audio: list[AudioPart] = []
    t_out = 0.0
    frame0 = 0
    for k, seg in enumerate(edl.segments):
        f_end = round((t_out + seg.duration) * fps)
        crop = seg.crop
        cx, cy, base = (crop.cx, crop.cy, crop.zoom) if crop else (0.5, 0.5, 1.0)
        layout = layouts[k] if layouts else "face"
        # Zoom steps: (output frame where it starts, zoom). Hard punch-ins, no animation.
        steps = [(frame0, base)]
        keys = seg.zoom_keys if layout == "face" else []
        for t, z in sorted(keys, key=lambda k: k[0]):  # ties: last in file wins
            f = round((t_out + t - seg.start) * fps)
            f = min(max(f, frame0), f_end)
            steps = [s for s in steps if s[0] < f] + [(f, z)]
        for i, (f, z) in enumerate(steps):
            nxt = steps[i + 1][0] if i + 1 < len(steps) else f_end
            if nxt > f:
                ranges.append(
                    Range(
                        src_in=seg.start + float((f - frame0) / fps),
                        frames=nxt - f,
                        zoom=z,
                        window=crop_window(src_size, out_size, cx, cy, z),
                        layout=layout,
                        seg=k,
                    )
                )
        dur = float((f_end - frame0) / fps)
        if dur > 0:
            audio.append((k, frame0, f_end))
        t_out += seg.duration
        frame0 = f_end
    parts = audio_parts(edl, fps, audio, xfade_s, source_duration, xfade_caps)
    return Plan(fps, out_size, ranges, parts, frame0)


def _sample(t) -> int:
    return round(t * RATE)


def audio_parts(
    edl: Edl, fps: Fraction, spans: list[tuple[int, int, int]], xfade_s: float,
    source_duration: float | None, caps: dict[int, float] | None = None,
) -> list[AudioPart]:  # fmt: skip
    """One AudioPart per (segment index, first frame, end frame) with its crossfades."""
    segs = edl.segments
    n = [_sample(Fraction(f1) / fps) - _sample(Fraction(f0) / fps) for _, f0, f1 in spans]
    src = [_sample(segs[k].start) for k, _, _ in spans]
    end = _sample(source_duration) if source_duration is not None else None
    x = [0] * len(spans)  # crossfade samples before part i
    joins = [False] * len(spans)
    for i in range(1, len(spans)):
        a, b = segs[spans[i - 1][0]], segs[spans[i][0]]
        joins[i] = abs(b.start - a.end) <= JOIN_S
        if joins[i]:
            want = JOIN_XFADE_S
        else:
            own = [v for v in (a.xfade_ms, b.xfade_ms) if v is not None]
            want = min(own) / 1000 if own else xfade_s
            k = spans[i][0]
            if caps and k in caps and want > 0:  # not into speech next to the cut
                want = min(want, max(caps[k], MIN_XFADE_S))
        half = min(_sample(want) // 2, n[i - 1] // 2, n[i] // 2, src[i])
        if end is not None:
            half = min(half, max(0, end - (src[i - 1] + n[i - 1])))
        x[i] = 2 * max(half, 0)
    parts = []
    for i, (k, _, _) in enumerate(spans):
        pre, post = x[i] // 2, (x[i + 1] // 2 if i + 1 < len(spans) else 0)
        dur = n[i] / RATE
        parts.append(
            AudioPart(
                src_in=segs[k].start,
                dur=float(Fraction(spans[i][2] - spans[i][1]) / fps),
                start=src[i] - pre,
                samples=n[i] + pre + post,
                xfade=x[i],
                join=joins[i],
                fade_in=min(EDGE_FADE_S, dur / 2) if i == 0 else 0.0,
                fade_out=min(EDGE_FADE_S, dur / 2) if i == len(spans) - 1 else 0.0,
            )
        )
    return parts


def with_tail(plan: Plan, frames: int) -> Plan:
    """`plan` extended by `frames` held frames (the last frame repeats, the audio is padded
    with silence after loudnorm); see _plates."""
    return replace(plan, frames=plan.frames - plan.tail + frames, tail=frames)


def _t(v: float) -> str:
    return f"{v:.6f}"


def audio_chain(plan: Plan, audio_in: str) -> list[str]:
    """Filter lines that cut `audio_in` (e.g. "1:a") into the segments (with their crossfade
    halves), crossfade them at every cut and join them as [cut]."""
    lines = []
    for i, a in enumerate(plan.audio):
        f = [
            # by timestamp (start=/end=): start_sample counts from the stream's first
            # sample, which is not source time when the audio starts after the video
            f"atrim=start={_t(a.start / RATE)}:end={_t((a.start + a.samples) / RATE)}",
            "asetpts=PTS-STARTPTS",
            f"apad=whole_len={a.samples}",
            f"atrim=end_sample={a.samples}",
        ]
        if a.fade_in > 0:
            f.append(f"afade=t=in:st=0:d={_t(a.fade_in)}")
        if a.fade_out > 0:
            f.append(f"afade=t=out:st={_t(a.samples / RATE - a.fade_out)}:d={_t(a.fade_out)}")
        lines.append(f"[a{i}]" + ",".join(f) + f"[ac{i}];")
    n = len(plan.audio)
    # first_pts=0 + async: an audio stream that starts after the video gets silence in
    # front, so a segment from before its first sample is not shifted early
    split = (
        f"[{audio_in}]aresample={RATE}:async=1:first_pts=0,aformat=channel_layouts=stereo,"
        f"asplit={n}"
    ) + "".join(f"[a{i}]" for i in range(n))
    if n == 1:
        return [split + ";", *lines, "[ac0]anull[cut]"]
    prev = "ac0"
    for i in range(1, n):
        a, nxt = plan.audio[i], ("cut" if i == n - 1 else f"x{i}")
        if a.xfade == 0:
            join = "concat=n=2:v=0:a=1"
        else:  # equal power across a cut, equal gain across a join (the same audio)
            curve = "tri" if a.join else "qsin"
            join = f"acrossfade=ns={a.xfade}:c1={curve}:c2={curve}"
        lines.append(f"[{prev}][ac{i}]{join}[{nxt}]" + (";" if i < n - 1 else ""))
        prev = nxt
    return [split + ";", *lines]


def loudnorm(measured: dict | str | None) -> str:
    """Second pass (linear, with first-pass numbers), single pass when `measured` is None,
    nothing for "silent" (loudnorm would turn digital silence into NaN)."""
    if measured == "silent":
        return f"aresample={RATE}"
    f = f"loudnorm=I={TARGET_I}:TP={TARGET_TP - TP_HEADROOM}:LRA={TARGET_LRA}"
    if measured:
        f += (
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true"
        )
    return f + f",aresample={RATE}"


def range_filters(plan: Plan, r: Range) -> list[str]:
    """Filters (no labels) that cut, crop and scale the source into face range `r`."""
    ow, oh = plan.out_size
    w = r.window
    end = r.src_in + float(r.frames / plan.fps)
    # Decode-side trim (frame accurate; a range starts with the first source frame at or
    # after src_in, so video may trail audio by < 1 source frame there, never drifting),
    # then a constant frame rate with the last frame
    # held if the source runs short, then exactly `frames` frames.
    return [
        f"trim=start={_t(r.src_in)}:end={_t(end)}",
        "setpts=PTS-STARTPTS",
        f"fps={plan.fps}",
        "tpad=stop_mode=clone:stop=-1",
        f"trim=end_frame={r.frames}",
        f"crop={w.w}:{w.h}:{w.x}:{w.y}",
        f"scale={ow}:{oh}:flags=lanczos",
        "setsar=1",
    ]


def video_chain(plan: Plan, video_in: str, to_sdr: str = "") -> list[str]:
    """Filter lines that cut, crop and scale `video_in` (e.g. "0:0") into the ranges and join
    them as [v]; `to_sdr` (a tonemap chain) runs once on the joined output-size frames."""
    n = len(plan.ranges)
    lines = [f"[{video_in}]split={n}" + "".join(f"[v{i}]" for i in range(n)) + ";"]
    for i, r in enumerate(plan.ranges):
        f = range_filters(plan, r)
        lines.append(f"[v{i}]" + ",".join(f) + f"[vc{i}];")
    tail = ",".join(f for f in (to_sdr, "format=yuv420p") if f)
    if plan.tail:
        tail = f"tpad=stop_mode=clone:stop={plan.tail},{tail}"
    joined = "".join(f"[vc{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,{tail}[v]"
    return [*lines, joined]


def graph(
    plan: Plan,
    measured: dict | str | None,
    video_in: str,
    audio_in: str,
    to_sdr: str = "",
    video: list[str] | None = None,
) -> str:
    """Full render graph: [v] video, [a] normalised audio (+ the silent tail). `video`
    replaces the face-only video lines (a render with layouts, _layouts.video_chain)."""
    pad = f",apad=whole_dur={_t(plan.duration)}" if plan.tail else ""
    lines = [
        *(video if video is not None else video_chain(plan, video_in, to_sdr)),
        ";",
        *audio_chain(plan, audio_in),
        ";",
        f"[cut]{loudnorm(measured)}{pad}[a]",
    ]
    return "\n".join(lines) + "\n"


def measure_graph(plan: Plan, audio_in: str) -> str:
    """First pass: the cut audio into a measuring loudnorm, output [a] to discard."""
    lines = [
        *audio_chain(plan, audio_in),
        ";",
        f"[cut]loudnorm=I={TARGET_I}:TP={TARGET_TP - TP_HEADROOM}:LRA={TARGET_LRA}"
        ":print_format=json[a]",
    ]
    return "\n".join(lines) + "\n"
