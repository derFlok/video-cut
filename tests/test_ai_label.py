"""AI labelling and provenance without media: which material is on screen (_ai_label), the
badge items, the verify flag, the disclosure document, the C2PA manifest and its tool
call (mocked), the API and the doctor line. A hand-written work/material.json + edl.json
(the shapes of the material contract) stand in for the material tools."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from videocut import doctor, settings
from videocut.edl import LAYOUTS, from_dict
from videocut.server import app as server_app
from videocut.tools import _ai_label, _c2pa, _disclosure, _verify_ai

# the material tools bring layout "broll"; until then the segment is a face segment that
# carries "material" all the same
BROLL = "broll" if "broll" in LAYOUTS else "face"


def candidate(cid: str, **kw) -> dict:
    base = {
        "id": cid, "kind": "image", "file": f"work/material/1/{cid}.png", "width": 1080,
        "height": 1920, "source": "local", "licence": "generated", "ai_generated": True,
        "model": "zentro-image-1 rev abc123", "prompt": "a calm desk with a plant, morning light",
        "seed": 7, "watermark": "none", "author": None, "attribution": None,
        "source_url": None,
    }  # fmt: skip
    return {**base, **kw}


STOCK = candidate(
    "s1", kind="video", source="pexels", licence="Pexels License", ai_generated=False,
    model=None, prompt=None, author="Mira Holm", attribution="Video by Mira Holm on Pexels",
    source_url="https://example.com/v/1",
)  # fmt: skip


def material(*wishes: dict) -> dict:
    return {"version": 1, "wishes": list(wishes)}


def wish(n: int, chosen: str | None, cands: list[dict], style: str = "photo", **kw) -> dict:
    return {"n": n, "kind": "still", "src_from": 1.0, "src_to": 3.0, "query": "desk plant",
            "prompt": "a calm desk", "style": style, "chosen": chosen, "candidates": cands,
            **kw}  # fmt: skip


def edl_doc(**extra) -> dict:
    return {
        "fps": 30,
        "size": [1080, 1920],
        "source": "input/take.mp4",
        "segments": [
            {"in": 0.0, "out": 2.0},
            {"in": 3.0, "out": 5.0, "layout": BROLL, "material": 1},
            {"in": 6.0, "out": 8.0},
        ],
        **extra,
    }


@pytest.fixture(autouse=True)
def hand_written_material(monkeypatch):
    """These tests hand the label logic a material document directly (work/material.json
    here); in the product _ai_label.read_material builds it from the trusted copy and the
    human's checked choices (test_material_label_ignores_an_edited_mirror)."""

    def read(job: Path) -> dict:
        try:
            return json.loads((job / "work" / "material.json").read_text())
        except OSError:
            return {}

    monkeypatch.setattr(_ai_label, "read_material", read)


def make_job(tmp_path: Path, mat: dict, edl: dict, lang: str = "de") -> Path:
    job = tmp_path / "job"
    (job / "work").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"id": "job", "lang": lang}))
    (job / "work" / "material.json").write_text(json.dumps(mat))
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    return job


def shown(job: Path, edl: dict) -> list[dict]:
    return _ai_label.on_screen(job, from_dict(edl), edl)


# ---------------------------------------------------------------- on screen, badges


def test_a_generated_photo_on_a_broll_segment_gets_a_badge(tmp_path):
    edl = edl_doc()
    job = make_job(tmp_path, material(wish(1, "c1", [candidate("c1")])), edl)
    items = shown(job, edl)
    assert [(i["n"], i["kind"], i["t0"], i["t1"]) for i in items] == [(1, "broll", 2.0, 4.0)]
    badges = _ai_label.badge_items(items, "de")
    assert badges == [{"scene": "ai_badge", "t0": 2.0, "t1": 4.0,
                       "props": {"text": "KI-generiert", "material": [1]}}]  # fmt: skip
    assert _ai_label.badge_items(items, "en")[0]["props"]["text"] == "AI-generated"


def test_stock_unchosen_and_unknown_material_get_no_badge(tmp_path):
    card = {"scene": "still_card", "t0": 5.0, "t1": 5.5, "props": {"material": 9}}
    edl = edl_doc(overlays=[card])
    mat = material(wish(1, "s1", [STOCK, candidate("c1")]))
    job = make_job(tmp_path, mat, edl)
    items = shown(job, edl)
    assert [i["candidate"]["id"] for i in items] == ["s1"]
    assert _ai_label.badge_items(items, "de") == []
    job2 = make_job(tmp_path / "b", material(wish(1, None, [candidate("c1")])), edl)
    assert shown(job2, edl) == []
    (job2 / "work" / "material.json").unlink()
    assert shown(job2, edl) == []


