"""`videocut tool hello`: connectivity check for the Claude runner."""

import os

from .. import __version__


def handle(args) -> int:
    print(f"videocut {__version__} ready in {os.getcwd()}")
    return 0


def register(subparsers) -> None:
    subparsers.add_parser("hello", help="connectivity check").set_defaults(handler=handle)
