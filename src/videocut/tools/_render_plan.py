"""Pure planning for `videocut tool render`: frame-snapped ranges, crop windows, filter graph.

No I/O here, so the timing and crop maths can be tested without ffmpeg.

Timing: every segment boundary and zoom key is snapped to the output frame grid
(round(t_out * fps)). Video gets an exact frame count per range and audio the same
snapped duration, so audio and video never drift apart however many cuts there are.
"""

from dataclasses import dataclass
from fractions import Fraction

from ..edl import Edl

SHARP_MIN_WIDTH = 608  # crop narrower than this is visibly soft at 1080 wide (BRIEF §10)
TARGET_I, TARGET_TP, TARGET_LRA = -14.0, -1.0, 11.0
# loudnorm aims this much below TARGET_TP: AAC encoding overshoots the true peak by a few
# tenths of a dB, and the finished file must still stay at or below TARGET_TP.
TP_HEADROOM = 0.5
MEASURED_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
RATE = 48000


@dataclass
class Window:
    w: int
    h: int
    x: int
    y: int


@dataclass
class Range:
    """A stretch of output frames that shows one source range with one crop."""

    src_in: float  # source seconds of the first frame
    frames: int
    zoom: float
    window: Window


@dataclass
class AudioPart:
    src_in: float
    dur: float  # snapped output duration of the segment
    fade: float  # seconds, already clamped to half the duration


@dataclass
class Plan:
    fps: Fraction
    out_size: tuple[int, int]
    ranges: list[Range]
    audio: list[AudioPart]
    frames: int

    @property
    def duration(self) -> float:
        return float(self.frames / self.fps)

    def min_crop_width(self) -> int:
        return min(r.window.w for r in self.ranges)


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def crop_window(
    src: tuple[int, int], out: tuple[int, int], cx: float, cy: float, zoom: float
) -> Window:
    """Window of the output aspect in a `src`-sized (display) frame, `zoom`× tighter than
    the largest one that fits, centred on (cx, cy) in normalised coords, kept inside."""
    sw, sh = src
    aspect = out[0] / out[1]
    if sw / sh > aspect:  # wider than the output (landscape): full height
        bw, bh = sh * aspect, sh
    else:  # narrower or equal (portrait): full width
        bw, bh = sw, sw / aspect
    w = min(_even(bw / zoom), sw - sw % 2)
    h = min(_even(bh / zoom), sh - sh % 2)
    x = min(max(int(round(cx * sw - w / 2)), 0), sw - w)
    y = min(max(int(round(cy * sh - h / 2)), 0), sh - h)
    return Window(w, h, x, y)


def build(edl: Edl, src_size: tuple[int, int], out_size: tuple[int, int]) -> Plan:
    fps = Fraction(edl.fps).limit_denominator(1001)
    ranges: list[Range] = []
    audio: list[AudioPart] = []
    t_out = 0.0
    frame0 = 0
    for seg in edl.segments:
        f_end = round((t_out + seg.duration) * fps)
        crop = seg.crop
        cx, cy, base = (crop.cx, crop.cy, crop.zoom) if crop else (0.5, 0.5, 1.0)
        # Zoom steps: (output frame where it starts, zoom). Hard punch-ins, no animation.
        steps = [(frame0, base)]
        for t, z in sorted(seg.zoom_keys, key=lambda k: k[0]):  # ties: last in file wins
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
                    )
                )
        dur = float((f_end - frame0) / fps)
        if dur > 0:
            audio.append(AudioPart(seg.start, dur, min(seg.fade_ms / 1000, dur / 2)))
        t_out += seg.duration
        frame0 = f_end
    return Plan(fps, out_size, ranges, audio, frame0)


def _t(v: float) -> str:
    return f"{v:.6f}"


def audio_chain(plan: Plan, audio_in: str) -> list[str]:
    """Filter lines that cut `audio_in` (e.g. "1:a") into the segments, fade every edge and
    join them as [cut]."""
    lines = []
    for i, a in enumerate(plan.audio):
        f = [
            f"atrim=start={_t(a.src_in)}:end={_t(a.src_in + a.dur)}",
            "asetpts=PTS-STARTPTS",
            f"apad=whole_dur={_t(a.dur)}",
            f"atrim=duration={_t(a.dur)}",
        ]
        if a.fade > 0:
            f += [
                f"afade=t=in:st=0:d={_t(a.fade)}",
                f"afade=t=out:st={_t(a.dur - a.fade)}:d={_t(a.fade)}",
            ]
        lines.append(f"[a{i}]" + ",".join(f) + f"[ac{i}];")
    n = len(plan.audio)
    split = f"[{audio_in}]aresample={RATE},aformat=channel_layouts=stereo,asplit={n}" + "".join(
        f"[a{i}]" for i in range(n)
    )
    joined = "".join(f"[ac{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cut]"
    return [split + ";", *lines, joined]


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


def video_chain(plan: Plan, video_in: str, to_sdr: str = "") -> list[str]:
    """Filter lines that cut, crop and scale `video_in` (e.g. "0:0") into the ranges and join
    them as [v]; `to_sdr` (a tonemap chain) runs once on the joined output-size frames."""
    ow, oh = plan.out_size
    n = len(plan.ranges)
    lines = [f"[{video_in}]split={n}" + "".join(f"[v{i}]" for i in range(n)) + ";"]
    for i, r in enumerate(plan.ranges):
        w = r.window
        end = r.src_in + float(r.frames / plan.fps)
        # Decode-side trim (frame accurate; a range starts with the first source frame at or
        # after src_in, so video may trail audio by < 1 source frame there, never drifting),
        # then a constant frame rate with the last frame
        # held if the source runs short, then exactly `frames` frames.
        f = [
            f"trim=start={_t(r.src_in)}:end={_t(end)}",
            "setpts=PTS-STARTPTS",
            f"fps={plan.fps}",
            "tpad=stop_mode=clone:stop=-1",
            f"trim=end_frame={r.frames}",
            f"crop={w.w}:{w.h}:{w.x}:{w.y}",
            f"scale={ow}:{oh}:flags=lanczos",
            "setsar=1",
        ]
        lines.append(f"[v{i}]" + ",".join(f) + f"[vc{i}];")
    tail = ",".join(f for f in (to_sdr, "format=yuv420p") if f)
    joined = "".join(f"[vc{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,{tail}[v]"
    return [*lines, joined]


def graph(
    plan: Plan, measured: dict | str | None, video_in: str, audio_in: str, to_sdr: str = ""
) -> str:
    """Full render graph: [v] video, [a] normalised audio."""
    lines = [
        *video_chain(plan, video_in, to_sdr),
        ";",
        *audio_chain(plan, audio_in),
        ";",
        f"[cut]{loudnorm(measured)}[a]",
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