def test_a_still_card_overlay_is_labelled_over_its_span(tmp_path):
    edl = edl_doc(overlays=[{"scene": "still_card", "t0": 0.5, "t1": 1.5,
                             "props": {"material": 2, "candidate": "g2"}}])  # fmt: skip
    mat = material(wish(1, "s1", [STOCK]), wish(2, "g1", [candidate("g1"), candidate("g2")]))
    job = make_job(tmp_path, mat, edl)
    items = shown(job, edl)
    assert [(i["kind"], i["candidate"]["id"]) for i in items] == [("still_card", "g2"),
                                                                 ("broll", "s1")]  # fmt: skip
    assert [(b["t0"], b["t1"]) for b in _ai_label.badge_items(items, "de")] == [(0.5, 1.5)]


def test_neighbouring_spans_merge_into_one_badge():
    def it(n, t0, t1):
        return {"n": n, "t0": t0, "t1": t1, "wish": {}, "candidate": candidate("c")}

    badges = _ai_label.badge_items([it(1, 0.0, 2.0), it(2, 2.2, 3.0), it(3, 5.0, 6.0)], "en")
    assert [(b["t0"], b["t1"], b["props"]["material"]) for b in badges] == [
        (0.0, 3.0, [1, 2]),
        (5.0, 6.0, [3]),
    ]


def test_illustrations_follow_the_setting_photos_never_do(tmp_path):
    edl = edl_doc()
    mat = material(wish(1, "c1", [candidate("c1")], style="illustration"))
    job = make_job(tmp_path, mat, edl)
    items = shown(job, edl)
    assert len(_ai_label.badge_items(items, "de")) == 1  # default: on
    settings.save(ai_label_illustrations=False)
    assert _ai_label.badge_items(items, "de") == []
    photo = make_job(tmp_path / "p", material(wish(1, "c1", [candidate("c1")])), edl)
    assert len(_ai_label.badge_items(shown(photo, edl), "de")) == 1  # cannot be switched off


def test_a_generator_source_counts_as_ai_even_without_the_flag(tmp_path):
    edl = edl_doc()
    cand = candidate("c1", ai_generated=False, source="gemini")
    job = make_job(tmp_path, material(wish(1, "c1", [cand])), edl)
    assert len(_ai_label.badge_items(shown(job, edl), "de")) == 1


# ---------------------------------------------------------------- verify


def _boxes(*spans) -> list[dict]:
    return [{"scene": "ai_badge", "t0": a, "t1": b, "box": [65, 250, 300, 300], "drawn": [a, b]}
            for a, b in spans]  # fmt: skip


@pytest.mark.parametrize(
    ("boxes", "missing"),
    [
        (_boxes((2.0, 3.967)), False),  # the last frame starts one frame before the end
        (_boxes((2.0, 3.0), (3.0, 3.967)), False),
        (_boxes((2.5, 3.967)), True),
        (_boxes((2.0, 3.5)), True),
        ([], True),
        (None, True),
    ],
)
def test_verify_flags_ai_material_without_a_drawn_badge(tmp_path, boxes, missing):
    edl = edl_doc()
    job = make_job(tmp_path, material(wish(1, "c1", [candidate("c1")])), edl)
    out = _verify_ai.check(job, from_dict(edl), edl, boxes, 30)
    assert bool(out["flagged"]) is missing
    if missing:
        assert out["flagged"][0]["flags"] == ["ai_label_missing"]
        assert out["flagged"][0]["material"] == 1
    assert out["items"][0]["model"] == "zentro-image-1 rev abc123"


def test_verify_does_not_flag_an_unlabelled_illustration_when_switched_off(tmp_path):
    settings.save(ai_label_illustrations=False)
    edl = edl_doc()
    mat = material(wish(1, "c1", [candidate("c1")], style="illustration"))
    job = make_job(tmp_path, mat, edl)
    out = _verify_ai.check(job, from_dict(edl), edl, [], 30)
    assert out["flagged"] == [] and out["items"][0]["labelled"] is False


def test_verify_fails_closed_when_the_edl_cannot_be_checked(tmp_path):
    job = make_job(tmp_path, material(wish(1, "c1", [candidate("c1")])), edl_doc())
    out = _verify_ai.unchecked(job, 9.0)
    assert out["flagged"] == [{"scene": "ai_badge", "t0": 0.0, "t1": 9.0,
                               "flags": ["ai_label_unchecked"], "material": 1}]  # fmt: skip
    stock = make_job(tmp_path / "s", material(wish(1, "s1", [STOCK])), edl_doc())
    assert _verify_ai.unchecked(stock, 9.0)["flagged"] == []


# ---------------------------------------------------------------- disclosure


