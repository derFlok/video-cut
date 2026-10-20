"""Local image generation (FLUX.2 [klein] 4B through mflux) with a fake backend: no model
download, no mflux import."""

import json
import sys
import zlib
from pathlib import Path

import pytest

from videocut import doctor, install, manifest, settings
from videocut.tools import _material_providers as mp
from videocut.tools._material_providers import local_flux as lf


def _png(path: Path, w: int = 2, h: int = 2) -> None:
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * w for _ in range(h))

    def chunk(kind, data):
        return (len(data).to_bytes(4, "big") + kind + data
                + zlib.crc32(kind + data).to_bytes(4, "big"))  # fmt: skip

    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))  # fmt: skip


@pytest.fixture
def installed(videocut_home, monkeypatch):
    """The model folder with its revision marker, mflux 'installed', a 32 GB Mac."""
    d = videocut_home / "models" / lf.MODEL
    d.mkdir(parents=True)
    (d / install.HF_MARKER).write_text(lf.ready_marker() + "\n")
    monkeypatch.setattr(lf, "mflux_version", lambda: "0.23.0")
    monkeypatch.setattr(lf, "memory_gb", lambda: 32.0)
    monkeypatch.setattr(lf.sys, "platform", "darwin")
    monkeypatch.setattr(lf.platform, "machine", lambda: "arm64")
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)
    return d


class FakeBackend:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout, limit):
        self.requests.append((request, timeout, limit))
        _png(Path(request["dest"]))
        return {"load_s": 1.0, "generate_s": 2.0, "peak_bytes": 9 * 2**30}


def test_generate_with_a_fake_backend(installed, tmp_path):
    fake = FakeBackend()
    dest = tmp_path / "material" / "1" / "g1.png"
    r = lf.LocalFlux(backend=fake).generate(
        "A tidy desk with a laptop and a coffee cup.", 1080, 1920, 42,
        ["#0b1a3a", "#F2A33A"], "photo", dest,
    )  # fmt: skip
    req, timeout, limit = fake.requests[0]
    assert req["prompt"] == (
        "A tidy desk with a laptop and a coffee cup; colours deep navy blue and warm orange; "
        "clean editorial photo; no text, no letters, no logos"
    )
    assert (req["width"], req["height"], req["seed"]) == (1024, 1792, 42)
    assert req["steps"] == 4 and req["tiling"] is False  # 32 GB Mac: no tiled decode
    assert req["model_dir"] == str(installed)
    assert timeout == lf.TIMEOUT_S and limit == lf.memory_limit()
    assert r.file == dest and dest.read_bytes().startswith(b"\x89PNG")
    assert (r.model, r.revision, r.seed, r.watermark) == (
        "flux2-klein-4b",
        manifest.models()["flux2-klein-4b"]["revision"],
        42,
        "none",
    )
    assert (r.width, r.height, r.steps, r.quantisation) == (1024, 1792, 4, "8-bit")
    assert r.prompt == req["prompt"] and r.elapsed_s >= 0
    assert r.extra["peak_gb"] == 9.0


def test_seed_is_chosen_and_recorded_when_none(installed, tmp_path):
    fake = FakeBackend()
    r = lf.LocalFlux(backend=fake).generate("a mountain lake", 1024, 1024, None, [],
                                            "illustration", tmp_path / "a.png")  # fmt: skip
    assert r.seed == fake.requests[0][0]["seed"] and 0 <= r.seed < 2**31
    assert r.prompt == "a mountain lake; flat illustration; " + mp.NO_TEXT


@pytest.mark.parametrize(
    ("w", "h", "want"),
    [
        (1024, 1024, (1024, 1024)),
        (1080, 1350, (1024, 1024)),  # 4:5 is cropped from the square
        (1080, 1920, (1024, 1792)),
        (1920, 1080, (1792, 1024)),
        (3000, 1000, (1792, 1024)),
        (500, 2000, (1024, 1792)),
    ],
)
def test_size_mapping(w, h, want):
    assert lf.size_for(w, h) == want
    assert all(v % 16 == 0 for v in want)


def test_size_must_be_positive():
    with pytest.raises(ValueError):
        lf.size_for(0, 100)


