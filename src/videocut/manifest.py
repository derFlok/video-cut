"""Pinned third-party downloads (manifest/tools.json)."""

import json
from functools import cache

from .paths import MANIFEST


@cache
def load() -> dict:
    return json.loads(MANIFEST.read_text())


def tool(name: str) -> dict:
    return load()["tools"][name]


def models() -> dict[str, dict]:
    return load()["models"]
