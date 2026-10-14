"""Cut style of a job: how much of the speaker's natural rhythm the cut keeps.

job.json "cut_style" is one of STYLES. Absent, the default rule picks it when the job is read
(never written back): a job with a screen recording or screenshot assets is a product demo
and is cut "calm" (the viewer reads the screen while the speaker talks), every other job
"standard".

What each parameter means for the editor (the cut skill, `edl snap`, render):

    keep_pause_s        the longest natural pause kept inside a segment. A longer pause is
                        cut (the segment ends after the last word before it and the next
                        one starts before the first word after it); a shorter one stays.
    min_segment_s       the shortest segment the cut may contain (output seconds). Shorter
                        pieces are merged with a neighbour or dropped with what they hold.
    splice_in_sentence  whether a sentence may be assembled from pieces. False: segments
                        start and end only at sentence boundaries or at a pause longer than
                        keep_pause_s; a filler inside flowing speech stays. True: a cut
                        inside a sentence is allowed, but only for what `splice_only` names.
    splice_only         what an in-sentence cut may remove (empty when splice_in_sentence is
                        False): "filler" ({äh}, {~…} marks), "false_start" (a sentence or
                        word started again), "repeat" (a repeated word), "pause" (any pause
                        above keep_pause_s, also inside a sentence).
    xfade_ms            the equal-power audio crossfade render applies at every cut (video
                        stays a hard cut); a segment's own "xfade_ms" overrides it at that
                        segment's edges (the legacy "fade_ms" is ignored once crossfades are
                        active).
    snap_window_s       how far `edl snap` may move an in/out to reach real silence.

    calm      demos and explanations: whole sentences, breathing room, long crossfade
    standard  talking heads: fillers and false starts go, sentences stay whole otherwise
    tight     fast reels: every pause and filler goes (the behaviour before cut styles)
"""

STYLES = {
    "calm": {
        "keep_pause_s": 1.0,
        "min_segment_s": 3.0,
        "splice_in_sentence": False,
        "splice_only": [],
        "xfade_ms": 120,
        "snap_window_s": 0.35,
    },
    "standard": {
        "keep_pause_s": 0.6,
        "min_segment_s": 1.5,
        "splice_in_sentence": True,
        "splice_only": ["filler", "false_start"],
        "xfade_ms": 80,
        "snap_window_s": 0.30,
    },
    "tight": {
        "keep_pause_s": 0.35,
        "min_segment_s": 0.8,
        "splice_in_sentence": True,
        "splice_only": ["filler", "false_start", "repeat", "pause"],
        "xfade_ms": 50,
        "snap_window_s": 0.25,
    },
}
AUTO = "auto"  # API/CLI value for "no explicit style: the default rule"
DEMO_DEFAULT, TALK_DEFAULT = "calm", "standard"


class StyleError(ValueError):
    """A cut style that is not one of STYLES."""


def parse_style(value: str) -> str:
    """A style name from user input (any case), else StyleError."""
    v = str(value).strip().lower()
    if v not in STYLES:
        raise StyleError(f"unknown cut style {value!r}; one of: {', '.join(STYLES)}")
    return v


def default_style(job: dict) -> str:
    """calm for a product demo (a screen recording or screenshot assets), else standard."""
    return DEMO_DEFAULT if job.get("screen") or job.get("assets") else TALK_DEFAULT


def job_style(job: dict) -> str:
    """The job's cut style (`job` is job.json as a dict): job.json "cut_style" when set
    (StyleError when invalid), else the default rule."""
    value = job.get("cut_style")
    return default_style(job) if value is None else parse_style(value)


def style(job: dict) -> dict:
    """The parameters of the job's cut style, with its "name"."""
    name = job_style(job)
    return {"name": name, **STYLES[name], "splice_only": list(STYLES[name]["splice_only"])}
