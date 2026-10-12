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

### Added — Phase 1: cutting core
- `videocut tool transcribe` (mlx-whisper turbo with a disfluency prompt, or parakeet-mlx;
  `--language`, default `de`, `auto` detects it) → `words.json`, `transcribe.json`,
  `candidates.json`, `takes_packed.md`.
- `videocut tool candidates`: pauses and filler candidates (filler words, isolated short words,
  voiced energy in gaps and in overlong words). Language-aware filler list, normalised "mm",
  no fillers from punctuation-only tokens, filler marks matched on time + text.
- `takes_packed.md` in the video-use format with pause, filler and retake marks (retake hints
  only for phrases > 3 words; MIT credit in `THIRD_PARTY_NOTICES.md`).
- `videocut tool render [--preview]`: frame-accurate cut with 30 ms fades, 9:16 crop with
  zoom punch-ins, two-pass loudnorm to −14 LUFS / −1 dBTP, H.264/AAC reel, cover and
  `render.json`. HDR (HLG/PQ) recordings are tonemapped to SDR BT.709 in the reel and the proxy.
- Ingest picks the audio stream deliberately (AAC/PCM first, never spatial audio) and records
  colour info and stream start times in `probe.json` (version 2; old jobs re-probe).
- `videocut tool sheet contact [--every S]` (3×3 sheets over the proxy, tiles labelled
  `mm:ss.ss · f<frame>`), `sheet burst <t> [--span]` (frames around t + waveform with a marker),
  `sheet timeline <t0> <t1>` (filmstrip + waveform + words + pause/filler shading). Pictures are
  drawn by ffmpeg only; the font is a macOS system font found at runtime (`labels: false`
  without one).
- `videocut tool verify`: per inner cut a picture (frames −2…+2, waveform ±100 ms) and numbers
  (visual jump score, audio click score), global checks (duration, −14 ±1 LUFS, ≤ −1 dBTP,
  1080×1920, black/frozen > 0.5 s) in `out/verify.json`.
- Claude skills `plan` and `cut` (plugin `video-cut`): the plan run writes `work/project.md`
  (hook, core message, target length, keep/cut list in source time, emphasis words, risks) and
  stops; the cut run writes `work/edl.json`, previews, verifies, fixes (max. 3 rounds), renders
  and writes `work/cut_report.md`. Both keep an image budget.
- `videocut tool job approve`: the human gate. Stores the SHA-256 of `work/project.md` in
  `job.json` (status `approved`); a changed plan, type or brief is no longer approved. Refused
  inside agent runs (`VIDEOCUT_AGENT`).
- Human gate: approvals are stored in `$VIDEOCUT_HOME/approvals/<id>.json`, outside the
  agent-writable job folder (in-folder approvals are ignored); `render` refuses without one.
- Agent runs may write only below `work/`; `job.json`, `.claude/`, `CLAUDE.md`, `.mcp.json` are
  denied. stderr of `claude` goes to a file (a full pipe could hang a run).
- `videocut run <video|job> [--type] [--brief] [--approve] [--replan] [--max-turns]`: prep →
  plan run → approval prompt → cut run (resumes the plan session) → `out/`. Every Claude run
  appends wall time, turns, cost and token usage to `work/runs.jsonl`.
- End-to-end test without Claude (generated clip → prep → hand-written EDL → render → verify).
- `videocut tool face` (step 4): MediaPipe Face Landmarker (VIDEO mode, blendshapes) at 30 fps
  over a 480p display-oriented pipe → `work/face.json` (box, centre, blink, gaze per frame,
  median, blink intervals with 0.5/0.35 hysteresis). Render centres segments without a crop on
  the face median and follows only moves > 8 %; `edl check` warns about cut points in a blink
  with a blink-free, word-free suggestion; verify flags `blink_at_cut`; prep runs face after
  ingest. Real 66 s HLG phone clip: 18–24 s, face in 100 % of frames, 25 blinks.

### Changed
- Verify: a talking-head jump cut is expected. A clear visual jump is reported as info
  `jump_cut` (`reframe` when the EDL changes crop/zoom there); the flag `visual_jump` is kept
  for a jump ≥ 3× (and ≥ 8) the median of the reel's other cuts without a reframe. Verify checks the
  preview (`out/preview.mp4`, half size, loudness not judged) when the last render was one.
- Waveforms in burst, timeline and verify pictures are peak-normalised per picture (gain capped
  at +26 dB) and drawn per-pixel peak, so normal speech levels are readable.
- `job show` reports `approved`; the plan lives at `work/project.md`.

### Observed
- Whisper large-v3-turbo dropped all three filler words ("äh", "ähm") from the synthetic test
  voice. Synthetic speech is not representative; filler retention must be measured on a real
  recording (Phase 0 in the brief).
