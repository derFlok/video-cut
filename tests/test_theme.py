"""Default brand and the app theme that follows it (videocut/theme.py, server/theme.py)."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_brand import make_brand
from test_server import BASE, ORIGIN, TOKEN, FakePipeline

from videocut import brand, doctor, settings, theme
from videocut.paths import ROOT
from videocut.server import app as server_app
from videocut.server import create_app
from videocut.server import theme as theme_server

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def tokens_of(**colors) -> dict:
    base = {"bg": "#0B1A3A", "surface": "#13264F", "text": "#F4F1EA", "muted": "#B8C0D4",
            "accent": "#F2A33A"}  # fmt: skip
    return {"colors": {**base, **colors}, "fonts": {}, "logo": {}}


def url(p: Path) -> str:
    return f"/a/{p.name}"


def block(css: str, selector: str) -> dict:
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, selector
    return dict(kv.split(":", 1) for kv in m.group(1).split(";") if kv)


@pytest.fixture
def client():
    c = TestClient(create_app(token=TOKEN, port=4300, runner=FakePipeline()), base_url=BASE)
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303
    c.headers.update(ORIGIN)
    return c


# ---------------------------------------------------------------- mapping


def test_neutral_tokens_match_styles_css():
    css = (ROOT / "app" / "src" / "styles.css").read_text()
    light = css.split(":root {", 1)[1].split("}", 1)[0]
    dark = css.split(':root:not([data-theme="light"]) {', 1)[1].split("}", 1)[0]
    for text, ref in ((light, theme.NEUTRAL_LIGHT), (dark, theme.NEUTRAL_DARK)):
        found = dict(re.findall(r"--([\w-]+):\s*([^;]+);", text))
        for k, v in ref.items():
            assert found[k] == v, k


def test_dark_brand_maps_onto_tokens_in_both_schemes():
    out = theme.build(tokens_of(), url)
    light = block(out["css"], "html:root")
    dark = block(out["css"], 'html:root:not([data-theme="light"])')
    assert light == dark  # a dark brand keeps its palette in the dark scheme
    assert (light["--bg"], light["--surface"], light["--text"]) == ("#0B1A3A", "#13264F", "#F4F1EA")
    assert light["--primary"] == light["--focus"] == "#F2A33A"
    assert light["--primary-text"] == "#000000"  # black reads better on the warm accent
    assert light["color-scheme"] == "dark"
    assert light["--ok"] == theme.NEUTRAL_DARK["ok"]  # status colours stay neutral
    assert light["--line"] not in (light["--surface"], light["--text"])
    assert out["notes"] == []


def test_light_brand_keeps_neutral_dark_tokens_and_brings_its_accent():
    out = theme.build(
        tokens_of(
            bg="#FAF7F0", surface="#FFFFFF", text="#1A1A1A", muted="#5C5C5C", accent="#C2410C"
        ),  # fmt: skip
        url,
    )
    dark = block(out["css"], 'html:root:not([data-theme="light"])')
    assert dark["--bg"] == theme.NEUTRAL_DARK["bg"]
    assert dark["--primary"] == "#C2410C" and dark["--primary-text"] == "#FFFFFF"
    assert block(out["css"], "html:root")["color-scheme"] == "light"


def test_low_contrast_tokens_fall_back_to_neutral_with_a_note():
    out = theme.build(tokens_of(bg="#FFFFFF", surface="#FFFFFF", text="#BBBBBB",
                                muted="#DDDDDD", accent="#F5F5F5"), url)  # fmt: skip
    light = block(out["css"], "html:root")
    assert light["--text"] == theme.NEUTRAL_LIGHT["text"]
    assert light["--muted"] == theme.NEUTRAL_LIGHT["muted"]
    assert light["--primary"] == theme.NEUTRAL_LIGHT["primary"]
    codes = {(n["code"], n["scheme"]) for n in out["notes"]}
    assert {("text", "light"), ("muted", "light"), ("accent", "light")} <= codes
    assert ("accent", "dark") not in codes  # the pale accent stands out on neutral dark
    for k in ("--text", "--muted"):
        assert brand.contrast(light[k], light["--bg"]) >= 4.5
    assert brand.contrast(light["--primary"], light["--bg"]) >= 3


def test_every_text_pair_passes_aa_for_random_palettes():
    import random

    rnd = random.Random(7)
    hexes = lambda: "#" + "".join(rnd.choice("0123456789ABCDEF") for _ in range(6))  # noqa: E731
    for _ in range(300):
        t = tokens_of(bg=hexes(), surface=hexes(), text=hexes(), muted=hexes(), accent=hexes())
        css = theme.build(t, url)["css"]
        for sel in ("html:root", 'html:root:not([data-theme="light"])'):
            b = block(css, sel)
            for fg in ("--text", "--muted"):
                for bg in ("--bg", "--surface"):
                    assert brand.contrast(b[fg], b[bg]) >= 4.5, (t, sel, fg, bg)
            assert brand.contrast(b["--primary-text"], b["--primary"]) >= 4.5
            assert brand.contrast(b["--primary"], b["--bg"]) >= 3


def test_fonts_and_logo():
    t = tokens_of()
    t["fonts"] = {"display_file": "/b/fonts/Head.woff2", "body_file": None, "files": {},
                  "display": "fonts/Head.woff2", "body": "Some Family; } x"}  # fmt: skip
    t["logo"] = {"light": "/b/logo/l.png", "dark": None}
    out = theme.build(t, url)
    assert (
        '@font-face{font-family:"vc-brand-display";src:url("/a/Head.woff2") format("woff2")'
        in out["css"]
    )
    light = block(out["css"], "html:root")
    assert light["--font"].startswith('"Some Family x", system-ui')  # sanitised, then system
    assert light["--font-display"].startswith('"vc-brand-display", "Some Family x"')
    # a dark brand shows the only logo it has in both schemes
    assert out["logo"] == {"light": "/a/l.png", "dark": "/a/l.png"}
    t["colors"]["bg"] = "#FFFFFF"
    t["colors"]["text"] = "#111111"
    t["logo"] = {"light": "/b/l.png", "dark": "/b/d.png"}
    assert theme.build(t, url)["logo"] == {"light": "/a/l.png", "dark": "/a/d.png"}


# ---------------------------------------------------------------- settings and API


def test_settings_round_trip_and_theme(client, videocut_home):
    make_brand(videocut_home, "zentrovia")
    view = client.get("/api/theme").json()
    assert view["brand_id"] is None and view["css"] == ""
    assert view["settings"] == {"default_brand": None, "theme_follows_brand": True}
    view = client.put("/api/settings/brand", json={"default_brand": "zentrovia"}).json()
    assert view["brand_id"] == "zentrovia" and "--bg:#101010" in view["css"]
    assert view["logo"]["dark"].startswith("/api/brands/zentrovia/assets/logo.svg?v=")
    assert json.loads(settings.path().read_text())["default_brand"] == "zentrovia"
    brands = client.get("/api/brands").json()
    assert [b["id"] for b in brands if b["default"]] == ["zentrovia"]
    off = client.put("/api/settings/brand", json={"theme_follows_brand": False}).json()
    assert off["css"] == "" and off["settings"] == {"default_brand": "zentrovia",
                                                    "theme_follows_brand": False}  # fmt: skip
    assert client.put("/api/settings/brand", json={"default_brand": "nope"}).status_code == 404
    assert (
        client.put("/api/settings/brand", json={"default_brand": None}).json()["settings"][
            "default_brand"
        ]
        is None
    )


def test_delete_brand_clears_the_default(client, videocut_home):
    make_brand(videocut_home, "zentrovia")
    client.put("/api/settings/brand", json={"default_brand": "zentrovia"})
    assert client.delete("/api/brands/_example").status_code == 409
    assert client.delete("/api/brands/zentrovia").json() == {"deleted": "zentrovia"}
    assert not (videocut_home / "brands" / "zentrovia").exists()
    assert settings.load()["default_brand"] is None
    assert client.get("/api/theme").json()["brand_id"] is None


def test_a_brand_removed_outside_the_app_reads_as_neutral(client, videocut_home):
    d = make_brand(videocut_home, "zentrovia")
    settings.save(default_brand="zentrovia")
    (d / "brand.json").unlink()
    assert settings.default_brand() is None
    assert client.get("/api/theme").json()["brand_id"] is None
    check = doctor.check_default_brand()
    assert not check.ok and "missing" in check.detail and not check.required


def test_doctor_shows_the_default_brand(videocut_home):
    assert doctor.check_default_brand().detail == "none (neutral)"
    make_brand(videocut_home, "zentrovia")
    settings.save(default_brand="zentrovia")
    assert doctor.check_default_brand().detail == "zentrovia (app look follows it)"


def test_preview_of_an_unsaved_brand(client, videocut_home):
    make_brand(videocut_home, "zentrovia")
    data = client.get("/api/brands/zentrovia").json()
    data["colors"]["text"] = "#151515"  # unreadable on the dark background
    r = client.post("/api/brands/zentrovia/theme", json=data).json()
    assert [n["code"] for n in r["notes"]] == ["text", "muted"]  # muted defaults to text
    assert (
        json.loads((videocut_home / "brands/zentrovia/brand.json").read_text())["colors"]["text"]
        == "#F0F0F0"
    )  # nothing saved
    data["vocabulary"] = [{"term": "", "variants": []}]  # a row being typed
    assert client.post("/api/brands/zentrovia/theme", json=data).status_code == 200
    data["colors"]["bg"] = "nope"
    assert client.post("/api/brands/zentrovia/theme", json=data).status_code == 422


def test_brand_assets_are_served_from_the_brand_folder_only(client, videocut_home):
    d = make_brand(videocut_home, "zentrovia")
    (d / "logo").mkdir()
    (d / "logo" / "l.png").write_bytes(PNG)
    r = client.get("/api/brands/zentrovia/assets/logo/l.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    svg = client.get("/api/brands/zentrovia/assets/logo.svg")
    assert svg.headers["content-security-policy"].startswith("default-src 'none'")
    assert client.get("/api/brands/zentrovia/assets/fonts/a.woff2").status_code == 200
    for bad in ("brand.json", "../zentrovia/brand.json", "%2e%2e/%2e%2e/settings.json"):
        assert client.get(f"/api/brands/zentrovia/assets/{bad}").status_code == 404


def test_index_carries_the_theme_inline(client, videocut_home, tmp_path, monkeypatch):
    ui = tmp_path / "dist"
    ui.mkdir()
    (ui / "index.html").write_text("<html><head><title>x</title></head><body></body></html>")
    monkeypatch.setattr(server_app, "_ui_dir", lambda: ui)
    c = TestClient(create_app(token=TOKEN, port=4300, runner=FakePipeline()), base_url=BASE)
    c.get(f"/?t={TOKEN}")
    assert '<style id="brand-theme"></style>' in c.get("/").text
    make_brand(videocut_home, "zentrovia", name="Zentrovia </script><b>")
    settings.save(default_brand="zentrovia")
    page = c.get("/").text
    assert "--bg:#101010" in page.split('<style id="brand-theme">')[1].split("</style>")[0]
    data = page.split('id="brand-theme-data">')[1].split("</script>")[0]
    assert json.loads(data)["name"] == "Zentrovia </script><b>"  # escaped as <
    assert c.get("/index.html").text == page


def test_a_broken_settings_file_leaves_the_app_neutral(client, videocut_home):
    settings.path().parent.mkdir(parents=True, exist_ok=True)
    settings.path().write_text("{broken")
    assert client.get("/api/theme").json()["css"] == ""
    assert '<style id="brand-theme"></style>' in theme_server.inject("<head></head>")
