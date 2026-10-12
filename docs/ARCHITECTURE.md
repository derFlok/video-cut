# Architecture (current state)

`docs/BRIEF.md` is the target design. This file describes what exists and records deviations.

## Layout

| Path | Purpose |
|---|---|
| `start.sh` | entry point: platform checks, bootstraps `uv`, syncs Python, runs `videocut setup` + `doctor` |
| `manifest/tools.json` | pinned downloads: version, URL, SHA-256, licence |
| `src/videocut/` | Python package and CLI (`setup`, `doctor`, `selftest`, `models pull`, `ui`, `auth`, `run`, `tool`) |
| `src/videocut/run.py` | `videocut run`: prep → plan run → human approval → cut run → overlay run |
| `src/videocut/brand.py` | brand profiles: load, validate, resolve `brand.json` into design tokens |
| `src/videocut/agent.py` | the restricted `claude` runner (see `SECURITY.md`) |
| `claude-plugin/` | Claude Code plugin `video-cut` with the skills `ping`, `plan`, `cut`, `overlays` |
| `src/videocut/tools/` | `videocut tool <name>`: one module per pipeline tool, used by Claude inside a job |
| `src/videocut/edl.py` | EDL schema (dataclasses), validation with JSON paths, load/save |
| `tests/clips.py` | generated test clips (macOS `say` + ffmpeg test pattern), no third-party media |
| `renderer/` | overlay renderer: `render.js`, `lib/`, `scenes/` (Node + `playwright-core`, headless Chromium → FFmpeg) |
| `brands/_example/` | the only bundled brand: neutral |
| `.tools/` (ignored) | installed tools: `bin/`, managed Python, Node, Chromium, caches |
| `~/VideoCut/` | user data: `models/`, `brands/`, `jobs/`, `music/`, `selftest/` (`VIDEOCUT_HOME`) |

## Pipeline tools (`src/videocut/tools/`)

`tools/__init__.py` lists the modules; each exposes `register(subparsers)` and sets a
`handler` default. `cli.py` only delegates, so a tool is extended by editing its own module.
`tools/common.py` holds the shared contract:

- the job is resolved from `--job <path|id>` or from the current directory upwards
  (Claude runs inside the job folder);
- the last stdout line is one JSON object `{"tool", "ok", ...}`; exit code 0 or 1.

| Command | Module |
|---|---|
| `job new <video> [--type] [--brief] [--brand] [--asset FILE]… [--id]`, `job show [job]`, `job approve [job]`, `job brand <id>` | `job.py` |
| `brand list`, `brand check <id>`, `brand show <id>`, `brand sheet <id> [--out]` | `brand.py` |
| `captions [--force]` | `captions.py` |
| `overlays [--no-render]` | `overlays.py` |
| `ingest [--denoise]` | `ingest.py` |
| `face` | `face.py` |
| `transcribe [--model turbo\|parakeet] [--language]`, `candidates` | `transcribe.py` |
| `sheet contact [--every]`, `sheet burst <t> [--span]`, `sheet timeline <t0> <t1>` | `sheets.py` |
| `edl check [file]` | `edl.py` |
| `render [--preview]` | `render.py` |
| `verify` | `verify.py` |

Helpers: `_transcribe_energy.py` (pauses, voiced gaps), `_transcribe_pack.py`
(`takes_packed.md`), `_image.py` (ffmpeg-drawn pictures, peak-normalised waveforms),
`_render_plan.py` (frame grid, filter graph), `_geometry.py` (crop windows, face-centred
framing, face track in output px, safe zones; shared by render, overlays and verify),
`_color.py` (HDR → SDR).

## Job folder (`$VIDEOCUT_HOME/jobs/<id>/`)

