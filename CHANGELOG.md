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

- Calmer captions: phrase blocks instead of 3-word flashes. New optional brand token
  `captions.pace` = `calm` (the default), `normal` or `fast`. `fast` behaves as before.
  `calm` makes blocks of 1–2 lines, at most 8 words / 38 characters, and breaks at sentence
  ends. A full block breaks at its last comma or short pause. Each block is on screen for at
  least 1.0 s: short blocks merge with a neighbour, or share words with it. A block is held
  across gaps under 0.6 s and hidden in longer pauses. The spoken word moves through the
  block (karaoke). The caption scene allows at most 2 lines. On the 23.8 s test cut the
  pace gives:

  | Pace | Blocks per minute | Median block duration |
  |---|---|---|
  | `calm` | 35 | 1.48 s |
  | `fast` | 78 | 0.70 s |

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
- Re-run after the end card, caption band, trust and pacing fixes (same cut, `calm` pace).
  Verify is ok with no flags for all three brands. Each reel has 14 caption blocks: 35 per
  minute of speech, median 1.48 s (was 31 groups, median 0.70 s). Each agent placed the end
  card at about 21.4 s, before the last word at 23.73 s. The tool moved it to 23.93 s and
  added a held tail of 2.43–2.60 s, at -14.0 LUFS. No caption sits on the end card.

  In this extreme close-up the face box reaches y ≈ 1250–1460 of 1920, so 11 of 14 blocks fit
  the lower band for brand-b and brand-c. For brand-a, with large upper-case text, 6 of 14
  fit; the rest fall back above the head.

  | Brand | Overlays | Wall | Turns |
  |---|---|---|---|
  | brand-a | `cta_end` | 156 s | 21 |
  | brand-b | 2 × `keyword`, `cta_end` | 172 s | 19 |
  | brand-c | 2 × `keyword`, `cta_end` | 136 s | 17 |
- Type 6 end to end on a generated walkthrough clip with a generated, neutral UI mock as the
  asset (`--asset`, brand-b): plan 69 s / 11 turns / ≈ $0.36, cut 60 s / 9 / ≈ $0.52,
  overlays 117 s / 15 / ≈ $0.44. The 25.8 s reel has four `screenshot_frame` steps zooming
  across the screen, with the e-mail line redacted in every frame, plus a `cta_end` with logo.
  Verify is ok.
- Phase 2 accepted by the speaker on 2026-10-12: all three brand reels (brand-a, brand-b,
  brand-c) approved as they are after the calm caption pacing; no further corrections.

### Added — Product demos: screen recording, sync, layouts, screenshot walkthroughs
- `job new --screen` / `job screen`; screen ingest (`screen_probe.json`, `screen_audio.wav`,
  CFR 30 fps `screen_proxy.mp4`); `tool sync` (audio xcorr + GCC-PHAT, drift ppm, clap
  fallback, `--offset` manual) → `work/sync.json`; EDL segment `layout` face|screen|pip|split,
  `screen_zoom` keyframes, `redact` blocks, overlap check in `edl check`.
- `tool screen`: activity, ring/stop clicks, scenes, zoom targets with `max_zoom`
  (`work/screen.json`); `tool sheet screen`: contact strip with analysis overlays.
- Render draws per-segment layouts screen/pip/split from the original screen recording at
  camera time + offset; eased `screen_zoom` (smoothstep 0.6 s); redact blocks in brand bg
  before scaling; sharpness guard (`screen_zoom_clamped`); pip inset below the screen view;
  split 58/42; face-only render unchanged (golden test).
- One source of truth: render, overlays and verify read `layout`, `screen_zoom` and `redact`
  from `edl.Segment` (the raw-dict reader in `_layouts` is gone), and render and overlays
  check the sync overlap like `edl check`. `_layouts.plan_all` is the one plan of the drawn
  screen segments for render, overlays and verify.
- `videocut run <camera> --screen <file>`: demo prep ingest (camera + screen) → sync →
  screen → face → transcribe → candidates → `sheet contact` + one `sheet screen` overview
  (no times = the whole recording). A failed or low-confidence sync (< 0.3) stops the run
  with the `videocut tool sync --offset` command to run.
- `tool screen` summary: camera times of zoom targets and clicks from the current sync.json,
  and each target's `view` box.
- Overlays in screen layouts: overlays.json `views` (screen view + speaker area per
  stretch); the renderer keeps every caption and scene off the screen view, the face track
  there is the speaker area, so captions sit below the screen view, outside the speaker.
  Verify flag `overlay_on_screen`.
- Verify for demos: redaction sampled in up to 5 frames per block through the drawn zoom
  window (`redaction_missing`), screen time mapping checked against the recording at ±0.5 s
  / ±1 s (`screen_time_mismatch`), `screen_overlap` check, `screen_zoom_clamped` as info.
  View changes count as reframes, a still screen is not a frozen picture, blinks only where
  the camera shows.
- Skills: `plan` gets a *Views* section (face for hook/close/emotional moments; screen, pip,
  split per step; zoom targets with reasons within `max_zoom`; redaction of anything that
  looks like personal or customer data) and a *Screenshots* table for screenshot-only
  demos; `cut` writes `layout`/`screen_zoom`/`redact` from the approved plan (≥ 2–4 s
  between view changes, never above `max_zoom`); `overlays` keeps items off screen views.
