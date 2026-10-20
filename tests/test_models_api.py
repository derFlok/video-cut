"""Settings → Material: install, watch, cancel and remove the local image model through
/api/models/image. The install child is a fake script (prints install_image's step lines,
writes a few bytes into the download folder); nothing is downloaded."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from videocut import install
from videocut.server import create_app
from videocut.server import models as mdl
from videocut.tools._material_providers import local_flux as lf

TOKEN = "test-token"
BASE = "http://127.0.0.1:4300"

FAKE_CHILD = r"""
import os, sys, time
from pathlib import Path
mode, src, dest, marker = sys.argv[1:5]
print("Local image generation: FLUX.2 [klein] 4B", flush=True)
print("  \x1b[1m▸\x1b[0m Installing the Python packages for image", flush=True)
time.sleep(0.2)
print("  \x1b[1m▸\x1b[0m Downloading model flux2-klein-4b (~16.0 GB)", flush=True)
src = Path(src)
src.mkdir(parents=True, exist_ok=True)
for i in range(4):
    (src / f"part{i}").write_bytes(b"x" * 1_000_000)
    time.sleep(0.1)
if mode == "slow":
    time.sleep(60)
if mode == "fail":
    msg = "\x1b[31m✗ Not installed: needs 26 GB free disk space\x1b[0m"
    print(msg, file=sys.stderr, flush=True)
    sys.exit(1)
