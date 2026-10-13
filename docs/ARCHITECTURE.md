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
| `job new <video> [--type] [--brief] [--brand] [--screen FILE] [--asset FILE…] [--id]`, `job show [job]`, `job approve [job]`, `job redaction [job] --all-demo\|--redact-all\|--redact IDS`, `job brand <id>`, `job screen <file>` | `job.py` |
| `brand list`, `brand check <id>`, `brand show <id>`, `brand sheet <id> [--out]` | `brand.py` |
| `captions [--force]` | `captions.py` |
| `overlays [--no-render]` | `overlays.py` |
| `ingest [--denoise]` | `ingest.py` |
| `sync [--offset S] [--redo]` | `sync.py` |
| `screen [--force]` | `screen.py` |
| `face` | `face.py` |
| `transcribe [--model turbo\|parakeet] [--language]`, `candidates` | `transcribe.py` |
| `sheet contact [--every]`, `sheet burst <t> [--span]`, `sheet timeline <t0> <t1>`, `sheet screen [t0 t1] [--tiles]`, `sheet assets` | `sheets.py` |
| `edl check [file]` | `edl.py` |
| `render [--preview]` | `render.py` |
| `verify` | `verify.py` |

Helpers: `_transcribe_energy.py` (pauses, voiced gaps), `_transcribe_pack.py`
(`takes_packed.md`), `_image.py` (ffmpeg-drawn pictures, peak-normalised waveforms),
`_render_plan.py` (frame grid, filter graph), `_geometry.py` (crop windows, face-centred
framing, face track in output px, safe zones; shared by render, overlays and verify),
`_color.py` (HDR → SDR), `_layouts.py` (screen/pip/split geometry, zoom windows and their
filter graph; shared by render, overlays and verify), `_verify_screen.py` (the product-demo
checks of verify).

## Job folder (`$VIDEOCUT_HOME/jobs/<id>/`)