- Screenshot-only demos: `job new --asset` / `run --asset` take many files; `tool sheet
  assets` numbers them on one contact sheet (≤ 1080×1920) with size and orientation.
  Portrait screenshots in `screenshot_frame` use the safe height (minus room for a caption
  block) instead of a landscape-sized box and may cover the speaker (`stage`).
- README / README.de: "Recording a product demo" / "Produktdemo aufnehmen".

- Personal data is a human decision at the plan gate: the plan writes candidates to
  `work/redaction.json` (and numbered in project.md); `videocut run` asks "Demo data? [a]ll
  demo / [r]edact all / [s]elect", `--demo-data` = all demo; `videocut tool job redaction
  --all-demo | --redact-all | --redact 1,3` (human only). The decision is stored outside the
  job folder with the SHA-256 of the list; a changed list invalidates it. Safe default: all
  candidates redacted, and render/overlays add them even when the EDL forgot one;
  `edl check` and verify flag `redaction_missing`.

### Fixed — Product demos

- Moving screen zooms were one frame ahead of the plan (ffmpeg `perspective` counts `in`
  from 1); verify then saw a redact block "not solid" during fast zoom moves and the cut run
  dropped its zoom-outs to get past it. Now frame-exact, with a test.
- A screenshot's personal data is matched by the file, not the spelling: `./input/x.png`,
  `file://…`, `work/../input/x.png` or a copy of the file are redacted (and checked) too.
- A changed personal-data decision makes `videocut run` cut and render again (job.json
  `cut.redaction_sha256`); before, the old reel stayed "done".
- A portrait screenshot is laid out before the captions, at the top of the safe area, so
  the caption fits below it (in the end-to-end run the phone screenshot collided with the
  caption and was dropped).
- Render no longer adds a candidate's redact block twice when the EDL already has it.
- `videocut run` tells the cut run why it cuts again (`--recut`, changed plan or decision),
  so it replaces the EDL instead of stopping to protect the earlier overlays; a cut record
  from before `redaction_sha256` keeps its reel.

- CI `lint-and-test` (red on main since #10): the unit tests ran without the project's
  dependencies (`--no-project --with pytest`, 14 collection errors for numpy). Now
  `uv sync --frozen` installs the locked Linux wheels; MLX imports are checked after the
  model and fail with a clear message off Apple silicon; `videocut run` takes its start time
  from the file clock (Linux stamps files from a coarser clock than `time.time()`, so a
  fresh render looked older than the run). Linux: 213 passed, 119 skipped (media).

### Observed — Product demos (real agent runs, generated test material, brand-c locally)

- Screen demo (25 s camera + screen recording with a fake e-mail and phone number, sync
  offset 3.27 s, confidence 0.88): plan 79.7 s / 12 turns / $0.65 (2 personal-data
  candidates, 6 screen views, zooms 1.45x and 2.66x = max_zoom, nothing clamped);
  cut 200.0 s / 23 turns / $1.40; overlays 173.9 s / 19 turns / $0.47; wall 7:46, verify
  not ok (an audio click at 2.73 s) and the cut dropped two zoom-outs because of the
  one-frame zoom bug (fixed above). After the fixes, with a human decision (`job redaction
  --redact-all`) and `--recut`: cut 153.5 s / 20 turns / $0.58, overlays 164.7 s / 15 turns
  / $0.37, wall 5:19, verify ok, render.json redaction source `decision`, 19.9 s reel.
- Screenshots only (20 s camera + 3 screenshots, one portrait, one with a fake e-mail):
  plan 75.8 s / 11 turns / $0.37 (1 candidate); cut 67.1 s / 14 turns / $0.65; overlays
  191.5 s / 27 turns / $0.56; wall 5:48, verify ok, but the portrait screenshot was dropped
  (caption collision, fixed above). Overlays again: 97.5 s / 17 turns / $0.37, all three
  screenshots shown, renderer overlaps 0, e-mail covered by the default, verify ok.
- Moving screen zooms ended in the top-left corner: `crop` after a per-frame `scale` clamps
  x/y to its configured input size. Moving windows now go through `perspective`
  (sub-pixel, eval=frame); a test checks that the zoomed view shows the target region.
- Review fixes: a manual `sync --offset` is reused by later runs (the only way for a screen
  recording without audio; `videocut run` looped on the sync stop); redact ranges get one
  frame of slack on each side plus the drift over the segment, and the measured drift is
  applied per segment; the screen stream starts at its first video frame
  (`setpts=PTS-STARTPTS`, as sync and the proxy count); verify fails closed when the screen
  checks cannot run or a demo reel's EDL is unreadable.
- `work/screen.json` is removed with the other screen outputs when the screen recording is
  replaced (a hard link keeps the old mtime, so freshness could not tell).

### Added — Output format per job (16:9, 9:16, 1:1, 4:5)
- Output format per job (`tools/_format.py`): job.json `format` (`9:16` 1080×1920, `16:9`
  1920×1080, `1:1` 1080×1080, `4:5` 1080×1350); absent, the default is resolved on read
  (16:9 with a screen recording or screenshots, else 9:16; never written back).
  `job new --format`, `job format <job> <f>` (human only), `run --format`; `job show`
  prints format and size. A changed format re-cuts (the cut record carries the format).
- Reel, preview, cover, overlays.json (`width`/`height`, safe zones per aspect), verify's
  size check and `brand sheet --format` follow the format; the job format beats the EDL
  `size` (with a warning). The soft-crop warning scales with the output width. The
  non-interactive stop names the full approve and redaction commands. Selftest renders and
  verifies a short 16:9 job.
- Demo layouts per format: 16:9 `screen` fills the frame, `pip` = round speaker bubble 26 %
  of the height in a bottom corner above the caption strip (the corner covering clearly
  less of the zoom targets, right on a near tie), `split` side by side 64/36; 1:1 bubble
  pip and stacked split; 4:5 and 9:16 portrait rules as before.
- 16:9 (and 1:1) screen fills the frame: the `screen`/`pip` view is the whole frame (1:1:
  the band between the safe zones) and the `split` screen part is filled at its own
  aspect; the screen is cropped to the view's aspect instead of letterboxed (a Mac
  recording of 4096×2648 no longer leaves ~125 px bars in `colors.bg` at the sides). Zoom
  windows have the view's aspect; "no zoom" is the full screen width (or height), placed
  around the segment's first zoom target. Bars only when the aspects differ by more than
  `LETTERBOX` (25 %; split `LETTERBOX_SPLIT` 50 %), e.g. a portrait phone screen. Caps
  follow the 1920 px view (4096 wide: 16:9 `max_zoom` 2.13, was 2.45); zoom factors count
  from the no-zoom window; screen.json `zoom_targets[].view` has the view's aspect and
  `geometry` (a new value re-derives the caps without a new analysis). 9:16 and 4:5 are
  unchanged (identical plans and filter graphs). Layout geometry follows the job format (`plan_all`
  `full_out`), also for previews and verify.
