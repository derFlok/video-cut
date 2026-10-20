"""Material (tools/material.py, _material_providers/, _job_material.py, server/material.py):
job setting, keychain secrets, providers against recorded JSON, the wishes check, fetch with
fake providers, the human-only choice and its trusted record, the run gate, the API."""

import base64
import io
import json
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import run_tool

from videocut import run as pipeline
from videocut import settings
from videocut.server import create_app
from videocut.server import material as server_material
from videocut.tools import _job_material
from videocut.tools import _material_providers as mp
from videocut.tools import material as mat
from videocut.tools import plan as plan_tool
from videocut.tools.common import ToolError, binary, jobs_root, read_job
from videocut.tools.job import approve_job

FIX = Path(__file__).parent / "fixtures" / "material"
TOKEN = "test-token"
BASE = "http://127.0.0.1:4300"


def ffmpeg_ok() -> bool:
    try:
        binary("ffmpeg")
        return True
    except ToolError:
        return False


media = pytest.mark.skipif(not ffmpeg_ok(), reason="needs ffmpeg")


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def keychain(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(settings, "secret", lambda a: store.get(a))
    monkeypatch.setattr(settings, "has_secret", lambda a: a in store)

    def put(a, v):
        settings._account(a)
        if v:
            store[a] = v
        else:
            store.pop(a, None)

    monkeypatch.setattr(settings, "set_secret", put)
    return store


WISHES = [
    {"n": 1, "kind": "broll", "src_from": 2.0, "src_to": 6.0, "query": "city street rain night",
     "prompt": None, "style": "photo", "why": "zeigt die Stadt"},
    {"n": 2, "kind": "still", "src_from": 10.0, "src_to": 14.0,
     "query": "hands typing laptop office", "prompt": "A tidy desk with a laptop in soft light",
     "style": "photo", "why": "zeigt den Alltag"},
]  # fmt: skip


def plan_json(wishes=WISHES) -> dict:
    return {
        "title": "Zentrovia", "format": "9:16", "target_s": 24,
        "hook": {"text": "Hallo", "src": [0.0, 2.0]},
        "keep_list": [{"n": 1, "src": [0.0, 8.0], "text": "a", "why": "b"},
                      {"n": 2, "src": [9.0, 20.0], "text": "c", "why": "d"}],
        "material_wishes": wishes,
    }  # fmt: skip


def make_job(job_id="m1", mode="both", wishes=WISHES, approve=True) -> Path:
    job = jobs_root() / job_id
    for d in ("input", "work", "out"):
        (job / d).mkdir(parents=True)
    (job / "input" / "take.mov").write_bytes(b"x")
    data = {"id": job_id, "source": "input/take.mov", "type": "thought", "status": "new",
            "lang": "de", "material": {"mode": mode, "wish": "ruhige Bilder"}}  # fmt: skip
    (job / "job.json").write_text(json.dumps(data))
    (job / "work" / "project.md").write_text("# Plan\n")
    (job / "work" / "plan.json").write_text(json.dumps(plan_json(wishes)))
    if approve:
        approve_job(job)
    return job


def _ffmpeg(*args):
    import subprocess

    subprocess.run([binary("ffmpeg"), "-v", "error", "-y", *args], check=True)


class FakeStock(mp.StockProvider):
    name = "pexels"
    calls: list = []

    def available(self):
        return True, ""

    def search(self, query, kind, orientation, locale, limit):
        FakeStock.calls.append((query, kind, orientation, locale))
        return [
            mp.StockHit(id=f"pexels-{kind}-{i}", kind=kind, width=1080, height=1920,
                        duration_s=3.0 if kind == "video" else None, preview_url="",
                        file_url="https://example.invalid/f", author="Zentra Lumen",
                        source_url=f"https://www.pexels.com/x/{i}", licence="Pexels License",
                        attribution="Video by Zentra Lumen on Pexels", source="pexels")
            for i in range(3)
        ]  # fmt: skip

    def download(self, hit, dest, min_size=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if hit.kind == "video":
            _ffmpeg("-f", "lavfi", "-i", "testsrc=s=360x640:r=30:d=3", "-pix_fmt", "yuv420p",
                    str(dest))  # fmt: skip
        else:
            _ffmpeg("-f", "lavfi", "-i", "testsrc=s=540x960:d=1", "-frames:v", "1", str(dest))
        return dest


class FakeGen(mp.ImageProvider):
    name = "fakegen"
    model = "fake-image-1"
    price_usd = 0.10
    prompts: list = []

    def available(self):
        return True, ""

    def generate(self, prompt, width, height, seed, palette, style, dest):
        FakeGen.prompts.append(mp.full_prompt(prompt, palette, style))
        dest = dest.with_suffix(".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _ffmpeg("-f", "lavfi", "-i", "color=c=0x336699:s=576x1024:d=1", "-frames:v", "1",
                str(dest))  # fmt: skip
        return mp.GenResult(
            file=dest, model=self.model, revision="r7", seed=seed, watermark="synthid",
            elapsed_s=0.1, cost_usd=self.price_usd,
        )  # fmt: skip


@pytest.fixture
def fakes(monkeypatch):
    mp._load_builtin()
    monkeypatch.setitem(mp._REGISTRY, "pexels", FakeStock)
    monkeypatch.setitem(mp._REGISTRY, "fakegen", FakeGen)
    settings.save(image_provider="fakegen", stock_providers=["pexels"])
    FakeStock.calls.clear()
    FakeGen.prompts.clear()
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)


# ------------------------------------------------------------------ settings, job setting


def test_secret_accounts_are_a_closed_list_and_keys_are_checked():
    with pytest.raises(ValueError):
        settings.set_secret("someone-elses-account", "abc")
    with pytest.raises(ValueError):
        settings.set_secret("pexels-api-key", "abc -w other\nadd-generic-password")
    assert "gemini-api-key" in settings.SECRET_ACCOUNTS
    assert settings.load()["material_default_mode"] == "none"
    assert settings.load()["material_budget_usd"] == 1.0


def test_api_key_wrappers_use_the_anthropic_account(monkeypatch):
    seen = []
    monkeypatch.setattr(settings, "secret", lambda a: seen.append(a) or "k")
    monkeypatch.setattr(settings, "set_secret", lambda a, k: seen.append((a, k)))
    assert settings.api_key() == "k"
    settings.set_api_key("sk-1")
    assert seen == ["anthropic-api-key", ("anthropic-api-key", "sk-1")]


def test_job_material_setting_is_human_only_and_makes_the_plan_stale(capsys, monkeypatch):
    job = make_job(mode="stock")
    code, out, _ = run_tool(capsys, "job", "material", "m1", "--mode", "both")
    assert code == 0 and out["mode"] == "both"
    assert read_job(job)["plan_material"] == {"mode": "stock", "wish": "ruhige Bilder"}
    assert out["plan_stale"] and out["approval_revoked"]
    with pytest.raises(ToolError, match="other material"):
        approve_job(job)
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, out, _ = run_tool(capsys, "job", "material", "m1", "--mode", "none")
    assert code == 1 and "human" in out["error"]
    code, out, _ = run_tool(capsys, "job", "material", "m1")  # reading is fine
    assert code == 0 and out["mode"] == "both"
    with pytest.raises(ToolError):
        _job_material.parse_mode("everything")


# ------------------------------------------------------------------ wishes check


def test_plan_check_validates_wishes():
    plan = plan_json()
    assert mat.wish_errors(plan, "both") == []
    assert any("mode none" in e for e in mat.wish_errors(plan, "none"))
    bad = json.loads(json.dumps(plan))
    bad["material_wishes"][0]["src_from"], bad["material_wishes"][0]["src_to"] = 7.0, 10.0
    assert any("inside one keep-list row" in e for e in mat.wish_errors(bad, "both"))
    bad = json.loads(json.dumps(plan))
    bad["material_wishes"][0]["query"] = "rain"
    assert any(".query" in e for e in mat.wish_errors(bad, "stock"))
    bad = json.loads(json.dumps(plan))
    bad["material_wishes"][0]["kind"] = "icon"
    assert any("needs a generator" in e for e in mat.wish_errors(bad, "stock"))
    assert any("broll is stock" in e for e in mat.wish_errors(plan, "generated"))
    many = dict(plan, target_s=15)  # 15 s: one wish at most
    assert any("at most one per 8 s" in e for e in mat.wish_errors(many, "both"))
    errs = plan_tool.validate({**plan, "material_wishes": [{"n": 1}]}, material_mode="both")
    assert any(e.startswith("$.material_wishes[0]") for e in errs)


# ------------------------------------------------------------------ providers (recorded JSON)


def _fixture(name):
    return json.loads((FIX / name).read_text())


def test_pexels_parses_videos_and_photos(monkeypatch, keychain):
    keychain["pexels-api-key"] = "pexels-test"
    calls = []

    def fake_get(url, params=None, headers=None):
        calls.append((url, params, headers))
        return _fixture("pexels_videos.json" if "videos" in url else "pexels_photos.json")

    monkeypatch.setattr("videocut.tools._material_providers.pexels.get_json", fake_get)
    prov = mp.get_provider("pexels")
    vids = prov.search("city street rain", "video", "portrait", "de-DE", 4)
    assert calls[0][1] == {"query": "city street rain", "orientation": "portrait",
                           "per_page": 4, "locale": "de-DE", "size": "medium"}  # fmt: skip
    assert calls[0][2] == {"Authorization": "pexels-test"}
    assert vids[0].kind == "video" and vids[0].attribution == "Video by Zentra Lumen on Pexels"
    assert vids[0].licence == "Pexels License" and vids[0].duration_s == 12
    # smallest rendition that covers a 1080x1920 frame
    assert mp.pick_rendition(vids[0], (1080, 1920)).endswith("1080_1920.mp4")
    assert mp.pick_rendition(vids[0], None).endswith("2160_3840.mp4")
    photos = prov.search("desk laptop", "image", "portrait", "de-DE", 4)
    assert photos[0].kind == "image" and photos[0].attribution.startswith("Photo by")
    assert "h=1920" in mp.pick_rendition(photos[0], (1080, 1920))


def test_coverr_filters_by_orientation(monkeypatch, keychain):
    keychain["coverr-api-key"] = "coverr-test"
    seen = {}

    def fake_get(url, params=None, headers=None):
        seen["h"] = headers
        return _fixture("coverr_videos.json")

    monkeypatch.setattr("videocut.tools._material_providers.coverr.get_json", fake_get)
    prov = mp.get_provider("coverr")
    tall = prov.search("rain", "video", "portrait", "de-DE", 4)
    wide = prov.search("rain", "video", "landscape", "de-DE", 4)
    assert [h.id for h in tall] == ["coverr-v2"] and [h.id for h in wide] == ["coverr-v1"]
    assert seen["h"] == {"Authorization": "Bearer coverr-test"}
    assert prov.search("rain", "image", "portrait", "de-DE", 4) == []


def _pixabay(monkeypatch, keychain):
    keychain["pixabay-api-key"] = "pixabay-test"
    calls = []

    def fake_get(url, params=None, headers=None):
        calls.append((url, params, headers))
        return _fixture("pixabay_videos.json" if "videos" in url else "pixabay_photos.json")

    monkeypatch.setattr("videocut.tools._material_providers.pixabay.get_json", fake_get)
    return mp.get_provider("pixabay"), calls


def test_pixabay_parses_videos_and_filters_orientation(monkeypatch, keychain):
    prov, calls = _pixabay(monkeypatch, keychain)
    tall = prov.search("city street rain", "video", "portrait", "de-DE", 4)
    url, params, headers = calls[0]
    assert url == "https://pixabay.com/api/videos/" and headers is None
    assert params == {"key": "pixabay-test", "q": "city street rain", "lang": "de",
                      "safesearch": "true", "per_page": 50, "min_height": 1280}  # fmt: skip
    assert [h.id for h in tall] == ["pixabay-video-201", "pixabay-video-203"]
    v = tall[0]
    assert (v.kind, v.width, v.height, v.duration_s) == ("video", 2160, 3840, 14.0)
    assert v.licence == "Pixabay Content License" and v.source == "pixabay"
    assert v.attribution == "Video by Zentra Lumen on Pixabay"
    assert v.source_url == "https://pixabay.com/videos/id-201/"
    assert v.preview_url.endswith("201-1_medium.jpg")
    assert mp.pick_rendition(v, (1080, 1920)).endswith("201-1_medium.mp4")
    assert mp.pick_rendition(v, None).endswith("201-1_large.mp4")
    # an empty large rendition (url "", size 0) is skipped; tiny is never offered
    assert [r[2].rsplit("_", 1)[1] for r in tall[1].renditions] == ["small.mp4", "medium.mp4"]
    wide = prov.search("street", "video", "landscape", "en-US", 4)
    square = prov.search("pattern", "video", "square", "xx", 4)
    assert [h.id for h in wide] == ["pixabay-video-202"]
    assert [h.id for h in square] == ["pixabay-video-204"]
    assert calls[1][1]["lang"] == "en" and calls[1][1]["min_width"] == 1280
    assert calls[2][1]["lang"] == "en" and "min_width" not in calls[2][1]


def test_pixabay_photos_respect_the_1280_cap(monkeypatch, keychain):
    prov, calls = _pixabay(monkeypatch, keychain)
    photos = prov.search("desk laptop", "image", "portrait", "de-DE", 4)
    assert calls[0][0] == "https://pixabay.com/api/"
    assert calls[0][1]["orientation"] == "vertical" and calls[0][1]["image_type"] == "photo"
    basic, full = photos
    # default access: only largeImageURL, long side 1280, the size that is downloaded
    assert (basic.width, basic.height) == (960, 1280)
    assert basic.renditions == [(960, 1280, "https://pixabay.com/get/e5f6a7b8_1280.jpg")]
    assert basic.attribution == "Photo by Zentra Lumen on Pixabay"
    assert basic.preview_url.endswith("_640.jpg")
    # full API access: fullHDURL and imageURL too
    assert [r[:2] for r in full.renditions] == [(853, 1280), (1280, 1920), (4000, 6000)]
    assert mp.pick_rendition(full, (1080, 1920)).endswith("_1920.jpg")
    prov.search("desk laptop", "image", "landscape", "de-DE", 4)
    assert calls[1][1]["orientation"] == "horizontal"


def test_pixabay_caches_searches_for_24_hours(monkeypatch, keychain):
    import os
    import time

    prov, calls = _pixabay(monkeypatch, keychain)
    first = prov.search("rain", "video", "portrait", "de-DE", 4)
    again = prov.search("rain", "video", "portrait", "de-DE", 4)
    assert len(calls) == 1 and [h.id for h in again] == [h.id for h in first]
    files = list((Path(os.environ["VIDEOCUT_HOME"]) / "cache" / "pixabay").glob("*.json"))
    assert len(files) == 1 and "pixabay-test" not in files[0].read_text()
    old = time.time() - 25 * 3600
    os.utime(files[0], (old, old))
    prov.search("rain", "video", "portrait", "de-DE", 4)
    assert len(calls) == 2
    prov.search("rain", "video", "landscape", "de-DE", 4)  # other parameters, other entry
    assert len(calls) == 3


def test_pixabay_rate_limit_is_a_plain_message(monkeypatch, keychain):
    keychain["pixabay-api-key"] = "pixabay-secret"

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "0"},
                                     io.BytesIO(b"API rate limit exceeded"))  # fmt: skip

    monkeypatch.setattr(mp.urllib.request, "urlopen", boom)
    with pytest.raises(mp.ProviderError) as e:
        mp.get_provider("pixabay").search("rain", "video", "portrait", "de-DE", 4)
    assert str(e.value) == ("pixabay: rate limit (100 requests per minute), "
                            "try again in a minute")  # fmt: skip
    assert "pixabay-secret" not in str(e.value)


