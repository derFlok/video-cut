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

    u = sub.add_parser("ui", help="start the local app")
    u.add_argument("--port", type=int, default=4300)
    u.add_argument("--no-open", action="store_true", help="do not open the browser")
    auth = sub.add_parser("auth", help="choose how Video Cut connects to Claude")
    asub = auth.add_subparsers(dest="auth_cmd", required=True)
    asub.add_parser("local", help="use your own Claude Code sign-in (personal use)")
    asub.add_parser("key", help="store an Anthropic API key in the keychain and use it")
    asub.add_parser("status", help="show the current mode")

    t = sub.add_parser("tool", help="pipeline commands used by Claude inside a job")
    tsub = t.add_subparsers(dest="tool_cmd", required=True)
    tsub.add_parser("hello", help="connectivity check")

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
    if a.cmd == "ui":
        from . import web

        web.serve(a.port, open_browser=not a.no_open)
        return 0
    if a.cmd == "auth":
        from . import settings

        if a.auth_cmd == "local":
            settings.save(auth="local")
        elif a.auth_cmd == "key":
            import getpass

            key = getpass.getpass("Anthropic API key (input hidden): ").strip()
            if not key.startswith("sk-ant-"):
                print("That does not look like an Anthropic API key.", file=sys.stderr)
                return 1
            settings.set_api_key(key)
            settings.save(auth="apikey")
        mode = settings.load()["auth"]
        print("Mode: " + ("API key (keychain)" if mode == "apikey" else "own Claude Code sign-in"))
        return 0
    if a.cmd == "tool" and a.tool_cmd == "hello":
        print(f"videocut {__version__} ready in {os.getcwd()}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