- Speaker inset/part centred on the segment's face median, head and shoulders (face height
  × 2.6), ≥ 0.75 camera px per output px (the EDL crop cap no longer limits it); render.json
  `speaker` lists `px_ratio` per segment, render warns when the speaker is upscaled.
  Intended change for 9:16 demo layouts too: pip and split no longer show the plain EDL
  crop (which showed a whole video-call tile), they show the speaker's head and shoulders.
  Talking-head (face-only) 9:16 renders are unchanged (byte-identical re-render).
- Face second pass for small faces (tile scan + upscaled region of interest) when the first
  pass finds the face in < 50 % of the samples: face.json `small_pass`, FACE_VERSION 2.
- Readability: screen.json per format (`by_format`: `max_zoom`, `text_px`,
  `readable_zoom`; `ui_scale`); `screen_text_small` (`edl check` warning, verify info
  `screen.readability`).
- Renderer: every scene lays out from `width`/`height` and the aspect class, type scale from
  the short side. Landscape: bottom-centre captions (≤ 70 % width, 2 lines), side-column
  cards and keyword, bottom-left lower third, aspect-keeping screenshots, centred `cta_end`;
  square and 4:5 caption at the foot. Node tests for every scene at all four presets;
  `check.js` follows width/height.
- Overlays in a frame-filling screen view (16:9 screen/pip): overlays.json `views` has
  `screen` null and lists the zoom targets (`focus`, with times); captions and scenes keep
  off the active target and the speaker bubble instead of the whole frame.
  `overlay_on_screen` covers the targets too. A target covering half the view or more (a
  screen-shaped zoom box) leaves nothing specific to keep clear and is not listed.
- Verify, landscape/square pip: the frozen check judges the camera inside the speaker
  bubble (the rest of the frame is a still screen); a frozen bubble is reported with
  `"where": "speaker"`.
- Landscape/square `face` segments: a small face (median lower than 25 % of the camera
  height, e.g. a video-call tile) gets a closer default crop (up to 2×); portrait framing
  is unchanged.
- Review fixes: `job format` repairs an invalid job.json value; render leaves out an
  overlay track made for another output size; `edl check` judges readability at the job
  size and says when the EDL size differs; zoom-target spans start with the zoom move.
- Skills follow the format: the cut skill writes the job's size (from `job show`), the plan
  states the format in its header, 16:9 prefers `screen`/`pip` and zooms only where
  `screen_text_small` / `readable_zoom` say so; the overlays skill explains the layout
  differences by aspect.
- Docs: output format in README / README.de (with a recording tip for 16:9 demos) and
  ARCHITECTURE.

### Known — Output formats
- The selftest 16:9 clip is too short for a loudness reading; only its size is judged.
- The HiDPI threshold (2700 px long side → 2× UI scale) is a heuristic; a 1920 px wide
  screen recording in 16:9 has no sharp zoom at all.
- 1:1 with a centred head, a card and a caption is cramped; a landscape screenshot fills
  only ~55 % of the height.
- The web UI has no job-creation form yet, so no format picker there (done in Phase 3).

### Added — Phase 3: local app (jobs, plan review and feedback, approval, revisions, brands)
- **Backend** (`src/videocut/server/`, FastAPI + uvicorn on 127.0.0.1): per-start token
  exchanged for an HttpOnly SameSite=Strict session cookie, Host and Origin checks, CSP and
  `nosniff`; jobs API (streamed, size-capped, ffprobe-checked uploads; plan view and edit,
  sha-checked; approval + personal-data decision written to the same stores as the CLI;
  plan feedback; feedback; stills from an allow-list of folders; media with HTTP Range incl.
  `out/history/reel-<k>.mp4`; download; delete to `$VIDEOCUT_HOME/trash`); brands API (list,
  validated edit returning the brand, create from the neutral example, logo/font upload with
  magic-byte check and `role` (logo light/dark/icon_*, font display/body) that sets the
  token, cached preview sheet); server-sent events with replay on connect; one Claude run
  at a time (queue); errors as `{detail: {message, code?, errors?}}` with 409 codes `busy`,
  `stale`, `state`, `exists`, `read_only`. `run.py` captures tool output per thread (CLI
  unchanged).