def _openverse(monkeypatch, answer=None):
    calls = []

    def fake_get(url, params=None, headers=None):
        calls.append((url, params, headers))
        return answer or _fixture("openverse_images.json")

    monkeypatch.setattr("videocut.tools._material_providers.openverse.get_json", fake_get)
    return mp.get_provider("openverse"), calls


def test_openverse_maps_results_and_asks_only_for_open_licences(monkeypatch, keychain):
    prov, calls = _openverse(monkeypatch)
    assert prov.available() == (True, "") and prov.secret_account is None and keychain == {}
    hits = prov.search("mountain lake", "image", "landscape", "de-DE", 4)
    url, params, headers = calls[0]
    assert url == "https://api.openverse.org/v1/images/"
    assert params == {"q": "mountain lake", "license": "cc0,pdm,by", "size": "large",
                      "page_size": 20, "mature": "false", "aspect_ratio": "wide"}  # fmt: skip
    assert headers["User-Agent"].startswith("videocut/")
    assert len(hits) == 3  # the third has no filetype: its file name says jpg
    h = hits[0]
    assert h.id == "openverse-2173901d-1566-41be-9603-341bc9b4c63f"
    assert (h.kind, h.width, h.height, h.duration_s) == ("image", 1616, 1096, None)
    assert h.file_url.startswith("https://upload.wikimedia.org/") and h.renditions == []
    assert h.preview_url.endswith("/thumb/") and h.author == "Stefan Karpiniec"
    assert h.source_url == "https://commons.wikimedia.org/w/index.php?curid=32459174"
    assert h.licence == "CC BY 2.0" and h.source == "openverse"
    assert h.attribution == ("Blue Lake, Snowy Mountains - Lake - 3 by Stefan Karpiniec, "
                             "CC BY 2.0, via Openverse/wikimedia")  # fmt: skip
    assert h.attribution_full.startswith('"Blue Lake, Snowy Mountains - Lake - 3" by Stefan')
    assert h.licence_url == "https://creativecommons.org/licenses/by/2.0/"
    assert hits[2].attribution.startswith("Blue Mountain Lake, Arkansas 001 by Adam")
    assert prov.search("mountain lake", "video", "landscape", "de-DE", 4) == []
    assert len(calls) == 1  # no video on Openverse: not even asked
    prov.search("mountain lake", "image", "portrait", "de-DE", 4)
    assert calls[1][1]["aspect_ratio"] == "tall"