def test_prompt_takes_only_hex_colours_and_known_styles():
    text = lf.prompt_text("  a  city at night. ", ["red", "#12345", "#abcdef", 7], "photo")
    assert text == "a city at night; colours pale blue; clean editorial photo; " + mp.NO_TEXT
    with pytest.raises(ValueError):
        lf.prompt_text("x", [], "watercolour")
    with pytest.raises(ValueError):
        lf.prompt_text("  ", [], "photo")
    many = ["#0b1a3a", "#f2a33a", "#ffffff", "#2e7d32", "#e91e63", "#00838f", "#7b1fa2"]
    text = lf.prompt_text("x", many, "photo")
    colours = text.split("; ")[1]
    assert "#" not in text and colours.count(" and ") == 1
    assert colours.count(",") == lf.MAX_COLOURS - 2  # five names: "a, b, c, d and e"


def test_refused_inside_an_agent_run(installed, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    fake = FakeBackend()
    with pytest.raises(lf.FluxError, match="agent run"):
        lf.LocalFlux(backend=fake).generate("x", 1024, 1024, 1, [], "photo", tmp_path / "a.png")
    assert not fake.requests
    ok, reason = lf.LocalFlux().available()
    assert not ok and "approval" in reason


def test_model_download_refused_inside_an_agent_run(monkeypatch):
    monkeypatch.setenv("VIDEOCUT_AGENT", "1")
    with pytest.raises(RuntimeError, match="agent run"):
        install.install_model(lf.MODEL)


def test_available_reasons(videocut_home, monkeypatch):
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)
    monkeypatch.setattr(lf.sys, "platform", "darwin")
    monkeypatch.setattr(lf.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(lf, "memory_gb", lambda: 8.0)
    ok, reason = lf.LocalFlux().available()
    assert not ok and "16 GB" in reason and "8 GB" in reason
    monkeypatch.setattr(lf, "memory_gb", lambda: 16.0)
    monkeypatch.setattr(lf, "mflux_version", lambda: None)
    ok, reason = lf.LocalFlux().available()
    assert not ok and "models pull --image" in reason
    monkeypatch.setattr(lf, "mflux_version", lambda: "0.23.0")
    ok, reason = lf.LocalFlux().available()
    assert not ok and "not downloaded" in reason
    d = videocut_home / "models" / lf.MODEL
    d.mkdir(parents=True)
    (d / install.HF_MARKER).write_text("0" * 40)  # another revision is not ready
    assert not lf.LocalFlux().available()[0]
    (d / install.HF_MARKER).write_text(lf.revision())  # the download, not the 8-bit copy
    assert not lf.LocalFlux().available()[0]
    (d / install.HF_MARKER).write_text(lf.ready_marker())
    ok, reason = lf.LocalFlux().available()
    assert ok and "mflux 0.23.0" in reason


def test_unavailable_generate_raises_the_reason(videocut_home, tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)
    monkeypatch.setattr(lf, "mflux_version", lambda: None)
    with pytest.raises(lf.FluxError):
        lf.LocalFlux(backend=FakeBackend()).generate("x", 1, 1, 1, [], "photo", tmp_path / "a.png")


def test_registry_and_settings(videocut_home):
    assert "local" in mp.providers()
    p = mp.get_provider("local")
    assert isinstance(p, lf.LocalFlux) and isinstance(p, mp.ImageProvider)
    assert settings.load()["image_provider"] == "gemini"  # the default generator
    settings.save(image_provider="local")
    assert isinstance(mp.get_provider(settings.load()["image_provider"]), lf.LocalFlux)
    with pytest.raises(KeyError):
        mp.get_provider("nope")


# ------------------------------------------------------------- the child process


def _worker_script(body: str) -> list[str]:
    return [sys.executable, "-c", "import json, sys\nreq = json.loads(sys.stdin.read())\n" + body]


def test_worker_runs_in_a_child_process_offline(tmp_path):
    dest = tmp_path / "x.png"
    cmd = _worker_script(
        "import os\n"
        "open(req['dest'], 'wb').write(b'png')\n"
        "print('progress noise')\n"
        "print(json.dumps({'offline': os.environ.get('HF_HUB_OFFLINE'),"
        " 'limit': req['memory_limit'], 'load_s': 0.1}))\n"
    )
    out = lf.run_worker({"dest": str(dest)}, timeout=30, limit=2**40, cmd=cmd)
    assert out["offline"] == "1" and out["limit"] == 2**40 and dest.read_bytes() == b"png"


def test_worker_timeout_stops_the_child(tmp_path):
    cmd = _worker_script("import time\ntime.sleep(30)\n")
    with pytest.raises(lf.FluxError, match="after 1 s"):
        lf.run_worker({}, timeout=1, limit=2**40, cmd=cmd)


def test_worker_memory_guard_stops_the_child(tmp_path):
    cmd = _worker_script("b = bytearray(300 * 2**20)\nb[::4096] = b'x' * len(b[::4096])\n"
                         "import time\ntime.sleep(30)\n")  # fmt: skip
    with pytest.raises(lf.FluxError, match="memory"):
        lf.run_worker({}, timeout=20, limit=100 * 2**20, cmd=cmd)


def test_worker_failure_names_the_error(tmp_path):
    cmd = _worker_script("raise SystemExit('the weights are damaged')\n")
    with pytest.raises(lf.FluxError, match="weights are damaged"):
        lf.run_worker({}, timeout=20, limit=2**40, cmd=cmd)


# ------------------------------------------------------------- install, manifest, doctor


def test_manifest_entry_for_the_image_model():
    m = manifest.models()[lf.MODEL]
    assert m["kind"] == "huggingface" and m["repo"] == "black-forest-labs/FLUX.2-klein-4B"
    assert m["license"] == "Apache-2.0" and m["default"] is False and m["extra"] == "image"
    assert m["approx_size_gb"] > 10 and m["min_memory_gb"] == lf.MIN_MEMORY_GB
    assert {"transformer/*", "text_encoder/*", "tokenizer/*", "vae/*"} <= set(m["allow_patterns"])


def test_the_image_extra_pins_mflux():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    extra = tomllib.loads((root / "pyproject.toml").read_text())["project"][
        "optional-dependencies"]["image"]  # fmt: skip
    assert any(d.startswith("mflux==") for d in extra)
    lock = (root / "uv.lock").read_text()
    pin = next(d for d in extra if d.startswith("mflux==")).split("==")[1]
    assert f'name = "mflux"\nversion = "{pin}"' in lock


def test_install_image_downloads_quantises_and_keeps_only_the_copy(
    videocut_home, monkeypatch, capsys
):
    models = videocut_home / "models"
    monkeypatch.setattr(install, "MODELS", models)
    monkeypatch.setattr(lf, "memory_gb", lambda: 32.0)
    monkeypatch.setattr(lf, "mflux_version", lambda: None)
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)
    extras, calls, saved = [], [], []
    monkeypatch.setattr(install, "install_extra", extras.append)
    import huggingface_hub

    def fake_download(**kw):
        calls.append(kw)
        Path(kw["local_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kw["local_dir"]) / "LICENSE.md").write_text("licence")

    def fake_save(request, timeout, limit):
        saved.append(request)
        Path(request["dest"]).mkdir(parents=True)
        return {"peak_bytes": 1}

    real_quantise = lf.quantise
    monkeypatch.setattr(lf, "quantise", lambda src: real_quantise(src, backend=fake_save))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_download)
    monkeypatch.setattr(install.shutil, "disk_usage", lambda p: type("U", (), {"free": 1e12}))
    install.install_image()
    m = manifest.models()[lf.MODEL]
    assert extras == ["image"]
    assert calls[0]["repo_id"] == m["repo"] and calls[0]["revision"] == m["revision"]
    assert calls[0]["allow_patterns"] == m["allow_patterns"]
    assert calls[0]["local_dir"] == lf.source_dir()
    assert saved[0]["action"] == "quantise" and saved[0]["quantize"] == 8
    assert lf.model_ready() and (lf.model_dir() / "LICENSE.md").is_file()
    assert not lf.source_dir().exists()  # the full download is gone
    out = capsys.readouterr().out
    assert "16.0 GB download" in out and "8 GB on disk" in out
    install.install_image()  # done: no second download
    install.install_model(lf.MODEL)  # the generic entry leads to the same place
    assert len(calls) == 1 and len(saved) == 1


def test_install_image_resumes_after_the_download(videocut_home, monkeypatch):
    """A run that stopped after the download only makes the 8-bit copy."""
    monkeypatch.setattr(install, "MODELS", videocut_home / "models")
    monkeypatch.setattr(lf, "memory_gb", lambda: 16.0)
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)
    monkeypatch.setattr(install, "install_extra", lambda extra: None)
    monkeypatch.setattr(install.shutil, "disk_usage", lambda p: type("U", (), {"free": 1e12}))
    src = lf.source_dir()
    src.mkdir(parents=True)
    (src / install.HF_MARKER).write_text(lf.revision())
    monkeypatch.setattr(install, "_hf_download", lambda *a: pytest.fail("downloaded again"))
    made = []
    monkeypatch.setattr(lf, "quantise", lambda s: made.append(s))
    install.install_image()
    assert made == [src] and not src.exists()