- **Revision runs:** `videocut run <job> --feedback TEXT [--at S]` with the new `revise`
  skill; `videocut tool edl locate <output_s> [--source]` maps a reel second to segment,
  source time, layout, zoom key and overlays. Guards: plan, personal-data candidates and
  decision, redaction still covered, final render of the current EDL, overlays kept, source
  time outside the approved plan only when `work/revision.json` quotes the feedback
  (`extends_plan`); a failed revision restores everything from a backup outside the job
  folder. `out/history/` keeps the last five reels; `work/revisions.jsonl` logs every run.
  The API reports `versions` (version 1 = first cut, one per successful revision, playable
  while kept) and `version`/`video_url` per revision.
- **Plan feedback:** `videocut run <job> --plan-feedback TEXT` (plan skill in revise-the-plan
  mode, changes only what the feedback asks, needs a new approval). The plan skill also
  writes `work/plan.json` (title, format, hook, core_message, keep_list, views, overlays,
  personal_data, risks; `change_note` after feedback), checked by `videocut tool plan
  check`; `videocut tool sheet plan` writes stills for views and personal-data candidates to
  `work/plan_stills/`.
- **UI** (`app/`, React 19 + TypeScript + Vite, built into `src/videocut/web/dist/`, not
  committed): job list, new job (type cards, drag & drop with upload progress, brand,
  format, one-line instruction), progress (stepper, live log, time, cost), plan review
  (plan.json with stills or the markdown, editor with stale-plan handling, plan feedback
  with the change highlighted), personal-data dialog (redact everything by default, per
  item choice), result (player, verify findings in plain words, feedback at the player
  position, versions "Version n" with playback of the kept ones, MP4/cover download, the
  approved plan on demand), brands (preview sheets per format, token editor with the
  server's validation, logo/font upload, new brand from the neutral example). German
  default, English switch; verify flag and check ids in plain words. Mock API for
  development (`npm run dev:mock`, `build:mock`, `screenshots`); 20 vitest tests.
- `videocut setup` / `./start.sh` build the UI with the bundled node (`npm ci`, rebuilt when
  the source changes); `./start.sh --ui` and `videocut ui` open the app with the session
  link; without a build every page says how to build it.
- End-to-end script through the browser (`tests/e2e/app_e2e.py` + `app/e2e/app.e2e.mjs`,
  real Claude runs on generated clips in a throwaway `VIDEOCUT_HOME`, screenshots per step;
  `VIDEOCUT_E2E=1`, not in CI). CI builds and tests the UI with the pinned node.

- Review fixes before merge: the approval also binds `work/plan.json` (shown and trusted
  only while unchanged; a human markdown edit drops it); feedback needs the recorded cut of
  the approved plan; a new cut archives `revisions.jsonl` and `out/history/`; a failed
  revision no longer prunes a kept reel and also restores `out/preview.mp4`; `redact` with
  no or duplicate ids is refused; uploads are checked by container format with
  `-protocol_whitelist file`; stale upload staging is removed at start; the run channel is
  idle before its `done` event. A finished reel whose overlay verify flagged something
  shows as done (result page) instead of offering Start again.

### Known — Phase 3
- The session cookie is not port-bound: other local servers on 127.0.0.1/localhost that
  the browser visits receive it (it changes with every `videocut ui` start).
- The plan's free-text fields (why, risks) are in the language Claude writes them in,
  often English, while the UI is German.
- FastAPI 0.143 pulls in `opentelemetry-api`. SSE keep-alive every 15 s; `?follow=0` replays
  the events once and closes.

### Fixed — Personal-data redaction: measured boxes and a real coverage check
- Redaction boxes are measured, never estimated by eye. In 2 of 2 real runs the plan agent
  placed the boxes from downscaled stills about one text line too high: the block covered
  the line above and the name stayed readable, and verify did not notice (it only checked
  that a block was drawn where the box said).
- Local text recognition (`tools/_ocr.py`): macOS Vision (`VNRecognizeTextRequest`) called
  through the Objective-C runtime with ctypes. No download, no package, no compiler;
  frames are handed over in memory and never leave the machine. Off macOS the tools say
  so and verify fails closed (`redaction_unchecked`).
- `videocut tool redact text` → `work/screen_text.json`: the text on the screen recording
  every 2 s and after every scene change, with camera times, boxes and pattern hits
  (e-mail, phone, IBAN-like); also the text of a job's screenshots. Part of the demo prep
  in `videocut run`, so the plan agent reads it instantly.
- `videocut tool redact measure`: the plan agent names WHICH text is personal (`text` in
  `work/redaction.json`, the whole panel as `box`); the tool finds WHERE, follows it while
  the page scrolls (1 s grid, halved down to 1/15 s where it moves, appears or
  disappears) and writes a `track` of time pieces with the measured box plus a margin.
  Fail closed: where the text is not found, the whole named area is covered and the
  candidate says so (`measured` partial/failed, `note`; the app shows it in the
  personal-data dialog). `plan check` fails while an e-mail/phone/name candidate has no
  text or a text candidate is not measured. Render, overlays, `edl check` (it now prints
  the exact block to add) and the plan stills use the measured track.
- verify `coverage`: for every enforced text candidate, sampled output frames of every
  screen segment that shows it (5 per segment plus one per second of every screen
  segment): the text is found again on the screen frame the reel shows, mapped through
  layout and zoom into output px, and must be ≥ 98 % block colour; the reel frame itself
  is read too (the text readable there is a leak). Either is `redaction_leak`, a hard
  failure with output time and candidate id.
- `redaction_unchecked` (verify not ok): an e-mail/phone/name candidate shown without
  `text`, a text candidate edited after it was measured (`measure_sig`), a frame that
  cannot be read, or no text recognition. A text found only outside the named area counts
  as not found there (the whole area is covered). Screenshot candidates: the text found
  on the image must lie inside the redact boxes of every `screenshot_frame` showing it.
- Frames of a screen recording are taken as the reel shows them: the last frame at or
  before the time (macOS records a frame only when the screen changes; a plain seek gave
  the next change, up to 2 s later on a real recording), seeking to the pts itself
  (`-seek_timestamp 1`; also in the screen-time check of verify).
- Jobs planned before this change report `redaction_unchecked` (verify not ok) until they
  are planned again: their e-mail/phone/name candidates have no measured text.
- Data rule (CLAUDE.md rule 3, decided by the human): only transcript text, downscaled
  stills and screen text read locally (OCR) go to Claude; recordings and audio never leave
  the machine.
- verify: a still screen over several consecutive screen segments is no longer a frozen
  picture (the exempt spans are merged first, not only when a pip bubble is present).

### Observed — measured redaction
- Real plan run (Opus 5.5) on a generated demo (24.8 s camera, contact card that scrolls,
  "redact all"): the agent named the text, the whole card as area, ran `redact measure`;
  3 of 3 measured "ok". Plan 66.9 s / 16 turns / USD 0.35, cut 221.6 s / 25 turns /
  USD 0.69. Verify: 404 coverage checks, 0 leaks, `screen_flagged` empty (17.7 s for
  the 20 s 16:9 reel); not ok only for a click at a cut in the generated speech.
- Measured boxes vs known positions on generated material: IoU > 0.8 for every static
  sample; the track covers the text at every frame while it scrolls.
- On a real 4K screen recording (248 s): ~0.48 s per sampled frame with 4 threads
  (Vision dominates); two texts over 215 s took 150 s; the packet scan for frame times
  0.5 s; 29 frame gaps > 0.5 s (up to 2 s) that a plain seek got wrong.

### Added — Cut quality: video time base, aligned words, snapped cuts, cut style per video
- **Video time base.** Every time in a job is video time (0 = the first camera frame).
  Ingest pads/trims `audio_raw.wav`, `audio.wav` and `screen_audio.wav` by the audio
  stream's start (`audio.json`/`screen_audio.json`: `time_base: "video"`,
  `audio_offset_s`); phones start the audio up to ~160 ms after the picture, so words,
  cuts and captions were that much early before. The proxy and render start the camera at
  its first frame (`setpts=PTS-STARTPTS`); a render from the source's own audio stream
  moves it by its start; render cuts `work/audio.wav` whenever it is in video time (also
  when the audio starts late). A job ingested before this is upgraded in place by the next
  ingest (every run starts with it; a revision runs it first): wavs shifted, `words.json`
  shifted instead of transcribed again, `edl.json` moved with the words its cuts were
  placed on, job.json cut/overlay records carried along (same cut, so a finished reel stays
  finished); an interrupted upgrade rebuilds instead of shifting twice. `sync` never
  shifts twice.
