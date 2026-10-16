"""scripts/fresh-install-test.sh: syntax and argument handling (the install itself is slow and
runs by hand; see docs/fresh-install-report.md)."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fresh-install-test.sh"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30
    )


def test_script_parses_under_system_bash():
    # macOS ships bash 3.2; the script must not need a newer one.
    out = subprocess.run(["/bin/bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_help_lists_the_modes():
    out = _run("--help")
    assert out.returncode == 0
    for word in ("--keep-models", "--claude", "--lang", "--report"):
        assert word in out.stdout


def test_bad_arguments_stop_before_any_work():
    assert _run("--nope").returncode == 2
    assert _run("--lang", "fr").returncode == 2