def test_openverse_public_domain_credit_and_skips(monkeypatch):
    base = _fixture("openverse_images.json")["results"][0]

    def item(**kw):
        return {**base, "id": kw.pop("id"), **kw}

    answer = {"results": [
        item(id="cc0", license="cc0", license_version="1.0", title=""),
        item(id="html", license="cc0", title="<div class='fn'> Night &amp; Day</div>",
             creator="<span>Ana B</span>"),
        item(id="long", license="cc0", title="A scenic view " * 10),
        item(id="pdm", license="pdm", license_version="1.0"),
        item(id="gif", filetype="gif"),
        item(id="small", width=799, height=600),
        item(id="nosize", width=None),
        item(id="http", url="http://example.org/a.jpg"),
        item(id="sa", license="by-sa"),
        item(id="webp", filetype="webp"),
    ]}  # fmt: skip
    prov, _ = _openverse(monkeypatch, answer)
    hits = {h.id.removeprefix("openverse-"): h for h in
            prov.search("lake", "image", "square", "en-US", 10)}  # fmt: skip
    assert list(hits) == ["cc0", "html", "long", "pdm", "webp"]
    assert hits["html"].attribution == "Night & Day by Ana B (CC0) via Openverse/wikimedia"
    assert hits["html"].author == "Ana B"
    assert hits["long"].attribution.startswith("A scenic view A scenic")
    assert len(hits["long"].attribution.split(" by ")[0]) <= 80
    assert hits["cc0"].licence == "CC0 1.0"
    assert hits["cc0"].attribution == "Photo by Stefan Karpiniec (CC0) via Openverse/wikimedia"
    assert hits["pdm"].licence == "Public Domain Mark 1.0"
    assert hits["pdm"].attribution.endswith("(Public Domain) via Openverse/wikimedia")


