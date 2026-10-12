import re

from videocut import manifest

SHA = re.compile(r"^[0-9a-f]{64}$")
REV = re.compile(r"^[0-9a-f]{40}$")


def test_every_tool_is_pinned_and_verified():
    for name, t in manifest.load()["tools"].items():
        assert t["url"].startswith("https://"), name
        assert SHA.match(t["sha256"]), name
        assert t["version"] in t["url"], f"{name}: version and url out of sync"
        assert t.get("license"), name


def test_every_model_is_pinned():
    for name, m in manifest.models().items():
        assert m.get("license"), name
        if m["kind"] == "huggingface":
            assert REV.match(m["revision"]), f"{name}: pin a commit, not a branch"
        else:
            assert m["url"].startswith("https://") and SHA.match(m["sha256"]), name


def test_at_least_one_default_transcription_model():
    assert any(m.get("default") and "whisper" in n for n, m in manifest.models().items())
