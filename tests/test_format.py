"""Output format presets, the default rule and safe zones (tools/_format.py)."""

import pytest

from videocut.tools import _format as fm
from videocut.tools import _geometry as geo


@pytest.mark.parametrize(
    ("fmt", "size", "aspect"),
    [
        ("9:16", (1080, 1920), "portrait"),
        ("16:9", (1920, 1080), "landscape"),
        ("1:1", (1080, 1080), "square"),
        ("4:5", (1080, 1350), "portrait"),
    ],
)
def test_presets(fmt, size, aspect):
    job = {"source": "input/a.mov", "format": fmt}
    assert fm.job_format(job) == fmt and fm.output_size(job) == size
    assert fm.FORMATS[fmt] == size and fm.aspect_class(size) == aspect
    assert fm.nearest_format(size) == fmt
    assert fm.nearest_format((size[0] // 2, size[1] // 2)) == fmt


@pytest.mark.parametrize(
    ("job", "fmt"),
    [
        ({"source": "input/a.mov"}, "9:16"),  # camera only: a reel
        ({"source": "input/a.mov", "screen": "input/s.mov"}, "16:9"),  # product demo
        ({"source": "input/a.mov", "assets": ["input/shot.png"]}, "16:9"),  # screenshots
        ({"source": "input/a.mov", "assets": []}, "9:16"),
        ({"source": "input/a.mov", "screen": "input/s.mov", "format": "9:16"}, "9:16"),
        ({"source": "input/a.mov", "format": "1:1"}, "1:1"),  # explicit wins
    ],
)
def test_default_rule_and_explicit_wins(job, fmt):
    assert fm.job_format(job) == fmt
    assert fm.default_format(job) == ("16:9" if job.get("screen") or job.get("assets") else "9:16")


def test_job_dict_is_never_rewritten():
    job = {"source": "input/a.mov", "screen": "input/s.mov"}
    fm.output_size(job)
    assert "format" not in job


def test_parse_format_accepts_spellings_and_lists_presets_on_error():
    assert fm.parse_format(" 16x9 ") == "16:9" and fm.parse_format("4/5") == "4:5"
    with pytest.raises(fm.FormatError) as e:
        fm.parse_format("21:9")
    assert all(k in str(e.value) for k in fm.FORMATS) and "1920x1080" in str(e.value)
    with pytest.raises(fm.FormatError):
        fm.job_format({"format": "wide"})


def test_safe_zones_keep_the_reel_numbers_and_follow_the_aspect():
    assert fm.safe_zones((1080, 1920)) == {"top": 250, "bottom": 350, "side": 65}
    assert fm.safe_zones((540, 960)) == {"top": 125, "bottom": 175, "side": 32}
    assert geo.safe_zones((1080, 1920)) == fm.safe_zones((1080, 1920))
    assert fm.safe_zones((1920, 1080)) == {"top": 65, "bottom": 130, "side": 77}
    assert fm.safe_zones((960, 540)) == {"top": 32, "bottom": 65, "side": 38}
    assert fm.safe_zones((1080, 1080)) == {"top": 76, "bottom": 151, "side": 54}
    assert fm.safe_zones((1080, 1350)) == {"top": 108, "bottom": 203, "side": 54}
    for size in fm.FORMATS.values():  # room for the picture in every format
        z = fm.safe_zones(size)
        assert z["top"] + z["bottom"] < size[1] * 0.35 and z["side"] < size[0] * 0.07


def test_old_reel_formula_is_kept_for_every_height():
    for h in range(2, 4000, 2):
        w = h * 9 // 16
        z = fm.safe_zones((w, h))
        assert (z["top"], z["bottom"]) == (round(250 * (h / 1920)), round(350 * (h / 1920)))
