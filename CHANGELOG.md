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
- HDR→SDR: reference white 203 nits (BT.2408) instead of 100; real iPhone HLG footage no longer
  looks oversaturated.
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
- Phase 0/1 measurement on one real recording (66 s German selfie video, iPhone HLG HDR,
  rotated, VFR; the recording is not part of the repository). Whisper turbo with the disfluency
  prompt found 9 of 9 filler words that the speaker confirmed, with none missed. Without the
  prompt it found 0. The 4 gap-energy candidates were false alarms (tails of "und").
  Transcription takes ~5 s.
- Phase 1 acceptance on that recording: `videocut run` produced a 23.8 s 9:16 reel. The speaker
  accepted it with 0 corrections, the target is ≤ 3. Fillers, a false start and an off-topic
  aside were removed, and the thesis moved to the front as the hook. The reel is at −14.1 LUFS
  and −1.45 dBTP, with no cut inside a blink after the face track (`edl check`, `verify`).
- Per run with the speaker's own sign-in: plan 82 s / 14 turns, cut 108 s / 19 turns, API-price
  equivalent ≈ $0.48 + $0.41 (logged in `work/runs.jsonl`).
- The reel came out at 23.8 s, below the 30–45 s target for type 1 (the material did not
  carry more). The island name is never spoken in the kept parts, so the plan proposes a text
  overlay (Phase 2).

### Added — Phase 2: overlays, captions and brand profiles
- Brand profiles (BRIEF §9) as validated design tokens with WCAG-AA caption colours; `videocut
  tool brand list|check|show`; `job new --brand` / `job brand <id>`.
- `videocut tool captions` (output-time caption groups, cut words never appear, emphasis from
  zoom keys / emphasis.json); `videocut tool overlays` (overlays.json + transparent overlay
  render); render composites the overlay; verify flags overlay_in_safe_zone / overlay_on_face and
  writes caption-change sheets; shared crop/face geometry (_geometry.py).
- Overlay renderer renderer/render.js: transparent ProRes 4444, --boxes/--still/--sheet/--blur N,
  brand fonts via FontFace; lib (closed-form spring, text fitting, face- and safe-zone-aware
  placement); scenes caption + keyword; node + pytest tests.
- Scene library: number_card, list_item, chat_bubble, screenshot_frame (zoom focus + redaction
  burnt in before first visible frame), split_compare, bar_chart, lower_third, cta_end (logo end
  card); asset access limited to the job folder.
- `videocut tool brand sheet <id>`: the brand's preview sheet (BRIEF §9), every installed scene
  with neutral sample content on the brand background → `~/VideoCut/brands/<id>/preview.jpg`.
  The renderer's `--sheet` takes fixed times, labels and a background from `overlays.json`.
- Verify writes `out/verify/overlays_NN.jpg`, one tile per overlay at its middle, so the agent
  checks overlays with one or two pictures. A new flag, `overlay_overlap`, marks an overlay and
  a caption that cover each other: when the face fills the frame, there is only one free strip.
  The full-frame `cta_end` card is exempt from the safe-zone, face and frozen-picture checks.
- Step 8 for Claude: the `plan` skill proposes overlay ideas (scene, time, spoken words, why;
  brand address du/sie, never a brand forbidden word); new `overlays` skill: captions → overlays
  in `work/edl.json` (output time, one at a time, text only from what is spoken) → overlay
  render → preview → verify (fix overlay_on_face / overlay_in_safe_zone) → final render →
  `work/overlay_report.md`. No new tool permissions: the renderer is only reached through
  `videocut tool overlays`.
- `videocut run --brand <id>` runs the overlay stage after the approved cut, with no second
  approval. It never runs before the cut is approved and fails when the overlay run changes the
  cut (the EDL segments). A finished cut of the approved plan is kept on a re-run (`--recut`
  cuts again), and `job.json` records `cut` and `overlays`. `--asset FILE` and `job new --asset`
  add screenshots or images to a new job (`input/`, listed in `job.json` `assets`).

### Changed — Phase 2
- Removed `brand scaffold-test-brands`: invented colours do not belong in shipped code.
- Overlay colour path: the renderer encodes the overlay as BT.709 limited range and tags it,
  and render converts it to the matrix of the cut video. Before, the untagged BT.601 conversion
  shifted brand colours in players; an accent read back as BT.709 was off by ΔE ≈ 100. Now the
  drawn brand colours survive in the H.264 reel at ΔE ≈ 1 (CIE76).
