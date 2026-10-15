"""The human's word edits (tools/transcript.py): edit, undo, the record outside the job,
composing with the vocabulary, hidden words, captions, the refresh and the API routes."""

import json
import os
import time

import pytest
from helpers import run_tool
from test_server import BASE, ORIGIN, TOKEN, FakePipeline, cut_job
from test_vocab import ZEN, make_brand, make_job, texts

from videocut.server import create_app
from videocut.tools import captions, transcript, vocab
from videocut.tools.job import approve_job

# make_job's words: Hallo von Zentrowia und Sentro via , Lumocloud heute  (one per second)


def words_of(job) -> list[dict]:
    return json.loads((job / "work" / "words.json").read_text())["words"]


def test_edit_keeps_timing_and_the_model_text(capsys):
    job = make_job()
    before = words_of(job)
    code, s, out = run_tool(capsys, "transcript", "edit", "v1", "0", "Hallo!")
    assert code == 0 and s["changed"], s
    ws = words_of(job)
    assert ws[0]["w"] == "Hallo!" and ws[0]["w_asr"] == "Hallo" and ws[0]["edit"] == "Hallo!"
    assert [(w["t0"], w["t1"]) for w in ws] == [(w["t0"], w["t1"]) for w in before]
    log = (job / "work" / "transcript_edits.jsonl").read_text().splitlines()
    e = json.loads(log[0])
    assert (e["i"], e["from"], e["to"]) == (0, "Hallo", "Hallo!") and e["at"]
    assert transcript.record_path(job).read_text() == "\n".join(log) + "\n"
    code, s, out = run_tool(capsys, "transcript", "show", "v1", "--from", "0", "--to", "1.2")
    assert s["words"] == 2 and s["edits"] == 1 and s["drift"] == 0
    assert "[was: Hallo]" in out


def test_text_is_validated(capsys):
    make_job()
    for bad, msg in (("x" * 61, "at most 60"), ("a\x00b", "control"), ("a‮b", "control")):
        code, s, _ = run_tool(capsys, "transcript", "edit", "v1", "0", bad)
        assert code == 1 and msg in s["error"], s
    code, s, _ = run_tool(capsys, "transcript", "edit", "v1", "99", "x")
    assert code == 1 and "no word 99" in s["error"]
    assert transcript.clean_text("  zwei   Wörter ") == "zwei Wörter"


def test_hidden_and_split_words_in_captions(capsys):
    job = make_job()
    run_tool(capsys, "transcript", "edit", "v1", "0", "")
    run_tool(capsys, "transcript", "edit", "v1", "8", "heute Abend")
    ws = words_of(job)
    assert ws[0]["hidden"] is True and ws[0]["w"] == "Hallo" and ws[0]["edit"] == ""
    assert len(ws) == 9 and ws[8]["w"] == "heute Abend"  # one timed token
    edl = {"fps": 30, "size": [1080, 1920], "source": "input/take.mov",
           "segments": [{"in": 0.0, "out": 9.0}]}  # fmt: skip
    from videocut.edl import from_dict

    mapped = captions.map_words(captions._words(job), from_dict(edl).segments, 30)
    shown = [w["w"] for w in mapped]
    assert "Hallo" not in shown and shown[-1] == "heute Abend"


