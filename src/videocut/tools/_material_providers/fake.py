"""Offline stand-ins for the material providers: `fake-stock` and `fake-image`.

Used only when VIDEOCUT_MATERIAL_FAKE=1 (or `tool material fetch --dry-run`): they make
moving gradients and still pictures locally with FFmpeg, so the whole chain (plan →
approve → fetch → choose → cut with broll → overlays → render → verify) runs without an
API key and without the network. Not a built-in (never listed in the settings); the
candidates say `source: fake-*` and `licence: test material` so they cannot pass for real
material. The fake images count as AI-generated, like the generator they stand in for.
"""

import subprocess
import time
import zlib
from pathlib import Path

from ..common import binary
from . import GenResult, ImageProvider, ProviderError, StockHit, StockProvider, register_provider

ENV = "VIDEOCUT_MATERIAL_FAKE"
STOCK = "fake-stock"
IMAGE = "fake-image"
CLIP_S = 6.0
SIZES = {"portrait": (1080, 1920), "landscape": (1920, 1080), "square": (1080, 1080)}


def _colours(text: str, k: int) -> tuple[str, str]:
    """Two stable colours per (text, k): a different picture per candidate."""
    h = zlib.crc32(f"{text}|{k}".encode())
    a, b = h & 0xFFFFFF, (h >> 8 ^ 0x5A5A5A) & 0xFFFFFF
    return f"0x{a:06x}", f"0x{b:06x}"


def _ffmpeg(*args: str) -> None:
    try:
        subprocess.run([binary("ffmpeg"), "-v", "error", "-y", *args], check=True,
                       capture_output=True, timeout=120)  # fmt: skip
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        raise ProviderError(f"fake material: ffmpeg failed ({e})") from None


def _gradient(dest: Path, w: int, h: int, c0: str, c1: str, seconds: float | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = f"gradients=s={w}x{h}:c0={c0}:c1={c1}:x0=0:y0=0:x1={w}:y1={h}:speed=0.02"
    if seconds:
        _ffmpeg("-f", "lavfi", "-i", f"{src}:r=30:d={seconds}", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(dest))  # fmt: skip
    else:
        _ffmpeg("-f", "lavfi", "-i", src, "-frames:v", "1", str(dest))


class FakeStock(StockProvider):
    name = STOCK

    def available(self) -> tuple[bool, str]:
        return True, ""

    def search(self, query: str, kind: str, orientation: str, locale: str,
               limit: int) -> list[StockHit]:  # fmt: skip
        w, h = SIZES.get(orientation, SIZES["landscape"])
        return [
            StockHit(id=f"{STOCK}-{kind}-{zlib.crc32(query.encode()):08x}-{i}", kind=kind,
                     width=w, height=h, duration_s=CLIP_S if kind == "video" else None,
                     preview_url="", file_url=f"fake://{kind}/{i}", author="Test Pattern",
                     source_url="", licence="test material",
                     attribution="Test material (offline run)", source=STOCK)
            for i in range(min(limit, 2))
        ]  # fmt: skip

    def download(self, hit: StockHit, dest: Path, min_size=None) -> Path:
        c0, c1 = _colours(hit.id, 0)
        _gradient(dest, hit.width, hit.height, c0, c1, hit.duration_s)
        return dest


class FakeImage(ImageProvider):
    name = IMAGE
    model = "fake-image"
    price_usd = 0.0

    def available(self) -> tuple[bool, str]:
        return True, ""

    def generate(self, prompt: str, width: int, height: int, seed: int | None,
                 palette: list[str], style: str, dest: Path) -> GenResult:  # fmt: skip
        t0 = time.monotonic()
        dest = dest.with_suffix(".png")
        c0, c1 = _colours(prompt, seed or 0)
        hexes = [p.lstrip("#") for p in palette if len(p.lstrip("#")) == 6]
        if hexes:
            c0 = "0x" + hexes[0]
        _gradient(dest, width, height, c0, c1, None)
        return GenResult(file=dest, model=self.model, revision="", seed=seed, watermark="none",
                         elapsed_s=round(time.monotonic() - t0, 2), width=width, height=height,
                         cost_usd=0.0)  # fmt: skip


register_provider(STOCK, FakeStock)
register_provider(IMAGE, FakeImage)
