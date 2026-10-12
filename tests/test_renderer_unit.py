"""Runs the renderer's node unit tests (spring, layout, scene lint) under pytest."""

import subprocess

import pytest

from videocut.paths import BIN, RENDERER, tool_env


@pytest.mark.skipif(not (BIN / "node").is_file(), reason="node not installed")
def test_renderer_node_tests():
    tests = sorted(str(p.relative_to(RENDERER)) for p in (RENDERER / "test").glob("*.test.js"))
    assert tests
    proc = subprocess.run(
        [str(BIN / "node"), "--test", *tests],
        cwd=RENDERER,
        env=tool_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]