```
job.json   id, created, source (relative), type, brief, status, approval, brand, assets,
           cut / overlays (what `videocut run` finished: plan and segments hashes, brand)
input/     source recording and assets (screenshots), hard-linked or copied
work/      probe.json, audio_raw.wav, audio.wav (+ audio.json), proxy.mp4        ingest
           face.json                                                             face
           words.json, transcribe.json, candidates.json, takes_packed.md         transcribe
           sheets/contact_NN.jpg, burst_*.jpg, timeline_*.jpg                    sheet
           contract.md (reel contract of the type, copied by `run` if one exists)
           project.md                                                            plan run
           edl.json, render_filter.txt, cut_report.md                            cut run
           overlays.json, overlay.mov, overlay_boxes.json, overlay_report.md     overlay run
           runs.jsonl, claude-plan.jsonl, claude-cut.jsonl                       videocut run
out/       preview.mp4 | reel.mp4, cover.jpg, render.json                        render
           verify.json, verify/cut_NN.jpg, captions_NN.jpg, overlays_NN.jpg      verify
```

Lifecycle (`status` in job.json): `new` → `ingested` (ingest) → `approved` (`job approve`, by a
human). Approval stores the SHA-256 of `work/project.md`; `job show` reports `approved: true`
only while the plan is unchanged, so a new plan needs a new approval. The trusted record lives outside the
agent-writable job folder, in `$VIDEOCUT_HOME/approvals/<id>.json` (job path, time, SHA-256);
job.json only mirrors it for display, and `render` (also `--preview`) refuses without it.

Ingest is idempotent: an output newer than its input is kept. DeepFilterNet runs with delay
compensation (`-D`) so cleaned audio stays in sync with the picture. Transcribe keeps a
`words.json` made from the same audio, model and language.

## Skills and `videocut run`

Claude only ever sees the job folder and `videocut tool *`. Three skills, three runs, a human
between plan and cut (BRIEF §5):

| Skill | Reads | Writes | Images |
|---|---|---|---|
| `plan` | `takes_packed.md`, `job.json`, `contract.md`, 1–2 contact sheets | `work/project.md`: hook, core message, target length, keep/cut list in source time, emphasis words, risks | ≤ 4 |
| `cut` | `project.md` (must be approved), `words.json`, flagged verify pictures | `work/edl.json` (segments), renders, `work/cut_report.md` | ~8, only at doubts |
| `overlays` | `project.md` overlay ideas, `edl.json`, `brand show`, overlay/caption sheets | `captions` + `overlays` in `work/edl.json`, overlay render, renders, `work/overlay_report.md` | ≤ 4 |

`videocut run <video|job>`:

1. **prep** (no Claude): `job new` when a file is given, `ingest --denoise`, `face`, `transcribe`
   (also candidates and the packed transcript), `sheet contact`; copies `templates/<type>.md`
   to `work/contract.md` when it exists.
2. **plan run**: `claude -p` with the `plan` skill. Fails if `project.md` is missing or
   `edl.json` was touched. An existing plan is offered again instead (`--replan` redoes it).
3. **approval**: prompt in the terminal (`--approve` skips it, for tests), or later
   `videocut tool job approve <id>` followed by `videocut run <id>`.
4. **cut run**: `claude -p` with the `cut` skill; resumes the plan session when the plan was
   approved unchanged in the same invocation, otherwise starts fresh. Loop inside the skill:
   `edl check` → `render --preview` → `verify` → fix, max. 3 rounds → `render` → `verify`.
   A finished cut of the approved plan is kept on a re-run (`--recut` cuts again); job.json
   `cut` records the plan and segments hashes.
5. **overlay run** (when the job has a brand; `--brand <id>` sets it and always redoes the
   stage): `claude -p` with the `overlays` skill. No second approval, but it only runs after an
   approved, finished cut, and fails when it changes the EDL segments, when it does not end with
   a fresh full render + verify, or when that render has no overlay composited. job.json
   `overlays` records brand, segments and EDL hashes and the verify result; a re-run skips the
   stage only when the same brand's overlays verified ok on exactly this EDL and the last
   render composited them. A new cut clears the record.
6. prints `out/`. Exit code 0 when verify is ok, 3 when not approved, 4 when verify is not ok.

Every Claude run appends `{phase, wall_s, turns, cost_usd, usage, duration_ms, session_id,
resumed, auth}` to `work/runs.jsonl` (the token and runtime log of BRIEF §11).

