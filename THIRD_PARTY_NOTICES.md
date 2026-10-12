# Third-party components

Nothing below is stored in this repository unless marked **bundled**. Everything else is
downloaded by `./start.sh` from the original source, pinned to an exact version and verified
by SHA-256 (see `manifest/tools.json`).

| Component | Use | Licence | How it gets here |
|---|---|---|---|
| [uv](https://github.com/astral-sh/uv) | Python toolchain | MIT OR Apache-2.0 | downloaded |
| [Node.js](https://nodejs.org) | renderer runtime | MIT | downloaded |
| [FFmpeg](https://ffmpeg.org) (build by [Martin Riedl](https://ffmpeg.martin-riedl.de)) | cutting, encoding, filters | GPL-3.0 build (includes libx264) | downloaded at install time; **not redistributed** by this project |
| [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) `deep-filter` | noise reduction | MIT OR Apache-2.0 | downloaded |
| [Playwright](https://playwright.dev) (`playwright-core`) + Chromium headless shell | overlay rendering | Apache-2.0 / BSD-3-Clause | npm + Playwright installer |
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | local transcription | MIT | Python package |
| Whisper large-v3-turbo weights ([mlx-community](https://huggingface.co/mlx-community/whisper-large-v3-turbo)) | transcription model | MIT (OpenAI) | downloaded |
| [parakeet-mlx](https://github.com/senstella/parakeet-mlx) | alternative transcription | Apache-2.0 | Python package |
| Parakeet TDT 0.6B v3 weights ([NVIDIA](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), MLX conversion by mlx-community) | alternative transcription model (optional) | CC-BY-4.0, attribution: NVIDIA | downloaded on request |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) + Face Landmarker model | face tracking, blinks, reframing | Apache-2.0 | Python package + downloaded |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | model downloads | Apache-2.0 | Python package |

## Design credits

- The editing approach (transcript first, contact sheets at decision points, edit decision list,
  self-check after render) follows [browser-use/video-use](https://github.com/browser-use/video-use)
  (MIT). Code taken from it will be marked in the source and listed here.
