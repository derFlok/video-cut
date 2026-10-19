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
    assert "#112233" in text and "No text" in text
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
    assert {"pexels", "coverr", "gemini", "openai"} <= set(names)
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
    assert v["material_default_mode"] == "none" and v["stock_providers"] == ["pexels", "coverr"]
    names = {p["name"]: p for p in v["image_providers"]}
    assert names["gemini"]["registered"] and not names["gemini"]["available"]
    v = client.put("/api/settings/material", json={"material_default_mode": "stock",
                   "image_provider": "gemini", "material_budget_usd": 2.5}).json()  # fmt: skip
    assert v["material_default_mode"] == "stock" and v["material_budget_usd"] == 2.5
    assert client.put("/api/settings/material", json={"image_provider": "x"}).status_code == 400
    r = client.put("/api/settings/secrets", json={"account": "gemini-api-key", "value": "g-123"})
    assert r.json() == {"secrets": {"pexels-api-key": False, "coverr-api-key": False,
                                    "gemini-api-key": True, "openai-api-key": False}}  # fmt: skip
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
