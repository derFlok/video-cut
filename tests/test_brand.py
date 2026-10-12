import json
from pathlib import Path

import pytest
from helpers import run_tool

from videocut import brand

FONT = b"\0\1\0\0not-a-real-font"


def make_brand(home: Path, bid: str = "t1", **over) -> Path:
    """A valid brand folder under $VIDEOCUT_HOME/brands; `over` replaces top-level keys."""
    d = home / "brands" / bid
    (d / "fonts").mkdir(parents=True)
    (d / "fonts" / "a.woff2").write_bytes(FONT)
    (d / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    data = {
        "name": "Test",
        "colors": {"bg": "#101010", "surface": "#202020", "text": "#F0F0F0", "accent": "#E03060"},
        "fonts": {
            "display": "fonts/a.woff2",
            "body": "Some Family",
            "files": {"400": "fonts/a.woff2"},
        },
        "logo": {"light": "logo.svg", "dark": None, "placement": "corner"},
        "captions": {"style": "box", "case": "upper", "highlight": "scale", "position": "lower"},
        "motion": {"character": "knackig", "spring": {"w": 20, "z": 0.9}, "transitions": "cut"},
        "voice": {"address": "du"},
        "future_field": {"kept": True},
    }
    data.update(over)
    (d / "brand.json").write_text(json.dumps(data))
    return d


def errors_of(ref: str) -> list[str]:
    return brand.inspect(ref)[1]


def test_example_brand_loads_and_passes(capsys):
    tokens = brand.load("_example")
    assert tokens["id"] == "_example"
    assert all(brand.contrast(tokens["caption_colors"][k], tokens["caption_colors"]["box"]) >= 4.5
               for k in ("text", "highlight"))  # fmt: skip
    code, summary, _ = run_tool(capsys, "brand", "check", "_example")
    assert code == 0 and summary["ok"] and summary["derived"] == []


def test_valid_profile_resolves_assets_and_keeps_extra_fields(videocut_home):
    d = make_brand(videocut_home)
    t = brand.load("t1")
    assert t["fonts"]["files"] == {"400": str((d / "fonts/a.woff2").resolve())}
    assert t["fonts"]["display_file"] == str((d / "fonts/a.woff2").resolve())
    assert t["fonts"]["body_file"] is None  # a family name, no file
    assert t["logo"]["light"] == str((d / "logo.svg").resolve()) and t["logo"]["dark"] is None
    assert t["future_field"] == {"kept": True}
    assert set(t["caption_colors"]) == {"text", "box", "highlight"}
    assert brand.list_ids() == ["_example", "t1"]


def test_light_accent_on_white_is_darkened_to_aa(videocut_home):
    colors = {"bg": "#FFFFFF", "surface": "#FFFFFF", "text": "#111111", "accent": "#FFD84D"}
    make_brand(videocut_home, colors=colors)
    t = brand.load("t1")
    hl = t["caption_colors"]["highlight"]
    assert hl != "#FFD84D" and brand.contrast(hl, "#FFFFFF") >= 4.5
    assert t["caption_colors"]["text"] == "#111111"  # already fine: untouched
    assert len(t["derived"]) == 1 and "highlight" in t["derived"][0]


def test_dark_text_on_dark_box_is_lightened():
    out, notes = brand.derive_caption_colors(
        {"bg": "#000000", "surface": "#101010", "text": "#303030", "accent": "#FFFFFF"}, "box"
    )
    assert brand.contrast(out["text"], "#101010") >= 4.5 and len(notes) == 1
    assert out["text"] != "#303030" and out["highlight"] == "#FFFFFF"


def test_plain_captions_use_background_as_backing(videocut_home):
    make_brand(videocut_home, captions={"style": "plain"})
    assert brand.load("t1")["caption_colors"]["box"] == "#101010"


def test_missing_required_and_bad_colours_have_json_paths(videocut_home):
    make_brand(videocut_home, name="", colors={"bg": "red", "text": "#fff"})
    errs = "\n".join(errors_of("t1"))
    assert "$.name" in errs and "$.colors.bg" in errs and "$.colors.accent: required" in errs


def test_enum_errors_name_the_path_and_allowed_values(videocut_home):
    make_brand(videocut_home, captions={"style": "banana", "highlight": "scale"})
    errs = errors_of("t1")
    assert any(e.startswith("$.captions.style") and "box|outline|plain" in e for e in errs)


def test_free_text_enum_resolves_to_its_first_known_word(videocut_home):
    make_brand(
        videocut_home,
        captions={"highlight": "accent_underline"},
        motion={"character": "ruhig, präzise", "transitions": "opacity + blur"},
    )
    t = brand.load("t1")
    assert t["captions"]["highlight"] == "accent"
    assert t["captions"]["highlight_text"] == "accent_underline"
    assert t["motion"]["character"] == "ruhig" and t["motion"]["transitions"] == "blur"


def test_asset_errors(videocut_home):
    d = make_brand(videocut_home)
    data = json.loads((d / "brand.json").read_text())
    data["fonts"] = {"display": "fonts/missing.woff2", "files": {"400": "fonts/a.woff2", "x": "a"}}
    data["logo"] = {"light": "logo.gif", "dark": "../escape.svg", "icon_light": "nope.png"}
    (d / "brand.json").write_text(json.dumps(data))
    errs = "\n".join(errors_of("t1"))
    assert "$.fonts.display: file not found" in errs
    assert "$.fonts.files.x" in errs
    assert "$.logo.light" in errs and ".png, .svg" in errs
    assert "$.logo.dark" in errs and "outside the brand folder" in errs
    assert "$.logo.icon_light: file not found" in errs


def test_font_with_unsupported_extension_is_an_error(videocut_home):
    d = make_brand(videocut_home)
    (d / "fonts" / "b.woff").write_bytes(FONT)
    data = json.loads((d / "brand.json").read_text())
    data["fonts"]["display"] = "fonts/b.woff"
    (d / "brand.json").write_text(json.dumps(data))
    assert any("$.fonts.display" in e and ".woff2" in e for e in errors_of("t1"))


def test_invalid_json_and_unknown_brand(videocut_home, capsys):
    d = make_brand(videocut_home)
    (d / "brand.json").write_text("{nope")
    assert errors_of("t1")[0].startswith("$: cannot read")
    with pytest.raises(brand.BrandError):
        brand.load("t1")
    with pytest.raises(brand.BrandError, match="no brand"):
        brand.load("ghost")
    code, summary, _ = run_tool(capsys, "brand", "check", "ghost")
    assert code == 1 and not summary["ok"]


def test_check_exit_codes_and_warnings(videocut_home, capsys):
    make_brand(videocut_home, "good", logo={"placement": "outro", "rules": "keep clear space"})
    code, summary, out = run_tool(capsys, "brand", "check", "good")
    assert code == 0
    assert any("placement is 'outro' but no logo file" in w for w in summary["warnings"])
    assert any("keep clear space" in w for w in summary["warnings"])
    assert "caption text on box" in out
    make_brand(videocut_home, "bad", captions={"style": "x"})
    code, summary, _ = run_tool(capsys, "brand", "check", "bad")
    assert code == 1 and "$.captions.style" in summary["error"]


def test_list_and_show(videocut_home, capsys):
    make_brand(videocut_home)
    code, summary, out = run_tool(capsys, "brand", "list")
    assert code == 0 and [b["id"] for b in summary["brands"]] == ["_example", "t1"]
    assert "t1\tTest\tok" in out
    code, summary, _ = run_tool(capsys, "brand", "show", "t1")
    assert code == 0 and summary["tokens"]["caption_colors"]["box"] == "#202020"


def test_scaffold_test_brands_are_valid(videocut_home, capsys):
    from videocut.tools import brand as tool

    if not any(
        (d / n).is_file()
        for d in tool.SYSTEM_FONT_DIRS
        for v in tool.TEST_FONTS.values()
        for n in v
    ):
        pytest.skip("no macOS system font found")  # fmt: skip
    code, summary, _ = run_tool(capsys, "brand", "scaffold-test-brands")
    assert code == 0
    for bid in ("brand-a", "brand-b"):
        t = brand.load(bid)
        assert t["fonts"]["files"] and Path(t["logo"]["light"]).is_file()
    assert brand.load("brand-a")["captions"]["case"] == "upper"
    assert brand.load("brand-b")["motion"]["character"] == "federnd"
    code, summary, _ = run_tool(capsys, "brand", "scaffold-test-brands")
    assert summary["created"] == []  # existing brands are kept
