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
- Claude runner (`agent.py`): official `claude` headless with the bundled plugin, exactly five
  tools, no MCP/web, Bash limited to `videocut tool *`, file access limited to the job folder,
  `dontAsk` permissions. Auth modes: own sign-in (`--setting-sources project`) or API key from
  the keychain (`--bare`). `videocut auth local|key|status`.
- `videocut ui`: local web app (127.0.0.1 only, per-session token, host check) with a
  connection check: one click starts Claude, progress streams live (server-sent events).
  Verified end to end: 9 s, 5 turns, ~USD 0.06 equivalent.
- Pipeline tool registry (`src/videocut/tools/`): `videocut tool job new|show`, `ingest
  [--denoise]` (probe, 48 kHz mono audio, 360p proxy, idempotent), `edl check`; stubs with final
  signatures for `transcribe`, `candidates`, `sheet contact|burst|timeline`, `render`, `verify`.
  Every tool ends with one JSON summary line. EDL schema module (`edl.py`) with validation by
  JSON path. Generated landscape/portrait/rotated test clips (`tests/clips.py`).

### Observed
- Whisper large-v3-turbo dropped all three filler words ("äh", "ähm") from the synthetic test
  voice. Synthetic speech is not representative; filler retention must be measured on a real
  recording (Phase 0 in the brief).
