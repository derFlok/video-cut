# Changelog

## Unreleased

### Added
- Repository scaffold, Apache-2.0 licence, design brief (`docs/BRIEF.md`).
- `start.sh`: installs pinned, checksum-verified tools into `./.tools` (uv, managed Python 3.12,
  Node 24, FFmpeg 9.0.2, DeepFilterNet 0.5.6, Playwright Chromium headless shell) and default
  models into `~/VideoCut/models` (Whisper large-v3-turbo, MediaPipe Face Landmarker).
- `videocut doctor`: checks tools, FFmpeg features, renderer, models, Claude availability and
  sign-in mode.
- `videocut selftest`: generated German test clip → noise reduction → loudness measurement →
  local transcription with word timestamps → face model → transparent overlay (ProRes 4444) →
  cut and join with fades.
- `uninstall.sh`.

### Observed
- Whisper large-v3-turbo dropped all three filler words ("äh", "ähm") from the synthetic test
  voice. Synthetic speech is not representative; filler retention must be measured on a real
  recording (Phase 0 in the brief).