Guardrails of an agent run (`agent.py`): tools `Bash,Read,Write,Edit,Skill` (no subagents, no
web, no MCP); Bash only `videocut tool *`; Read only `./**` (the job folder); Write/Edit only
`./work/**` (plus the ping file); `job.json`, `.claude/`, `CLAUDE.md` and `.mcp.json` denied,
so an injected run cannot plant settings or hooks for the next one; `VIDEOCUT_AGENT=1` is
set, so `job approve` refuses. `videocut run` also fails when the plan run wrote an EDL or
rendered, when `project.md` changed during the cut run, or when the cut run did not end with
a fresh full render + verify.

**Deviation from BRIEF §5/§6:** pictures are not handed to subagents (the restricted runner
has no Task tool); the skills keep an image budget instead.

## Face (`tools/face.py`)

MediaPipe Face Landmarker in VIDEO mode with blendshapes over the source, decoded by ffmpeg
in display orientation at short side 480 px and 30 fps (scaled before the HDR tonemap) and
read frame by frame from a pipe. `work/face.json` holds per frame `t`, `found`, a normalised
`box`, `cx/cy`, `blink` (max of both eyeBlink scores) and `gaze_off` (0 = into the lens),
the global median, and blink intervals: start at a score ≥ 0.5, end below 0.35 (hysteresis),
closures longer than 0.8 s are not blinks. 30 fps instead of the brief's 5 fps, because
blinks (100–400 ms) need it and the Landmarker costs only ~7.5 ms per 480p frame (66 s phone
clip: 18–24 s in total). The file counts only while it is newer than the source and names it.

Uses: `render` centres a segment without `crop` on the face median, or on the segment's own
median when that is > 8 % of the frame away (fixed crop, follow only large moves; centre
when no face was found); `edl check` warns (never fails) for an `in`/`out` inside a blink
±1 frame, with the nearest time within ±150 ms outside blinks and `words.json` words; `verify`
flags `blink_at_cut` when one of the two output frames on either side of a cut maps (via the
EDL) into a blink, and uses the same face crops to tell reframes from jump cuts.

## Brands (`brand.py`, `tools/brand.py`)

A brand is a folder `$VIDEOCUT_HOME/brands/<id>/` with `brand.json` (BRIEF §9) plus its font
and logo files; the bundled `brands/_example` is the only one in the repository. `load(id)`
validates and returns the **resolved tokens** every scene reads: absolute asset paths
(relative ones must stay inside the brand folder), enums resolved from free text (an ambiguous
one warns), `fonts.files` per weight (from `display`/`body` files when only those are given),
default spring (13, 0.8) and `caption_colors` {text, box, highlight}, each ≥ 4.5:1 (WCAG AA)
against the box — a colour that misses is darkened or lightened until it passes, and the
derivation is reported. `brand check` prints the contrast table and advisory warnings
(logo variants, sparing accent). `brand sheet` renders every installed scene with neutral
sample content on the brand background (one labelled tile per scene) as the preview sheet.
Fonts are never fetched from the network; a family name without a file falls back to the
system UI font.

## Overlays and captions (step 8)

`captions` maps `work/words.json` through the segments to output time (a word cut to less
than half is dropped, so removed fillers never show), groups words into phrase blocks paced by the brand
token `captions.pace` (`calm`, the default: 1–2 lines of ~5–8 words, ≥ 1.0 s on screen, broken
at punctuation and pauses ≥ 0.25 s, held across gaps < 0.6 s so nothing flickers; `normal`;
`fast` = the earlier ≤ 3 words / 18 characters), the active word highlight moving inside the
block, and marks emphasis from zoom keys or
`work/emphasis.json`. `overlays` writes `work/overlays.json` (size, fps, duration, resolved
tokens, safe zones, the face track mapped to output px through the same crop windows render
uses, captions, overlays) and runs the renderer; render composites `work/overlay.mov` when it
is newer than the EDL. Full-frame plates (`cta_end`) start no earlier than the last spoken
word + 0.2 s; `overlays` shifts an earlier one and render appends a held last frame with
silence for the plate. Captions are hidden under plates. Captions sit in the `center` or
`lower` band (chest, below the face) per `captions.position`, falling back to the other band.
`overlays` records SHA-256 of `overlay.mov`, `overlay_boxes.json` and `overlays.json` in
`$VIDEOCUT_HOME/state/<job>/overlay_render.json`, outside the agent-writable job folder;
verify flags `overlay_boxes_untrusted` when they do not match.