- Overlay composite on an exact frame grid: overlay frame n lands on reel frame n. The joined
  segments' rounded timestamps used to show captions one frame late.
- Caption style `plain` draws a soft halo in the derived box colour. With only a shadow, white
  text on bright video measured a median of 4.3:1 against its surroundings (worst ≈ 1.8:1);
  with the halo it measures ≥ 16:1. Covered by an end-to-end test (real renderer → render →
  verify, ΔE and drawn contrast). The halo uses shadows only: a canvas blur filter made a
  24 s overlay take 164 s instead of 15 s.
- A word scaled by the `scale` highlight pushes its line apart instead of covering the space to
  its neighbours. SVG logos without width/height are sized from their viewBox. `cta_end`
  registers its content, so captions keep clear of the end card.
- Brand module review: malformed sections, non-finite numbers and invalid weights are errors,
  not crashes. `display`/`body` font files are registered when `files` is missing. Ambiguous
  free-text enums warn. The default spring is BRIEF's (13, 0.8). Folders whose names are not
  valid ids are not listed. `job brand` takes ids only and refuses inside an agent run.
- Review of the whole diff:
  - A recut clears the overlay record, so the overlays are made again. The record names the
    exact EDL and needs a verify that passed, so a failed overlay verify is retried.
  - The overlay prompt asks for `captions --force` after a changed cut.
  - `brand sheet` is refused inside agent runs; its `--out` could overwrite job files.
  - Logos load from a brand given as a folder path.
  - Asset paths such as `..name.png` are accepted, and font `file://` URLs are percent-decoded.
- Layout also scans the whole allowed height for a free spot between several obstacles.

- End card after the speech: `cta_end` (any full-frame plate scene) starts at the last spoken
  word + 0.2 s. `videocut tool overlays` moves an earlier one there, keeping its length; when
  it then runs past the cut, the reel gets a held tail: the last frame repeats under the card
  and the audio is padded with silence after loudnorm, so the loudness is unchanged
  (`render.json` `tail_s`). Chosen over rejecting the EDL because it always succeeds, also
  when the recording ends right on the last word. overlays, render and verify compute the
  same schedule from the EDL and the transcript (`_plates.py`).
- No caption is drawn while a plate is on screen. `cta_end` no longer registers its content
  for caption layout. Boxes carry the drawn times. verify flags `caption_on_plate` and
  `plate_over_speech`.
- Caption bands: `center` and `lower` (chest area, below the face, above the bottom safe zone).
  `tokens.captions.position` picks the first one, and the other is the fallback. Both win over
  the strip above the head, which close-ups used to get. A block shrinks before it leaves both
  bands.
- Verify trusts the overlay files only as written: `videocut tool overlays` records the
  sha256 of `overlay.mov`, `overlay_boxes.json` and `overlays.json` in
  `$VIDEOCUT_HOME/state/<job id>/overlay_render.json`. That folder is outside the job folder,
  like the approvals. A mismatch is flagged `overlay_boxes_untrusted`.

### Observed — Phase 2
- Phase 2 acceptance on the Phase 1 recording (the approved 23.8 s cut, reused unchanged):
  `videocut run <job> --brand <id>` once per brand, each reel with 31 caption groups. Verify ok
  with no flags for all three brands. Overlay stage per run with the speaker's own sign-in:

  | Brand | Overlays | Wall | Turns | API-price equivalent |
  |---|---|---|---|---|
  | brand-a (dark, plain upper-case captions) | 2 × keyword, cta_end | 265 s | 21 | ≈ $0.54 |
  | brand-b (light, box captions) | cta_end | 187 s | 18 | ≈ $0.54 |
  | brand-c (the user's own brand, local only) | keyword | 247 s | 22 | ≈ $0.52 |

  brand-b dropped its two keyword cards because `overlay_overlap` showed they covered
  captions: in this close-up the face fills the frame, and the only free strip is above the
  head. Earlier attempts found the static end card failing the frozen check, the end card
  failing the safe-zone check, and the slow caption halo; all three are fixed above.
- Type 6 end to end on a generated walkthrough clip with a generated, neutral UI mock as the
  asset (`--asset`, brand-b): plan 69 s / 11 turns / ≈ $0.36, cut 60 s / 9 / ≈ $0.52,
  overlays 117 s / 15 / ≈ $0.44. The 25.8 s reel has four `screenshot_frame` steps zooming
  across the screen, with the e-mail line redacted in every frame, plus a `cta_end` with logo.
  Verify is ok.
