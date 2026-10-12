import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import clips  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "media: needs macOS `say` (voice Anna) and ffmpeg; skipped where missing"
    )


def pytest_collection_modifyitems(config, items):
    if clips.available():
        return
    skip = pytest.mark.skip(reason="needs macOS `say` -v Anna and ffmpeg/ffprobe")
    for item in items:
        if "media" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def videocut_home(tmp_path, monkeypatch):
    """Every test gets its own VIDEOCUT_HOME, so user data is never touched."""
    home = tmp_path / "videocut-home"
    monkeypatch.setenv("VIDEOCUT_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def clip_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("clips")


@pytest.fixture(scope="session")
def portrait_clip(clip_dir):
    return clips.build(clip_dir, "portrait")


@pytest.fixture(scope="session")
def landscape_clip(clip_dir):
    return clips.build(clip_dir, "landscape")


@pytest.fixture(scope="session")
def rotated_clip(clip_dir):
    """Landscape-coded clip that a player shows upright (phone recording)."""
    return clips.build(clip_dir, "landscape", rotate=90)