def test_disclosure_lists_ai_items_attributions_and_platforms(tmp_path):
    card = {"scene": "still_card", "t0": 0.5, "t1": 1.5, "props": {"material": 2}}
    edl = edl_doc(overlays=[card])
    mat = material(wish(1, "c1", [candidate("c1")]), wish(2, "s1", [STOCK]))
    job = make_job(tmp_path, mat, edl, lang="de")
    doc = _disclosure.write(job, from_dict(edl), edl, "de", {"comment": "x", "c2pa": "skipped"})
    assert [(a["n"], a["t0"], a["t1"], a["model"], a["prompt"]) for a in doc["ai"]] == [
        (1, 2.0, 4.0, "zentro-image-1 rev abc123", "a calm desk with a plant, morning light")
    ]
    assert doc["stock"] == [{"n": 2, "source": "pexels", "author": "Mira Holm",
                             "licence": "Pexels License",
                             "attribution": "Video by Mira Holm on Pexels",
                             "source_url": "https://example.com/v/1"}]  # fmt: skip
    assert [(p["id"], p["status"]) for p in doc["platforms"]] == [
        ("youtube", "required"), ("meta", "required"), ("tiktok", "required"),
    ]  # fmt: skip
    assert doc["snippet"] == (
        "Hinweis: Dieses Video enthält KI-generiertes Bildmaterial (zentro-image-1 rev abc123).\n"
        "Bildnachweise: Video by Mira Holm on Pexels."
    )
    assert doc["c2pa"] == "skipped"
    assert json.loads((job / "work" / "disclosure.json").read_text()) == doc
    md = (job / "work" / "disclosure.md").read_text()
    assert "- [x] YouTube" in md and "Video by Mira Holm on Pexels" in md and "Prompt:" in md


def test_disclosure_without_material_says_so(tmp_path):
    edl = edl_doc()
    job = make_job(tmp_path, material(), edl, lang="en")
    doc = _disclosure.write(job, from_dict(edl), edl, "en")
    assert doc["ai"] == [] and doc["stock"] == [] and doc["snippet"] == ""
    assert all(p["status"] == "not needed" for p in doc["platforms"])
    assert "nothing to disclose" in (job / "work" / "disclosure.md").read_text()


def test_illustrations_only_need_no_platform_label(tmp_path):
    edl = edl_doc()
    mat = material(wish(1, "c1", [candidate("c1")], style="illustration"))
    job = make_job(tmp_path, mat, edl, lang="en")
    doc = _disclosure.build(shown(job, edl), "en")
    assert all(p["status"] == "not needed" for p in doc["platforms"])
    assert doc["snippet"].startswith("Note: this video contains AI-generated imagery")


def test_container_comment_names_the_models(tmp_path):
    edl = edl_doc()
    job = make_job(tmp_path, material(wish(1, "c1", [candidate("c1")])), edl)
    assert _disclosure.comment(shown(job, edl)) == (
        "Contains AI-generated material: zentro-image-1 rev abc123"
    )
    stock = make_job(tmp_path / "s", material(wish(1, "s1", [STOCK])), edl)
    assert _disclosure.comment(shown(stock, edl)) is None


# ---------------------------------------------------------------- C2PA


def test_manifest_has_the_actions_and_nothing_personal():
    ai = [{"model": "zentro-image-1", "prompt": "secret prompt"}]
    m = _c2pa.manifest(ai, "1.2.3", "reel.mp4")
    assert m["claim_generator_info"] == [{"name": "Video Cut", "version": "1.2.3"}]
    actions = m["assertions"][0]["data"]["actions"]
    assert [a["action"] for a in actions] == ["c2pa.created", "c2pa.edited", "c2pa.created"]
    assert actions[0]["digitalSourceType"].endswith("/compositeWithTrainedAlgorithmicMedia")
    assert actions[2]["digitalSourceType"] == _c2pa.AI_SOURCE
    assert actions[2]["softwareAgent"] == {"name": "zentro-image-1"}
    assert "secret prompt" not in json.dumps(m)
    plain = _c2pa.manifest([], "1.2.3", "reel.mp4")["assertions"][0]["data"]["actions"]
    assert [a["action"] for a in plain] == ["c2pa.created", "c2pa.edited"]
    assert plain[0]["digitalSourceType"].endswith("/compositeCapture")


def test_sign_is_skipped_without_c2patool(tmp_path, monkeypatch):
    monkeypatch.setattr(_c2pa, "binary", lambda: None)
    src = tmp_path / "reel.mp4"
    src.write_bytes(b"x")
    assert _c2pa.sign(src, tmp_path / "out.mp4", [], "1")["c2pa"] == "skipped"
    assert _c2pa.inspect(src) == {"iptc_ai": False, "c2pa": "unknown"}