```
job.json   id, created, source (relative), type, brief, status, approval, brand, assets,
           format (optional output format; absent = the default rule, resolved on read),
           cut / overlays (what `videocut run` finished: plan and segments hashes, brand)
input/     source recording, screen recording and assets (screenshots), hard-linked or copied
work/      probe.json, audio_raw.wav, audio.wav (+ audio.json), proxy.mp4        ingest
           screen_probe.json, screen_audio.wav, screen_proxy.mp4                ingest (demo)
           sync.json                                                            sync
           screen.json                                                          screen
           face.json                                                             face
           words.json, transcribe.json, candidates.json, takes_packed.md         transcribe
           sheets/contact_NN.jpg, burst_*.jpg, timeline_*.jpg, screen_*.jpg,
           assets_NN.jpg                                                         sheet
           contract.md (reel contract of the type, copied by `run` if one exists)
           project.md, redaction.json (personal-data candidates)                 plan run
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
| `plan` | `takes_packed.md`, `job.json`, `contract.md`, 1–2 contact sheets; demos: `sync` + `screen` summaries, the screen overview or the assets sheet | `work/project.md`: hook, core message, target length, keep/cut list in source time, emphasis words, risks; demos: views, zoom targets, redactions, screenshot matches | ≤ 4 |
| `cut` | `project.md` (must be approved), `words.json`, flagged verify pictures | `work/edl.json` (segments), renders, `work/cut_report.md` | ~8, only at doubts |
| `overlays` | `project.md` overlay ideas, `edl.json`, `brand show`, overlay/caption sheets | `captions` + `overlays` in `work/edl.json`, overlay render, renders, `work/overlay_report.md` | ≤ 4 |

`videocut run <video|job>`:

1. **prep** (no Claude): `job new` when a file is given, `ingest --denoise`, `face`, `transcribe`
   (also candidates and the packed transcript), `sheet contact`; copies `templates/<type>.md`
   to `work/contract.md` when it exists. A product demo (`--screen`, job.json `screen`) runs
   `ingest` (camera and screen) → `sync` → `screen` → `face` → `transcribe` → `sheet contact`
   → `sheet screen` (one overview) and stops with a `tool sync --offset` hint when sync fails
   or its confidence is below 0.3.
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

## Output format (`tools/_format.py`)

Every job has one output format: job.json `format`, one of `9:16` 1080×1920, `16:9`
1920×1080, `1:1` 1080×1080, `4:5` 1080×1350. Absent, the default rule decides when the job
is read (never written back): a job with a screen recording or screenshot assets is a
product demo and gets `16:9`, every other job `9:16`. `job new --format`, `run --format`
and `job format <job> <f>` (human only) set it; `job show` prints format and size. A
changed format makes the finished cut stale (the cut record carries the format), so `run`
cuts again; the plan stays.

`output_size(job)` is the size of reel, preview (half), cover, overlays.json
(`size`/`width`/`height`), verify's size check and `brand sheet --format`. The job format
beats the EDL's own `size` (render and overlays use the job's and warn). Safe zones
(`safe_zones(size)`, px at the preset; other sizes of the same aspect scale with the
height): 9:16 top 250 / bottom 350 / sides 6 %, 4:5 108 / 203 / 5 %, 1:1 76 / 151 / 5 %,
16:9 65 / 130 / 4 % (player UI; the bottom band is the caption strip). The soft-crop
warning (`sharp_min_width`) scales with the output width.

Renderer: every scene gets `width`/`height` and lays out by aspect class (`portrait`
h/w > 1.2, `landscape` w/h > 1.2, else `square`), the type scale from the short side.
Landscape: captions bottom-centre (≤ 70 % of the width, 2 lines), cards and keywords in a
side column, the lower third bottom-left, screenshots keep their aspect, `cta_end`
centred; square and 4:5 put the caption at the foot. The 9:16 layout is unchanged.

## Product demos

A demo job has the camera recording (`source`, the speaker; its time is "camera time", the
time of every EDL segment) plus a screen recording (`screen`) made at the same time, or only
screenshots (`assets`).

**Screen source.** `job new --screen` / `job screen` link the recording into `input/`;
replacing it deletes everything made from the old one. `ingest` writes `screen_probe.json`
(audio fields null without a microphone), `screen_audio.wav` (48 kHz mono) and
`screen_proxy.mp4` (long side 960, constant 30 fps, SDR; screen time 0 = its first video
frame). macOS recordings are variable frame rate; the proxy and render put them on a fixed
grid (sample and hold), so long static stretches keep their timing. The original stays the
render input.

**Sync** (`tool sync` → `work/sync.json`). Both recordings carry the voice, so the offset is
found by audio: loudness envelopes cross-correlated over all lags (confidence = 1 − second
peak / peak), refined with GCC-PHAT on the raw samples of the loudest 12 s, drift measured
from two windows (ppm; offset_s is exact at `drift_ref`, the middle of the overlap). Below
0.3 confidence a single clap in both tracks is tried; otherwise the tool fails and asks for
`--offset` (method `manual`). `screen_time = camera_time + offset_s`; `overlap` is the
camera time in which both have pictures. Drift is reported, not applied (a few frames per
minute at most).

**Screen analysis** (`tool screen` → `work/screen.json`, screen time). A grey 10 fps stream
(long side 960) gives activity boxes (change against the previous sample plus the
persistent change against one second earlier), the pointer (reported, never a target),
clicks (the expanding ring of "show mouse clicks", or a pointer stop followed by a change),
hard scene changes and text density. Activity and clicks merge into zoom targets that stay
put ≥ 1.2 s, each with `max_zoom` / `max_zoom_split` and a `view` box of the "screen" view's aspect. The
summary adds camera times (`camera_t0`, `camera_t`) from the current sync.json, because the
EDL is in camera time. `sheet screen` draws the analysis onto a strip of frames (activity
yellow, clicks red, targets green).

**Layouts** (`_layouts.py`, per segment `layout`, geometry by the job format): `face`
(unchanged render path; an EDL of face segments renders byte-identically, golden test; in
16:9 a landscape reframe of the camera). **9:16 and 4:5:** `screen` (the screen fills the
reel width, centred in the safe area, on the brand's `colors.bg`), `pip` (screen at the top
of the safe area, the speaker as a rounded inset of 34 % of the width below it, on the side
away from the zoom targets, so it never covers screen content; the strip below the inset
is the caption room), `split` (screen in the top 58 %, the speaker face-centred below).
**16:9:** `screen` fills the frame: the recording is cropped to the frame's aspect (zoom
windows have the view's aspect; no zoom = the full screen width, placed around the first
zoom target), letterboxed in `colors.bg` only when the aspects differ by more than 25 %
(`LETTERBOX`, e.g. a portrait phone screen). `pip` adds the speaker as a round bubble 26 %
of the frame height in a bottom corner above the caption strip, in the corner that covers
clearly less of the zoom targets (right on a near tie), `split` fills the left 64 % with
the screen (`LETTERBOX_SPLIT` 50 %) and the speaker face-centred in the right 36 %.
**1:1:** the screen fills the band between the top and bottom safe zones (same rule), the
bubble pip, `split` stacked. The speaker crop (inset, split part) is centred on the segment's face median,
head and shoulders (face height × 2.6), and at least 0.75 camera px per output px; render
reports the ratio per segment in render.json `speaker` (`px_ratio`) and warns when it is
below 1 (upscaled). For a small face (e.g. a video-call tile) `face` runs a second pass
when the first finds it in < 50 % of the samples: a tile scan with the region of interest
upscaled (face.json `small_pass`, FACE_VERSION 2). In landscape and square `face`
segments such a small face (< 25 % of the camera height) gets a closer default crop (zoom
up to 2×, `face.small_face_zoom`); portrait framing is unchanged. The screen
frame shown at camera time t is screen time t + offset_s, drawn from the original
recording. `screen_zoom` keys move the window with a 0.6 s smoothstep: a segment with one
window is a plain crop + scale; a moving one goes through `perspective` (sense=source,
eval=frame) with the same piecewise function of the frame number that `window_at`
computes in Python. (A per-frame `scale` followed by `crop` does not work: crop clamps to
its input link size, fixed at configuration, so every zoom ended in the top-left corner.)
`redact` blocks are drawn in `colors.bg` onto the screen frames before any scaling, on
every frame that overlaps their range, snapped outward to even pixels. HDR cameras are
tonemapped per branch (inset, split part) like the face path.

**Sharpness rule.** A zoom window narrower than the view's width at the job's output size
would upscale the screen (< 1 source px per output px); render widens it to that width,
warns, and lists it in render.json `screen_zoom_clamped` (verify repeats it as info). For a
2880 px wide recording in a 1080 px reel that is 2.67×, in a 1920 px wide 16:9 frame
1.5× (a 1920 px wide recording has no sharp zoom at all in 16:9); `screen.json` reports
the caps of the job format as `max_zoom` and of every format in `by_format`.

**Readability.** UI text is taken as 13 logical px (`ui_scale` 2 for recordings with a
long side ≥ 2700 px, a heuristic); shown smaller than 18 output px it is hard to read.
`screen.json` `by_format` gives per format and layout `text_px` (whole screen) and
`readable_zoom` (zoom counts from the no-zoom window); `edl check` warns `screen_text_small` per window that stays too small,
verify lists the same as info (`screen.readability`). In 16:9 the whole screen is often
readable, so the skills zoom only where this says so.

**Overlays.** overlays.json `views` lists per screen/pip/split stretch the screen view, the
zoom targets (`focus`: output box from the frame a target is reached to the next) and the
speaker area (from the same plan render draws). The renderer registers the screen view and
every target as blocks before laying out any item, so captions and scenes keep off them;
the face track there is the speaker area (pip inset; the speaker's face in split). A screen
view that fills the frame (≥ 80 % of both sides: 16:9 screen/pip) is the picture itself,
so its `screen` is null and only the targets and the speaker are kept clear (a target
covering half the view or more, a screen-shaped zoom box, is not listed: nothing specific
to avoid); in 9:16 the captions sit below the screen view, outside the speaker. A portrait `screenshot_frame` (screenshot-only
demos) is drawn as tall as the safe area allows minus room for one caption block and may
cover the speaker (`stage`: verify does not flag `overlay_on_face` for it).

**Verify** (`_verify_screen.py`): every redact block is sampled on up to 5 output frames of
its range through the drawn zoom window; ≥ 90 % of its pixels must be the brand background
(`redaction_missing`). The middle frame of each screen segment is compared with the screen
recording at the mapped time and at ±0.5 s / ±1 s (`screen_time_mismatch` when another time
matches clearly better). `screen_overlap` repeats the overlap rule of `edl check`. A cut
into, out of or between screen views counts as a reframe (no `visual_jump`), a still screen
in a `screen` segment is not a frozen picture, and blinks are only checked where the camera
is visible. In a landscape/square pip the frozen check looks inside the speaker bubble
only (the rest is a still screen). `overlay_on_screen` flags an item that covers a screen view (one that does not
fill the frame) or an active zoom target.

**Personal data is a human decision** (`src/videocut/redaction.py`). The plan run lists
every suspected item (e-mail, phone, name, customer data, other) in `work/redaction.json`
(camera time + screen box, or screenshot file + box) and numbered in project.md. Whether
it is demo data is decided by a human only: the approval prompt of `videocut run` asks
"[a]ll demo / [r]edact all / [s]elect", `--demo-data` records "all demo" (method `flag`),
and `videocut tool job redaction <job> --all-demo | --redact-all | --redact 1,3` refuses
inside an agent run. The decision lives in `$VIDEOCUT_HOME/state/<job>/redaction_decision.json`
with the SHA-256 of redaction.json and a copy of it. Safe default: without a valid decision
(none, or the list changed) every candidate is redacted; what a decision once marked
"redact" and what the approval record saw (`redaction_candidates`) stay redacted even if
the list is edited later. Render adds a redact block for every enforced screen candidate to
the screen segments it overlaps (render.json `redaction`: source `decision` or `default`),
overlays add enforced screenshot boxes to `screenshot_frame` items, and `edl check` /
verify flag `redaction_missing` when the EDL itself lacks one (the cut must be explicit).
An unreadable redaction.json fails closed (no approval, no render).

**Screenshots only.** `job new --asset FILE...` takes many images; `sheet assets` puts them
on one numbered contact sheet (≤ 1080×1920, with sizes and orientation in the summary), so
the plan can match each screenshot to its sentence with one image and propose
`screenshot_frame` overlays with focus and redaction boxes.

## EDL (`src/videocut/edl.py`)

Schema from BRIEF §6. Segment times (`in`, `out`, `zoom_keys`) are source time, everything else
output time. `validate()` returns every problem with a JSON path (`$.segments[2].out`); it checks
`in < out`, segments within the source duration (from `work/probe.json`), crop `cx/cy` in 0..1,
zoom 1.0..2.0, `fade_ms` 0..200 and that all file paths stay inside the job folder. Product
demos: `layout` (face|screen|pip|split), `screen_zoom` [{t, box}] (increasing t inside the
segment, boxes 0..1 of the screen) and `redact` [{t0, t1, box}] inside the segment, both
only on non-face segments; with a job, every non-face segment must lie inside the sync
overlap (`overlap_errors`). edl.py is the only reader of these fields (render, overlays and
verify get them from `edl.Segment`). Captions,
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