def test_missing_packages_are_repaired_without_disk_space_for_the_model(installed, monkeypatch):
    monkeypatch.setattr(install, "MODELS", installed.parent)
    monkeypatch.setattr(lf, "mflux_version", lambda: None)
    monkeypatch.setattr(install.shutil, "disk_usage", lambda p: type("U", (), {"free": 1e9}))
    extras = []
    monkeypatch.setattr(install, "install_extra", extras.append)
    monkeypatch.setattr(lf, "quantise", lambda s: pytest.fail("quantised again"))
    install.install_image()
    assert extras == ["image"] and lf.model_ready()


def test_quantise_replaces_a_half_copy(videocut_home):
    src = videocut_home / "src"
    src.mkdir(parents=True)
    (lf.model_dir().with_name(lf.MODEL + ".part")).mkdir(parents=True)  # an earlier crash
    lf.model_dir().mkdir()
    (lf.model_dir() / "stale").write_text("x")

    def save(request, timeout, limit):
        assert timeout == lf.QUANTISE_TIMEOUT_S
        Path(request["dest"]).mkdir()
        (Path(request["dest"]) / "transformer").mkdir()
        return {}

    lf.quantise(src, backend=save)
    assert lf.model_ready() and (lf.model_dir() / "transformer").is_dir()
    assert not (lf.model_dir() / "stale").exists()


