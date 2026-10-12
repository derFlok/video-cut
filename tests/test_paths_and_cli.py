import importlib

import pytest


def test_user_data_lives_outside_the_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOCUT_HOME", str(tmp_path / "vc"))
    from videocut import paths

    paths = importlib.reload(paths)
    assert paths.HOME == tmp_path / "vc"
    assert paths.MODELS == tmp_path / "vc" / "models"
    assert not str(paths.HOME).startswith(str(paths.ROOT))


def test_tool_env_puts_installed_tools_first():
    from videocut.paths import BIN, tool_env

    assert tool_env()["PATH"].split(":")[0] == str(BIN)


def test_cli_rejects_unknown_command():
    from videocut.cli import main

    with pytest.raises(SystemExit):
        main(["nope"])
