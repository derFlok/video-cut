"""OpenAI Image API (https://developers.openai.com/api/docs/guides/image-generation, checked
2026-10-19): POST /v1/images/generations with model MODEL, a custom size (both sides
multiples of 16), quality "medium", PNG, returned as base64. Sent: the prompt text (with
the style words and the brand's hex colours) and the size. OpenAI's images carry C2PA
content credentials; the returned file is stored byte for byte and never re-encoded."""

import base64
import time

from . import (
    GenResult,
    ImageProvider,
    ProviderError,
    full_prompt,
    key,
    post_json,
    register_provider,
)

API = "https://api.openai.com/v1/images/generations"
MODEL = "gpt-image-2.5-flare"
QUALITY = "medium"
# Priced per output token ($30 / 1M image output tokens, 2026-10-19); a medium image of
# about two megapixels is estimated at this much (rounded up).
PRICE_USD = 0.06
MAX_EDGE = 2048


def api_size(width: int, height: int) -> tuple[int, int]:
    """The request size: the output aspect, long side ≤ MAX_EDGE, multiples of 16, at least
    655,360 px (the API's lower bound)."""
    k = min(1.0, MAX_EDGE / max(width, height))
    w, h = width * k, height * k
    while w * h < 655_360:
        w, h = w * 1.1, h * 1.1
    return max(16, round(w / 16) * 16), max(16, round(h / 16) * 16)


class OpenAIImage(ImageProvider):
    name = "openai"
    model = MODEL
    secret_account = "openai-api-key"
    price_usd = PRICE_USD

    def generate(self, prompt, width, height, seed, palette, style, dest):
        t0 = time.monotonic()
        w, h = api_size(width, height)
        body = {
            "model": MODEL,
            "prompt": full_prompt(prompt, palette, style),
            "size": f"{w}x{h}",
            "quality": QUALITY,
            "output_format": "png",
            "n": 1,
        }
        data = post_json(API, body, {"Authorization": f"Bearer {key(self.secret_account)}"})
        items = data.get("data") or []
        b64 = items[0].get("b64_json") if items and isinstance(items[0], dict) else None
        if not b64:
            raise ProviderError("openai: the answer has no image (blocked or refused prompt?)")
        dest = dest.with_suffix(".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(b64))
        return GenResult(
            file=dest, model=MODEL, revision=str(data.get("model") or MODEL), seed=None,
            watermark="c2pa", elapsed_s=round(time.monotonic() - t0, 2), width=w, height=h,
            cost_usd=PRICE_USD,
        )  # fmt: skip


register_provider("openai", OpenAIImage)