def test_edits_win_over_the_vocabulary_and_survive_apply(capsys):
    make_brand(vocabulary=[ZEN])
    job = make_job(brand="acme")
    run_tool(capsys, "vocab", "apply", "v1")
    assert texts(words_of(job))[2] == "Zentrovia"
    # the human writes it differently: the edit wins, "vocab" is gone, w_asr stays the model's
    run_tool(capsys, "transcript", "edit", "v1", "2", "ZentroVIA")
    w = words_of(job)[2]
    assert (w["w"], w["w_asr"], "vocab" in w) == ("ZentroVIA", "Zentrowia", False)
    code, s, _ = run_tool(capsys, "vocab", "apply", "v1")
    assert code == 0 and words_of(job)[2]["w"] == "ZentroVIA"
    # the merged span "Sentro via" -> one word; editing it keeps it one word through apply
    ws = words_of(job)
    k = texts(ws).index("Zentrovia")
    run_tool(capsys, "transcript", "edit", "v1", str(k), "Zentrovia Plus")
    run_tool(capsys, "vocab", "apply", "v1")
    assert "Zentrovia Plus" in texts(words_of(job))
    # undo both: the vocabulary corrects them again
    run_tool(capsys, "transcript", "undo", "v1")
    run_tool(capsys, "transcript", "undo", "v1")
    ws = words_of(job)
    assert texts(ws)[2] == "Zentrovia" and texts(ws).count("Zentrovia") == 2
    assert not any("edit" in w for w in ws)
    code, s, _ = run_tool(capsys, "transcript", "undo", "v1")
    assert code == 1 and "no edit to undo" in s["error"]


def test_undo_restores_the_previous_edit(capsys):
    job = make_job()
    run_tool(capsys, "transcript", "edit", "v1", "1", "vom")
    run_tool(capsys, "transcript", "edit", "v1", "1", "")
    assert words_of(job)[1]["hidden"] is True
    run_tool(capsys, "transcript", "undo", "v1")
    w = words_of(job)[1]
    assert w["w"] == "vom" and "hidden" not in w
    run_tool(capsys, "transcript", "undo", "v1")
    w = words_of(job)[1]
    assert w == {k: v for k, v in w.items() if k not in ("edit", "w_asr")} and w["w"] == "von"


def test_agent_runs_cannot_edit_and_cannot_drop_edits(capsys, monkeypatch):
    job = make_job()
    run_tool(capsys, "transcript", "edit", "v1", "0", "Hi")
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    code, s, _ = run_tool(capsys, "transcript", "edit", "v1", "1", "x")
    assert code == 1 and "only a human" in s["error"]
    monkeypatch.delenv("VIDEOCUT_AGENT")
    # an agent run rewrites words.json and the job's copy of the log: the record wins
    doc = json.loads((job / "work" / "words.json").read_text())
    doc["words"][0] = {k: v for k, v in doc["words"][0].items() if k != "edit"}
    doc["words"][0]["w"] = "Hallo"
    doc["words"][3].update(w="oder", w_asr="und", edit="oder")
    (job / "work" / "words.json").write_text(json.dumps(doc))
    (job / "work" / "transcript_edits.jsonl").write_text("")
    code, s, _ = run_tool(capsys, "transcript", "show", "v1")
    assert s["drift"] == 2
    run_tool(capsys, "vocab", "apply", "v1")
    ws = words_of(job)
    assert ws[0]["w"] == "Hi" and ws[3]["w"] == "und" and "edit" not in ws[3]


def captioned_job(job_id="v1"):
    job = make_job(job_id, brand="acme")
    (job / "work" / "project.md").write_text("# Plan\n")
    edl = {"fps": 30, "size": [1080, 1920], "source": "input/take.mov",
           "segments": [{"in": 0.0, "out": 4.0}, {"in": 6.0, "out": 9.0}]}  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    os.environ.pop("VIDEOCUT_AGENT", None)
    run_tool_quiet("captions", "--job", str(job))
    data = json.loads((job / "job.json").read_text())
    data["overlays"] = {"brand": "acme", "verify_ok": True}
    (job / "job.json").write_text(json.dumps(data))
    return job


def run_tool_quiet(*argv):
    import contextlib
    import io

    from videocut.cli import main

    with contextlib.redirect_stdout(io.StringIO()):
        assert main(["tool", *argv]) == 0


def caption_words(job) -> list[str]:
    caps = json.loads((job / "work" / "edl.json").read_text())["captions"]
    return [w["w"] for c in caps for w in c["words"]]