- **Word alignment** (`tools/align.py`; run by `transcribe` unless `--no-align`,
  `videocut tool align` re-aligns): energy-based boundaries for every word (silence pins,
  chained words split at the level valley, a word stretched over a pause shrinks to its
  voice, hallucinated words in silence untouched); model times kept in `t0_asr`/`t1_asr`,
  `transcribe.json` `align` records method, version and shifts; candidates refreshed.
- **Snapped cuts.** `videocut tool edl snap` (also run by every render) moves each `in`/`out`
  to the nearest quiet point within the style's window, never across a kept word and never
  into a dropped one; keeps `in_src`/`out_src` (idempotent; a hand-edited point becomes the
  new original), moves zoom/screen-zoom/redact keys on a moved edge, remaps output times,
  stays inside the sync overlap for screen segments. `edl check` reports `cut_in_speech`
  unless a segment says `"speech_cut_ok": "<reason>"`.
- **Crossfades at every cut.** Equal-power audio crossfade, centred on the cut (half of it
  from the slack snapping leaves), sample-exact so the reel length is the sum of the
  frame-snapped segments; shortened to the quiet room around both cut points (min. 10 ms),
  to half a short segment and to the source end; joins get 10 ms linear; a segment's
  `xfade_ms` overrides the style; the legacy `fade_ms` is ignored. The video stays a hard cut.
- **verify `speech_clipped`**: a hard check on the reel's audio (speech level and spectral
  flux at every cut) for a word cut off or started mid-syllable.