Renderer (`renderer/render.js`): one headless Chromium page, a canvas with a pure `draw(t)`
per scene, frames piped as PNG to FFmpeg → ProRes 4444 with alpha, BT.709 limited range and
tagged; `--boxes` (what every item really drew, per frame span), `--still`, `--sheet`,
`--blur N` (motion-blur subframes). Every item is laid out once, in time order, before the
first frame: away from the face over its lifetime, from blocks placed earlier, and outside
the safe zones (organic: top 250 px, bottom 350 px, 6 % at the sides), shrinking to 60 %
before it gives up. Scenes read only tokens (a lint test keeps colour and font literals out).
Scene images (`screenshot_frame`, logos) are read on the Node side only from the job folder
(`VIDEOCUT_JOB_DIR`) or the brands folders; redactions are burnt into a private copy before
any frame is drawn.

Composite: the overlay is converted from BT.709 to the cut video's matrix and both streams are
put on the same frame grid (pts = frame number), so overlay frame n is reel frame n. Measured:
brand colours survive into the yuv420p H.264 reel at ΔE ≈ 1 (CIE76, read as BT.709).

## Verify

Per inner cut: a luma jump score, audio click numbers and a picture. A jump cut in a talking
head is expected: a clear jump is info `jump_cut` (plus `reframe` when the EDL changes crop or
zoom at that cut). The flag `visual_jump` is raised only for a jump ≥ 3× (and ≥ 8) the median jump score
of the reel's other cuts without a reframe; `click` for an audio step. Overlays: per drawn box
(`work/overlay_boxes.json`) `overlay_in_safe_zone` and `overlay_on_face` (> 2 % of the face
box at any face sample while visible) and `overlay_overlap` (an overlay and a caption on
screen together share > 10 % of the smaller box); the full-frame `cta_end` plate is exempt
from these and from the frozen check. Pictures: `captions_NN.jpg` (one tile per caption
group change) and `overlays_NN.jpg` (one tile per overlay, at its middle). Global checks: duration,
loudness, true peak, size, black/frozen stretches. After `render --preview`, verify checks
`out/preview.mp4` at half size and does not judge its single-pass loudness.

## EDL (`src/videocut/edl.py`)

Schema from BRIEF §6. Segment times (`in`, `out`, `zoom_keys`) are source time, everything else
output time. `validate()` returns every problem with a JSON path (`$.segments[2].out`); it checks
`in < out`, segments within the source duration (from `work/probe.json`), crop `cx/cy` in 0..1,
zoom 1.0..2.0, `fade_ms` 0..200 and that all file paths stay inside the job folder. Captions,
overlays, b-roll and music are validated loosely until their phase. `output_duration()` and
`cut_points()` (output time of every segment boundary, including 0 and the end) serve render
and verify.

## Decisions

- **macOS on Apple Silicon first.** Transcription runs on MLX.
- **No Homebrew, no sudo.** Everything is installed into the repository folder, so
  `uninstall.sh` can remove it completely.
- **Every download pinned and checksum-verified.** Hugging Face models are pinned to a commit
  revision; plain files and binaries to a SHA-256.
- **FFmpeg is downloaded, never redistributed.** The build with libx264 is GPL; keeping it out of
  the repository keeps the project itself Apache-2.0.
- **Managed Python** (`UV_PYTHON_PREFERENCE=only-managed`), so every machine runs the same
  interpreter regardless of pyenv/Homebrew.
- **Claude skills ship as a Claude Code plugin** loaded with `claude --plugin-dir`, so they work
  regardless of the job folder.
- **Agent runs are restricted** (see `SECURITY.md`); in local mode they use `--setting-sources`
  to keep the user's personal Claude Code hooks and settings out of job runs (to be verified).
