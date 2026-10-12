import argparse

import pytest

from videocut import tools
from videocut.cli import main


def test_every_module_registers_handlers():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="tool_cmd", required=True)
    tools.register_all(sub)
    for argv in (
        ["hello"],
        ["job", "show"],
        ["ingest", "--denoise"],
        ["transcribe", "--model", "parakeet"],
        ["candidates"],
        ["sheet", "contact", "--every", "3"],
        ["sheet", "burst", "1.5", "--span", "0.5"],
        ["sheet", "timeline", "1", "2"],
        ["edl", "check", "x.json"],
        ["render", "--preview"],
        ["verify"],
    ):
        assert callable(p.parse_args(argv).handler), argv


def test_hello(capsys):
    assert main(["tool", "hello"]) == 0
    assert "ready in" in capsys.readouterr().out


def test_unknown_tool_is_rejected():
    with pytest.raises(SystemExit):
        main(["tool", "nope"])