print("  \x1b[1m▸\x1b[0m Preparing the model for this Mac (8-bit copy)", flush=True)
dest = Path(dest)
dest.mkdir(parents=True, exist_ok=True)
(dest / ".videocut-revision").write_text(marker + "\n")
import shutil
shutil.rmtree(src.parent)
"""


@pytest.fixture
def mac(monkeypatch):
    """A 32 GB Apple Silicon Mac with plenty of disk; mflux comes with the 'install'."""
    monkeypatch.setattr(lf.sys, "platform", "darwin")
    monkeypatch.setattr(lf.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(lf, "memory_gb", lambda: 32.0)
    monkeypatch.setattr(mdl, "_free_gb", lambda: 500.0)
    monkeypatch.setattr(lf, "mflux_version", lambda: "0.23.0" if lf.model_ready() else None)
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)


def fake_argv(tmp_path: Path, mode: str = "ok"):
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD)
    return lambda: [sys.executable, str(script), mode, str(lf.source_dir()),
                    str(lf.model_dir()), lf.ready_marker()]  # fmt: skip


def client_for(argv) -> TestClient:
    app = create_app(token=TOKEN, port=4300, models_router=mdl.router(argv))
    c = TestClient(app, base_url=BASE)
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303
    c.headers.update({"Origin": BASE})
    return c


def wait_for(c: TestClient, state: str, timeout: float = 15) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = c.get("/api/models/image").json()
        if v["state"] == state:
            return v
        time.sleep(0.05)
    raise AssertionError(f"state stayed {v['state']}, wanted {state}: {v}")


def test_install_runs_the_child_and_ends_installed(mac, tmp_path, videocut_home):
    c = client_for(fake_argv(tmp_path))
    v = c.get("/api/models/image").json()
    assert v["state"] == "not_installed"
    assert v["download_gb"] == 16.0 and v["disk_gb"] == 8.0 and v["min_memory_gb"] == 16
    assert v["need_gb"] == 26.0 and v["disk_ok"] and v["progress"] is None
    v = c.post("/api/models/image/install").json()
    assert v["state"] == "installing"
    assert v["progress"]["step"] in ("packages", "download", "preparing")
    v = wait_for(c, "installed")
    assert v["mflux_version"] == "0.23.0" and v["progress"] is None and v["error"] is None
    log = (videocut_home / "logs" / "model-image.log").read_text()
    assert "Downloading model" in log
    assert not lf.source_dir().parent.exists()


def test_progress_reads_the_download_folder(mac, tmp_path):
    c = client_for(fake_argv(tmp_path, "slow"))
    c.post("/api/models/image/install")
    end = time.monotonic() + 10
    while time.monotonic() < end:
        p = c.get("/api/models/image").json()["progress"]
        if p and p["step"] == "download" and p["downloaded_gb"] >= 0.004:
            break
        time.sleep(0.05)
    assert p["step"] == "download"
    assert p["pct"] == pytest.approx(0.004 / 16 * 100, abs=0.1)
    c.post("/api/models/image/cancel")


def test_second_install_is_refused_while_one_runs(mac, tmp_path):
    c = client_for(fake_argv(tmp_path, "slow"))
    assert c.post("/api/models/image/install").status_code == 200
    r = c.post("/api/models/image/install")
    assert r.status_code == 409
    assert c.delete("/api/models/image").status_code == 409
    c.post("/api/models/image/cancel")


def test_cancel_stops_the_child_and_leaves_a_partial_download(mac, tmp_path):
    c = client_for(fake_argv(tmp_path, "slow"))
    c.post("/api/models/image/install")
    end = time.monotonic() + 10
    while not (lf.source_dir() / "part3").exists() and time.monotonic() < end:
        time.sleep(0.05)
    pid = c.app.state.models.installer.proc.pid
    v = c.post("/api/models/image/cancel").json()
    assert v["state"] == "partial" and v["error"] is None
    assert "GB of 16 GB downloaded" in v["reason"] and v["partial_gb"] == 0.0  # 4 MB
    assert (lf.source_dir() / "part0").exists()  # finished parts stay
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert c.post("/api/models/image/cancel").status_code == 409
    # a new server (restart): nothing runs, the folder still reads as partial
    assert client_for(fake_argv(tmp_path)).get("/api/models/image").json()["state"] == "partial"


def test_cancel_kills_a_child_that_ignores_sigterm(mac, tmp_path, monkeypatch):
    script = tmp_path / "stubborn.py"
    script.write_text("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                      "print('ready', flush=True)\ntime.sleep(60)\n")  # fmt: skip
    monkeypatch.setattr(mdl, "KILL_AFTER_S", 0.5)
    c = client_for(lambda: [sys.executable, str(script)])
    c.post("/api/models/image/install")
    proc = c.app.state.models.installer.proc
    time.sleep(0.3)
    c.post("/api/models/image/cancel")
    assert proc.returncode == -signal.SIGKILL


def test_a_failed_install_shows_the_error_and_log(mac, tmp_path):
    c = client_for(fake_argv(tmp_path, "fail"))
    c.post("/api/models/image/install")
    v = wait_for(c, "failed")
    assert v["error"] == "Not installed: needs 26 GB free disk space"
    assert any("Downloading model" in ln for ln in v["log_tail"])
    assert not any("\x1b" in ln for ln in v["log_tail"])
    # retry is allowed and clears the error
    c.app.state.models.installer.argv = fake_argv(tmp_path)
    c.post("/api/models/image/install")
    assert wait_for(c, "installed")["error"] is None


def test_delete_removes_the_model_and_the_download(mac, tmp_path):
    c = client_for(fake_argv(tmp_path))
    d = lf.model_dir()
    d.mkdir(parents=True)
    (d / install.HF_MARKER).write_text(lf.ready_marker() + "\n")
    (d / "weights.safetensors").write_bytes(b"x" * 1000)
    lf.source_dir().mkdir(parents=True)
    (lf.source_dir() / "left").write_bytes(b"x")
    assert c.get("/api/models/image").json()["state"] == "installed"
    v = c.delete("/api/models/image").json()
    assert v["state"] == "not_installed"
    assert not d.exists() and not lf.source_dir().parent.exists()


def test_unsupported_mac_is_refused(mac, tmp_path, monkeypatch):
    monkeypatch.setattr(lf, "memory_gb", lambda: 8.0)
    c = client_for(fake_argv(tmp_path))
    v = c.get("/api/models/image").json()
    assert v["state"] == "unsupported"
    assert v["reason"] == ("local image generation needs at least 16 GB memory; "
                           "this Mac has 8 GB")  # fmt: skip
    r = c.post("/api/models/image/install")
    assert r.status_code == 400 and "16 GB memory" in r.text
    assert c.app.state.models.installer.proc is None


def test_too_little_disk_is_refused_with_install_images_message(mac, tmp_path, monkeypatch):
    monkeypatch.setattr(mdl, "_free_gb", lambda: 10.0)
    c = client_for(fake_argv(tmp_path))
    v = c.get("/api/models/image").json()
    assert v["state"] == "not_installed" and not v["disk_ok"]
    msg = "needs 26 GB free disk space while it installs (8 GB afterwards), 10 GB are free"
    assert v["reason"] == msg
    r = c.post("/api/models/image/install")
    assert r.status_code == 400 and msg in r.text


def test_never_inside_an_agent_run(mac, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    c = client_for(fake_argv(tmp_path))
    assert c.post("/api/models/image/install").status_code == 403


def test_routes_need_the_session(mac, tmp_path):
    app = create_app(token=TOKEN, port=4300, models_router=mdl.router(fake_argv(tmp_path)))
    anon = TestClient(app, base_url=BASE)
    assert anon.get("/api/models/image").status_code == 403
    assert anon.post("/api/models/image/install", headers={"Origin": BASE}).status_code == 403
    assert anon.delete("/api/models/image", headers={"Origin": BASE}).status_code == 403
    assert app.state.models.installer.proc is None


def test_parse_step():
    assert mdl.parse_step([]) == "packages"
    lines = ["Local image generation", "▸ Installing the Python packages for image",
             "▸ Downloading model flux2-klein-4b (~16.0 GB)"]  # fmt: skip
    assert mdl.parse_step(lines) == "download"
    assert mdl.parse_step([*lines, "▸ Preparing the model for this Mac"]) == "preparing"


def test_speed_and_eta_from_two_samples(mac, monkeypatch):
    inst = mdl.Installer()
    clock = iter([100.0, 110.0])
    monkeypatch.setattr(mdl.time, "monotonic", lambda: next(clock))
    log = ["▸ Downloading model x"]
    assert "speed_mbps" not in inst._progress(log, 4.0, 16.0)
    p = inst._progress(log, 5.0, 16.0)  # 1 GB in 10 s
    assert p["speed_mbps"] == 800.0 and p["eta_s"] == 110 and p["pct"] == 31.2


def test_shutdown_stops_the_child(mac, tmp_path):
    app = create_app(token=TOKEN, port=4300, models_router=mdl.router(fake_argv(tmp_path, "slow")))
    with TestClient(app, base_url=BASE) as c:  # runs the lifespan
        c.get(f"/?t={TOKEN}", follow_redirects=False)
        c.headers.update({"Origin": BASE})
        c.post("/api/models/image/install")
        proc = app.state.models.installer.proc
        assert proc.poll() is None
    assert proc.poll() is not None


def test_a_new_server_stops_a_child_the_last_one_left_behind(mac, tmp_path):
    """The server was killed before its shutdown ran: its child must not write on while a
    new server starts another install into the same folder."""
    c = client_for(fake_argv(tmp_path, "slow"))
    c.post("/api/models/image/install")
    old = c.app.state.models.installer.proc
    assert mdl.pid_path().read_text().startswith(f"{old.pid}\n")
    c2 = client_for(fake_argv(tmp_path))  # the next start
    assert old.wait(15) == -signal.SIGTERM
    assert not mdl.pid_path().exists()
    assert c2.get("/api/models/image").json()["state"] in ("partial", "not_installed")


def test_a_reused_pid_is_left_alone(mac, tmp_path):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             start_new_session=True)  # fmt: skip
    try:
        mdl.pid_path().parent.mkdir(parents=True, exist_ok=True)
        mdl.pid_path().write_text(f"{other.pid}\n{tmp_path}/fake_child.py ok\n")
        client_for(fake_argv(tmp_path))
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()
