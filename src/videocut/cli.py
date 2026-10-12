"""Command line: videocut setup | doctor | selftest | models pull <name>."""

import argparse
import os
import sys

from . import __version__
from .paths import tool_env


def _use_installed_tools() -> None:
    """Libraries such as mlx-whisper call `ffmpeg` via PATH; point them at ours."""
    os.environ.update(tool_env())
    os.environ.setdefault("GLOG_minloglevel", "2")  # MediaPipe C++ logs
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")  # TFLite delegate notices


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="videocut", description="Video Cut")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="install pinned tools and default models")
    s.add_argument("--no-models", action="store_true", help="skip model downloads")
    sub.add_parser("doctor", help="check the installation")
    sub.add_parser("selftest", help="run the pipeline on a generated test clip")
    m = sub.add_parser("models", help="manage models")
    msub = m.add_subparsers(dest="models_cmd", required=True)
    pull = msub.add_parser("pull", help="download an optional model")
    pull.add_argument("name")

    a = p.parse_args(argv)
    _use_installed_tools()
    if a.cmd == "setup":
        from . import install

        try:
            install.run(models=not a.no_models)
        except Exception as e:  # noqa: BLE001 — one readable line instead of a traceback
            print(
                f"\033[31m✗ Setup failed: {e}\033[0m\n  Fix the cause and run ./start.sh again; "
                "finished steps are skipped.",
                file=sys.stderr,
            )
            return 1
        return 0
    if a.cmd == "doctor":
        from . import doctor

        return doctor.run()
    if a.cmd == "selftest":
        from . import selftest

        return selftest.run()
    if a.cmd == "models":
        from . import install, manifest

        if a.name not in manifest.models():
            print(f"unknown model {a.name}; known: {', '.join(manifest.models())}")
            return 2
        install.install_model(a.name)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
