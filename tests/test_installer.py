"""Phase 4: setup endpoints, shutdown, launcher bundle, doctor --json, resumable downloads,
update swap, and a text lint of the install instructions."""

import hashlib
import http.server
import json
import plistlib
import re
import struct
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from videocut import doctor, download, launcher, settings, update
from videocut.paths import ROOT
from videocut.server import create_app
from videocut.server import setup as setup_api

TOKEN = "test-token"
PORT = 4391  # never the human's 4300
BASE = f"http://127.0.0.1:{PORT}"
KEY = "sk-ant-api03-" + "x" * 40


@pytest.fixture
def keychain(monkeypatch):
    """settings.api_key/set_api_key against a dict instead of the macOS keychain."""
    store: dict[str, str] = {}
    monkeypatch.setattr(settings, "api_key", lambda: store.get("key"))
    monkeypatch.setattr(settings, "set_api_key", lambda k: store.__setitem__("key", k))
    return store


def make_client(check=None, tester=None, on_shutdown=None):
    calls: list = []

    def fake_check():
        return check or {
            "installed": True,
            "path": "/somewhere/claude",
            "version": "2.0.0 (Claude Code)",
            "signed_in": True,
            "method": "claude.ai",
            "account": "person@example.com",
            "subscription": "pro",
            "error": None,
        }

    def fake_tester(key):
        calls.append(key)
        return tester or {"ok": True, "message": "OK", "seconds": 1.0}

    r = setup_api.router(check=fake_check, tester=fake_tester)
    app = create_app(token=TOKEN, port=PORT, on_shutdown=on_shutdown, setup_router=r)
    c = TestClient(app, base_url=BASE)
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303
    c.headers.update({"Origin": BASE})
    return c, r, calls


def status(c, r):
    c.get("/api/setup/status")
    r.detector.wait()
    return c.get("/api/setup/status").json()


# ------------------------------------------------------------------ setup endpoints


def test_setup_status_reports_the_detection_without_the_path(keychain):
    c, r, _ = make_client()
    first = c.get("/api/setup/status").json()
    assert first["done"] is False and first["mode"] == "local"
    s = status(c, r)
    assert s["claude"]["state"] == "done"
    assert s["claude"]["signed_in"] and s["claude"]["account"] == "person@example.com"
    assert "path" not in s["claude"]
    assert s["api_key_stored"] is False
    assert s["console_url"].startswith("https://console.anthropic.com")


