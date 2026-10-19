"""Local image generation: FLUX.2 [klein] 4B (Apache-2.0) through mflux (MIT, MLX).

Nothing leaves the Mac: the weights come once from Hugging Face at a pinned revision
(`videocut models pull --image`, manifest/tools.json) and are saved once as an 8-bit copy,
the only thing kept (`quantise`); generation runs offline from that copy. The prompt is
the wish's sentence plus the brand's colour hex values and the style words, nothing else.

Every image runs in its own child process (`python -I -m <this module>`) with a timeout and a
memory guard, so a model that hangs or grows beyond what the Mac can spare is stopped and the
app keeps running. Never inside an agent run (VIDEOCUT_AGENT): the agent only proposes prompts.
"""

import ctypes
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

from ... import manifest
from ...install import HF_MARKER
from . import NO_TEXT, GenResult, ImageProvider, colour_words, register_provider

MODEL = "flux2-klein-4b"  # manifest/tools.json models key and the model name we record
STEPS = 4  # the distilled klein model is made for 4 steps
QUANTIZE = 8  # bits; 8-bit fits 16 GB Macs with no visible loss
MIN_MEMORY_GB = 16
TIMEOUT_S = 240
QUANTISE_TIMEOUT_S = 900
# MLX keeps freed buffers for reuse up to its memory limit (27 GB footprint measured on a
# 36 GB Mac); 1 GB is as fast and keeps the footprint near what the model needs.
CACHE_LIMIT = 2**30
# Below this the VAE decodes in tiles: 1024x1792 needs a 15.4 GB footprint untiled, 11.2 GB
# tiled (measured). Tiling leaves a faint grid in fine detail, so only where it is needed.
TILE_BELOW_GB = 20
AGENT_ENV = "VIDEOCUT_AGENT"
# The three sizes we generate (multiples of 16, as the model needs); the material step crops
# to the output format, so the nearest aspect is enough.
SIZES = {"square": (1024, 1024), "portrait": (1024, 1792), "landscape": (1792, 1024)}
STYLE_WORDS = {"photo": "clean editorial photo", "illustration": "flat illustration"}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_COLOURS = 5


class FluxError(RuntimeError):
    pass


def model_dir() -> Path:
    """$VIDEOCUT_HOME/models/flux2-klein-4b, read at call time (tests set VIDEOCUT_HOME)."""
    home = Path(os.environ.get("VIDEOCUT_HOME", Path.home() / "VideoCut"))
    return home / "models" / MODEL


def source_dir() -> Path:
    """Where the full download waits until `quantise` made the copy we keep."""
    return model_dir().parent / ".download" / MODEL


def revision() -> str:
    return manifest.models()[MODEL]["revision"]


def ready_marker() -> str:
    return f"{revision()} {QUANTIZE}-bit"


def model_ready() -> bool:
    """The 8-bit copy of the pinned revision is complete."""
    try:
        return (model_dir() / HF_MARKER).read_text().strip() == ready_marker()
    except OSError:
        return False


