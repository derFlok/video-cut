from videocut.edl import from_dict
from videocut.tools import _color
from videocut.tools import _render_plan as rp

OUT = (1080, 1920)


def _edl(segments, fps=30):
    return from_dict({"fps": fps, "size": list(OUT), "source": "input/a.mp4", "segments": segments})


def test_crop_landscape_full_height_centred():
    w = rp.crop_window((1920, 1080), OUT, 0.5, 0.5, 1.0)
    assert (w.w, w.h, w.x, w.y) == (608, 1080, 656, 0)


def test_crop_zoom_and_clamp_inside_frame():
    w = rp.crop_window((1920, 1080), OUT, 0.0, 1.0, 1.5)
    assert (w.w, w.h) == (404, 720)
    assert (w.x, w.y) == (0, 1080 - 720)  # pushed back inside the frame
    w = rp.crop_window((1920, 1080), OUT, 1.0, 0.0, 1.0)
    assert w.x + w.w == 1920 and w.y == 0


def test_crop_portrait_full_width_and_zoom():
    w = rp.crop_window((1080, 1920), OUT, 0.5, 0.5, 1.0)
    assert (w.w, w.h, w.x, w.y) == (1080, 1920, 0, 0)
    w = rp.crop_window((1080, 1920), OUT, 0.5, 0.5, 2.0)
    assert (w.w, w.h, w.x, w.y) == (540, 960, 270, 480)
    w = rp.crop_window((1080, 1440), OUT, 0.5, 0.5, 1.0)  # 3:4 portrait: full width
    assert (w.w, w.h) == (810, 1440)


def test_frames_snap_to_grid_without_drift():
    # 40 segments of 1/7 s: per-segment rounding would drift; the total must not.
    segs = [{"in": i, "out": i + 1 / 7} for i in range(40)]
    plan = rp.build(_edl(segs), (1920, 1080), OUT)
    assert plan.frames == round(40 / 7 * 30)
    assert sum(r.frames for r in plan.ranges) == plan.frames
    assert abs(sum(a.dur for a in plan.audio) - plan.duration) < 1e-9


def test_zoom_keys_split_a_segment_into_hard_punch_ins():
    segs = [
        {"in": 10, "out": 12, "crop": {"cx": 0.5, "cy": 0.5, "zoom": 1.0}},
        {"in": 20, "out": 22, "zoom_keys": [[21.5, 1.6], [21.0, 1.25]]},
    ]
    plan = rp.build(_edl(segs), (1920, 1080), OUT)
    assert [(r.src_in, r.frames, r.zoom) for r in plan.ranges] == [
        (10, 60, 1.0),
        (20, 30, 1.0),
        (21.0, 15, 1.25),
        (21.5, 15, 1.6),
    ]
    assert plan.ranges[-1].window.w < plan.ranges[-2].window.w < plan.ranges[-3].window.w
    assert plan.min_crop_width() == plan.ranges[-1].window.w


def test_zoom_key_at_segment_start_replaces_base_zoom():
    plan = rp.build(_edl([{"in": 1, "out": 2, "zoom_keys": [[1, 1.5]]}]), (1920, 1080), OUT)
    assert [(r.frames, r.zoom) for r in plan.ranges] == [(30, 1.5)]


def test_edge_fades_are_clamped_to_half_a_short_segment():
    plan = rp.build(_edl([{"in": 0, "out": 0.02, "fade_ms": 100}]), (1920, 1080), OUT)
    (a,) = plan.audio
    assert a.fade_in == a.fade_out == a.samples / rp.RATE / 2


def test_crossfade_is_centred_and_keeps_the_duration():
    segs = [{"in": 1, "out": 2}, {"in": 5, "out": 6.5}, {"in": 9, "out": 10}]
    plan = rp.build(_edl(segs), (1920, 1080), OUT, xfade_s=0.1)
    a, b, c = plan.audio
    assert (a.xfade, b.xfade, c.xfade) == (0, 4800, 4800)
    assert a.start == 48000 and a.samples == 48000 + 2400  # runs on half the fade
    assert b.start == 5 * 48000 - 2400 and b.samples == 72000 + 4800
    # parts minus the overlaps = the video's duration, to the sample
    total = sum(p.samples for p in plan.audio) - sum(p.xfade for p in plan.audio)
    assert total == round(plan.duration * rp.RATE)
    chain = "".join(rp.audio_chain(plan, "1:a"))
    assert chain.count("acrossfade=ns=4800:c1=qsin:c2=qsin") == 2
    assert "concat" not in chain