def test_sign_calls_c2patool_with_the_local_certificate(tmp_path, monkeypatch, videocut_home):
    exe = tmp_path / "c2patool"
    exe.write_text("")
    monkeypatch.setattr(_c2pa, "binary", lambda: exe)
    calls = []
    report = {
        "active_manifest": "m1",
        "manifests": {"m1": {
            "claim_generator_info": [{"name": "Video Cut", "version": "1"}],
            "assertions": [{"label": "c2pa.actions.v2", "data": {"actions": [
                {"action": "c2pa.created", "digitalSourceType": _c2pa.AI_SOURCE}]}}],
            "signature_info": {"issuer": "Video Cut local"},
        }},
        "validation_status": [{"code": "signingCredential.untrusted"}],
        "validation_state": "Valid",
    }  # fmt: skip

    def fake_run(args, **kw):
        calls.append(args)
        if "-m" in args:
            definition = json.loads(Path(args[args.index("-m") + 1]).read_text())
            calls.append(definition)
            Path(args[args.index("-o") + 1]).write_bytes(b"signed")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps(report), "")

    monkeypatch.setattr(_c2pa, "_run", fake_run)
    src, dst = tmp_path / "reel.mp4", tmp_path / "signed.mp4"
    src.write_bytes(b"video")
    out = _c2pa.sign(src, dst, [{"model": "zentro-image-1"}], "1")
    assert out["c2pa"] == "signed" and out["valid"] is True, out
    assert out["verify"]["signer"] == "Video Cut local"
    assert out["verify"]["digital_source_types"] == [_c2pa.AI_SOURCE]
    definition = calls[1]
    assert definition["alg"] == "es256" and "ta_url" not in definition  # no network
    key, chain = Path(definition["private_key"]), Path(definition["sign_cert"])
    assert key.parent == videocut_home / "state" / "c2pa" and chain.is_file()
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert chain.read_text().count("BEGIN CERTIFICATE") == 2
    assert not [p for p in key.parent.iterdir() if p.is_dir()]  # the temp folder is gone
    assert oct(key.parent.stat().st_mode & 0o777) == "0o700"
    assert not list(key.parent.glob("*.key"))  # the root key never stays
    # the certificate is made once
    before = chain.read_text()
    _c2pa.sign(src, dst, [], "1")
    assert chain.read_text() == before


def test_inspect_finds_iptc_ai_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(_c2pa, "binary", lambda: None)
    f = tmp_path / "still.png"
    f.write_bytes(b"\x89PNG" + b"0" * 2_000_000 + b"Iptc4xmpExt:DigitalSourceType trainedAlgorithm"
                  + b"icMedia" + b"0" * 10)  # fmt: skip
    assert _c2pa.inspect(f)["iptc_ai"] is True


@pytest.mark.skipif(_c2pa.binary() is None, reason="c2patool is not installed")
def test_real_c2patool_signs_and_validates_an_mp4(tmp_path):
    import clips

    src = tmp_path / "in.mp4"
    subprocess.run(
        [clips.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x240:d=1",
         "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a",
         "aac", "-shortest", str(src)],
        check=True,
    )  # fmt: skip
    out = _c2pa.sign(src, tmp_path / "out.mp4", [{"model": "zentro-image-1"}], "1")
    assert out["c2pa"] == "signed" and out["valid"], out
    assert out["verify"]["codes"] == ["signingCredential.untrusted"]
    assert out["verify"]["generator"] == "Video Cut 1"
    assert _c2pa.inspect(tmp_path / "out.mp4")["c2pa"] == "present"
    assert _c2pa.inspect(src)["c2pa"] == "absent"


# ---------------------------------------------------------------- API, doctor


@pytest.fixture
def client(videocut_home):
    from test_server import BASE, ORIGIN, TOKEN, FakePipeline

    c = TestClient(server_app.create_app(token=TOKEN, runner=FakePipeline()), base_url=BASE)
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303
    c.headers.update(ORIGIN)
    return c


def test_api_disclosure_and_label_settings(client, videocut_home):
    from videocut.tools.common import write_json

    job = videocut_home / "jobs" / "j1"
    (job / "work").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"id": "j1"}))
    assert client.get("/api/jobs/j1/disclosure").json() == {"ready": False}
    write_json(job / "work" / "disclosure.json", {"version": 1, "snippet": "s"})
    assert client.get("/api/jobs/j1/disclosure").json() == {"version": 1, "snippet": "s",
                                                           "ready": True}  # fmt: skip
    assert client.get("/api/jobs/nope/disclosure").status_code == 404
    assert client.get("/api/settings/labels").json() == {"ai_label_illustrations": True}
    r = client.put("/api/settings/labels", json={"ai_label_illustrations": False})
    assert r.json() == {"ai_label_illustrations": False}
    assert settings.load()["ai_label_illustrations"] is False


def test_doctor_reports_c2patool_as_optional(monkeypatch):
    c = doctor.check_c2patool("0.0.0-not-installed")
    assert c.name == "c2patool" and c.required is False