- **Cut style per video** (`_style.py`): `calm` (whole sentences, pauses up to 1 s,
  segments ≥ 3 s, 120 ms crossfade), `standard` (fillers and false starts go, 80 ms),
  `tight` (every pause and filler goes, 50 ms; the behaviour before). Default `calm` with a
  screen recording or screenshots, else `standard`. `job new --cut-style`, `job cut-style
  <job> <style|auto>` (human only; refused inside an agent run), `videocut run --cut-style`;
  the cut, plan, overlays and revise skills follow it (and snap + check + `xfade_ms`). A
  changed style keeps the approval and makes the cut stale (job.json `cut.cut_style`);
  `run.recut` / `videocut run --recut` cuts again with the approved plan and the
  personal-data decision unchanged.
- **Job language**: job.json `lang` (`de` default, `en`; `job new --lang`); plan and reports
  are written in it.
- API: `cut_style`, `cut_style_resolved`, `cut_stale`, `source_duration_s`, `lang`,
  `stage_since` in the job detail; `PUT /api/jobs/{id}/cut-style`, `POST /api/jobs/{id}/recut`;
  SSE `stage` events carry `at` (the stage's start, ISO), kept on replay.
- App: cut style picker on "New video" and on the plan/result page, "Neu schneiden" for a
  stale cut with a time and cost hint from the last runs; a plain-language "What is
  happening" line with the time in this step (from the server's stage start, right after a
  reload) and a rough expectation from the recording length; the locked correction box
  says why; a reel left by failed overlays is shown as interim, never as the result.
- Tests: `tests/test_video_time.py` (a generated clip whose audio starts 157 ms after the
  video and one whose audio starts 120 ms before it: aligned words, candidates, snapped
  cuts, the reel's picture, sound and captions and the proxy agree to one frame; the
  render from `audio.wav` matches the render from the source stream sample for sample),
  `test_snap.py`, `test_align.py`, `test_time_base.py`, cut style in `test_job.py`,
  `test_run.py`, `test_server.py`, app `progress.test.tsx`.
- **Target length per video** (`tools/_length.py`): job.json `target_s` or automatic (30–45 s
  for a thought, 30–60 s for other talking heads, as long as needed for a demo with screen
  material); a set target means ± 15 %. `job new --target`, `job target <job> <len|auto>`,
  `videocut run --target` (90, 90s, 5m, 2:30), human only; `job show` prints the range.
  A changed target makes the plan stale and revokes the approval (also recorded in the
  approval record); the next run plans again. Skills: the plan sizes the keep list to it
  and never pads, overlays about 2–4 ideas per minute, cut/revise stay inside it. API:
  `target_s` on create, `target_s`/`target_resolved`/`plan_target_stale` in the detail,
  `PUT /api/jobs/{id}/target`. App: "Ziellänge" on "New video" (Automatisch, 30 s … 10 Min,
  custom mm:ss) and on the plan page next to the estimated length, "Plan neu erstellen"
  for a stale plan.
- README: cut style and target length; recording tip: the microphone on in both
  recordings.

### Fixed — Cut quality
- A recording whose audio starts before its video was cut and proxied in container time:
  the picture of every cut was off by the lead (render, layouts and the proxy now start
  the camera at its first frame).
- Review fixes: the video-time pad/trim was counted at the source's sample rate (a
  44.1 kHz recording got 9 % too much: 20 ms for 0.25 s); an old job whose audio is rebuilt
  rather than shifted now moves its EDL too (once); `edl snap` waits for the upgrade of an
  old job instead of mixing time bases; a segment that keeps no word never snaps into a
  dropped word that spans its edge; a segment's own longer `xfade_ms` gets the quiet room
  it needs; a reel from before cut records goes stale when a style is set; a recut shows
  no plan stage.

### Observed — cut quality (real runs, Opus 5.5, copies of two real jobs)
- 10-min product demo (camera + screen, 16:9, approved plan and personal-data decision
  kept, recut in style `calm` through `run.recut`): its audio starts 157 ms after the
  video. The legacy upgrade alone moved the old cut's cut points in speech from 53 of 94 to
  31 of 94. Alignment: median word shift 55 ms, p90 199 ms. Recut: 47 → 21 segments,
  median segment 2.17 → 4.85 s, cut points in speech 53/94 → 3/38 (all three marked
  `speech_cut_ok`: words without a pause between them), `speech_clipped` 0, reel 120.3 →
  119.9 s. Cut 1507 s / 52 turns / USD 2.59 (before 1459 s / 71 / 2.49), overlays 1167 s /
  22 turns / USD 0.90 (before 1062 s / 22 / 0.86). Verify not ok: 5 × `click`, 1 ×
  `blink_at_cut`. Four of the clicks sit where the denoised track is digital silence on one
  side (DeepFilterNet gates to zero) and room tone at −50…−70 dBFS on the other: the click
  metric divides by a silent baseline; the cut makes no sample step (crossfaded). One
  (44.3 s) fades a non-speech sound at −30…−46 dBFS into silence over the 30 ms crossfade
  the agent set.