def test_openverse_puts_large_hits_first(monkeypatch):
    base = _fixture("openverse_images.json")["results"][0]
    sizes = {"a": (1000, 900), "b": (2000, 1500), "c": (1279, 800), "d": (1280, 720)}
    answer = {"results": [{**base, "id": k, "width": w, "height": h}
                          for k, (w, h) in sizes.items()]}  # fmt: skip
    prov, _ = _openverse(monkeypatch, answer)
    hits = prov.search("lake", "image", "landscape", "en-US", 10)
    assert [h.id.removeprefix("openverse-") for h in hits] == ["b", "d", "a", "c"]


def test_openverse_rate_limit_and_one_retry_on_a_server_error(monkeypatch):
    seen = []

    def boom(req, timeout=None):
        seen.append(req.full_url)
        code = 502 if len(seen) == 1 else 429
        raise urllib.error.HTTPError(req.full_url, code, "no", {"Retry-After": "0"},
                                     io.BytesIO(b""))  # fmt: skip

    monkeypatch.setattr(mp.urllib.request, "urlopen", boom)
    with pytest.raises(mp.ProviderError) as e:
        mp.get_provider("openverse").search("rain", "image", "portrait", "de-DE", 4)
    assert str(e.value) == ("openverse: rate limit (20 searches per minute, 200 per day "
                            "without a key), try again later")  # fmt: skip
    assert len(seen) == 1 + 1 + mp.RETRIES_429  # the 502 once, then 429 with its retries
    assert "license=cc0%2Cpdm%2Cby" in seen[0]


