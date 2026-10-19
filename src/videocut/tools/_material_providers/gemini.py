"""Gemini API image generation (https://ai.google.dev/gemini-api/docs/image-generation,
checked 2026-10-19): the Interactions endpoint with model MODEL, a text prompt and
`response_format` {type image, aspect_ratio, image_size}. Sent: the prompt text (with the
style words and the brand's hex colours), the aspect ratio and size; nothing else. Every
image carries Google's SynthID watermark; the returned file is stored byte for byte and
never re-encoded (derived files are made beside it)."""

import base64
import time

from . import (
    GenResult,
    ImageProvider,
    ProviderError,
    aspect_label,
    full_prompt,
    key,
    post_json,
    register_provider,
)

API = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODEL = "gemini-nano-banana-2.1"
RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
# Published list price per output image (Standard tier, 2026-10-19): 1K $0.0336, 2K $0.0504.
PRICE = {"1K": 0.0336, "2K": 0.0504}


def image_size(width: int, height: int) -> str:
    return "1K" if max(width, height) <= 1024 else "2K"


def _image_data(data: dict) -> str | None:
    """The base64 image of an interaction (the last image block)."""
    out = data.get("output_image")
    if isinstance(out, dict) and isinstance(out.get("data"), str):
        return out["data"]
    found = None
    for step in data.get("steps") or data.get("outputs") or []:
        if not isinstance(step, dict):
            continue
        blocks = step.get("content") if isinstance(step.get("content"), list) else [step]
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "image" and isinstance(b.get("data"), str):
                found = b["data"]
    return found


class Gemini(ImageProvider):
    name = "gemini"
    model = MODEL
    secret_account = "gemini-api-key"

    def estimate_usd(self, width, height):
        return PRICE[image_size(width, height)]

    def generate(self, prompt, width, height, seed, palette, style, dest):
        t0 = time.monotonic()
        size = image_size(width, height)
        body = {
            "model": MODEL,
            "input": [{"type": "text", "text": full_prompt(prompt, palette, style)}],
            "response_format": {
                "type": "image",
                "mime_type": "image/png",
                "aspect_ratio": aspect_label(width, height, RATIOS),
                "image_size": size,
            },
        }
        data = post_json(API, body, {"x-goog-api-key": key(self.secret_account)})
        b64 = _image_data(data)
        if not b64:
            raise ProviderError("gemini: the answer has no image (blocked or refused prompt?)")
        dest = dest.with_suffix(".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(b64))
        return GenResult(
            file=dest, model=MODEL, revision=str(data.get("model_version") or MODEL),
            seed=None, watermark="synthid", elapsed_s=round(time.monotonic() - t0, 2),
            cost_usd=PRICE[size],
        )  # fmt: skip


register_provider("gemini", Gemini)