def quantise(source: Path, backend=None) -> dict:
    """Save the downloaded model as the 8-bit copy mflux loads (model_dir()); about 8 GB
    instead of 16 GB, and loading it never holds the full-precision weights in memory."""
    dest = model_dir()
    part = dest.with_name(dest.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    request = {"action": "quantise", "source": str(source), "dest": str(part),
               "quantize": QUANTIZE}  # fmt: skip
    out = (backend or run_worker)(request, QUANTISE_TIMEOUT_S, memory_limit())
    if (source / "LICENSE.md").is_file():
        shutil.copy2(source / "LICENSE.md", part / "LICENSE.md")
    (part / HF_MARKER).write_text(ready_marker() + "\n")
    shutil.rmtree(dest, ignore_errors=True)
    part.rename(dest)
    return out


def memory_gb() -> float:
    return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30


def mflux_version() -> str | None:
    try:
        return metadata.version("mflux")
    except metadata.PackageNotFoundError:
        return None


def memory_limit() -> int:
    """Bytes the child may use: the Mac's memory minus 4 GB for macOS and the app, at most
    80 %. A 16 GB Mac gives 12 GB, more than the 8-bit model needs."""
    total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    return int(min(total - 4 * 2**30, total * 0.8))


def size_for(width: int, height: int) -> tuple[int, int]:
    """The supported size nearest to the asked aspect: 1024x1024, 1024x1792 or 1792x1024."""
    if width <= 0 or height <= 0:
        raise ValueError(f"size must be positive, got {width}x{height}")
    ratio = width / height
    if ratio < 0.8:
        return SIZES["portrait"]
    if ratio > 1.25:
        return SIZES["landscape"]
    return SIZES["square"]


def prompt_text(prompt: str, palette: list[str], style: str) -> str:
    """The text the model gets: the sentence, the palette as colour names (never hex: the
    model draws "#F2A33A" as text), the style words, and NO_TEXT last. "a desk with a
    laptop; colours deep navy blue and warm amber; clean editorial photo; no text, ..."."""
    if style not in STYLE_WORDS:
        raise ValueError(f"style must be one of {', '.join(STYLE_WORDS)}, got {style!r}")
    text = " ".join(prompt.split()).rstrip(" .;")
    if not text:
        raise ValueError("the prompt is empty")
    parts = [text]
    colours = colour_words([c for c in palette if isinstance(c, str) and HEX.match(c)],
                           MAX_COLOURS)  # fmt: skip
    if colours:
        parts.append("colours " + colours)
    parts.append(STYLE_WORDS[style])
    parts.append(NO_TEXT)
    return "; ".join(parts)


def _footprint(pid: int) -> int | None:
    """Physical footprint of a process in bytes (what Activity Monitor calls Memory; it
    includes the GPU buffers MLX allocates in unified memory), or None when unknown.
    Off macOS (CI) the resident set from /proc stands in, so the guard is still exercised."""
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError:
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        return None
    buf = (ctypes.c_uint64 * 64)()  # rusage_info_v2 and more
    if libproc.proc_pid_rusage(pid, 2, ctypes.byref(buf)) != 0:  # RUSAGE_INFO_V2
        return None
    return int(buf[9])  # 16 bytes uuid, then user, system, idle, interrupts, pageins,
    # wired, resident, phys_footprint (u64 each)


def run_worker(request: dict, timeout: float, limit: int, cmd: list[str] | None = None) -> dict:
    """Run one generation in a child process; stop it after `timeout` seconds or when its
    memory footprint passes `limit` bytes. Returns the child's JSON result."""
    cmd = cmd or [sys.executable, "-I", "-m", __name__]
    env = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",  # the weights are local; generation never goes online
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TQDM_DISABLE": "1",
    }
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, env=env)
        try:
            proc.stdin.write(json.dumps({**request, "memory_limit": limit}).encode())
            proc.stdin.close()
        except BrokenPipeError:  # the child died at start; its stderr says why (below)
            pass
        start, peak, stopped = time.monotonic(), 0, ""
        while proc.poll() is None:
            used = _footprint(proc.pid) or 0
            peak = max(peak, used)
            if used > limit:
                stopped = (
                    f"stopped: the image model needed more than {limit / 2**30:.0f} GB memory; "
                    "close other apps and try again"
                )
            elif time.monotonic() - start > timeout:
                stopped = f"stopped after {timeout:.0f} s without an image"
            if stopped:
                proc.kill()
                proc.wait()
                raise FluxError(stopped)
            time.sleep(0.25)
        out.seek(0)
        err.seek(0)
        lines = out.read().decode(errors="replace").strip().splitlines()
        tail = err.read().decode(errors="replace").strip().splitlines()[-3:]
    if proc.returncode != 0 or not lines:
        raise FluxError(
            f"the image model failed (exit {proc.returncode}): " + (" | ".join(tail) or "no output")
        )
    result = json.loads(lines[-1])
    result["peak_bytes"] = max(peak, int(result.get("peak_bytes") or 0))
    return result