def test_small_macs_decode_in_tiles(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(lf, "memory_gb", lambda: 16.0)
    fake = FakeBackend()
    r = lf.LocalFlux(backend=fake).generate("x", 1024, 1024, 1, [], "photo", tmp_path / "a.png")
    assert fake.requests[0][0]["tiling"] is True and r.extra["tiling"] is True


def test_install_image_refuses_a_small_mac_and_a_full_disk(videocut_home, monkeypatch):
    monkeypatch.setattr(install, "MODELS", videocut_home / "models")
    monkeypatch.setattr(lf, "memory_gb", lambda: 8.0)
    with pytest.raises(RuntimeError, match="16 GB memory"):
        install.install_image()
    monkeypatch.setattr(lf, "memory_gb", lambda: 16.0)
    monkeypatch.setattr(install.shutil, "disk_usage", lambda p: type("U", (), {"free": 5e9}))
    monkeypatch.delenv("VIDEOCUT_AGENT", raising=False)
    with pytest.raises(RuntimeError, match="free disk space"):
        install.install_image()


def test_uv_env_keeps_dev_tools_out_for_end_users(monkeypatch):
    monkeypatch.delenv("UV_NO_DEV", raising=False)
    monkeypatch.setattr(install.importlib.util, "find_spec", lambda name: None)
    env = install.uv_env()
    assert env["UV_NO_DEV"] == "1" and env["UV_PYTHON_DOWNLOADS"] == "never"
    assert env["UV_PROJECT_ENVIRONMENT"] == sys.prefix
    monkeypatch.setenv("UV_NO_DEV", "0")
    assert install.uv_env()["UV_NO_DEV"] == "0"


def test_install_extra_syncs_inexact_with_the_extra(monkeypatch):
    runs = []
    monkeypatch.setattr(install.subprocess, "run", lambda cmd, **kw: runs.append(cmd))
    install.install_extra("image")
    assert runs[0][1:] == ["sync", "--frozen", "--inexact", "--quiet", "--extra", "image"]
    assert runs[1][1:4] == ["cache", "prune", "--ci"]


def test_start_sh_keeps_the_image_extra():
    text = (Path(__file__).resolve().parents[1] / "start.sh").read_text()
    assert f'"$DATA/models/{lf.MODEL}"' in text and "--extra image" in text
    assert f'"$DATA/models/.download/{lf.MODEL}"' in text


def test_doctor_image_check(installed, monkeypatch):
    c = doctor.check_image()
    assert c.ok and not c.required and "mflux 0.23.0" in c.detail
    (installed / install.HF_MARKER).unlink()
    c = doctor.check_image()
    assert not c.ok and "not downloaded completely" in c.detail and "--image" in c.fix
    monkeypatch.setattr(lf, "mflux_version", lambda: None)
    c = doctor.check_image()
    assert not c.ok and "optional" in c.detail and "16 GB" in c.fix
    assert "Settings → Material" in c.fix
    monkeypatch.setattr(lf, "memory_gb", lambda: 8.0)
    c = doctor.check_image()
    assert c.ok and "not on this Mac" in c.detail
    names = [c.name for c in doctor.check_models()]
    assert f"model {lf.MODEL}" not in names  # covered by the image check
    assert json.dumps(doctor.report([doctor.check_image()]))
