"""Material providers: stock search and image generation behind one small interface.

A provider module calls `register_provider(name, cls)`; `get_provider(name)` imports the
built-in module for `name` on first use (so `import mflux` and friends only happen when that
provider is asked for) and returns an instance. `settings.json` `image_provider` names one."""

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

_REGISTRY: dict[str, type] = {}
# provider name -> module in this package that registers it (imported lazily)
BUILTIN = {"local": "local_flux"}


@dataclass
class GenResult:
    """What a generator made: the file and everything material.json records about it."""

    file: Path
    width: int
    height: int
    model: str
    revision: str
    seed: int
    watermark: str  # "synthid" | "c2pa" | "none"
    elapsed_s: float
    prompt: str  # the exact text sent to the model
    steps: int | None = None  # local models: denoising steps
    quantisation: str | None = None  # local models: e.g. "8-bit"
    extra: dict = field(default_factory=dict)  # provider specifics


class ImageProvider:
    name = ""

    def available(self) -> tuple[bool, str]:
        """(usable now, plain-language reason when not)."""
        raise NotImplementedError

    def generate(self, prompt: str, width: int, height: int, seed: int | None,
                 palette: list[str], style: str, dest: Path) -> GenResult:  # fmt: skip
        raise NotImplementedError


def register_provider(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def get_provider(name: str):
    if name not in _REGISTRY and name in BUILTIN:
        import_module(f"{__name__}.{BUILTIN[name]}").register()
    if name not in _REGISTRY:
        raise KeyError(f"unknown material provider {name!r}; known: {', '.join(providers())}")
    return _REGISTRY[name]()


def providers() -> list[str]:
    return sorted({*_REGISTRY, *BUILTIN})