def test_setup_auth_local_and_apikey(keychain):
    c, r, calls = make_client()
    s = c.post("/api/setup/auth", json={"mode": "local"}).json()
    assert s["done"] and s["mode"] == "local"
    assert settings.load()["auth"] == "local" and settings.setup_done()

    bad = c.post("/api/setup/auth", json={"mode": "apikey", "api_key": "hello"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "bad_key"
    none = c.post("/api/setup/auth", json={"mode": "apikey"})
    assert none.status_code == 422 and none.json()["detail"]["code"] == "no_key"

    ok = c.post("/api/setup/auth", json={"mode": "apikey", "api_key": KEY})
    assert ok.status_code == 200
    assert keychain["key"] == KEY
    assert KEY not in ok.text  # the key never comes back
    assert ok.json()["api_key_stored"] and settings.load()["auth"] == "apikey"
    assert KEY not in (settings.path().read_text())
    # switching back to apikey later keeps the stored key
    assert c.post("/api/setup/auth", json={"mode": "apikey"}).status_code == 200

    t = c.post("/api/setup/test").json()
    assert t["ok"] and calls == [KEY]
    c.post("/api/setup/auth", json={"mode": "local"})
    c.post("/api/setup/test")
    assert calls[-1] is None  # local mode: no key handed to the program
    assert c.post("/api/setup/auth", json={"mode": "other"}).status_code == 422


def test_setup_needs_session_and_origin(keychain):
    c, _, _ = make_client()
    anon = TestClient(c.app, base_url=BASE)
    assert anon.get("/api/setup/status").status_code == 403
    foreign = c.post("/api/setup/auth", json={"mode": "local"},
                     headers={"Origin": "http://evil.example"})  # fmt: skip
    assert foreign.status_code == 403
    assert not settings.setup_done()


def test_setup_done_for_a_mode_chosen_before_the_screen_existed():
    assert not settings.setup_done()
    settings.path().parent.mkdir(parents=True, exist_ok=True)
    settings.path().write_text(json.dumps({"auth": "apikey"}))
    assert settings.setup_done()


def test_detection_errors_are_shown_not_raised(keychain):
    def broken():
        raise OSError("boom")

    r = setup_api.router(check=broken, tester=lambda k: {})
    app = create_app(token=TOKEN, port=PORT, setup_router=r)
    c = TestClient(app, base_url=BASE, headers={"X-Token": TOKEN})
    c.get("/api/setup/status")
    r.detector.wait()
    s = c.get("/api/setup/status").json()["claude"]
    assert s["state"] == "done" and s["installed"] is False and "boom" in s["error"]


# ------------------------------------------------------------------ shutdown


def test_shutdown_is_a_guarded_write_and_waits_for_runs(keychain):
    stopped = threading.Event()
    c, _, _ = make_client(on_shutdown=stopped.set)
    anon = TestClient(c.app, base_url=BASE)
    assert anon.post("/api/shutdown", json={}).status_code == 403
    assert c.post("/api/shutdown", json={}, headers={"Origin": "http://x"}).status_code == 403
    assert not stopped.wait(0.5)

    runs = c.app.state.runs
    gate = threading.Event()
    runs.submit("j1", "start", lambda say, ask: gate.wait(5) and 0)
    busy = c.post("/api/shutdown", json={})
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "busy"
    assert c.post("/api/shutdown", json={"force": True}).json()["stopping"]
    assert stopped.wait(2)
    gate.set()


def test_shutdown_without_a_handler_is_refused(keychain):
    c, _, _ = make_client(on_shutdown=None)
    assert c.post("/api/shutdown", json={}).status_code == 501


# ------------------------------------------------------------------ launcher


def test_launcher_bundle_in_a_temp_dir(tmp_path, monkeypatch, videocut_home):
    app = launcher.bundle(tmp_path, port=4391)
    assert app.name == "Video Cut.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == launcher.STUB
    assert info["CFBundleName"] == "Video Cut" and "Claude" not in json.dumps(info)
    stub = app / "Contents" / "MacOS" / launcher.STUB
    assert stub.stat().st_mode & 0o111
    text = stub.read_text()
    assert str(ROOT) in text and str(videocut_home) in text and "--port 4391" in text
    assert subprocess.run(["/bin/bash", "-n", str(stub)]).returncode == 0
    for word in ("sudo", "brew", "python3 "):
        assert word not in text
    icns = (app / "Contents" / "Resources" / "icon.icns").read_bytes()
    assert icns[:4] == b"icns" and struct.unpack(">I", icns[4:8])[0] == len(icns)
    assert b"\x89PNG" in icns
    # again: replaced in place, no leftovers
    launcher.bundle(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Video Cut.app"]
    assert "--port" not in stub.read_text()


def test_launcher_stub_alerts_when_the_install_is_gone(tmp_path, monkeypatch):
    app = launcher.bundle(tmp_path, root=tmp_path / "gone")
    stub = app / "Contents" / "MacOS" / launcher.STUB
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    text = stub.read_text().replace("/usr/bin/osascript", str(fake_bin / "osascript"))
    stub.write_text(text)
    (fake_bin / "osascript").write_text('#!/bin/bash\necho "$@" > "$(dirname "$0")/said"\n')
    (fake_bin / "osascript").chmod(0o755)
    r = subprocess.run([str(stub)], capture_output=True, text=True)
    assert r.returncode == 1
    said = (fake_bin / "said").read_text()
    assert "Repair Video Cut" in said and "Video Cut reparieren" in said


def test_login_item_is_opt_in_and_removable(tmp_path):
    agents = tmp_path / "LaunchAgents" / "x.plist"
    app = launcher.bundle(tmp_path)
    launcher.set_login_item(app, True, agents)
    plist = plistlib.loads(agents.read_bytes())
    assert plist["RunAtLoad"] and plist["ProgramArguments"][-1] == "--no-browser"
    other = tmp_path / "other"
    (other / "Video Cut.app").mkdir(parents=True)  # not ours: never deleted
    gone = launcher.remove([tmp_path, other], agents)
    assert app in gone and agents in gone
    assert not app.exists() and (other / "Video Cut.app").exists()


def test_server_state_and_running(tmp_path):
    f = launcher.write_state(4391, "tok")
    assert f.stat().st_mode & 0o777 == 0o600
    assert json.loads(f.read_text())["url"] == "http://127.0.0.1:4391/?t=tok"
    assert launcher.running(timeout=0.2) is None  # nothing listens there
    launcher.clear_state()
    assert not f.exists()


def test_port_from_env_and_free_port(monkeypatch):
    monkeypatch.setenv("VIDEOCUT_PORT", "4555")
    assert launcher.default_port() == 4555
    monkeypatch.setenv("VIDEOCUT_PORT", "nope")
    assert launcher.default_port() == 4300
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        taken = s.getsockname()[1]
        assert not launcher.port_free(taken)
        assert launcher.free_port(taken) != taken


def test_open_app_reuses_a_running_server(monkeypatch):
    opened = []
    info = {"pid": 1, "port": 4391, "url": "http://u/?t=x"}
    monkeypatch.setattr(launcher, "running", lambda timeout=2: info)
    monkeypatch.setattr(launcher, "open_url", opened.append)
    monkeypatch.setattr(launcher, "start_background", lambda p: pytest.fail("started twice"))
    assert launcher.open_app() == 0 and opened == ["http://u/?t=x"]


# ------------------------------------------------------------------ doctor


def test_doctor_json_has_a_fix_for_every_failed_check(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor.claude_cli,
        "status",
        lambda: {"installed": False, "signed_in": False, "error": None, "version": None,
                 "path": None, "method": None},
    )  # fmt: skip
    code = doctor.run(as_json=True)
    data = json.loads(capsys.readouterr().out)
    assert code in (0, 1) and (code == 0) == data["ok"]
    assert {"ok", "root", "data", "checks", "failed", "warnings"} <= set(data)
    for c in data["checks"]:
        assert set(c) == {"name", "ok", "detail", "required", "fix"}
        assert bool(c["fix"]) == (not c["ok"]), c
    claude = next(c for c in data["checks"] if c["name"] == "claude")
    assert not claude["ok"] and not claude["required"] and "API key" in claude["fix"]


# ------------------------------------------------------------------ downloads


class _Range(http.server.BaseHTTPRequestHandler):
    payload = b"0123456789" * 1000
    fail_after: int | None = None

    def do_GET(self):  # noqa: N802
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
        body = self.payload[start:]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.fail_after is not None and not rng:
            self.wfile.write(body[: self.fail_after])
            return  # connection closes early
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_download_resumes_after_a_broken_connection(tmp_path, monkeypatch):
    _Range.fail_after = 3000
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Range)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(download.time, "sleep", lambda s: None)
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/f"
        sha = hashlib.sha256(_Range.payload).hexdigest()
        dest = tmp_path / "f.bin"
        download.fetch(url, sha, dest, "f")
        assert dest.read_bytes() == _Range.payload
        assert not (tmp_path / "f.bin.part").exists()
    finally:
        srv.shutdown()
        _Range.fail_after = None


# ------------------------------------------------------------------ update


def test_update_swap_keeps_tools_and_old_code(tmp_path):
    root = tmp_path / "app"
    (root / ".tools" / "bin").mkdir(parents=True)
    (root / ".tools" / "bin" / "uv").write_text("uv")
    (root / ".venv").mkdir()
    (root / "start.sh").write_text("old")
    new = tmp_path / "new"
    new.mkdir()
    (new / "start.sh").write_text("new")
    old = update.swap(new, root)
    assert (root / "start.sh").read_text() == "new"
    assert (root / ".tools" / "bin" / "uv").read_text() == "uv"
    assert (root / ".venv").is_dir()
    assert (old / "start.sh").read_text() == "old" and not (old / ".tools").exists()


def test_update_never_uses_git_without_the_command_line_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "ROOT", tmp_path)
    assert update.has_git() is False  # no .git: a downloaded install


# ------------------------------------------------------------------ text lint

DOCS = [
    ROOT / "INSTALL.md",
    ROOT / ".claude" / "skills" / "install-videocut" / "SKILL.md",
    ROOT / "CLAUDE.md",
]


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_install_texts_are_neutral(path: Path):
    text = path.read_text()
    # the product is "Video Cut"; "Claude Code" is the person's helper, never the product
    assert not re.search(r"Claude Code (Video|Cut)|Video Cut (for|für) Claude Code", text)
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", text)  # no colours
    for banned in ("sudo ", "brew install", "git clone", "python3 -"):
        assert banned not in text.replace("never `git`", ""), banned
    forbidden = Path(ROOT / ".git").resolve()
    words = _forbidden_words(forbidden)
    for w in words:
        assert w.lower() not in text.lower(), "a forbidden word is in " + path.name


def _forbidden_words(git: Path) -> list[str]:
    """The local pre-commit hook's list (never committed); empty where it does not exist."""
    candidates = [git / "info" / "forbidden-words"]
    if git.is_file():  # a worktree: .git is a file pointing at the main repository
        target = git.read_text().split("gitdir:", 1)[-1].strip()
        candidates.append(Path(target).parents[1] / "info" / "forbidden-words")
    for c in candidates:
        if c.is_file():
            return [w.strip() for w in c.read_text().splitlines() if w.strip()
                    and not w.startswith("#")]  # fmt: skip
    return []


def test_skill_has_frontmatter_and_the_fixed_paths():
    text = DOCS[1].read_text()
    head = text.split("---")[1]
    assert re.search(r"^name: install-videocut$", head, re.M)
    assert re.search(r"^description: .+", head, re.M)
    for must in ("~/VideoCut/app", "Video Cut.app", "./start.sh", "videocut doctor --json",
                 "codeload.github.com", "videocut update", "~/VideoCut/jobs"):  # fmt: skip
        assert must in text, must


def test_start_sh_needs_nothing_outside_macos():
    text = (ROOT / "start.sh").read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for banned in ("python3", "git", "brew", "sudo", "xcode-select", "arch"):
        # as a command: at the start of a statement, not inside a message
        assert not re.search(rf"(^|[;&|(]|then|do)\s*{banned}\b", code, re.M), banned
    assert 'uv" cache prune --ci' in code


def test_update_refuses_to_overwrite_a_git_checkout(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(update, "has_git", lambda: False)  # no command line tools
    monkeypatch.setattr(update, "stop_server", lambda force: pytest.fail("stopped the app"))
    with pytest.raises(update.UpdateError, match="git checkout"):
        update.run(root=tmp_path)


def test_update_downloads_before_it_stops_the_app(monkeypatch, tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    order = []

    def broken(ref, into):
        order.append("download")
        raise OSError("offline")

    monkeypatch.setattr(update, "ROOT", root)
    monkeypatch.setattr(update, "download_tree", broken)
    monkeypatch.setattr(update, "stop_server", lambda force: order.append("stop"))
    with pytest.raises(OSError):
        update.run(ref="main", root=root)
    assert order == ["download"]


def test_open_app_accepts_a_lost_start_race(monkeypatch):
    answers = iter([None, {"pid": 1, "port": 4391, "url": "http://u/?t=x"}])
    monkeypatch.setattr(launcher, "running", lambda timeout=2: next(answers))
    monkeypatch.setattr(launcher, "free_port", lambda p: 4391)

    class Exited:
        def poll(self):
            return 1

    monkeypatch.setattr(launcher, "start_background", lambda p: Exited())
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher, "open_url", lambda u: None)
    assert launcher.open_app() == 0


def test_set_api_key_refuses_odd_characters(monkeypatch):
    monkeypatch.setattr(settings.subprocess, "run", lambda *a, **k: pytest.fail("ran security"))
    with pytest.raises(ValueError):
        settings.set_api_key("sk-ant-x -w other\nadd-generic-password")