def test_gemini_request_and_response(monkeypatch, keychain, tmp_path):
    keychain["gemini-api-key"] = "g-test"
    png = base64.b64encode(b"\x89PNG fake").decode()
    sent = {}

    def fake_post(url, payload, headers=None):
        sent.update(url=url, payload=payload, headers=headers)
        return {"steps": [{"type": "model_output", "content": [{"type": "image", "data": png}]}]}

    monkeypatch.setattr("videocut.tools._material_providers.gemini.post_json", fake_post)
    prov = mp.get_provider("gemini")
    res = prov.generate("A tidy desk", 1080, 1920, 7, ["#112233"], "photo", tmp_path / "c1")
    assert res.file.read_bytes() == b"\x89PNG fake"  # stored as returned (SynthID kept)
    assert res.watermark == "synthid" and res.model == "gemini-nano-banana-2.1"
    fmt = sent["payload"]["response_format"]
    assert fmt["aspect_ratio"] == "9:16" and fmt["image_size"] == "2K"
    text = sent["payload"]["input"][0]["text"]
    assert "deep navy blue" in text and "#" not in text  # a hex string would be drawn as text
    assert text.endswith("No text, no letters, no logos.")
    assert sent["headers"] == {"x-goog-api-key": "g-test"}
    assert prov.estimate_usd(1080, 1920) == 0.0504


def test_openai_request_and_response(monkeypatch, keychain, tmp_path):
    keychain["openai-api-key"] = "o-test"
    sent = {}

    def fake_post(url, payload, headers=None):
        sent.update(payload=payload, headers=headers)
        return {"data": [{"b64_json": base64.b64encode(b"img").decode()}]}

    monkeypatch.setattr("videocut.tools._material_providers.openai_image.post_json", fake_post)
    res = mp.get_provider("openai").generate("A desk", 1080, 1920, None, [], "illustration",
                                             tmp_path / "c2")  # fmt: skip
    w, h = (int(v) for v in sent["payload"]["size"].split("x"))
    assert w % 16 == 0 and h % 16 == 0 and abs(w / h - 1080 / 1920) < 0.02
    assert res.watermark == "c2pa" and sent["headers"]["Authorization"] == "Bearer o-test"


def test_http_waits_on_429_then_gives_up(monkeypatch):
    waits = []

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "2"},
                                     io.BytesIO(b""))  # fmt: skip

    monkeypatch.setattr(mp.urllib.request, "urlopen", boom)
    with pytest.raises(mp.ProviderError, match="429"):
        mp.request("GET", "https://api.pexels.com/v1/search?key=secret", sleep=waits.append)
    assert waits == [2.0, 2.0, 2.0]


def test_http_errors_never_show_a_key(monkeypatch):
    def boom(req, timeout=None):
        body = io.BytesIO(b'{"error": "bad key sk-abcdefghijklmnopqrstuvwxyz0123"}')
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, body)

    monkeypatch.setattr(mp.urllib.request, "urlopen", boom)
    with pytest.raises(mp.ProviderError) as e:
        mp.request("GET", "https://api.coverr.co/videos?api_key=topsecret")
    assert "topsecret" not in str(e.value) and "sk-abcdefghij" not in str(e.value)


def test_no_network_inside_an_agent_run(monkeypatch):
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    with pytest.raises(mp.ProviderError, match="agent run"):
        mp.request("GET", "https://api.pexels.com/v1/search")
    with pytest.raises(mp.ProviderError, match="HTTPS"):
        monkeypatch.delenv("VIDEOCUT_AGENT")
        mp.request("GET", "http://api.pexels.com/v1/search")


def test_registry_lists_builtin_providers():
    names = mp.providers()
    assert {"pixabay", "pexels", "coverr", "openverse", "gemini", "openai"} <= set(names)
    assert set(mp.providers("image")) >= {"gemini", "openai"}
    with pytest.raises(mp.ProviderError):
        mp.get_provider("nope")


# ------------------------------------------------------------------ fetch, choose, record


