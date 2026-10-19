"""Command line: videocut setup | doctor | selftest | models | ui | open | launcher | update |
auth | run | tool <name>."""

import argparse
import os
import subprocess
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
    s.add_argument(
        "--image",
        action="store_true",
        help="also install local image generation (FLUX.2 [klein] 4B, 16 GB download)",
    )
    d = sub.add_parser("doctor", help="check the installation (each failed check names a fix)")
    d.add_argument("--json", action="store_true", help="the checks as JSON, for a helper")
    sub.add_parser("selftest", help="run the pipeline on a generated test clip")
    m = sub.add_parser("models", help="manage models")
    msub = m.add_subparsers(dest="models_cmd", required=True)
    pull = msub.add_parser("pull", help="download an optional model")
    pull.add_argument("name", nargs="?")
    pull.add_argument(
        "--image",
        action="store_true",
        help="local image generation: FLUX.2 [klein] 4B (16 GB download, 8 GB kept)",
    )

    u = sub.add_parser("ui", help="start the local app")
    u.add_argument(
        "--port",
        type=int,
        default=None,
        help="port (default: VIDEOCUT_PORT or 4300; the next free one if it is taken)",
    )
    u.add_argument("--no-open", action="store_true", help="do not open the browser")
    o = sub.add_parser("open", help="open the app; start it in the background if needed")
    o.add_argument("--port", type=int, default=None, help="port to start on (VIDEOCUT_PORT, 4300)")
    o.add_argument("--no-browser", action="store_true", help="only start, open nothing")
    la = sub.add_parser("launcher", help="create the double-click app ~/Applications/Video Cut.app")
    la.add_argument("--dir", help="folder for Video Cut.app (default: ~/Applications)")
    la.add_argument("--system", action="store_true", help="put it in /Applications instead")
    la.add_argument("--port", type=int, default=None, help="fixed port for the app")
    la.add_argument(
        "--at-login",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="start Video Cut in the background at login (off unless asked)",
    )
    la.add_argument("--remove", action="store_true", help="remove the launcher and login item")
    st = sub.add_parser("stop", help="stop the running app")
    st.add_argument("--force", action="store_true", help="also while a job is running")
    up = sub.add_parser("update", help="get the newest Video Cut and install it")
    up.add_argument("--force", action="store_true", help="stop a running job to update")
    up.add_argument("--ref", help="a release tag or branch instead of the newest release")
    auth = sub.add_parser("auth", help="choose how Video Cut connects to Claude")
    asub = auth.add_subparsers(dest="auth_cmd", required=True)
    asub.add_parser("local", help="use your own Claude Code sign-in (personal use)")
    asub.add_parser("key", help="store an Anthropic API key in the keychain and use it")
    asub.add_parser("status", help="show the current mode")

    r = sub.add_parser("run", help="cut a recording: prep, plan, your approval, cut, overlays")
    r.add_argument("target", help="a recording (creates a job) or an existing job folder/id")
    r.add_argument("--type", help="video type, e.g. thought (thought of the day)")
    r.add_argument("--brief", help="free-text instruction for Claude")
    r.add_argument("--approve", action="store_true", help="approve the plan without asking")
    r.add_argument("--replan", action="store_true", help="plan again even if a plan exists")
    r.add_argument("--max-turns", type=int, default=60, help="turn limit per Claude run")
    r.add_argument("--brand", help="brand id: captions and overlays in this brand's look")
    r.add_argument("--recut", action="store_true", help="cut again even if the cut is done")
    r.add_argument(
        "--asset",
        action="extend",
        nargs="+",
        metavar="FILE",
        help="extra input images (screenshots) for a new job; several files, repeatable",
    )
    r.add_argument(
        "--demo-data",
        action="store_true",
        help="the personal data the plan found is demo data: redact nothing (your decision)",
    )
    r.add_argument(
        "--screen",
        metavar="FILE",
        help="screen recording made at the same time (product demo) for a new job",
    )
    r.add_argument(
        "--format",
        help="output format 9:16, 16:9, 1:1 or 4:5 (default: 16:9 with a screen recording or "
        "screenshots, else 9:16); for an existing job it replaces the format",
    )
    r.add_argument(
        "--cut-style",
        help="cut style calm, standard or tight (default: calm with a screen recording or "
        "screenshots, else standard); for an existing job it replaces the style and a "
        "finished cut is cut again",
    )
    r.add_argument(
        "--target",
        dest="target_length",
        metavar="LENGTH",
        help="target length: seconds or 90s, 5m, 2:30, or auto (default: 30-45 s for "
        "thought, 30-60 s for other types; with a screen recording or screenshots as long "
        "as the content needs); for an existing job a plan made for another length is "
        "planned again and needs a new approval",
    )
    r.add_argument(
        "--vocab",
        action="append",
        metavar="TERM=VARIANT,...",
        help="transcript vocabulary for a new job: the canonical spelling and the "
        'misspellings the transcriber writes, e.g. "Zentrovia=Zentrowia,Sentrovia" '
        "(repeatable; the brand's vocabulary applies too)",
    )

    r.add_argument(
        "--redact-style",
        metavar="STYLE",
        help="how personal data on the screen is hidden: auto (default: cut off full-width "
        "bars at the screen's top/bottom, blur the rest), blur, crop or block; for an "
        "existing job a finished reel is rendered again",
    )
    r.add_argument(
        "--slides-mode",
        metavar="MODE",
        help="slides and animations: none (default), upload (your own pages, added with "
        "`videocut tool job slides-import`), auto (Claude proposes slides for what is said), "
        "both; for an existing job a plan made for other slides is planned again",
    )
    r.add_argument(
        "--slides-wish",
        metavar="TEXT",
        help='what the slides should show, in plain words (e.g. "the three steps as a flow")',
    )
    r.add_argument(
        "--feedback",
        metavar="TEXT",
        help="revise the finished reel: what to change, in plain words (no new approval)",
    )
    r.add_argument(
        "--at", type=float, metavar="S", help="with --feedback: the reel second it is about"
    )
    r.add_argument(
        "--plan-feedback",
        metavar="TEXT",
        help="revise the plan before approval: what to change (implies --replan)",
    )

    t = sub.add_parser("tool", help="pipeline commands used by Claude inside a job")
    tsub = t.add_subparsers(dest="tool_cmd", required=True)
    from . import tools

    tools.register_all(tsub)

    a = p.parse_args(argv)
    _use_installed_tools()
    if a.cmd == "setup":
        from . import install

        try:
            install.run(models=not a.no_models)
            if a.image:
                install.install_image()
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

        return doctor.run(as_json=a.json)
    if a.cmd == "selftest":
        from . import selftest

        return selftest.run()
    if a.cmd == "models":
        from . import install, manifest
        from .tools._material_providers import local_flux

        if a.image or a.name == local_flux.MODEL:
            try:
                install.install_image()
            except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
                print(f"\033[31m✗ Not installed: {e}\033[0m\n  Run the command again; "
                      "finished parts are kept.", file=sys.stderr)  # fmt: skip
                return 1
            return 0
        if a.name not in manifest.models():
            if a.name is None:
                print("name a model or pass --image; known: " + ", ".join(manifest.models()))
                return 2
            print(f"unknown model {a.name}; known: {', '.join(manifest.models())}")
            return 2
        install.install_model(a.name)
        return 0
    if a.cmd == "ui":
        from . import launcher, server

        port = a.port or launcher.free_port(launcher.default_port())
        server.serve(port, open_browser=not a.no_open)
        return 0
    if a.cmd == "open":
        from . import launcher

        return launcher.open_app(a.port, browser=not a.no_browser)
    if a.cmd == "launcher":
        return _launcher(a)
    if a.cmd == "stop":
        from . import update

        try:
            print("Stopped." if update.stop_server(a.force) else "Video Cut was not running.")
        except update.UpdateError as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    if a.cmd == "update":
        from . import update

        try:
            return update.run(force=a.force, ref=a.ref)
        except (update.UpdateError, OSError, subprocess.CalledProcessError) as e:
            print(f"\033[31m✗ Update failed: {e}\033[0m", file=sys.stderr)
            return 1
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
            try:
                settings.set_api_key(key)
            except (ValueError, RuntimeError, OSError) as e:
                print(f"Not stored: {e}", file=sys.stderr)
                return 1
            settings.save(auth="apikey")
        mode = settings.load()["auth"]
        print("Mode: " + ("API key (keychain)" if mode == "apikey" else "own Claude Code sign-in"))
        return 0
    if a.cmd == "run":
        from . import run

        return run.main(a)
    if a.cmd == "tool":
        return a.handler(a)
    return 2


def _launcher(a) -> int:
    from pathlib import Path

    from . import launcher

    if a.remove:
        for p in launcher.remove():
            print(f"Removed {p}")
        return 0
    dest = Path(a.dir).expanduser() if a.dir else (
        Path("/Applications") if a.system else Path.home() / "Applications"
    )  # fmt: skip
    dest.mkdir(parents=True, exist_ok=True)
    app = launcher.bundle(dest, port=a.port)
    print(f"Created {app}")
    if a.at_login is not None:
        item = launcher.set_login_item(app, a.at_login)
        print(("Starts at login: " if a.at_login else "No start at login: ") + str(item))
    return 0


if __name__ == "__main__":
    sys.exit(main())