def test_patch_captions_keeps_the_blocks_and_marks_stale(capsys):
    make_brand()
    job = captioned_job()
    before = caption_words(job)
    assert before == ["Hallo", "von", "Zentrowia", "und", ",", "Lumocloud", "heute"]
    edl = json.loads((job / "work" / "edl.json").read_text())
    edl["captions"][0]["words"][0]["emph"] = True  # the agent's emphasis stays
    (job / "work" / "edl.json").write_text(json.dumps(edl))
    run_tool(capsys, "transcript", "edit", "v1", "2", "Zentrovia")
    run_tool(capsys, "transcript", "edit", "v1", "3", "")
    assert json.loads((job / "job.json").read_text())["overlays"]["stale"] == "transcript"
    assert caption_words(job) == before  # nothing re-rendered by the edit itself
    res = transcript.patch_captions(job)
    assert res == {"changed": 2, "rebuilt": False}
    assert caption_words(job) == ["Hallo", "von", "Zentrovia", ",", "Lumocloud", "heute"]
    assert json.loads((job / "work" / "edl.json").read_text())["captions"][0]["words"][0]["emph"]
    # a hidden word shown again: only a rebuild puts it back
    run_tool(capsys, "transcript", "undo", "v1")
    assert transcript.patch_captions(job)["rebuilt"] is True
    # a word the cut removed: edited, but the captions never show it, so no rebuild
    run_tool(capsys, "transcript", "edit", "v1", "3", "")
    run_tool(capsys, "transcript", "edit", "v1", "4", "Zentro")  # 4.0-4.5 s: cut away
    assert transcript.patch_captions(job) == {"changed": 0, "rebuilt": False}


def test_a_job_without_captions_just_keeps_the_edit(capsys):
    job = make_job(brand="acme")
    run_tool(capsys, "transcript", "edit", "v1", "0", "Hi")
    assert "overlays" not in json.loads((job / "job.json").read_text())
    assert transcript.patch_captions(job) == {"changed": 0, "rebuilt": False}


def test_refresh_runs_tool_steps_only(capsys, monkeypatch):
    from videocut import run as pipeline

    make_brand()
    job = captioned_job()
    run_tool(capsys, "transcript", "edit", "v1", "2", "Zentrovia")
    calls = []

    def fake_tool(j, *argv, need_ok=True):
        calls.append(argv)
        if argv[0] == "verify":
            (j / "out" / "verify.json").write_text(json.dumps({"ok": True}))
        return {"ok": True}

    monkeypatch.setattr(pipeline, "tool", fake_tool)
    monkeypatch.setattr(pipeline, "cut_done", lambda j: True)
    monkeypatch.setattr(pipeline, "claude", lambda *a, **k: pytest.fail("no Claude run"))
    lines = []
    assert transcript.refresh(job, say=lines.append) == 0
    assert calls == [("overlays",), ("render",), ("verify",)]
    assert "Zentrovia" in caption_words(job)
    rec = json.loads((job / "job.json").read_text())["overlays"]
    assert not rec.get("stale") and rec["verify_ok"] is True
    assert lines[0].startswith("overlays:") and any(x.startswith("verify:") for x in lines)


def test_vocab_apply_leaves_edited_caption_words():
    caps = [{"t0": 0, "t1": 1, "words": [{"w": "Zentrowia", "t0": 0, "t1": 1, "edit": True}]}]
    assert vocab.apply_captions(caps, [ZEN]) == 0 and caps[0]["words"][0]["w"] == "Zentrowia"


# --- API ------------------------------------------------------------------------------


class Pipeline(FakePipeline):
    def captions(self, job, say, ask):
        self.calls.append(("captions", job.name))
        say("overlays: setting the captions again")
        data = json.loads((job / "job.json").read_text())
        data["overlays"] = {**data["overlays"], "stale": None}
        (job / "job.json").write_text(json.dumps(data))
        return 0


@pytest.fixture
def fake():
    return Pipeline()


@pytest.fixture
def client(fake):
    from fastapi.testclient import TestClient

    c = TestClient(create_app(token=TOKEN, port=4300, runner=fake), base_url=BASE)
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303
    c.headers.update(ORIGIN)
    return c