@media
def test_fetch_choose_round_trip(fakes, capsys, monkeypatch):
    job = make_job()
    res = mat.fetch(job, say=lambda _: None)
    assert res["stock"] == 4 + 3 and res["generated"] == 2  # broll: 3 videos + 1 photo
    assert FakeStock.calls[0] == ("city street rain night", "video", "portrait", "de-DE")
    data = json.loads((job / mat.FILE).read_text())
    w1, w2 = data["wishes"]
    assert [c["kind"] for c in w1["candidates"]] == ["video", "video", "video", "image"]
    gen = [c for c in w2["candidates"] if c["ai_generated"]]
    assert len(gen) == 2 and gen[0]["model"] == "fake-image-1@r7"
    assert gen[0]["licence"] == "generated" and gen[0]["watermark"] == "synthid"
    assert gen[0]["source_sha256"] == gen[0]["sha256"]
    for c in w1["candidates"] + w2["candidates"]:
        assert (job / c["file"]).is_file() and (job / c["thumb"]).is_file()
        assert c["sha256"] and c["attribution"]
    assert data["cost_usd"] == pytest.approx(0.2)
    runs = (job / "work" / "runs.jsonl").read_text()
    assert '"phase": "material"' in runs

    assert mat.pending(job) == [1, 2]
    code, out, _ = run_tool(capsys, "material", "choose", "m1", "1", "c2")
    assert code == 0 and out["pending"] == [2]
    code, out, _ = run_tool(capsys, "material", "choose", "m1", "2", "none")
    assert out["pending"] == []
    ch = mat.chosen(job)
    assert list(ch) == [1] and ch[1]["id"] == "c2" and ch[1]["kind"] == "video"
    data = json.loads((job / mat.FILE).read_text())
    assert data["wishes"][0]["chosen"] == "c2" and data["wishes"][1]["decided"]
    code, out, text = run_tool(capsys, "material", "show", "m1")
    assert out["chosen"][0]["file"] == "work/material/1/c2.mp4" and "chosen c2" in text

    # an agent editing material.json does not make a choice
    data["wishes"][1]["chosen"] = "c1"
    (job / mat.FILE).write_text(json.dumps(data))
    assert 2 not in mat.chosen(job)
    # a replaced file loses its choice
    (job / "work/material/1/c2.mp4").write_bytes(b"other")
    assert mat.chosen(job) == {} and mat.pending(job) == [1]


class SizedStock(FakeStock):
    """Photos whose downloaded file has the size in SIZES (the reported one is larger)."""

    SIZES = [(520, 760), (1080, 1920), (600, 400), (960, 1440), (1200, 1800), (1365, 2048)]

    def search(self, query, kind, orientation, locale, limit):
        if kind != "image":
            return []
        return [
            mp.StockHit(id=f"sized-{i}", kind="image", width=2000, height=3000, duration_s=None,
                        preview_url="", file_url="https://example.invalid/f", author="Zentra",
                        source_url="", licence="CC0 1.0", attribution="Photo", source="pexels")
            for i in range(len(self.SIZES))
        ]  # fmt: skip

    def download(self, hit, dest, min_size=None):
        w, h = self.SIZES[int(hit.id.split("-")[1])]
        dest.parent.mkdir(parents=True, exist_ok=True)
        _ffmpeg("-f", "lavfi", "-i", f"testsrc=s={w}x{h}:d=1", "-frames:v", "1", str(dest))
        return dest


@media
def test_fetch_drops_photos_too_small_on_disk_and_marks_small_ones(fakes, monkeypatch):
    monkeypatch.setitem(mp._REGISTRY, "pexels", SizedStock)
    still = [{**WISHES[1], "n": 1, "src_from": 2.0, "src_to": 6.0}]
    job = make_job(mode="stock", wishes=still)
    said = []
    mat.fetch(job, say=said.append)
    cands = json.loads((job / mat.FILE).read_text())["wishes"][0]["candidates"]
    # the probed size counts: 520x760 and 600x400 are dropped, the next hits fill up to 4
    assert [(c["id"], c["width"], c["height"], c["small"]) for c in cands] == [
        ("c1", 1080, 1920, False), ("c2", 960, 1440, False), ("c3", 1200, 1800, False),
        ("c4", 1365, 2048, False),
    ]  # fmt: skip
    assert any("sized-0: 520x760 px, too small" in m for m in said)
    assert not list((job / mat.DIR / "1").glob("c5*"))
    monkeypatch.setattr(SizedStock, "SIZES", [(1080, 1920), (900, 1200), (1200, 800)])
    mat.fetch(job, only=1, say=lambda _: None)
    cands = json.loads((job / mat.FILE).read_text())["wishes"][0]["candidates"]
    assert [(c["width"], c["small"]) for c in cands[-3:]] == [(1080, False), (900, True),
                                                               (1200, True)]  # fmt: skip


@media
def test_dry_run_uses_the_offline_fakes_without_keys(capsys, monkeypatch):
    job = make_job()
    settings.save(image_provider="gemini", stock_providers=["pexels"])  # no key stored
    monkeypatch.setenv("VIDEOCUT_MATERIAL_FAKE", "0")  # restored after --dry-run sets it
    code, out, _ = run_tool(capsys, "material", "fetch", "m1", "--dry-run")
    assert code == 0 and out["stock"] == 4 + 2 and out["generated"] == 2 and out["cost_usd"] == 0
    data = json.loads((job / mat.FILE).read_text())
    for c in data["wishes"][0]["candidates"] + data["wishes"][1]["candidates"]:
        assert c["source"] in ("fake-stock", "fake-image") and (job / c["file"]).is_file()
        assert c["ai_generated"] == (c["source"] == "fake-image")
    video = next(c for c in data["wishes"][0]["candidates"] if c["kind"] == "video")
    assert video["height"] > video["width"] and video["duration_s"] > 1
    assert "fake-stock" not in {p["name"] for p in server_material.image_providers()}