def test_crossfade_override_join_and_source_limits():
    segs = [
        {"in": 0.02, "out": 1},  # 20 ms before it: a 40 ms crossfade at most
        {"in": 1, "out": 2},  # continues the first: a join
        {"in": 3, "out": 4, "xfade_ms": 40},
        {"in": 4.5, "out": 9.9},  # the source ends at 9.92 s
        {"in": 2, "out": 3},
        {"in": 0, "out": 1},
    ]
    plan = rp.build(_edl(segs), (1920, 1080), OUT, xfade_s=0.2, source_duration=9.92)
    x = [p.xfade for p in plan.audio]
    assert x[1] == round(rp.JOIN_XFADE_S * rp.RATE) and plan.audio[1].join
    assert x[2] == 1920  # the segment's own 40 ms
    assert x[3] == 1920  # set on one side of the cut: that length
    assert x[4] == 1920  # 20 ms of source left after 9.9 s: a 40 ms crossfade
    assert x[5] == 0  # in at 0: nothing to lead in with
    chain = "".join(rp.audio_chain(plan, "1:a"))
    assert "c1=tri:c2=tri" in chain and "concat=n=2" in chain
    total = sum(p.samples for p in plan.audio) - sum(x)
    assert total == round(plan.duration * rp.RATE)


def test_graph_has_every_range_and_loudnorm_second_pass():
    plan = rp.build(_edl([{"in": 0, "out": 1}, {"in": 2, "out": 3}]), (1920, 1080), OUT)
    measured = {
        "input_i": "-20",
        "input_tp": "-3",
        "input_lra": "4",
        "input_thresh": "-30",
        "target_offset": "0.1",
    }
    g = rp.graph(plan, measured, "0:0", "1:a", _color.to_sdr({"color_transfer": "smpte2084"}))
    assert g.count("crop=") == 2 and g.count("atrim=start=") == 2
    assert "[0:0]split=2" in g and "[1:a]aresample" in g
    assert "linear=true" in g and "measured_I=-20" in g and "tonemap=" in g
    assert "linear=true" not in rp.graph(plan, None, "0:0", "0:1")
    assert "print_format=json" in rp.measure_graph(plan, "0:1")


def test_color_only_tonemaps_hdr():
    assert _color.to_sdr({"color_transfer": "bt709"}) == ""
    assert _color.output_tags({"color_transfer": None}) == []
    hlg = {"color_transfer": "arib-std-b67", "color_primaries": "bt2020", "color_space": "x"}
    chain = _color.to_sdr(hlg)
    assert "tin=arib-std-b67" in chain and "min=bt2020nc" in chain and "bt709" in chain
    assert "-color_trc" in _color.output_tags(hlg)
    assert ":npl=203" in chain and "tonemap=tonemap=mobius:desat=0" in chain


def test_audio_stream_is_picked_deliberately():
    from videocut.tools.ingest import _pick_audio

    v = {"index": 0, "codec_type": "video", "codec_name": "hevc"}
    spatial = {"index": 1, "codec_type": "audio", "codec_name": "apple_apac", "channels": 4}
    aac = {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2}
    opus = {"index": 3, "codec_type": "audio", "codec_name": "opus", "channels": 2}
    assert _pick_audio([v, spatial, aac])["index"] == 2
    assert _pick_audio([v, opus, aac])["index"] == 2  # AAC/PCM first
    assert _pick_audio([v, spatial, opus])["index"] == 3
    assert _pick_audio([v, spatial]) is None


def test_zoom_keys_at_the_same_time_last_in_file_wins():
    seg = {"in": 0, "out": 2, "zoom_keys": [[1.0, 1.6], [1.0, 1.2]]}
    plan = rp.build(_edl([seg]), (1920, 1080), OUT)
    assert [r.zoom for r in plan.ranges] == [1.0, 1.2]


def test_held_tail_repeats_the_last_frame_and_pads_silence_after_loudnorm():
    plan = rp.build(_edl([{"in": 0, "out": 1}, {"in": 2, "out": 3}]), (1920, 1080), OUT)
    assert plan.tail == 0 and "a=0,format=yuv420p" in rp.graph(plan, None, "0", "1:a")
    long = rp.with_tail(plan, 24)
    assert long.frames == plan.frames + 24 and long.tail == 24
    assert rp.with_tail(long, 6).frames == plan.frames + 6  # replaces, never stacks
    g = rp.graph(long, None, "0:0", "1:a")
    assert "concat=n=2:v=1:a=0,tpad=stop_mode=clone:stop=24," in g
    # silence goes on after loudnorm, so the spoken part's loudness does not change
    assert g.index("apad=whole_dur=2.800000") > g.index("loudnorm=")
    assert "apad=whole_dur=2.8" not in rp.measure_graph(long, "1:a")


def test_crossfade_is_capped_to_the_quiet_room():
    segs = [{"in": 1, "out": 2}, {"in": 5, "out": 6}, {"in": 8, "out": 9}]
    caps = {1: 0.04, 2: 0.0}  # 20 ms of quiet around cut 1, none around cut 2
    plan = rp.build(_edl(segs), (1920, 1080), OUT, xfade_s=0.12, xfade_caps=caps)
    assert [p.xfade for p in plan.audio] == [0, 1920, round(rp.MIN_XFADE_S * rp.RATE)]