def test_transcript_api_edit_undo_and_refresh(client, fake):
    make_brand()
    job = captioned_job("j1")
    approve_job(job)
    cut_job(job, overlay=True)  # segments 0-2 s
    data = json.loads((job / "job.json").read_text())
    data["overlays"] = {"brand": "acme", "verify_ok": True}
    (job / "job.json").write_text(json.dumps(data))
    edl = json.loads((job / "work" / "edl.json").read_text())
    edl["captions"] = [{"t0": 0.0, "t1": 1.5, "words": [{"w": "Hallo", "t0": 0.0, "t1": 0.5},
                                                        {"w": "von", "t0": 1.0, "t1": 1.5}]}]  # fmt: skip
    (job / "work" / "edl.json").write_text(json.dumps(edl))

    r = client.get("/api/jobs/j1/transcript")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["total"] == 9 and t["has_reel"] and not t["can_undo"] and t["edits"] == 0
    w0, w5 = t["words"][0], t["words"][5]
    assert (w0["out_t0"], w0["t0"]) == (0.0, 0.0) and w5["out_t0"] is None  # cut away
    page = client.get("/api/jobs/j1/transcript?offset=6&limit=1").json()
    assert [w["i"] for w in page["words"]] == [6]

    for body in ({"i": 0, "text": "x" * 61}, {"i": 0, "text": "a\x07"}, {"i": -1, "text": "a"}):
        assert client.patch("/api/jobs/j1/transcript", json=body).status_code == 422
    r = client.patch("/api/jobs/j1/transcript", json={"i": 1, "text": "vom", "t0": 0.5})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale"
    r = client.patch("/api/jobs/j1/transcript", json={"i": 1, "text": "vom", "t0": 1.0})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["words"][1] == {**t["words"][1], "w": "vom", "edited": True, "asr": "von"}
    assert t["captions_stale"] and t["can_undo"] and t["edits_at"]
    assert client.get("/api/jobs/j1").json()["captions_stale"] is True

    r = client.post("/api/jobs/j1/transcript/undo")
    assert r.status_code == 200 and r.json()["words"][1]["w"] == "von"
    assert client.post("/api/jobs/j1/transcript/undo").status_code == 409

    client.patch("/api/jobs/j1/transcript", json={"i": 1, "text": "vom"})
    r = client.post("/api/jobs/j1/captions/refresh")
    assert r.status_code == 200 and r.json()["run"] == "captions"
    for _ in range(100):
        if ("captions", "j1") in fake.calls and not client.get("/api/jobs/j1").json()[
            "captions_stale"
        ]:
            break
        time.sleep(0.05)
    assert ("captions", "j1") in fake.calls
    assert client.post("/api/jobs/j1/captions/refresh").status_code == 409  # up to date


def test_transcript_api_needs_a_transcript_and_no_run(client, fake):
    from test_server import make_job as bare_job

    bare_job("j2")
    assert client.get("/api/jobs/j2/transcript").json()["detail"]["code"] == "state"
    make_job("j3")
    fake.gate.clear()
    assert client.post("/api/jobs/j3/start").status_code == 200
    r = client.patch("/api/jobs/j3/transcript", json={"i": 0, "text": "Hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "busy"
    fake.gate.set()


def test_skills_treat_the_human_edits_as_the_truth():
    from videocut import agent

    for n in ("plan", "cut", "overlays", "revise"):
        text = (agent.PLUGIN / "skills" / n / "SKILL.md").read_text()
        assert '`"edit"` is the human\'s text' in text and '"hidden": true' in text, n


def test_refresh_refuses_when_a_render_would_move_the_cut(capsys, monkeypatch):
    from videocut import run as pipeline
    from videocut.tools import edl as edl_tool

    make_brand()
    job = captioned_job()
    monkeypatch.setattr(pipeline, "cut_done", lambda j: True)
    monkeypatch.setattr(edl_tool, "snap_job", lambda j, write=True: {"moved": 3})
    monkeypatch.setattr(pipeline, "tool", lambda *a, **k: pytest.fail("nothing may run"))
    with pytest.raises(transcript.ToolError, match="move 3 cut point"):
        transcript.refresh(job, say=lambda _: None)
