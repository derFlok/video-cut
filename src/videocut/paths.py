"""Where things live. Repo-local tools vs. user data outside the repo."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".tools"
BIN = TOOLS / "bin"
MANIFEST = ROOT / "manifest" / "tools.json"
RENDERER = ROOT / "renderer"
BROWSERS = TOOLS / "ms-playwright"

HOME = Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut"))
MODELS = HOME / "models"
JOBS = HOME / "jobs"
BRANDS = HOME / "brands"
MUSIC = HOME / "music"


def tool(name: str) -> Path:
    """Path of an installed binary in .tools/bin."""
    return BIN / name


def tool_env() -> dict[str, str]:
    """Environment for child processes: our tools first on PATH."""
    env = dict(os.environ)
    env["PATH"] = f"{BIN}{os.pathsep}{env.get('PATH', '')}"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env