@media
def test_material_label_ignores_an_edited_mirror(fakes):
    from videocut.tools import _ai_label

    job = make_job()
    mat.fetch(job, say=lambda _: None)
    gen = next(c for c in mat.load(job)["wishes"][1]["candidates"] if c["ai_generated"])
    assert gen["provenance"]["c2pa"] in ("absent", "unknown")  # inspected, file untouched
    mat.choose(job, 2, gen["id"], say=lambda _: None)
    data = json.loads((job / mat.FILE).read_text())
    for c in data["wishes"][1]["candidates"]:
        c["ai_generated"], c["source"], c["licence"] = False, "pexels", "Pexels License"
    data["wishes"][0]["chosen"] = data["wishes"][0]["candidates"][0]["id"]  # never chosen
    (job / mat.FILE).write_text(json.dumps(data))
    read = _ai_label.read_material(job)
    w1, w2 = read["wishes"]
    assert w1["chosen"] is None and w2["chosen"] == gen["id"]
    assert _ai_label.is_ai({"candidate": next(c for c in w2["candidates"]
                                              if c["id"] == gen["id"])})  # fmt: skip


@media
def test_a_generator_failure_is_an_error_line_and_dest_is_png(fakes, monkeypatch):
    seen = []

    class Picky(FakeGen):  # like local_flux: a .png dest only, ValueError / RuntimeError
        def generate(self, prompt, width, height, seed, palette, style, dest):
            seen.append(dest.suffix)
            if len(seen) == 1:
                raise ValueError("dest must be a .png file")
            return super().generate(prompt, width, height, seed, palette, style, dest)

    monkeypatch.setitem(mp._REGISTRY, "fakegen", Picky)
    job = make_job()
    res = mat.fetch(job, say=lambda _: None)
    assert seen == [".png", ".png"] and res["generated"] == 1
    gen = [c for w in mat.load(job)["wishes"] for c in w["candidates"] if c["ai_generated"]]
    assert gen[0]["prompt"] == "A tidy desk with a laptop in soft light" and "steps" in gen[0]
    assert any("fakegen: dest must be a .png file" in e for e in res["errors"])


@media
def test_refusals_inside_an_agent_run(fakes, capsys, monkeypatch):
    make_job()
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    for argv in (("fetch", "m1"), ("generate", "m1"), ("choose", "m1", "1", "none")):
        code, out, _ = run_tool(capsys, "material", *argv)
        assert code == 1 and "agent run" in out["error"], argv
    code, out, _ = run_tool(capsys, "material", "show", "m1")
    assert code == 0 and out["pending"] == [1, 2]


@media
def test_cost_guard_refuses_generation_above_the_budget(fakes):
    job = make_job()
    settings.save(material_budget_usd=0.15)
    res = mat.fetch(job, say=lambda _: None)
    assert res["generated"] == 0
    assert any("cost about $0.20" in e and "$0.15 limit" in e for e in res["errors"])


@media
def test_refetch_one_wish_with_an_edited_query(fakes):
    job = make_job()
    mat.fetch(job, say=lambda _: None)
    mat.fetch(job, 1, query="quiet harbour morning boats", say=lambda _: None)
    assert FakeStock.calls[-1][0] == "quiet harbour morning boats"
    w = mat.load(job)["wishes"][0]
    assert w["query"] == "quiet harbour morning boats" and w["query_edited"]
    # the regenerate-only path keeps the stock candidates of wish 2
    before = [c["id"] for c in mat.load(job)["wishes"][1]["candidates"] if not c["ai_generated"]]
    mat.fetch(job, 2, prompt="A calm desk at dawn", stock=False, say=lambda _: None)
    w2 = mat.load(job)["wishes"][1]
    assert [c["id"] for c in w2["candidates"] if not c["ai_generated"]] == before
    assert FakeGen.prompts[-1].startswith("A calm desk at dawn")
    ids = [c["id"] for c in w2["candidates"]]
    assert len(ids) == len(set(ids))


def test_no_wishes_without_an_approved_plan(fakes):
    job = make_job(approve=False)
    assert mat.approved_wishes(job) == []
    with pytest.raises(ToolError, match="no material wishes"):
        mat.fetch(job, say=lambda _: None)


# ------------------------------------------------------------------ run gate


@media
def test_run_material_stage_waits_then_goes_on(fakes):
    job = make_job()
    lines = []
    assert pipeline.material_stage(job, False, lines.append) == pipeline.MATERIAL_WAIT
    assert any("material choose m1" in line for line in lines)
    mat.choose(job, 1, "c1", say=lambda _: None)
    mat.choose(job, 2, None, say=lambda _: None)
    assert pipeline.material_stage(job, False, lines.append) is None
    assert "1 item(s) chosen" in lines[-1]


@media
def test_run_material_skip_chooses_none(fakes):
    job = make_job()
    assert pipeline.material_stage(job, True, lambda _: None) is None
    assert mat.chosen(job) == {} and mat.pending(job) == []
    assert not (job / mat.DIR).exists()  # nothing fetched for a skip


def test_choices_change_makes_the_cut_stale_hash():
    job = make_job()
    before = mat.choices_sha(job)
    mat.choose(job, 1, None, say=lambda _: None)
    assert mat.choices_sha(job) != before
    assert mat.choices_sha(make_job("m2", mode="none")) is None