- 66-s selfie talking head (9:16, recut in style `standard`): 14 → 9 segments, median
  1.41 → 2.35 s, cut points in speech 11/28 → 3/18 (all `speech_cut_ok`), `speech_clipped`
  0, reel 24.7 → 29.3 s; cut 390 s / 53 turns / USD 1.58 (before 190 s / 34 / 0.75),
  overlays 251 s / 19 turns / USD 0.61. Verify not ok: 1 × `click` (a −63 dBFS mouth tick
  of the source inside the outgoing crossfade tail, next to digital silence), 1 ×
  `blink_at_cut` (no blink-free point in that pause).
- Known: the click check flags low-level sound next to the denoiser's digital silence; a
  follow-up should judge clicks against the source or an audible level, or keep a noise
  floor in the denoised track.

### Fixed — verify `click` false positives
- The click baseline is the audible audio around the cut (blocks quieter than −70 dBFS, the
  denoiser's digital silence, are left out; floor −60 dBFS instead of −80). A `click` now
  also needs an audible step (`step_db` ≥ −45 dBFS) that stands alone (`step_sharpness` ≥ 10:
  the step vs. the median step within ±1 ms; a sample discontinuity, not a breath, consonant
  or mouth tick). New numbers per cut: `step_db`, `step_sharpness`.
- Real checks (copies of the two jobs above, verify run on the existing reels): demo 5 →
  0 `click` flags, selfie 1 → 0 (highest click_peak 7.2 and 8.9, before 44.2 and 22.0).
  Generated hard cuts still flag: sine step click_peak 31 / sharpness 32, speech-like clip
  without crossfade 22 and 44 / sharpness 45–51; crossfaded cuts 5–6 / sharpness ≤ 6.

### Added — duplicate and rename a job
- `videocut tool job duplicate <job> [--title T] [--keep-approval]` → `<id>-copy-N`:
  input/ hard-linked, work/ and out/ copied (copy-on-write clones where the filesystem
  has them). The approval and the personal-data decision move along only with
  `--keep-approval` (a human choice); otherwise the copy needs its own approval. The
  overlay record (hash-checked provenance of work/overlay*) always moves along.
  `videocut tool job title <job> <title>`: job.json `title`, shown instead of the plan's
  title. Both human only (refused inside an agent run).
- API: `POST /api/jobs/{id}/duplicate {title?, keep_approval?}`, `PUT /api/jobs/{id}/title
  {title}`; list and detail return the display title and `copy_of`.
- App: job page title with inline rename and a "⋯" menu with "Duplizieren" (dialog with
  the title and "Freigabe übernehmen", explained, offered only for an approved job); the job
  list shows a "Kopie" badge on copies.

### Added — Transcript vocabulary
- brand.json and job.json `vocabulary`: `[{term, variants, note?}]`, the canonical spelling
  of names the transcriber gets wrong and their known misspellings; validated by `brand
  check`, the brand editor and the job routes (non-empty term, literal variants, no key used
  twice). The effective vocabulary of a job is brand ∪ job, the job wins conflicts.
- `videocut tool brand vocab <brand> add|remove|list`, `tool job vocab <job>
  add|remove|list` (human only), `videocut run <file> --vocab "Term=variant,..."`
  (repeatable, new jobs), `tool job new --vocab`.
- Transcription: turbo's prompt names the canonical terms after the filler sample, within
  Whisper's 223-token prompt budget (job terms first); an unstable decode retries with the
  filler sample alone, then without a prompt. `transcribe.json` `prompt_terms`.
- Deterministic correction after transcription and alignment (`tools/vocab.py`): variants
  and the term in another case or split into words become the term (case and punctuation
  ignored, spans of up to 4 words, inflection endings, hyphen compounds, never inside a
  longer word); `w_asr` keeps the model's text, re-applying starts from it.
  `transcribe.json` `vocabulary` has the counts.
- `videocut tool vocab apply` (existing job, no new transcription; also the captions in
  `edl.json`/`overlays.json`, then job.json `overlays.stale` makes the next run redo only the
  overlay stage), `vocab suggest` (near misses of a term, reported only), `vocab show`.
- `job show` → `vocabulary_effective`; the plan, cut, overlays and revise skills write
  every name in its canonical spelling.
- API: brand GET/PUT carry `vocabulary`; job create takes `vocabulary`; `GET/PUT
  /api/jobs/{id}/vocabulary`, `POST /api/jobs/{id}/vocabulary/apply`, `POST
  /api/jobs/{id}/overlays`; job detail `overlays_stale`.
- App: "Wörterbuch" in the brand editor (correct spelling, misspellings as chips), a
  collapsible "Begriffe für dieses Video" in Neues Video and on the job page (save and apply,
  "Meintest du …? Zum Wörterbuch hinzufügen"), "Untertitel neu setzen" when the captions of a
  finished reel changed. German and English.

### Observed — transcript vocabulary
- Prompt stability, turbo on two generated clips (the filler clip; a clip with three
  invented names and three fillers), 12 runs with 3, 10, 20 and 40 terms (37-222 prompt
  tokens): 3/3 fillers in every run, no fallback to sampling. With the terms in the prompt all
  three names were spelled right; with the filler sample alone one was misspelled.
- Real check on a copy of a real job (744 words) and of its brand: the company name was
  transcribed with one wrong spelling twice, the brand's own name once with two words run together.
  With the term and that variant in the copy's vocabulary, `vocab apply` changed 3 words and
  1 caption word (edl.json and overlays.json) and marked the overlays stale; `vocab suggest`
  with the term alone found the misspelling (2×, lowest conf 0.44) and nothing else. The copies
  were deleted afterwards.

### Added — Redaction styles: blur, crop, block
- Personal data on the screen is no longer only covered by a solid block in the brand
  background (a white bar over the browser's tabs and address bar in a real demo). job.json
  `redact_style`: `blur` (pixelated into cells of the UI text height, or a quarter of the
  box up to three text heights, then a gaussian of half a cell: the box keeps its colours),
  `crop` (a band along a screen edge — touching it, spanning 90 % of the other side — is cut
  off for the whole segment; the rest of the screen is laid out by the normal view rules,
  zoom windows and targets in its coordinates; more than 20 % of the height or width blurs
  instead, noted in render.json), `block` (as before) and the default `auto` (crop full-width
  bars at the top/bottom, blur the rest). Absent, the brand's new `redaction.style` decides.
- Render reads every blurred box on its middle frame with local text recognition and makes
  the blur stronger while anything is readable (a solid block when even one cell reads):
  never weaker. render.json `redaction.style` and `redaction.blocks` (per block: segment,
  times, box, style, edge or cell, note).
- Verify: a blurred box must show no sharp edges and no readable text (seams where two
  blurred boxes meet are left out); a cropped segment must match the recording without the
  band clearly better than the uncropped view; the coverage check counts a blurred box
  without edges as hidden and maps text in a cropped band outside the view.
- `videocut tool job redact-style [job] <auto|blur|crop|block>` and `videocut run
  --redact-style` (human only); a reel that hid personal data in another style is stale
  (job.json `cut.redact_style`; a cut from before styles counts as `block`). API: field
  `redact_style` on `POST /api/jobs`, `PUT /api/jobs/{id}/redact-style`, `redact_style` in
  the job detail. App: select "Schwärzen als: Automatisch / Unscharf / Abschneiden /
  Fläche" in the personal-data step of the approval and in the new-job form (with a screen
  recording or screenshots).
- Screenshots (`screenshot_frame` `props.redact`): `blur` or `block` (`props.redact_style`,
  set by overlays; crop and auto blur there). The renderer averages the box into cells and
  draws them smoothed back up over the solid fill.
- Real check (a copy of a 4096x2648 screen-recording job, 1920x1080 reel, both personal-data
  candidates enforced): the top bar (candidate 1, `[0, 0, 1, 0.075]`) was cut off in all 39
  segments that show it; the second candidate (a dialog box, shown in one added segment) was
  blurred at a 78 px cell, which local text recognition could not read. Verify: 0 screen
  flags; each cropped segment matched the recording without the bar (mean difference 2-13
  grey levels) clearly better than the uncropped view (5-26). Render 133 s, verify 184 s.
- Redact boxes grow so they never cut through text or a UI bar (every style; a measured box
  ending in the middle of the browser tab titles left their lower half readable, with
  `block` as well): text recognition reads the screen frames around each box, a line the
  box cuts pulls it over the whole line plus a margin, and a band along a screen edge
  then moves on to the next UI boundary (the bottom of the browser chrome) within three
  text heights. render.json `redaction.blocks[]` `planned` / `grown`. Verify `text_cut`:
  a line read on the reel over a box's planned position, or on the recording across the
  edge a crop cuts at, is `redaction_partial` (a failure).
  Real check again (fresh copy of the same job): the top bar grew from 7.5 % to 9.2-9.3 %
  of the height (199 -> 246/248 px of 2648: the tab title line plus the chrome's edge),
  the dialog box over the lines it cut; 0 screen flags, `text_cut` clean on 40 boxes and
  39 crop edges. Growth adds OCR at render (render 292 s, verify 235 s on 126 s of reel).
  The app names the flag ("Schwärzung schneidet Text an" / "redaction cuts through text").
  Review fix: a block whose screen frames could not be read is not cached as "nothing to
  grow" (the next render tries again); a retune of the growth constants drops the cache.

### Fixed — verify on long reels; resuming without a second overlay run
- Frame selection for verify, sheets and the screen checks is a balanced `(a+b)+(c+d)`
  expression tree (`_image.any_of`, `select_times`, `select_frames`) instead of a flat sum
  of one term per frame. ffmpeg refuses expressions nested deeper than 100 ("Error
  initializing filters / Cannot allocate memory"): a flat sum failed at 101 frames, so a
  ~5 min demo with 111 caption groups could not be verified, and "Weiter" restarted the
  overlay run, which failed the same way. Measured on a 5 min 1080p30 reel, 295 frames:
  one pass with the tree 7.3 s, batches of 40 with input seeks 8.2 s, one seek per frame
  (pool of 4–8) 23–25 s; all three frame-identical, so the single pass stays.
- `videocut run` (and the app's "Weiter"): when the overlay run finished but its final
  render or verify failed, job.json `overlays_pending` records brand, approved plan, cut,
  EDL and the overlay files. The next run repeats `render` + `verify` as tool steps
  instead of a new Claude run, when all of them still match, the overlay record outside
  the job folder confirms the files and overlay.mov is newer than its inputs. Same checks
  afterwards as after a Claude run; `--brand` still redoes the overlays.
- Real check (copy of a real ~3.4 min 16:9 demo, preview reel, 111 caption groups +
  10 overlays): verify runs (before: ffmpeg error); 13 caption + 2 overlay sheets, 852 s
  wall (most of it the screen checks; the human's app was rendering at the same time).