class LocalFlux(ImageProvider):
    name = "local"
    timeout_s = TIMEOUT_S

    def __init__(self, backend=None):
        # backend(request, timeout, limit) -> dict; tests pass a fake, the default is the child
        self.backend = backend or run_worker

    def available(self) -> tuple[bool, str]:
        if sys.platform != "darwin" or platform.machine() != "arm64":
            return False, "local image generation needs a Mac with Apple Silicon"
        mem = memory_gb()
        if mem < MIN_MEMORY_GB - 0.5:
            return False, (
                f"local image generation needs at least {MIN_MEMORY_GB} GB memory; "
                f"this Mac has {mem:.0f} GB"
            )
        version = mflux_version()
        if version is None:
            return False, (
                "the local image generator is not installed: run `videocut models pull --image`"
            )
        if not model_ready():
            size = manifest.models()[MODEL]["approx_size_gb"]
            return False, (
                f"the local image model is not downloaded (about {size:.0f} GB): "
                "run `videocut models pull --image`"
            )
        if os.environ.get(AGENT_ENV):
            return False, "images are generated by the app after your approval, not by Claude"
        return True, f"FLUX.2 [klein] 4B on this Mac (mflux {version}, {mem:.0f} GB memory)"

    def generate(self, prompt: str, width: int, height: int, seed: int | None,
                 palette: list[str], style: str, dest: Path) -> GenResult:  # fmt: skip
        if os.environ.get(AGENT_ENV):
            raise FluxError(
                "images are generated only outside an agent run, after the plan is approved"
            )
        ok, reason = self.available()
        if not ok:
            raise FluxError(reason)
        w, h = size_for(width, height)
        seed = secrets.randbelow(2**31) if seed is None else int(seed)
        text = prompt_text(prompt, palette, style)
        dest = Path(dest)
        if dest.suffix.lower() != ".png":
            raise ValueError(f"dest must be a .png file, got {dest.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = {
            "model_dir": str(model_dir()),
            "prompt": text,
            "width": w,
            "height": h,
            "seed": seed,
            "steps": STEPS,
            "tiling": memory_gb() < TILE_BELOW_GB,
            "dest": str(dest),
        }
        start = time.monotonic()
        out = self.backend(request, self.timeout_s, memory_limit())
        elapsed = round(time.monotonic() - start, 1)
        if not dest.is_file():
            raise FluxError("the image model reported success but wrote no image")
        return GenResult(
            file=dest,
            width=w,
            height=h,
            model=MODEL,
            revision=revision(),
            seed=seed,
            watermark="none",
            elapsed_s=elapsed,
            prompt=text,
            steps=STEPS,
            quantisation=f"{QUANTIZE}-bit",
            extra={
                "mflux": mflux_version(),
                "load_s": out.get("load_s"),
                "generate_s": out.get("generate_s"),
                "peak_gb": round(out.get("peak_bytes", 0) / 2**30, 1),
                "tiling": request["tiling"],
            },
        )


def register() -> None:
    register_provider(LocalFlux.name, LocalFlux)


def _worker() -> int:
    """Child side: one request on stdin, one JSON line on stdout."""
    req = json.loads(sys.stdin.read())
    import mlx.core as mx
    from mflux.models.common.config import ModelConfig
    from mflux.models.common.vae.tiling_config import TilingConfig
    from mflux.models.flux2.variants import Flux2Klein

    mx.set_memory_limit(int(req["memory_limit"]))
    mx.set_cache_limit(CACHE_LIMIT)
    config = ModelConfig.flux2_klein_4b()
    if req.get("action") == "quantise":
        model = Flux2Klein(model_config=config, model_path=req["source"],
                           quantize=req["quantize"])  # fmt: skip
        model.save_model(req["dest"])
        print(json.dumps({"peak_bytes": int(mx.get_peak_memory())}))
        return 0
    t0 = time.monotonic()
    model = Flux2Klein(model_config=config, model_path=req["model_dir"])  # already 8-bit
    if req.get("tiling"):
        model.tiling_config = TilingConfig()
    t1 = time.monotonic()
    image = model.generate_image(
        seed=req["seed"],
        prompt=req["prompt"],
        num_inference_steps=req["steps"],
        width=req["width"],
        height=req["height"],
    )
    t2 = time.monotonic()
    dest = Path(req["dest"])
    part = dest.with_name(dest.name + ".part")
    image.image.save(part, format="PNG")  # no metadata: the log in material.json has it all
    part.replace(dest)
    print(json.dumps({
        "file": str(dest),
        "load_s": round(t1 - t0, 1),
        "generate_s": round(t2 - t1, 1),
        "peak_bytes": int(mx.get_peak_memory()),
    }))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(_worker())
