import json

from videocut.cli import main


def run_tool(capsys, *argv: str) -> tuple[int, dict, str]:
    """Run `videocut tool ...`; return exit code, the JSON summary line and all stdout."""
    capsys.readouterr()
    code = main(["tool", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out.strip().splitlines()[-1]), out
