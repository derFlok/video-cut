# Third-party components

Nothing below is stored in this repository. Everything is downloaded by `./start.sh` (or by the
package managers it runs) from the original source, pinned to an exact version: the tools and
models in `manifest/tools.json` with a SHA-256 checksum or a Hugging Face commit, the Python and
npm packages through `uv.lock`, `renderer/package-lock.json` and `app/package-lock.json`.
Licences checked on 2026-10-16.

| Component | Use | Licence | How it gets here |
|---|---|---|---|
| [uv](https://github.com/astral-sh/uv) 0.13.0 | Python toolchain | MIT OR Apache-2.0 | downloaded |
| [CPython](https://www.python.org) 3.12 ([python-build-standalone](https://github.com/astral-sh/python-build-standalone)) | Python runtime | PSF-2.0 (bundled libraries under their own licences) | downloaded (3.12.15, SHA-256 pinned in `manifest/tools.json`), installed by uv |
| [Node.js](https://nodejs.org) 24.21.0 | renderer runtime, UI build | MIT | downloaded |
| [FFmpeg](https://ffmpeg.org) 9.0.2 `ffmpeg` + `ffprobe` (build by [Martin Riedl](https://ffmpeg.martin-riedl.de), [build script](https://git.martin-riedl.de/ffmpeg/build-script) Apache-2.0) | cutting, encoding, filters | GPL-3.0-or-later (configured `--enable-gpl --enable-version3`, no `--enable-nonfree`; includes libx264, libx265, OpenH264, VVenC and others) | downloaded at install time; **not redistributed** by this project |
| [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) `deep-filter` 0.5.6, including the DeepFilterNet3 model compiled into the binary | noise reduction | MIT OR Apache-2.0 (the repository licence covers the bundled [models](https://github.com/Rikorose/DeepFilterNet/tree/main/models)) | downloaded |
| [Playwright](https://playwright.dev) `playwright-core` 1.63.0 | drives the headless browser | Apache-2.0 | npm |
| [anime.js](https://animejs.com) `animejs` 4.5.0 | motion scenes (spike): seekable animation timeline in the page | MIT | npm |
| [Chrome Headless Shell](https://developer.chrome.com/blog/chrome-headless-shell) 153.0.8010.12 (Playwright revision 1243) | overlay rendering | BSD-3-Clause ([Chromium](https://www.chromium.org)) plus third-party components under their own licences, including LGPL-2.1 (see `LICENSE.headless_shell` in the download) | downloaded (SHA-256 pinned in `manifest/tools.json`) |
| [React](https://react.dev) + React DOM 19.2.0 (+ `scheduler`) | local web UI | MIT | npm; compiled into the locally built UI |
| [FastAPI](https://github.com/fastapi/fastapi), [Starlette](https://github.com/encode/starlette), [Uvicorn](https://github.com/encode/uvicorn), [python-multipart](https://github.com/Kludex/python-multipart) | local web server | MIT / BSD-3-Clause / BSD-3-Clause / Apache-2.0 | Python package |
| [NumPy](https://numpy.org) | numerics | BSD-3-Clause (with bundled 0BSD, MIT, Zlib, CC0-1.0 parts) | Python package |
| [MLX](https://github.com/ml-explore/mlx) | Apple-silicon ML runtime | MIT | Python package |
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | local transcription | MIT | Python package |
| [PyTorch](https://github.com/pytorch/pytorch) | dependency of mlx-whisper | BSD-3-Clause (with bundled third-party parts) | Python package |
| Whisper large-v3-turbo weights ([OpenAI](https://huggingface.co/openai/whisper-large-v3-turbo), MLX conversion by [mlx-community](https://huggingface.co/mlx-community/whisper-large-v3-turbo)) | transcription model | MIT, Copyright (c) 2022 OpenAI ([licence](https://github.com/openai/whisper/blob/main/LICENSE)) | downloaded |
| [parakeet-mlx](https://github.com/senstella/parakeet-mlx) | alternative transcription | Apache-2.0 | Python package |
| [librosa](https://github.com/librosa/librosa) | audio loading for parakeet-mlx | ISC | Python package |
| [python-soxr](https://github.com/dofuuz/python-soxr) (bundles libsoxr) | resampling for librosa | **LGPL-2.1-or-later** | Python package (unmodified, dynamically loaded) |
| [python-soundfile](https://github.com/bastibe/python-soundfile) (bundles [libsndfile](https://github.com/libsndfile/libsndfile)) | audio file I/O for librosa | BSD-3-Clause; libsndfile **LGPL-2.1** | Python package (unmodified, dynamically loaded) |
| Parakeet TDT 0.6B v3 weights ([NVIDIA](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), converted to MLX by [mlx-community](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3)) | alternative transcription model (optional) | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/legalcode.en), attribution: NVIDIA; format converted, weights unchanged | downloaded on request |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) | face tracking, blinks, reframing | Apache-2.0 | Python package |
| MediaPipe [Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) model (BlazeFace short range, Face Mesh V2, Blendshape V2) | face tracking model | Apache-2.0 (model cards: [BlazeFace](https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20(Short%20Range).pdf), [Face Mesh V2](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf), [Blendshape V2](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf)) | downloaded |
| [OpenCV](https://github.com/opencv/opencv-python) `opencv-contrib-python` | dependency of MediaPipe | Apache-2.0; the wheel bundles an FFmpeg built with `--enable-gpl` (libx264, libx265), i.e. **GPL-3.0** for those libraries (see `cv2/LICENSE-3RD-PARTY.txt`) | Python package from PyPI; **not redistributed** by this project |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) (bundles PortAudio) | dependency of MediaPipe | MIT | Python package |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | model downloads | Apache-2.0 | Python package |
| [certifi](https://github.com/certifi/python-certifi), [tqdm](https://github.com/tqdm/tqdm) | CA bundle, progress bars | MPL-2.0 / MPL-2.0 AND MIT (used unmodified) | Python package |

All other Python and npm packages are installed in exactly the versions pinned in `uv.lock`, `renderer/package-lock.json`
and `app/package-lock.json`. All of them are under permissive licences (MIT, BSD-2/3-Clause, ISC, Apache-2.0, PSF-2.0,
MIT-CMU, CNRI-Python). Each package's licence text ships inside the installed package. Development tools (Vite, Vitest,
TypeScript, Testing Library, jsdom, pytest, ruff, httpx) are installed for building the UI and for tests, are not used
at runtime, and are all under permissive licences.

Copyleft summary: the only GPL code is the FFmpeg binaries (and the FFmpeg inside the OpenCV wheel). They are downloaded
from their original sources on the user's machine and run as separate programs or as libraries of an unmodified
third-party package. The LGPL libraries (libsoxr, libsndfile, Chromium's FFmpeg) are used unmodified and loaded
dynamically. This project's own code stays Apache-2.0. Anyone who redistributes a packaged installation (for example an
app bundle that contains `.tools/` or `.venv/`) must comply with the GPL-3.0 and LGPL-2.1 terms of those components.

The repository itself contains only this project's own code and text (Apache-2.0, see
[`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)) plus the adapted parts credited below (MIT, compatible
with Apache-2.0). It contains no fonts, images, icons, audio, video or model files: the neutral
example brand uses the system font (`system-ui`). Anything of that kind added later must be
generated or come from a permissive source and be listed here.

- Launcher icon (`Video Cut.app`): no image file in the repository. `videocut launcher` draws
  it on the person's Mac from code (`src/videocut/launcher.py` `icon_png`: a grey rounded
  square with a play triangle, Apache-2.0 like the rest of this project); no third-party
  artwork.

## Design credits

- The editing approach (transcript first, contact sheets at decision points, edit decision list,
  self-check after render) follows [browser-use/video-use](https://github.com/browser-use/video-use)
  (MIT). Code taken from it will be marked in the source and listed here.
- `src/videocut/tools/_transcribe_pack.py` adapts the packed-transcript format of
  video-use `helpers/pack_transcripts.py` (commit 43cfc56): phrase grouping on silences
  ≥ 0.5 s, one line per phrase with a fixed-width `[start-end]` prefix (`NNN.NN` seconds),
  the `takes_packed.md` name and layout. Rewritten for this project's `words.json`; pause
  markers, filler marks and retake hints are additions.
- `claude-plugin/skills/plan/SKILL.md` and `claude-plugin/skills/cut/SKILL.md` follow the
  editing workflow of video-use's `SKILL.md` (read the packed transcript fully, decide on text,
  look at pictures only at decision points, EDL → render → self-check, bounded fix rounds,
  cuts at word boundaries). The wording is this project's own; no text is copied.

  Licence of the original (applies to the adapted parts above):

  ```
  MIT License

  Copyright (c) 2026 Browser Use

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  SOFTWARE.
  ```
