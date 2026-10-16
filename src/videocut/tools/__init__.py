"""Registry of `videocut tool <name> ...` subcommands.

Each module in MODULES exposes `register(subparsers)`, which adds its parser(s) and sets
`handler=<callable(args) -> int>` as default. cli.py only calls `register_all` and then
the parsed handler, so adding or extending a tool never touches cli.py or this file.
"""

import argparse
from importlib import import_module

MODULES = [
    "hello",
    "job",
    "ingest",
    "transcribe",
    "align",
    "sheets",
    "edl",
    "render",
    "verify",
    "face",
    "brand",
    "captions",
    "overlays",
    "sync",
    "screen",
    "plan",
    "redact",
    "vocab",
    "slides",
    "transcript",
]


def register_all(subparsers: argparse._SubParsersAction) -> None:
    for name in MODULES:
        import_module(f"{__name__}.{name}").register(subparsers)