# ------------------------------------------------------------------ API


@pytest.fixture
def client():
    c = TestClient(create_app(token=TOKEN, port=4300), base_url=BASE)
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303
    c.headers.update({"Origin": BASE})
    return c


def test_api_settings_and_write_only_secrets(client, keychain):
    v = client.get("/api/settings/material").json()
    assert v["material_default_mode"] == "none"
    assert v["stock_providers"] == ["pixabay", "pexels", "coverr", "openverse"]
    names = {p["name"]: p for p in v["image_providers"]}
    assert names["gemini"]["registered"] and not names["gemini"]["available"]
    v = client.put("/api/settings/material", json={"material_default_mode": "stock",
                   "image_provider": "gemini", "material_budget_usd": 2.5}).json()  # fmt: skip
    assert v["material_default_mode"] == "stock" and v["material_budget_usd"] == 2.5
    assert client.put("/api/settings/material", json={"image_provider": "x"}).status_code == 400
    r = client.put("/api/settings/secrets", json={"account": "gemini-api-key", "value": "g-123"})
    assert r.json() == {"secrets": {"pixabay-api-key": False, "pexels-api-key": False,
                                    "coverr-api-key": False, "gemini-api-key": True,
                                    "openai-api-key": False}}  # fmt: skip
    assert "g-123" not in client.get("/api/settings/material").text
    assert "g-123" not in client.get("/api/settings/secrets").text
    bad = client.put("/api/settings/secrets", json={"account": "anthropic-api-key", "value": "a"})
    assert bad.status_code == 400
    client.put("/api/settings/secrets", json={"account": "gemini-api-key", "value": ""})
    assert keychain == {}


@media
def test_api_job_material_view_choose_and_files(client, fakes):
    job = make_job()
    mat.fetch(job, say=lambda _: None)
    v = client.get("/api/jobs/m1/material").json()
    assert v["mode"] == "both" and v["pending"] == [1, 2] and v["fetched"]
    c = v["wishes"][0]["candidates"][0]
    assert c["thumb_url"] == "/api/jobs/m1/material/files/1/c1_sm.jpg"
    assert client.get(c["file_url"]).headers["content-type"] == "video/mp4"
    assert client.get("/api/jobs/m1/material/files/1/..%2Fjob.json").status_code == 404
    assert client.get("/api/jobs/m1/material/files/x/c1.mp4").status_code == 404
    v = client.post("/api/jobs/m1/material/choose", json={"n": 1, "candidate": "c3"}).json()
    assert v["wishes"][0]["chosen"] == "c3" and v["pending"] == [2]
    r = client.post("/api/jobs/m1/material/choose", json={"n": 1, "candidate": "c9"})
    assert r.status_code == 400
    d = client.get("/api/jobs/m1").json()
    assert d["stage"] == "material" and d["gate"] == "material" and d["status"] == "waiting"
    assert d["material"]["pending"] == [2]
    client.post("/api/jobs/m1/material/choose", json={"n": 2, "candidate": None})
    d = client.get("/api/jobs/m1").json()
    assert d["stage"] == "cut" and d["gate"] is None


def test_api_fetch_needs_wishes(client):
    make_job(mode="none", wishes=[])
    r = client.post("/api/jobs/m1/material/fetch", json={})
    assert r.status_code == 409


@media
def test_an_edited_material_json_never_steers_the_choice_or_render(fakes):
    job = make_job()
    mat.fetch(job, say=lambda _: None)
    mat.choose(job, 2, "c5", say=lambda _: None)  # a generated still
    planted = job / "work/material/2/evil.mp4"
    planted.write_bytes(b"x")
    data = json.loads((job / mat.FILE).read_text())
    for c in data["wishes"][1]["candidates"]:
        c.update(file="work/material/2/evil.mp4", ai_generated=False, kind="video")
    (job / mat.FILE).write_text(json.dumps(data))
    got = mat.chosen(job)[2]
    assert got["file"] == "work/material/2/c5.png" and got["ai_generated"]
    # a candidate the agent adds to the mirror cannot be chosen
    data["wishes"][1]["candidates"].append({"id": "c99", "file": "work/material/2/evil.mp4"})
    (job / mat.FILE).write_text(json.dumps(data))
    with pytest.raises(ToolError, match="no candidate"):
        mat.choose(job, 2, "c99", say=lambda _: None)


@media
def test_a_refetch_that_finds_nothing_keeps_the_old_candidates(fakes, monkeypatch):
    job = make_job()
    mat.fetch(job, say=lambda _: None)
    mat.choose(job, 1, "c1", say=lambda _: None)
    monkeypatch.setattr(FakeStock, "search", lambda *a, **k: [])
    with pytest.raises(ToolError, match="no material found"):
        mat.fetch(job, 1, query="nothing to see here", say=lambda _: None)
    assert len(mat.load(job)["wishes"][0]["candidates"]) == 4
    assert mat.chosen(job)[1]["id"] == "c1"


def test_plan_check_survives_odd_wish_numbers():
    plan = plan_json([{**WISHES[0], "n": [1]}, {**WISHES[1], "n": {}}])
    assert any(".n: must be an integer" in e for e in mat.wish_errors(plan, "both"))
