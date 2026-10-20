# Architecture (current state)

`docs/BRIEF.md` is the target design. This file describes what exists and records deviations.

## Layout

| Path | Purpose |
|---|---|
| `start.sh` | entry point: steps 1-3 (this Mac, `uv`, Python), then `videocut setup` (steps 4-7), `doctor`, a summary; resumable and idempotent |
| `INSTALL.md`, `.claude/skills/install-videocut/` | the install path for a person's own Claude Code (install, repair, update, uninstall) |
| `scripts/fresh-install-test.sh` | clean-environment install test (`env -i`), timings and sizes; `--claude` lets the official program install from the person's sentence; results in `docs/fresh-install-report.md` |
| `manifest/tools.json` | pinned downloads: version, URL, SHA-256, licence |
| `src/videocut/` | Python package and CLI (`setup`, `doctor`, `selftest`, `models pull`, `ui`, `auth`, `run`, `tool`) |
| `src/videocut/run.py` | `videocut run`: prep → plan run → human approval → cut run → overlay run; `--plan-feedback` (revise the plan), `--feedback [--at]` (revise the reel) |
| `src/videocut/server/` | local app backend (FastAPI + uvicorn): `app.py` routes, `security.py` session/Host/Origin guard, `uploads.py` streamed uploads, `runs.py` run queue + events, `state.py` job views, `brands.py` |
| `app/` | local app UI source (React + TypeScript + Vite, vitest); built into `src/videocut/web/dist/` (not committed) by `videocut setup`; `app/e2e/` the browser end-to-end script |
| `src/videocut/brand.py` | brand profiles: load, validate, resolve `brand.json` into design tokens |
| `src/videocut/agent.py` | the restricted `claude` runner (see `SECURITY.md`) |
| `src/videocut/claude_cli.py` | finds the official `claude` program (also outside PATH), asks `claude auth status`, the connection test call |
| `src/videocut/launcher.py` | `videocut launcher` (the "Video Cut.app" bundle) and `videocut open`; `server.json` |
| `src/videocut/update.py` | `videocut update` (tarball swap or `git pull`) |
| `claude-plugin/` | Claude Code plugin `video-cut` with the skills `ping`, `plan`, `cut`, `overlays`, `revise` (slides in each) |
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
| `job new <video> [--type] [--brief] [--brand] [--screen FILE] [--asset FILE…] [--cut-style] [--lang de\|en] [--id]`, `job show [job]`, `job approve [job]`, `job redaction [job] --all-demo\|--redact-all\|--redact IDS`, `job brand <id>`, `job screen <file>`, `job format [job] <fmt>`, `job cut-style [job] <calm\|standard\|tight\|auto>`, `job redact-style [job] <auto\|blur\|crop\|block>`, `job duplicate [job] [--title T] [--keep-approval]`, `job title [job] <title>`, `job vocab <job> add\|remove\|list`, `job slides [job] [--mode] [--wish]`, `job slides-import <job> FILE… [--replace]` | `job.py`, `_job_slides.py` |
| `brand list`, `brand check <id>`, `brand show <id>`, `brand sheet <id> [--out]`, `brand vocab <id> add\|remove\|list` | `brand.py` |
| `vocab show [job]`, `vocab apply [job]`, `vocab suggest [job] [--all]` | `vocab.py` |
| `transcript show [job] [--from t --to t]`, `transcript edit <job> <i> <text>`, `transcript undo <job>` | `transcript.py` |
| `captions [--force]` | `captions.py` |
| `overlays [--no-render]` | `overlays.py` |
| `ingest [--denoise]` | `ingest.py` |
| `sync [--offset S] [--redo]` | `sync.py` |
| `screen [--force]` | `screen.py` |
| `redact text [--force]`, `redact measure` | `redact.py` |
| `face` | `face.py` |
| `transcribe [--model turbo\|parakeet] [--language] [--no-align]`, `candidates` | `transcribe.py` |
| `align [--no-candidates]` | `align.py` |
| `sheet contact [--every]`, `sheet burst <t> [--span]`, `sheet timeline <t0> <t1>`, `sheet screen [t0 t1] [--tiles]`, `sheet assets`, `sheet slides` | `sheets.py` |
| `edl check [file]`, `edl snap [--dry-run]`, `edl locate <t> [--source]` | `edl.py` |
| `slides check [job]`, `slides stills [job] [--id]`, `slides render [job] [--force]` | `slides.py` |
| `material fetch [job] [--wish N --query/--prompt]`, `material generate`, `material choose <job> <n> <id\|none>`, `material show [job]`, `job material [job] [--mode] [--wish]` | `material.py`, `_material_providers/`, `_job_material.py` |
| `render [--preview]` | `render.py` |
| `verify` | `verify.py` |

Helpers: `_transcribe_energy.py` (pauses, voiced gaps), `_transcribe_pack.py`
(`takes_packed.md`), `_image.py` (ffmpeg-drawn pictures, peak-normalised waveforms),
`_render_plan.py` (frame grid, filter graph), `_geometry.py` (crop windows, face-centred
framing, face track in output px, safe zones; shared by render, overlays and verify),
`_color.py` (HDR → SDR), `_layouts.py` (screen/pip/split and slide geometry, zoom windows and
their filter graph; shared by render, overlays and verify), `_verify_screen.py` (the
product-demo checks of verify), `_verify_slides.py` (the slide checks of verify), `_ocr.py` (local text recognition: macOS Vision through ctypes),
`_pdf.py` (PDF pages → PNG, macOS CoreGraphics in a child process), `_job_slides.py` (job slide settings, page import),
`_audio_env.py` (energy envelope of `audio.wav` in video time), `_snap.py` (cut-point
snapping, quiet room for crossfades, cut points in speech), `_verify_audio.py`
(`speech_clipped`), `_style.py` (cut styles).

## Job folder (`$VIDEOCUT_HOME/jobs/<id>/`)

```
job.json   id, created, source (relative), type, brief, status, approval, brand, assets,
           format (optional output format; absent = the default rule, resolved on read),
           cut_style (optional; absent = the default rule), lang (de|en, default de),
           vocabulary (optional, the video's own terms; see Transcript vocabulary),
           redact_style (optional auto|blur|crop|block; absent = the brand's
           redaction.style, default auto),
           material (optional {mode, wish}; plan_material: what the plan was made for),
           cut / overlays (what `videocut run` finished: plan and segments hashes, brand,
           format, cut style and chosen material of the cut)
input/     source recording, screen recording and assets (screenshots), hard-linked or copied
work/      probe.json, audio_raw.wav, audio.wav (+ audio.json), proxy.mp4        ingest
           screen_probe.json, screen_audio.wav (+ screen_audio.json),
           screen_proxy.mp4                                                     ingest (demo)
           sync.json                                                            sync
           screen.json                                                          screen
           screen_text.json                                                     redact text
           face.json                                                             face
           words.json, transcribe.json, candidates.json, takes_packed.md         transcribe
           sheets/contact_NN.jpg, burst_*.jpg, timeline_*.jpg, screen_*.jpg,
           assets_NN.jpg                                                         sheet
           contract.md (reel contract of the type, copied by `run` if one exists)
           project.md, redaction.json (personal-data candidates)                 plan run
           redaction.json track, redaction_measure.json (every sample)          redact measure
           plan.json (the plan as data for the app), plan_stills/view_NN.jpg,
           pd_NN.jpg (`sheet plan`), plan_feedback.json (during a plan feedback run)
           edl.json, render_filter.txt, cut_report.md                            cut run
           overlays.json, overlay.mov, overlay_boxes.json, overlay_report.md     overlay run
           runs.jsonl, claude-plan.jsonl, claude-cut.jsonl                       videocut run
           revision_request.json, revision.json, revision_report.md,
           revisions.jsonl (one line per feedback run)                           revise run
           redact_grown.json (redact boxes grown over the text they cut)        render
           slides.json (the slides as data)                                     plan run
           slides/stills/<id>.jpg, <id>_sm.jpg (≤ 960 px), <id>.layout.json    slides stills
           slides/<id>.mov, <id>.json (layout report), <id>.spec.json           slides render
           material.json, material/<n>/c<k>.mp4|jpg|png, c<k>_sm.jpg           material fetch
out/       preview.mp4 | reel.mp4, cover.jpg, render.json                        render
           verify.json, verify/cut_NN.jpg, captions_NN.jpg, overlays_NN.jpg,
           slides_NN.jpg                                                         verify
           history/reel-<k>.mp4 (the reel before a revision; the last five)       revise run
```

Lifecycle (`status` in job.json): `new` → `ingested` (ingest) → `approved` (`job approve`, by a
human). Approval stores the SHA-256 of `work/project.md`; `job show` reports `approved: true`
only while the plan is unchanged, so a new plan needs a new approval. The trusted record lives outside the
agent-writable job folder, in `$VIDEOCUT_HOME/approvals/<id>.json` (job path, time, SHA-256);
job.json only mirrors it for display, and `render` (also `--preview`) refuses without it.

`job duplicate` (human only; `POST /api/jobs/{id}/duplicate`) makes `<id>-copy-N` with
input/ hard-linked and work/, out/ copied (copy-on-write where possible; never hard links,
tools rewrite files there). The approval record and the personal-data decision are copied
(naming the new folder) only with `--keep-approval` / `keep_approval`, otherwise the copy
needs its own approval; the overlay record always moves along (it is checked against the
files' hashes). job.json gets `copy_of`; an optional `title` (`job title`, `PUT
/api/jobs/{id}/title`) is the display title before the plan's.

Ingest is idempotent: an output newer than its input is kept. DeepFilterNet runs with delay
compensation (`-D`) so cleaned audio stays in sync with the picture. Transcribe keeps a
`words.json` made from the same audio, model and language.

## Time base, word alignment, snapping and crossfades

**Video time.** Every time in a job is video time: 0 is the first frame of the camera's video
stream. Phones and AAC encoders start the audio stream a little later (typically 20-160 ms of
priming; 157 ms on the real demo recording) or earlier. Ingest therefore pads (or trims) both
wavs by `audio_offset_s = audio_start - video_start`, so a time in `audio.wav`,
`audio_raw.wav` and `words.json` is a time in the picture; `audio.json` records
`{"time_base": "video", "audio_offset_s"}` (`screen_audio.json` the same for the screen
recording's microphone, in screen time). The proxy and render start the camera video at its
first frame (`setpts=PTS-STARTPTS`), and a render that cuts the source's own audio stream
moves it by its start (`asetpts=PTS-STARTPTS±offset`); render cuts `work/audio.wav` (the
denoised track) whenever `audio.json` says video time. Every consumer of word times (captions,
candidates, face/blink checks at cuts, `edl snap`/`check`/`locate`, revise, redaction measure,
plan stills and bursts) reads them as video time without any further offset;
`tests/test_video_time.py` checks words, cuts, picture, sound, captions and the proxy on a
generated clip whose audio starts 157 ms after the video and one whose audio starts first.

A job ingested before this rule (`audio.json` without `time_base`) is upgraded in place by
the next ingest (every `videocut run` starts with it; a revision runs it first): the wavs are
shifted, `words.json` (with alignment and candidates) moves along instead of being
transcribed again, and `edl.json` moves with the words its cut points were placed on (zoom
and screen-zoom keys too; redact blocks were measured on the video and stay). The job.json
cut and overlay records follow the EDL (same cut, new time base), so a finished reel stays
finished. The plan keeps its numbers: they guide the cut, the words decide it. A
`"time_base": "shifting"` marker makes an interrupted upgrade rebuild instead of shifting
twice; the EDL moves exactly once also when the audio is rebuilt instead of shifted (a
changed `--denoise`, a re-probe; marker `edl_shifted`), and keeps its file time. Pads and
trims are counted in 48 kHz samples whatever the source rate. Before an upgrade,
`_audio_env.wav_offset` still reads such a job correctly for checks; `edl snap` waits
(skipped with a hint) instead of mixing both time bases.

**Word alignment** (`tools/align.py`, run by `transcribe` unless `--no-align`, again by
`videocut tool align`): Whisper chains consecutive words and ends them 40-330 ms early.
Alignment moves every boundary onto the energy of `audio.wav`: silence pins a word's edges,
chained words split at the level valley between them, a word stretched over a pause shrinks
to its voice, words in silence (hallucinations) keep their times. The model times stay in
`t0_asr`/`t1_asr`; `transcribe.json` `align` records method, version and median shift. On the
real demo recording: median shift 55 ms, p90 199 ms.

**Snapping** (`edl snap`, also run by every render): every segment `in`/`out` moves to the
nearest acoustically quiet point within the cut style's `snap_window_s`, never across a kept
word and never into a dropped one (an `out` stays before the middle of the next dropped
word, an `in` after the middle of the dropped word before it). The originals are kept as
`in_src`/`out_src`, so snapping again is idempotent; a hand-edited cut point becomes the new
original. Zoom, screen-zoom and redact keys on a moved edge follow it; output times
(captions, overlays) are remapped. `edl check` reports `cut_in_speech` for a cut point
inside full speech unless the segment says `"speech_cut_ok": "<reason>"`.

**Crossfades** (`_render_plan.audio_parts`): the video is a hard cut on the frame grid; the
audio crossfades equal-power at every cut (`xfade_ms` of the style, a segment's own
`xfade_ms` overrides it), centred on the cut: the outgoing segment runs on half of it past its
`out`, the incoming starts half of it before its `in`, so the reel length is exactly the sum
of the frame-snapped segments. A crossfade is shortened to the quiet room around both cut
points (never under 10 ms), to half of a short segment and to the source end; two segments
that continue each other (a join) get a 10 ms linear crossfade. The legacy `fade_ms` is
ignored.

**speech_clipped** (`verify`, `_verify_audio.py`): a hard check on the reel's own audio: at
every cut, speech level and spectral flux right before and after the cut tell a word cut off
or started mid-syllable from a clean cut; it fails verify like a click.

## Transcript vocabulary (`tools/vocab.py`)

Names the transcriber gets wrong (the company name, a product, a customer in a demo):
`[{term, variants, note?}]` in brand.json `vocabulary` (every job of the brand) and job.json
`vocabulary` (this video). `brand check` / the brand PUT validate it: a non-empty term, literal
variants (no pattern characters), no key used twice (key = case-folded letters and digits).
The effective vocabulary is brand ∪ job; the job wins every conflict (same term: one entry
with the job's spelling and both variant lists; a brand term the job lists as a variant is
dropped; a variant claimed by both belongs to the job's term). `job show` reports it as
`vocabulary_effective` and prints one line for the skills, which use the canonical spelling
in every text they write (plan, reports, cards, captions fixes).

- **Prompt:** turbo's initial prompt is the filler sample followed by the canonical terms,
  as many as fit Whisper's 223 prompt tokens (counted with Whisper's own tokenizer, the job's
  terms first), so the sample is never cut off. When decoding falls back to sampling, it
  retries with the filler sample alone, then without a prompt. `transcribe.json`
  `prompt_terms` lists the terms used. Measured on two generated clips (filler clip, a clip
  with three invented names), 12 runs from 3 to 40 terms (37-222 tokens): 3/3 fillers every
  time, no fallback; the names were all spelled right whenever they were in the prompt (one
  of three was misspelled with the filler sample alone).
- **Correction:** after transcription and alignment (every `transcribe`, also of a kept
  transcript, and `vocab apply`), a word matching a variant or the term itself
  (case-insensitive, punctuation stripped) becomes the term, the punctuation around it kept.
  A match may span up to 4 consecutive words ("Sentro Via", the term split by the model;
  only for keys of ≥ 5 characters and never across punctuation inside the span), keeps an
  inflection ending (`s`, `es`, `'s`) and corrects parts of a hyphen compound; a longer word
  that only contains a variant never matches. A corrected word keeps `w_asr` (the model's
  text) and `vocab` (the term); words merged from a span keep the originals in `parts`
  (fitted into the merged word's current times when restored), so every apply starts from
  the transcript as the model wrote it and a removed variant goes back.
  `transcribe.json` `vocabulary` = `{applied, terms: {term: n}, entries}`.
- **Existing jobs:** `vocab apply` corrects `words.json` without transcribing again,
  rewrites `candidates.json`/`takes_packed.md`, and corrects caption words in `edl.json` and
  `overlays.json` (forwards only: captions may carry the agent's text). When captions changed,
  job.json `overlays.stale = "vocabulary"`: `run.overlays_done` is false, the next run keeps
  the cut and redoes only the overlay stage (the app: "Untertitel neu setzen", `POST
  /api/jobs/{id}/overlays`). Nothing is re-rendered by `apply` itself.
- **Suggestions:** `vocab suggest` reports words (and word pairs) within edit distance 1
  (keys < 7 characters) or 2 of a term or variant that no variant covers, with conf < 0.9
  (`--all`: any conf); they are never applied. The app shows them as "Meintest du …?".
- Editing is human only (`job vocab`/`brand vocab` refuse inside an agent run; job.json and
  the brands folder are outside what an agent run may write).

## Transcript edits (`tools/transcript.py`)

The human fixes single words (the vocabulary is for systematic misspellings, a revise run
for changes to the cut). `transcript edit <job> <i> <text>` / `PATCH
/api/jobs/{id}/transcript` change the text of word `i` of `work/words.json`, never its
times: the word keeps `w_asr` (the model's text, the vocabulary's convention) and gets
`edit` (the human's text, also in `w`). An empty edit hides the word from the captions
(`"hidden": true`, `w` stays the model's text, the word stays in the timing data for cuts,
fillers and alignment); text with spaces stays one timed token. Text: ≤ 60 characters, no
control characters, whitespace collapsed. Human only (`VIDEOCUT_AGENT` refuses).

- **Record:** every edit and undo is a line `{at, i, t0, asr, from, to, prev}` (`undo:
  true`) in `$VIDEOCUT_HOME/state/<job>/transcript_edits.jsonl`, outside what an agent run
  may write, mirrored to `work/transcript_edits.jsonl` for reading only (the mirror is
  never read back: an agent could write it). An entry is recorded only together with its
  words.json save (a failed save takes it back). Replaying the record gives each edited
  word's text; words are found again by the model's text and the nearest start within
  3 s, so indices may shift (vocabulary merges, the video time shift). `enforce` writes
  exactly the record onto words.json on every edit, undo, `vocab apply`, `transcribe`
  (through it), caption refresh and after every Claude run (`run.claude`); without a
  record (no human edit in this folder) it changes nothing. `transcript show` and the
  GET report `drift` (words that differ; the app warns). Undo takes back the last edit not
  yet undone (the word's previous edit, or none).
- **Vocabulary:** an edited word is never restored, matched or suggested by `vocab.py`,
  and an edited caption word is never re-corrected: the manual edit wins and survives
  every `vocab apply`. After an edit or undo the vocabulary is applied again around it.
- **Captions:** `captions` leaves hidden words out and shows an edited word's text. An
  edit on a job whose reel has captions sets job.json `overlays.stale = "transcript"`
  (also over "vocabulary": the refresh covers both; `vocab apply` keeps "transcript");
  job detail `captions_stale`; `POST /overlays` (a Claude run) is refused while it is set.
  After an overlay or revise run records the overlays, caption words that do not show the
  human's text make them stale again. `refresh` (`POST /api/jobs/{id}/captions/refresh`, run kind
  `captions`; the app's "Untertitel neu setzen", no Claude) puts the edited text into the
  EDL's caption blocks by output time (keeping the agent's blocks and emphasis; hidden
  words dropped, an undone edit back to the transcript's text) or, when a word the human
  edited is in the cut but not in the captions (a hide taken back) or a patched block is
  longer than the brand's pace allows, rebuilds them with `captions --force` (and marks
  the human's words again); then `overlays`, `render`, `verify` and the overlay record (no
  longer stale). A failure puts edl.json back as it was, so the refresh can be retried. It refuses before anything runs when `edl snap --dry-run` would move a
  cut point (a reel cut before snapping; render would change the cut) and fails when the
  segments changed anyway. Measured on a copy of a 27 s 9:16 demo job (2 words): 61 s
  wall at load average ~17 (164 s at ~70: overlays 72 s, render 68 s, verify 23 s);
  verify flags identical to the reel before, exactly the 2 caption words changed, the
  cut unchanged. That job was cut before cut-point snapping: refresh refused it (0.8 s,
  "would move 17 cut points") until its cut was brought up to date.
- **Read:** `GET /api/jobs/{id}/transcript[?offset&limit]` returns every word with its
  source time and, when the finished reel keeps it, its reel time (the captions' mapping
  rule, one pass), plus `edits`, `can_undo`, `edits_at`, `drift`, `captions_stale`.
- Skills (plan, cut, overlays, revise): an edited word is the truth, hidden words never
  appear in a caption, and the skills never edit `words.json` text or the edit log.

## Cut style and job language

job.json `cut_style` is `calm`, `standard` or `tight` (`_style.py`); absent, a job with a
screen recording or screenshots is cut `calm` (the viewer reads the screen while the speaker
talks), every other job `standard`. The style sets `keep_pause_s` (the longest natural
pause kept inside a segment: 1.0 / 0.6 / 0.35 s), `min_segment_s` (3.0 / 1.5 / 0.8),
whether a sentence may be assembled from pieces and what an in-sentence cut may remove
(calm: nothing; standard: fillers and false starts; tight: also repeats and pauses),
`xfade_ms` (120 / 80 / 50) and `snap_window_s` (0.35 / 0.30 / 0.25). The cut, overlays and
revise skills read it from `job show`; `edl snap` and render use it directly. Only a human
sets it (`job cut-style`, `videocut run --cut-style`, the app's `PUT cut-style`; refused
inside an agent run, like format and brand). Changing it keeps the approval (the plan did
not change) and makes a finished cut stale (job.json `cut.cut_style`); a recut
(`videocut run --recut`, `POST recut`) cuts again with the approved plan and the
personal-data decision as they are.

job.json `lang` (`de` default, `en`) is the language of the plan's and reports'
human-facing text; the skills write in it.

## Target length (`tools/_length.py`)

job.json `target_s` (seconds) is the length the human wants; absent means automatic: 30-45 s
for type `thought`, 30-60 s for the other talking-head types, and no fixed length for a job
with a screen recording or screenshots ("as long as the content needs, tight but complete";
the plan states its estimate, `plan.json` `estimated_s`). A set target means target ± 15 %
(`target_range`). `job show` prints it with its range; the plan sizes the keep list to it
(never pads; too little material: it says so and proposes the natural length), overlays
plan about 2-4 ideas per minute, cut and revise stay inside the range (revise goes beyond
only when the feedback asks). Set by a human only: `job new --target`, `job target <job>
<len|auto>` (90, 90s, 5m, 2:30; 5 s - 3 h), `videocut run --target`, `POST /api/jobs`
`target_s`, `PUT /api/jobs/{id}/target`; refused inside an agent run. Unlike the cut style
it changes the content: a plan run records the target it planned for (job.json
`plan_target`); a changed target makes the plan stale and revokes the approval (the
approval record also stores `target_s`, and `is_approved` compares it), a stale plan cannot
be approved, and the next run plans again (the app offers "Plan neu erstellen" through the
plan-feedback path, keeping earlier plan edits). A cut records its target.

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
of the reel's other cuts without a reframe; `click` for an audible (≥ −45 dBFS), isolated
sample step at the cut, measured against the audible audio around it (the denoiser's
digital silence is no baseline). Overlays: per drawn box
(`work/overlay_boxes.json`) `overlay_in_safe_zone` and `overlay_on_face` (> 2 % of the face
box at any face sample while visible) and `overlay_overlap` (an overlay and a caption on
screen together share > 10 % of the smaller box); the full-frame `cta_end` plate is exempt
from these and from the frozen check. Pictures: `captions_NN.jpg` (one tile per caption
group change) and `overlays_NN.jpg` (one tile per overlay, at its middle). Global checks: duration,
loudness, true peak, size, black/frozen stretches. Slides (`_verify_slides.py`, see Slides):
`slide_text_overflow`, `slide_reading_time`, `slide_bubble_over_text`, `slide_missing` /
`slide_untrusted` / `slide_stale` / `slide_unchecked`; `slides_NN.jpg` (one tile per slide, near
the end of its stretch). After `render --preview`, verify checks
`out/preview.mp4` at half size and does not judge its single-pass loudness.

## AI labelling and provenance (`_ai_label.py`, `_c2pa.py`, `_disclosure.py`)

EU AI Act Art. 50 (in force since 2026-08-02) and the platforms' own rules: realistic
AI-generated material in a video is labelled visibly, and the file carries machine-readable
provenance. Everything here is built from the material the host fetched (`material.load`: the
trusted copy outside the job folder, every candidate with source, licence, attribution and
`ai_generated`), the human's checked choices (`material.chosen`, never the `chosen` field of
the work/material.json mirror) and the EDL; the agent cannot add, move or remove a label.
`edl snap` belongs before `overlays`: an EDL changed afterwards leaves the overlay layer
(with the badge) out of the render, and verify flags `ai_label_missing`.

- **What is on screen** (`_ai_label.on_screen`): a segment with `"material": <wish n>`
  (layout `broll`) over its output span (`edl.output_spans`), and an overlay `still_card`
  with `props.material` over its `t0..t1`; the candidate is the wish's `chosen` one (a
  `still_card` may name another candidate of the wish in `props.candidate`). A candidate
  counts as AI-generated when `ai_generated` is true, its `source` is a generator
  (`gemini`, `openai`, `local`) or its licence is `generated` (fail closed).
- **Visible label**: `videocut tool overlays` adds one `ai_badge` item per merged span
  (gaps < 0.5 s close) after the slide trimming and drops any `ai_badge` the EDL carries;
  overlays.json `ai_label` lists them. Every AI item of style `photo` (anything not
  `illustration`) is labelled; illustrations too while `settings.json`
  `ai_label_illustrations` is true (default; Settings → "KI-Kennzeichnung"). The photo label
  has no switch. The scene (`renderer/scenes/ai_badge.js`) is a small pill from tokens only
  (surface fill, text colour, muted edge), text by job language ("KI-generiert" /
  "AI-generated"), in the top safe-area corner opposite the logo (`tokens.logo.corner` when
  `logo.placement` is `corner`, else top-left), in the other corner or below the face when
  the face is there; laid out before every other item (they keep clear of it), drawn above
  captions and plates (`top`) and also over a full-frame slide (`badge`). No motion.
- **Verify**: `ai_label_missing` (`_verify_ai.py`) when an item that must be labelled has no
  drawn `ai_badge` box (overlay_boxes.json) covering its span (1.5 frames slack); no boxes
  at all count as missing. verify.json `ai_material` lists the AI items; each badge gets a
  tile in `overlays_NN.jpg`.
- **Container metadata** (full render): `comment` = "Contains AI-generated material:
  <models>" when any AI item is shown.
- **C2PA** (full render, `_c2pa.sign`): the pinned `c2patool` (manifest/tools.json,
  contentauth/c2pa-rs, universal macOS build; optional install step) embeds a manifest:
  claim generator "Video Cut <version>", `c2pa.actions` = `c2pa.created` (the reel:
  digitalSourceType `compositeCapture`, or `compositeWithTrainedAlgorithmicMedia` with AI
  material), `c2pa.edited`, and per AI model a `c2pa.created` with `trainedAlgorithmicMedia`
  and the model as softwareAgent. No prompts, names or paths; no timestamp authority (no
  network). Signed with a local chain created once with macOS openssl in
  `$VIDEOCUT_HOME/state/c2pa/` (a "Video Cut local" root and an ES256 signer it issued; C2PA
  refuses a self-signed end certificate; the root key is deleted after issuing). Validators
  report the manifest as valid with `signingCredential.untrusted` ("unknown signer"); a
  certificate from a trusted issuer is a later step. After writing, `c2patool <reel>` is read
  back into render.json `provenance.c2pa_verify`; render.json `c2pa` is `signed`, `skipped`
  (no c2patool: the reel is delivered without a manifest) or `failed` (a warning).
- **Incoming provenance** of generated stills is never stripped: the material tools keep the
  original file next to any derived copy and record `_c2pa.inspect(path)` (C2PA manifest
  present/absent/unknown, its state, generator, digital source types, a hash of the active
  manifest, and whether IPTC `trainedAlgorithmicMedia` metadata is in the file) in
  material.json as each generated candidate's `provenance`. SynthID cannot be read locally; material.json `watermark` comes from the
  provider.
- **Disclosure** (`_disclosure.py`, full render): `work/disclosure.json` + `disclosure.md`:
  the AI items (model, output span, prompt, style, labelled), one attribution line per
  stock candidate shown (for CC BY the full credit `attribution_full` — title, creator,
  licence and its link — plus the source link, in disclosure.md and the snippet), the platform checklist (YouTube "Altered or synthetic content",
  Meta "AI info", TikTok "AI-generated content": `required` for realistic AI material, else
  `not needed`), and a description snippet in the job language with the attributions.
  `GET /api/jobs/{id}/disclosure` (`{"ready": false}` before a full render); the job page
  shows it as "Veröffentlichen"/"Publish" under the result (checklist, snippet with a copy
  button, AI items; one line when there is nothing to disclose).
  `GET/PUT /api/settings/labels` reads and sets `ai_label_illustrations`.
- `videocut doctor`: `c2patool` present and at the pinned version (optional check).

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
`redact` blocks first **grow** so they never cut through text or a UI bar
(`_redact_grow.py`, in `plan_all`, so render, overlays and verify share the boxes): local
text recognition reads up to 5 distinct screen frames of each block's range around the
box; a line the box cuts (≥ 10 % of its height inside, not wholly inside) pulls the box
over the whole line plus 35 % of the line height; a band along a screen edge then moves
its inner edge on to the next strong UI boundary (row/column median step ≥ 8 grey
levels) within three text heights, e.g. the bottom of a browser's chrome; both repeat
until nothing moves. Never smaller; cached in `work/redact_grown.json`; render.json
`redaction.blocks[]` has `planned` (the box before) and `grown` (`text`, `band`). They are
then drawn onto the screen frames before any scaling, on every frame that
overlaps their range, snapped outward to even pixels, in the job's **redaction style**
(`_redact_style.py`, see *Personal data*): `block` a solid block in `colors.bg`; `blur`
the box pixelated into cells of the UI text height, or a quarter of its short side up to
three text heights (`blur_cell`), nearest-neighbour back up and a gaussian of half a cell (split →
crop → scale → overlay with `enable`, so it keeps its colours); `crop` a band along a
screen edge (touches it within 1 %, spans 90 % of the other side; `auto` only top and
bottom bands: browser tabs, address and bookmark bar) is cut off for the whole segment:
the segment's screen is the rest of the recording (`Segment.origin`/`screen`), laid out
by the same view rules, so zoom windows, targets and other boxes are in its coordinates.
A crop that would remove more than 20 % of the screen height (or width) blurs those
bands instead (render.json `redaction.blocks[].note`). Before the graph is built render
reads every blurred box on its middle frame with local text recognition; while anything
is readable the cell doubles (never smaller), and a box still readable as one cell is
drawn as a block (fail closed). render.json `redaction` lists per block the segment,
camera times, box, style used, edge (crop) and cell (blur). HDR cameras are tonemapped
per branch (inset, split part) like the face path.

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
its range through the drawn zoom window; ≥ 90 % of a solid block's pixels must be the brand
background, a blurred box must show no sharp edges (≤ 0.5 % px with a grey step ≥ 40,
seams where two blurred boxes meet left out) and text recognition must read nothing there
(`redaction_missing`). A segment that cuts a band off must show, on its middle frame, the
recording without the band clearly better (3 grey levels) than the uncropped window.
`text_cut`: text recognition on output frames around each blocked or blurred box's
planned position (before it grew) must read no line overlapping it (≥ 25 % of the line's
height), and on the recording (first, middle, last frame) no line may cross an edge a
crop cuts at: `redaction_partial`, a failure. `coverage` checks the personal data itself (see *Personal data*
below): `redaction_leak`. The middle frame of each screen segment is compared with the screen
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
verify flag `redaction_missing` when the EDL itself lacks one (the cut must be explicit;
`edl check` prints the exact block to add). An unreadable redaction.json fails closed (no
approval, no render). How it is hidden is the human's choice too: job.json `redact_style`
(`job redact-style`, `videocut run --redact-style`, the app's select "Schwärzen als" in the
personal-data step and in the new-job form, `PUT /api/jobs/{id}/redact-style`; absent: the
brand's `redaction.style`, default `auto`). A reel that hid personal data in another
style is stale (job.json `cut.redact_style`; a record from before styles counts as
`block` once the style is set). Screenshots know `blur` and `block` only (`crop`/`auto`
blur them): overlays set `props.redact_style`, the renderer averages the box into cells
and draws them smoothed back up over the solid fill.

**Where personal data is: measured, not estimated** (`tools/redact.py`, `tools/_ocr.py`).
Boxes estimated by eye from downscaled stills landed a text line too high in real runs (the
name stayed readable). The agent now chooses WHICH text is personal, the tools say WHERE.
Text recognition is macOS Vision (`VNRecognizeTextRequest`, accurate level, de/en, no
language correction) called through the Objective-C runtime with ctypes: part of the OS,
no download; frames are passed in memory, nothing is written or sent. Up to 4 frames are
read at once (Vision runs outside the GIL; ~0.4 s per 4K frame on an M-series Mac, more
threads do not help). A frame of the screen recording is the one the reel shows at that
time: the last frame with pts ≤ t (+ half an output frame), found from the packet
timestamps (macOS writes frames only on change; a plain seek returns the next change),
grabbed with `-seek_timestamp 1` (the pts itself, not an offset from the file start).
`redact text` (also in the demo prep) reads the screen every 2 s and 0.2 s after every
scene change → `work/screen_text.json` [{t, camera_t, box, text, conf}] and the summary:
distinct texts with times and `suggest` (e-mail, phone, IBAN-like by pattern), plus the
text of the job's screenshots. The plan writes each candidate with `text` (required for
email/phone/name; `plan check` fails otherwise) and `box` = the whole panel it is in;
`redact measure` finds the text (approximate match: case, spacing, punctuation and a few
OCR errors tolerated; the matched part of a line via Vision's `boundingBoxForRange`,
tightened to its ink) on a 1 s grid over the candidate's range and outward from every
sighting until it is gone (two empty grid samples: one recognition miss does not end it),
halving every interval where it appears, disappears or moves by more than a quarter of
its height, down to 1/15 s. A frame that cannot be decoded (except at the very end) stops
the tool instead of counting as "not shown". Consecutive sightings become
`track` pieces {t0, t1, box (union of both ends + 0.3 × text height, ≥ 6 px), text_box,
source "ocr"}; a gap under 1 s between two matching sightings is bridged. Where the text
is not found inside the plan's range, or only outside the named area (the same text
elsewhere on the screen), the piece covers the whole `box` + 0.03 (source "area",
`measured` "partial"/"failed", `note`): fail closed. `measure_sig` fingerprints text,
box and range: a candidate edited after measuring no longer counts as measured
(`redaction.is_measured`; `plan check` fails, verify flags `redaction_unchecked`). `t0`/`t1` grow to every time
the text is shown; `range` keeps the plan's times. `sheet plan` draws the blocks of the
track at the middle of the first measured piece (`still_t`, `still_boxes`), the app shows
those boxes and the note.

**Coverage check** (verify, `coverage`): for every enforced candidate with `text`, up to 5
output frames of every screen segment overlapping its time plus one frame per second of
every screen segment (for all of them). On each, the text is searched on the screen frame
that the reel shows there; every place found, and the track's text box of that time (or
its area), is mapped through the layout and the zoom window to output px, and ≥ 98 % of
the visible part (not under a pip bubble) must be the block colour (`colors.bg`, ±28 per
channel). The reel frame's screen view is read as well: the text found there is readable. Inside a blurred
box the px count as hidden when that box shows no sharp edges; a place in a cropped band
maps outside the view.
Either flags `redaction_leak` (verify not ok) with output time, segment and candidate id.
`redaction_unchecked` (not ok either): no text recognition, a reel or screen frame that
cannot be read, an e-mail/phone/name candidate shown without `text`, or a text candidate
not measured for its current text and area. The reel is decoded once and streamed (32
frames at a time). Screenshots: the text found on the image must lie inside a redact box
of every `screenshot_frame` item that shows it (`props.redact` plus the measured boxes
overlays adds); the overlay itself is not read back from the reel.

**Screenshots only.** `job new --asset FILE...` takes many images; `sheet assets` puts them
on one numbered contact sheet (≤ 1080×1920, with sizes and orientation in the summary), so
the plan can match each screenshot to its sentence with one image and propose
`screenshot_frame` overlays with focus and redaction boxes.

## Slides (`tools/slides.py`, `renderer/slides/`, `renderer/lib/slide.js`)

work/slides.json `{"slides": [{id, template, content, source: auto|upload, why}]}`; ten
templates with text limits (`slides.LIMITS`): title, bullets, big_number, compare, steps,
timeline, bar_chart, quote, cta, image (an uploaded page `input/slides/page_NN.png`,
letterboxed with a 5 % push-in). The renderer draws every template from the brand tokens
only (colours, fonts, spring; `muted`/accent text only where its contrast holds) and lays it
out at the largest of the scales 1.3 … 0.6 that fits its content box; text is never shrunk
below 60 % and never clipped (the layout report says `fits: false` instead). Build steps
(bullets, compare, steps, timeline, bar_chart) appear at the segment's `reveal` times, else
0.5 s + 0.7 s per step. `slides check` validates slides.json, its assets and the EDL's slide
fields (ids, reveal counts over all segments of a slide) and warns on reading time.
`slides stills` renders the final state per slide (layout and moment from the EDL, else
plan.json, else `slide`; the speaker from the camera frame). `slides render` (also called by
`render`) writes per slide id one opaque ProRes 422 HQ clip at output size and fps, as long
as all its segments together: a slide that spans a cut keeps its clock (`_layouts.slide_runs`
trims each segment's part from the running total). A clip is reused while its spec (content,
layout, size, fps, length, reveal times, brand tokens, language, renderer code) matches; its
SHA-256 sits outside the job folder (`state/<job>/slides_render.json`), so verify believes
only clips it wrote.

Geometry (`_layouts.slide_geometry`, all four formats): `slide_full` fills the frame (content
inside the safe zones, no camera); `slide` shows the speaker as a round bubble (size and corner
from the optional brand token `pip {corner: left|right, size: share of the width}`, default
the pip inset) above the bottom safe zone, with two candidate content boxes (above / beside
the bubble; the renderer takes the one with the larger scale); `slide_split` puts the slide
beside (16:9) or above (portrait, square) the speaker part from the split geometry, face
tracked like `split`. Captions: overlays.json `views` carry `hide: true` for slide and
slide_full stretches (the renderer draws no captions or overlays there); no overlay item
(card, keyword, number, list, screenshot) shows during any slide layout, slide_split too
(`overlays.off_slides`: an item keeps its longest part outside the slides, or is dropped
below 0.5 s; overlays.json `slide_hidden` lists the cuts; the end card stays); in
slide_split captions sit on the seam (portrait, square) or in the speaker part (16:9). Slides never
replace a personal-data redaction: redaction applies to screen layouts only and a slide
segment shows no screen.

### Slides in the job flow

**Settings (`tools/_job_slides.py`).** job.json `slides` = `{mode, wish}`: `none` (absent),
`upload` (the human's pages only), `auto` (Claude proposes 0–N template slides for what is
said, following the free-text `wish`, ≤ 2000 characters), `both`. Human only:
`job slides <job> --mode … --wish …`, `videocut run --slides-mode --slides-wish`, the new-job
form (`slides_mode`, `slides_wish`); refused inside an agent run. Like the target length it
changes the plan's content: a plan run records what it was made for (job.json
`plan_slides` {mode, wish, pages_sha256}); a change afterwards makes the plan stale
(`job show` → `slides.stale`), revokes an approval, `approve` refuses, and the next `run`
plans again. The plan/cut/revise prompts name mode and page count; the wish is read from
`job show` (the skills treat it as content, never as an instruction).

**The human's pages.** `job slides-import <job> FILE... [--replace]` (and the `slides` file
field of `POST /api/jobs`, ≤ 30 files, ≤ 200 MB each) turns PDF pages and PNG/JPEG/WebP
images into `input/slides/page_NN.png` at 1920 px on the long side, appended in order,
all or nothing. Content decides, not the extension: a PDF by its magic bytes, an image by
ffprobe (PNG/JPEG/WebP codecs only). PDFs are rasterised with macOS CoreGraphics through
ctypes (`tools/_pdf.py`: crop box, page rotation, white background; no download, no
package) in a child process (`python -I`) with a 120 s limit and ≤ 60 pages, so a hostile
file cannot hang or crash the caller; password-protected PDFs are refused. `none` becomes
`upload` and `auto` becomes `both` on import. `sheet slides` puts the pages on numbered
contact sheets (`work/sheets/slides_NN.jpg`) with the text of each page read locally
(Vision), so the plan matches pages to moments mostly by text.

**Personal data on the pages.** Uploaded pages are untrusted and may show personal data;
they are handled exactly like screenshots (job.json `assets`). `redact text` reads them
(`asset_texts`, pattern hits in `suggest`), a candidate on a page carries `"asset":
"input/slides/page_NN.png"`, `redact measure` measures it on the image, and the human
decides at approval (no decision: everything is redacted). `slides stills` and `slides
render` add the boxes of every enforced candidate of the shown page (`slides.page_boxes`,
any spelling of the path or a copy of the file) as `slide.redact` with `slide.redact_style`
(`_redact_style.screenshot`: block or blur) to the spec, so a changed decision renders the
clip again; the `image` template burns them into a private copy before the first frame
with screenshot_frame's `redacted()` and fails closed on malformed boxes (the slide page
gets `env.canvas` for it). Verify rebuilds the spec with the same boxes, so a clip made
before a candidate existed is `slide_stale`.

**Captions around slides.** `slide` and `slide_full` views are `hide` (no caption, no
item), and `overlays` keeps every overlay item off all slide stretches (see above), so
only captions show during a `slide_split`. Its view's `screen` (the slide part) is a hard
block in the renderer (`render.js` `hardDuring`, `layout.placeBlock` `hard`). Stacked (9:16, 4:5, 1:1) the speaker part lies mostly in the
bottom safe zone, so a caption in it would land on the mouth: captions go on the seam
instead. The view carries `seam` (output y of the boundary, `_layouts.slide_seam`) and
`content` (the slide's content box); the caption scene asks for `seamDuring` and
`placeBlock` position "seam" centres the block on that y (pushed down below the content
box if it reaches lower), with only the content box hard and the face counted from the
brow line down (`FOREHEAD` = top 25 % of the face box: hair and forehead may be covered,
eyes and mouth not); when the face still gets in the way it falls back to below the face.
A caption group on screen across the start or end of such a view is cut there by its word
times (`lib/seams.js` `splitAtSeams`; a part under 0.4 s gives its words to its neighbour),
so the part in the face layout is not left on the seam over the face.
`slide_geometry` keeps `SLIDE_SEAM` of the short side (12 % in tall frames, 9 % in 4:5
and 1:1) free above the seam for the block's upper half; every template still fits its
smaller box at >= 60 % (renderer tests). Verify judges a caption whose box crosses a
view's seam on the content box (`overlay_on_slide_content`, which also flags any other
item touching it) and on the face below the brow line, not on the whole slide part
(`overlay_on_screen` stays for cards there). Side by side (16:9) nothing changes.

**Plan.** The plan skill writes `work/slides.json` (`{slides: [{id, template, content,
source, why}]}`, checked by `slides check`) and `plan.json` `slides` (the same plus `t0`/`t1`,
the source time of the words a slide illustrates, and `layout`); `plan check` validates the
copy (`slides.content_errors`: templates, text limits; ids unique; inside a keep row; no two
at once; mode none → none, upload → only `image` with an uploaded page, auto → no `image`;
`views` never take a slide layout: slides go in `slides`)
and that it repeats `work/slides.json`. `slides stills` writes the previews the plan gate
shows (`work/slides/stills/`, served by the stills route). Content comes from the
transcript or the wish only; overlay ideas never duplicate a slide.

**Cut and guards.** The cut skill turns each slide into EDL segment fields (`slide`,
`layout` `slide|slide_split|slide_full`, `reveal` on the words), runs `slides render`
before `render`. `run` fails a cut run whose EDL shows a slide not in the approved
`plan.json`, or whose `work/slides.json` changed an approved slide's template or content
(`run.slides_kept`; a plan approved as markdown only is not compared), and an overlay
run that touched `work/slides.json`. Revise may change, move or remove slides (the
feedback is the authority); `work/slides.json` is backed up and restored with the other
revision files.

**App.** New job: "Folien & Animationen" (Keine / Eigene hochladen / Claude erstellt sie /
Beides, files, "Was soll gezeigt werden?"). Plan gate: one card per slide (still, template
in plain words, time and the keep row it belongs to, layout), "Folie entfernen" and a
per-slide change, both `POST plan/feedback {text, slide}` (the id goes into
`work/plan_feedback.json` `slide`; an unknown id is a 400). Job detail `slides` = `{mode,
wish, pages, stale, marks}`; `marks` are the slide moments in output seconds from the EDL
(consecutive segments of one slide merged), drawn as a strip under the result player.

## EDL (`src/videocut/edl.py`)

Schema from BRIEF §6. Segment times (`in`, `out`, `zoom_keys`) are source time, everything else
output time. `validate()` returns every problem with a JSON path (`$.segments[2].out`); it checks
`in < out`, segments within the source duration (from `work/probe.json`), crop `cx/cy` in 0..1,
zoom 1.0..2.0, `fade_ms` 0..200 and that all file paths stay inside the job folder. Product
demos: `layout` (face|screen|pip|split), `screen_zoom` [{t, box}] (increasing t inside the
segment, boxes 0..1 of the screen) and `redact` [{t0, t1, box}] inside the segment, both
only on screen layouts (`SCREEN_LAYOUTS`); with a job, every screen-layout segment must lie
inside the sync overlap (`overlap_errors`). Slides: `layout` slide|slide_split|slide_full
(`SLIDE_LAYOUTS`, any job type) needs `slide` (an id of work/slides.json) and may carry
`reveal` [s] (increasing, counted from the segment's `in_src`, within the segment); one slide
keeps one layout in the whole EDL (`slide_layout_errors`). edl.py is the only reader of these fields (render, overlays and
verify get them from `edl.Segment`). Captions,
overlays, b-roll and music are validated loosely until their phase. `output_duration()` and
`cut_points()` (output time of every segment boundary, including 0 and the end) serve render
and verify.

## Plan as data (`work/plan.json`, `tools/plan.py`)

The plan run writes `work/project.md` (the authority, what the human approves) and its twin
`work/plan.json` for the app: `title`, `format`, `hook {text, src}`, optional `core_message`,
`target_s`, `keep_list [{n, src, text, why}]` (output order, source seconds), `views [{n, src,
layout, zoom_box?, zoom_t?, why, still}]` (demos), `overlays [{n, src, scene, text, why}]`,
`personal_data` (repeats `work/redaction.json` exactly), `risks`, and after plan feedback
`change_note` (what the feedback changed, in a few words). `videocut tool plan check`
validates it against the job (format, source duration, layouts, redaction candidates);
`videocut tool sheet plan` writes downscaled stills of the views and personal-data
candidates to `work/plan_stills/` and their paths (relative to the job folder) back into
`still`. A plan without plan.json still works: the app shows the markdown.

**Plan feedback** (`videocut run <job> --plan-feedback TEXT`, `POST /api/jobs/{id}/plan/feedback`):
a plan run in revise-the-plan mode. The text goes to `work/plan_feedback.json` (removed after
the run); the skill changes only what the feedback asks and adds a "Changed after feedback"
line. The approval is sha-bound, so a changed plan needs a new approval (the API also revokes
an existing one before the run starts).

## Material (B-roll and generated stills)

`tools/_job_material.py`, `tools/material.py`, `tools/_material_providers/`,
`server/material.py`, `app/src/components/Material.tsx`.

**Setting.** job.json `material` = `{mode, wish}`: `none` (absent), `stock` (stock video and
photos), `generated` (AI stills), `both`; the wish ≤ 2000 characters. Human only (`job
material`, `run --material-mode/--material-wish`, the new-job form, whose default is
settings `material_default_mode`); refused in an agent run. Like slides it changes the
plan: a plan run records `plan_material`, a change makes the plan stale and revokes an
approval. Settings also hold `image_provider` (none | gemini (default) | openai | local),
`stock_providers` (pixabay, pexels, coverr, openverse; default all four in that order:
Openverse needs no key, so the chain finds stills without any key, but its quality is mixed
and it comes last) and
`material_budget_usd` (default 1.00 per job). Keys live only in the keychain, service
`videocut`, accounts `pixabay-api-key`, `pexels-api-key`, `coverr-api-key`,
`gemini-api-key`, `openai-api-key` (`settings.set_secret/secret/has_secret`; `api_key()` /
`set_api_key()` are the wrappers for `anthropic-api-key`); the API takes them write-only
(`PUT /api/settings/secrets`, GET says only which are stored).

**Plan (agent, no network).** The plan skill writes `material_wishes` [{n, kind
broll|still|icon, src_from, src_to, query, prompt, style photo|illustration, why}] into
plan.json and a *Material* table into project.md. `plan check` (`material.wish_errors`):
none for mode none; at most one wish per 8 s of the plan's target (else estimate, else the
keep rows' sum); each span ≥ 1 s inside one keep row; broll only with stock, icon only with
a generator; a 2–8 word stock query where stock is searched, a prompt (≤ 600 characters)
where one is generated. The human approves the wishes with the plan.

**Fetch (host).** After the approval, `videocut run` (or `POST /api/jobs/{id}/material/fetch`,
a queued run of kind `material`) calls `material fetch`: for broll/still wishes the stock
providers in settings order (Pixabay videos then photos: `lang` from the job language,
`safesearch`, `min_width`/`min_height` 1280 on the long side; the video endpoint has no
orientation filter, so hits are kept by their renditions' aspect (portrait h > w, landscape
w > h, square within 10 %), photos use `orientation` vertical/horizontal and download as
`largeImageURL`, long side at most 1280 px, unless the key has full API access
(`fullHDURL`/`imageURL`), so a Pixabay still may be upscaled on 1080p output and the
broll_aspect/verify rules decide; search answers are cached 24 h in
`$VIDEOCUT_HOME/cache/pixabay/<sha256 of the parameters>.json`, as Pixabay requires, and a
429 after the retries reads "rate limit (100 requests per minute)"; Pexels videos then
photos with `orientation` from the job format, `locale` de-DE/en-US from the job language,
`size=medium`; Coverr videos filtered by `is_vertical`; Openverse photos only, no key:
`GET api.openverse.org/v1/images/` with `q`, `license=cc0,pdm,by` (never SA/ND/NC: the
picture is cropped and recoloured and the video may be commercial), `aspect_ratio`
tall/wide/square, `size=large`, `mature=false`, User-Agent `videocut/<version> (repo URL)`
also on the download from the source site; hits that are not jpg/png/webp, under 800 px on
the long side or not HTTPS are skipped; the licence becomes "CC0 1.0" / "Public Domain Mark
1.0" / "CC BY <version>", the API's full credit sentence is kept as `attribution_full` and
the licence link as `licence_url`; hits reported at ≥ 1280 px come first; one retry on a 5xx, and a 429 after the retries reads
"rate limit (20 searches per minute, 200 per day without a key)"), up to 4 candidates, each downloaded in the smallest rendition whose short
side reaches the output's, cut to 30 s without re-encoding, probed (a photo under 800 px on
its long side as probed is dropped and the next hit tried; under 1280 px the candidate gets
`small: true`, shown as "will be upscaled"), thumbed (≤ 480 px) and hashed; for still/icon wishes with mode generated/both the image provider makes up to 2
(prompt + style words + the brand's colours as names, never hex codes, which models draw
as text + "no text, no letters, no logos" last; Gemini: Interactions API,
model `gemini-nano-banana-2.1`, `response_format` aspect ratio and 1K/2K; OpenAI: Image API,
`gpt-image-2.5-flare`, a 16-px-aligned size of the output aspect, quality medium). The
generator's file is stored byte for byte (SynthID / C2PA intact; `source_sha256`). Cost
guard: the estimate (published per-image prices: Gemini 2K $0.0504, OpenAI ~$0.06) plus what
the job spent must stay within the budget, otherwise generation is refused with a plain
message and the stock part still runs. Every fetch appends to material.json `runs` and to
`work/runs.jsonl` (`phase: material`). The HTTP helper refuses non-HTTPS and any call under
`VIDEOCUT_AGENT=1`, waits on 429 (Retry-After, else 2^k s; 3 times), and scrubs key-like
strings and URL queries from errors. Providers share one registry
(`register_provider/get_provider/providers`); the local generator registers itself as
`local`. Offline run: `VIDEOCUT_MATERIAL_FAKE=1` (or `fetch|generate --dry-run`) swaps in
`_material_providers/fake.py` (`fake-stock`: moving FFmpeg gradients and stills in the job's
orientation; `fake-image`: a still in the brand's first colour, AI-generated, $0), so the
whole chain runs without keys or network; never listed in the settings, and the candidates
say `licence: test material`.

**Choice (human).** `material choose <job> <n> <id|none>` / `POST material/choose` writes
`$VIDEOCUT_HOME/state/<job>/material_choice.json` (candidate, file, SHA-256, the wish's
signature n/kind/span) and mirrors `chosen`/`decided` into material.json. What was fetched is kept
outside the job folder as well (`state/<job>/material.json`, the trusted copy;
`work/material.json` is its mirror), so the candidates, their files, hashes and the AI flag
come from the host, never from a file an agent run may edit. `material.chosen()`
counts a choice only while the wish is still in the approved plan.json and the file still
has that hash, so an agent run that edits material.json or swaps a file gains nothing.
`videocut run` stops with exit code 5 (gate `material`, stage `material`, status `waiting`)
while a wish is open, printing `material show` and the choose command; `--material-skip`
chooses none for every open wish. The cut record carries `material_sha256` (a changed choice
makes the cut stale). A Coverr pick is reported to Coverr's download counter (its API terms).

**App.** New job: "Material" (Keins / Stock-Material / KI-Bilder / Beides, wish). Settings:
section Material (default mode, generator — "local" only when registered, disabled with its
reason when not available —, stock libraries, cost limit, the four keys write-only). Plan
view: the wishes. Material screen (between approval and cut): per wish the why, candidates
as thumbs (videos play muted on hover/click), licence (linked to `licence_url` when there is
one), attribution and source link, a "KI" marker on
generated ones, choose / "Kein Material" / "Neu suchen" (edited query) / "Neu erzeugen"
(edited prompt), progress while fetching, cost against the budget, then "Weiter zum
Schnitt" (`/start`). Candidate files are served only from `work/material/<n>/c<k>[_sm].<ext>`.

**B-roll in the cut.** EDL layout `broll` (`edl.BROLL_LAYOUTS`) is a normal segment in
camera time with `"material": <wish n>`; it shows the material the human chose for that wish
full frame while the voice goes on. `edl check` refuses a wish without a valid choice
(`material.chosen`: the record outside the job folder and the file's SHA-256), a segment
outside the wish's `src_from`–`src_to`, b-roll in the first 2 s of the output (the hook), and
more than 40 % of the output in b-roll. Render plans each b-roll range from the same choices
(`_broll.plan_broll`; without one it refuses) and draws it with `_layouts.compose_broll`:
centre-cropped to fill the output aspect (never letterboxed); images get a 5 % Ken Burns
push-in (zoompan on a 2× upscale); clips start at their beginning, muted (the audio graph is
unchanged); a clip shorter than its range by less than 1 s loops (`-stream_loop`), a longer
gap lets the clip end and the face-framed camera shows for the rest (`overlay
eof_action=pass`). HDR clips are tonemapped like the camera; everything is converted to the
reel's matrix in limited range. Material inputs are extra ffmpeg inputs (indexes counted by
`-i`). render.json `broll` lists `{segment, wish, candidate, file, kind, ai_generated, style,
source, model, out_t0, out_t1, looped}` (`out_t1`: the end of the shown material); the AI
label and the disclosure are built from it. Captions: overlays.json `views` carries `{layout:
"broll", band: "lower"}` per b-roll range; the face track has no samples there and the
renderer puts captions in the lower band while such a view is on screen (`env.bandDuring`).
Verify (`_verify_broll.py`) reads the middle frame of each stretch: `broll_black` (mostly
black), `broll_aspect` (flat dark bands on two opposite edges), `broll_unreadable`; a broll
segment counts as showing no camera for `blink_at_cut`. `videocut tool sheet material` puts
the candidates' thumbs on `work/sheets/material_NN.jpg`, labelled `<wish>/<id>` (+ "AI").
The cut skill uses only chosen material, only inside its wish's span, never over the hook's
first 2 s, at most 40 % of the reel.

## Revision runs (`run.revise`, skill `revise`)

`videocut run <job> --feedback TEXT [--at S]` (and `POST /api/jobs/{id}/feedback`) revises the
finished reel without a new approval: the human's feedback is the authority for these changes.
`--at` is an output second; `videocut tool edl locate <s>` maps it to the segment, source
time, layout, zoom key and overlays shown there (`work/revision_request.json`). The skill
changes only what the feedback asks (segments, zoom keys, layouts, overlays, caption text),
renders and verifies. Before the run, `out/reel.mp4` is copied to `out/history/reel-<k>.mp4`
(k counts on, the last five are kept) and the files a revision may touch are backed up
outside the job folder (`state/<job>/revision_backup`). The run fails, and everything comes
back, when it changed `project.md`, the personal-data candidates or decision, dropped a
redaction that is still in the reel, ended without a full render of the current EDL, lost the
overlays, or used source time outside the approved plan without `work/revision.json`
`extends_plan` quoting the feedback words that ask for it. Every run appends to
`work/revisions.jsonl`: `n, time, text, at_s, located, ok, changed, extends_plan, asked,
summary, verify_ok, cost, turns, wall_s, previous_reel, error`.

Versions: version 1 is the reel of the approved plan, each successful revision makes the next
one. `previous_reel` is the reel *before* revision n, so the reel revision n made is the next
successful revision's `previous_reel`, or `out/reel.mp4` for the latest. `GET /api/jobs/{id}`
reports this as `versions [{version, revision, text, video_url, current}]` and on each
revision `version` + `video_url` (null once out/history dropped the file).

## Local app (`videocut ui`, `src/videocut/server/`, `app/`)

`videocut ui` starts uvicorn on `127.0.0.1:4300` and opens `http://127.0.0.1:4300/?t=TOKEN`.
The UI is a static React build served from `src/videocut/web/dist/` (`videocut setup` and
`./start.sh` build it with the bundled node: `npm ci`, `npm run build`, rebuilt when a source
file or the lock file changed); without it, every page explains how to build it.

Routes (prefix `/api`): `session`, `types`, `brands` (list with the `default` flag, read,
validated edit, create from the neutral example, delete, logo/font upload with `role`, cached
preview sheet, `theme` preview, `assets/<file>`), `theme`, `settings/brand` (see "Default
brand and app theme"), `jobs` (list,
create by multipart upload, detail, delete to `$VIDEOCUT_HOME/trash`), `jobs/{id}/start`,
`events` (server-sent events: `stage` (with `at`, the stage's start, ISO), `log`, `tool`,
`gate`, `error`, `done`; replayed on connect, so a reloaded page keeps the stage's start),
`cut-style` (PUT), `target` (PUT), `recut` (POST: cut + overlays again with the approved plan),
`vocabulary` (GET: job/brand/effective terms, applied counts, suggestions; PUT: the job's
terms, validated), `vocabulary/apply` (POST), `transcript` (GET, PATCH {i, text, t0?}), `transcript/undo`
(POST), `captions/refresh` (POST: the tool-only caption refresh run, only while the
overlays are stale), `overlays` (POST: the overlay stage again on the
kept cut, only while `overlays_stale`), job create takes a `vocabulary` field (JSON), `plan` (GET/PUT, sha-checked), `plan/feedback`, `approve`, `feedback`, `stills/<path>`,
`media/{reel|preview|cover|screen_proxy|camera_proxy|history/reel-<k>.mp4}` (HTTP Range),
`download`. Errors are `{"detail": {"message", "code"?, "errors"?}}`; 409 codes: `busy` (a
run is queued or active), `stale` (the plan changed since it was read), `state`, `exists`,
`read_only`.

Runs: one at a time (`runs.py`, a queue and one worker thread). A run calls the same
`run.run(...)` as the CLI with `say` bound to the job's event channel (thread-local stdout
capture keeps tool output of parallel requests apart) and an `ask` that never answers yes:
a run stops at the plan gate (exit code 3 → gate `plan`) and the UI answers through
`/approve`. Status/stage of a job come from its files plus the channel: status `new |
running | waiting | done | error`, stage `prep, plan, approval, cut, overlays, verify, done,
revise, plan_revise, error`, gate `plan | result | null`. The job detail also carries
`stage_since` (start of the running stage), `failed_stage`, `has_screen`, `cut_style`
(stored or `auto`), `cut_style_resolved`, `cut_stale`, `source_duration_s`, `lang`,
`target_s`, `target_resolved`, `plan_target_stale`, `plan_target_s` and `overlays_stale`
(captions changed by a vocabulary or a transcript edit since the overlays were rendered),
`captions_stale` (by a transcript edit: the app offers the refresh without Claude); the
app's "What is happening" line counts the time in the step from `stage_since`.

Security model:
- bound to 127.0.0.1; a request whose Host header is not `127.0.0.1:PORT`/`localhost:PORT`
  is refused (DNS rebinding);
- the per-start token is only in the link: `GET /?t=TOKEN` sets an HttpOnly
  `SameSite=Strict` session cookie and redirects (303) to `/`; every other request needs the
  cookie (scripts and tests may send `X-Token`); the token is never in JS;
- writes (POST/PUT/PATCH/DELETE) by cookie also need `Origin` = the app's own origin;
- known limit: cookies are not port-bound, so another server on `127.0.0.1`/`localhost`
  that the browser visits receives the session cookie (a local program could replay it);
  the cookie dies with the `videocut ui` process (a new value per start);
- CSP `default-src 'self'` (no inline script, no CDN), `nosniff`, `frame-ancestors 'none'`,
  `no-referrer`, `Cache-Control: no-store` on the API;
- uploads stream to a staging folder with a size cap, are checked by extension and ffprobe
  (videos need a video stream, the camera an audio stream; images must probe as PNG, JPEG
  or WebP), get a plain file
  name and enter the job through `job new` like a CLI file; brand files are checked by magic
  bytes; nothing uploaded is executed;
- stills only from `work/plan_stills/`, `work/sheets/`, `out/verify/` (plain path parts,
  image extensions, symlinks resolved and confined); media only the fixed outputs and
  `out/history/reel-<k>.mp4`;
- the human decisions (approve, the personal-data decision, feedback) only come from these
  authenticated requests. The approval and the decision are written outside the job folder
  (`$VIDEOCUT_HOME/approvals/`, `$VIDEOCUT_HOME/state/<job>/redaction_decision.json`) by the
  server, exactly like the CLI; an
  agent run cannot reach them (no network, writes only `./work/**`, `VIDEOCUT_AGENT=1` makes
  `job approve`/`job redaction` refuse).
- the approval binds `work/project.md` and the `work/plan.json` the human saw
  (`plan_json_sha256`); afterwards the app shows plan.json only while it is that one, and
  the revise guard reads the approved keep list only from it. A human edit of the markdown
  drops plan.json (the markdown is the plan then). `/feedback` and `revise` need the
  recorded cut of the approved plan; a new cut moves `revisions.jsonl` and `out/history/`
  aside (`*-before-<time>`), so versions always belong to one plan;
- uploads are judged by content: ffprobe with `-protocol_whitelist file`, and the
  container must be a video container (MOV/MP4, MKV/WebM, AVI) or an image; staging
  folders a killed server left behind are removed at start.

`app/`: screens Home (job list), NewJob (type cards, drag & drop with upload progress, brand,
format, one-line instruction, collapsible terms for this video), JobPage (stepper, live log, time, cost), PlanReview
(structured plan with stills or the markdown, editor with stale-sha handling, plan feedback
with the change highlighted), PersonalData (redact everything by default, per item choice),
Result (player, verify findings in plain words, feedback at the player position, versions,
MP4/cover download), Brands (preview sheet per format, token editor with the server's
validation, logo/font upload, new brand from the neutral example, Wörterbuch: terms with
their misspellings as chips). The job page has a collapsible "Begriffe für dieses Video"
panel (save and apply, suggestions added to the brand's Wörterbuch when the term is the
brand's, else to the job) and the "Untertitel neu setzen" banner (`components/Vocabulary.tsx`). The "Transkript" panel
(`components/Transcript.tsx`) lists the words as sentences with times (only what the cut
keeps unless "Auch herausgeschnittene Stellen"), edits a word inline (Enter saves, Esc
cancels; the result player seeks there through a window event), marks edited words (hover:
the original), "Immer so schreiben" adds term = new text / variant = old text to the brand's
Wörterbuch (the job's terms without an own brand) and applies it, search, undo; after edits
the "Untertitel neu setzen" banner runs `captions/refresh`. One plain button per word,
sentences memoized, so ~1500 words stay fast. German default, English
switch (`app/src/i18n.tsx`); verify flag and check ids map to plain words, unknown ids show
as they are. `npm run dev:mock` runs the UI against an in-memory mock of the API.

### Default brand and app theme (`theme.py`, `server/theme.py`, `app/src/theme.tsx`)

`settings.json` holds `default_brand` (a brand id or null = neutral) and
`theme_follows_brand` (default true). `settings.default_brand()` reads a brand whose folder
is gone as null; deleting a brand (`DELETE /api/brands/{id}`, the example is read-only) clears
the setting; renaming changes only `name`, the id stays, so the setting follows. The new-job
form preselects the default brand ("Neutral (ohne Marke)" stays an option); the Brands screen
sets it ("Als Standard", badge "Standard", "Kein Standard (neutral)"), and so does Settings
("Marke und Aussehen"). `videocut doctor` lists it (informational, never a failed install).

`GET /api/theme` and `PUT /api/settings/brand` (only the fields sent change) answer
`{brand_id, name, css, logo: {light, dark}, notes, invalid, settings}`; css is empty while the
UI is neutral (no default, the switch "App-Design an Marke anpassen" off, or a brand with
errors: `invalid`). `theme.build(tokens)` maps the resolved brand tokens onto the CSS custom
properties of `app/src/styles.css`:

| brand.json | CSS token |
|---|---|
| `colors.bg`, `surface`, `text`, `muted` | `--bg`, `--surface`, `--text`, `--muted`; `--surface-2`, `--line`, `--line-strong` mixed from surface towards text (5/14/26 %) |
| `colors.accent` | `--primary`, `--focus`; `--primary-text` black or white, whichever reads better |
| `fonts.display` / `fonts.body` | `--font-display` (headings, product name) / `--font`; a file becomes `@font-face` served from `/api/brands/{id}/assets/…`, a name falls back to that family, then the system font |
| `logo.light` / `logo.dark` | the header logo (28 px high) for a light / dark page; none: the text "Video Cut" |
| — | status colours (ok, warn, error, redact, change, info) stay neutral: the light or dark set by the brand background |

Dark mode: a dark brand (bg luminance < 0.18) keeps its palette in both schemes; a light
brand keeps the neutral dark tokens in the dark scheme and brings only its accent (when it
reaches 3:1 there), its fonts and `logo.dark`. `data-theme="light"` still forces light.
Contrast guard (WCAG 2.x, `brand.contrast`): text and muted need 4.5:1 on bg and surface, the
accent 3:1 on bg; a failing token falls back to the neutral one (black/white for a mid-tone
background; when bg and surface are too far apart for one text colour, surface follows bg),
and each fallback is a `note` the Brands screen shows in plain words. No radius field exists
in brand.json, so `--radius` stays. The rules are `html:root` (outranks `:root`) and
`html:root:not([data-theme="light"])` in a dark media query (outranks the neutral dark block).

The served `index.html` carries the stylesheet as `<style id="brand-theme">` and the view as
`<script type="application/json" id="brand-theme-data">` (`<` escaped), so a reload paints the
brand at once; `ThemeProvider` keeps that element current after a click. The brand editor's
"Vorschau auf der Oberfläche" posts the unsaved brand to `POST /api/brands/{id}/theme` (also
the source of its contrast hints) and shows it app-wide until the editor is left. Brand assets
are served from the brand folder only (svg/png/woff2/ttf/otf), SVGs with a CSP that runs
nothing. The renderer's brand contract is unchanged; this is the app shell only.

End to end through the browser: `VIDEOCUT_E2E=1 .tools/bin/uv run python
tests/e2e/app_e2e.py` (real Claude runs on generated clips in a throwaway `VIDEOCUT_HOME`,
screenshots per step; not in CI).

## Install, launcher, first start (Phase 4)

Distribution: no signed app, no binary release. A person asks their own Claude Code to
install Video Cut; it follows `.claude/skills/install-videocut/SKILL.md` and `INSTALL.md`.
Nothing is written into `~/.claude`: later requests ("Video Cut startet nicht") are handled
from `~/VideoCut/app`, where Claude Code reads the project's `CLAUDE.md` and skill (INSTALL.md
tells a helper in another folder to `cd` there first). Sign-in happens only in the app's
first-start screen, never in a terminal. Measured (`docs/fresh-install-report.md`): about
2.5 GB downloaded, about 3.9 GB on disk, `start.sh` about 5 minutes on fast broadband, a
Claude-driven install 3-4 minutes plus the model download, no question to the person. Code in `~/VideoCut/app`, data in `~/VideoCut`. Nothing needs the Xcode command
line tools, Homebrew, Rosetta or sudo: start.sh uses `curl`, `shasum`, `tar`, `plutil`,
`sw_vers`, `sysctl`, `df`; the code is fetched as a GitHub tarball (on a fresh Mac `git`
and `python3` are stubs that open the developer tools installer).

`start.sh` steps: 1 this Mac, 2 uv, 3 Python + packages (CPython from
`manifest/tools.json` `python`: curl + SHA-256 into `.tools/downloads/python-mirror/`, then
`UV_PYTHON_INSTALL_MIRROR=file://… uv python install`, `UV_PYTHON=<version>`,
`UV_PYTHON_DOWNLOADS=never`; `uv sync` with `UV_NO_DEV` unless `--dev`, `VIDEOCUT_DEV=1`
or a git checkout; retried, then `uv cache prune --ci` with a 5 s lock timeout), 4 tools, 5 renderer + Chromium, 6 app
build, 7 models (the renderer's Chrome headless shell is a pinned manifest entry too,
`chromium-headless-shell`: fetched and verified by `install.py`, unpacked with
`/usr/bin/unzip` into `.tools/ms-playwright/chromium_headless_shell-<revision>/` with
playwright's marker files; the revision must equal playwright-core's `browsers.json`;
`install.py` prints 4-7 with sizes from `manifest/tools.json`
`approx_size_mb` / `approx_size_gb` and a time at 100 Mbit/s). Finished steps are recorded
in `.tools/installed.json`; archives download to `.tools/downloads/*.part` and resume
with HTTP Range (`download.fetch`, 4 attempts; a checksum mismatch deletes the part).

Launcher: `~/Applications/Video Cut.app` = `Contents/Info.plist` (`LSUIElement`),
`Contents/MacOS/videocut-launcher` (bash: `.venv/bin/python -m videocut.cli open`, an
`osascript` alert with "ask your Claude Code" on failure), `Contents/Resources/icon.icns`
(generated PNGs: a grey square with a play triangle). Made locally, so no quarantine and
no Gatekeeper check. `videocut open` reads `$VIDEOCUT_HOME/state/server.json` (pid, port,
this start's link; 0600, removed at exit), checks the server with `X-Token`, else starts
`videocut ui --no-open` detached (log `state/server.log`) and opens the link. The token
on disk is readable only by the same user, who can already reach the app; it changes per
start. Port: `--port`, `VIDEOCUT_PORT`, 4300, then the next free one.

The `claude` program (`claude_cli.exe`): PATH, then `~/.local/bin`, `~/.claude/local`,
`/opt/homebrew/bin`, `/usr/local/bin`, `~/.npm-global/bin`, then the Claude desktop app's own
copy (`~/Library/Application Support/Claude/claude-code/<version>/<id>/claude.app/Contents/MacOS/claude`,
newest version). A missing `USER`/`LOGNAME` is filled in (without it the program reports
"not signed in"). Runs set `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`; local mode adds
`--no-bare` when the installed program lists it (not in 2.1.301).

First start: `GET /api/setup/status` → `done` (`settings.json` has `setup_done` or an
`auth` chosen with `videocut auth`), `mode`, `api_key_stored`, `claude` (the background
detection: `claude --version` and `claude auth status --json`, cached 60 s, `?refresh=1`;
only `loggedIn`, `authMethod`, `email`, `subscriptionType` are used, for display, never
stored), `console_url`. `POST /api/setup/auth {mode, api_key?}` stores a key in the
keychain (`settings.set_api_key`, as `videocut auth key`) and never returns it.
`POST /api/setup/test` runs `claude -p` once in a temp folder with no tools, one turn,
`--model haiku` (about USD 0.001), the same auth mode as a job run. `POST /api/shutdown`
(`{force}`; 409 `busy` while a run is queued or active) sets uvicorn's `should_exit`.
The app shows the first-start screen until `done`, then "Dein erstes Video" tips; the
settings page (`#/settings`) shows the mode and switches it; "Beenden" in the top bar.

`videocut doctor [--json]`: every check has `name, ok, detail, required, fix`; `fix` is
empty when ok. `videocut update`: see `update.py`; it downloads first and only then stops
the app (`videocut stop [--force]`, refused during a run), refuses to overwrite a git
checkout, and keeps the previous code (without its tools) as `app.old-<time>` until
`start.sh` succeeds. The API key reaches the keychain through `security -i` on stdin,
never as a command line argument.

Human gate on the command line: `videocut run --approve` and `--demo-data` are the person's
decisions. `run.human_flags_refused` refuses them inside an agent run (`VIDEOCUT_AGENT`) and
whenever stdin is not a terminal, unless `VIDEOCUT_UI_SERVER=1` (set by `videocut ui` for its
own process; the app's decisions come through its authenticated requests; `agent.command`
removes it from agent runs). A helper such as the person's Claude Code, which runs commands
without a terminal, therefore cannot approve a plan; the install skill also forbids cutting.

## Local image generation (optional)

`tools/_material_providers/local_flux.py`, provider name `local` (`settings.json`
`image_provider: "local"`, `get_provider("local")` in `_material_providers/__init__.py`):
generated stills for a person without any API key, made on the Mac and never sent anywhere.
Model: FLUX.2 [klein] 4B (Black Forest Labs, Apache-2.0), the distilled 4-step variant, run
by mflux (MIT, MLX), quantised to 8 bit when it loads.

Install: in the app (Settings → Material, card "Local image model", below), or
`videocut models pull --image` (or `videocut setup --image`; `models pull
flux2-klein-4b` leads to the same place). Refused inside an agent run and on a Mac with less
than 16 GB memory. It prints the sizes first (16.0 GB download, the time at 100 Mbit/s, 8 GB
on disk afterwards), checks free disk space (download + copy + 2 GB while it installs), then
(1) adds the `image` extra from `uv.lock` (`pyproject.toml` `[project.optional-dependencies]
image = ["mflux==<pin>"]`) with `uv sync --frozen --inexact --extra image` in start.sh's uv
environment (`install.uv_env`: pinned Python only, `.tools/uv-cache`, `UV_NO_DEV` where no
developer tools are installed), then `uv cache prune --ci`; (2) downloads
`black-forest-labs/FLUX.2-klein-4B` at the revision pinned in `manifest/tools.json`
(`models.flux2-klein-4b`: `allow_patterns` take only what mflux loads, transformer, Qwen3
text encoder, tokenizer, VAE, plus the licence) into `$VIDEOCUT_HOME/models/.download/`
(`snapshot_download` resumes; `.videocut-revision` marks it complete); (3) saves the 8-bit
copy the app loads into `models/flux2-klein-4b/` (`local_flux.quantise`, mflux
`save_model` in the child process below, about 15 s; written to `.part`, renamed when done,
marker `<revision> 8-bit`) and deletes the download. A run that stopped after (2) only does
(3). Why the copy: loading the full-precision weights and quantising at every start costs
memory and 16 GB more disk for nothing. Hugging Face models now keep the marker next to the
model; older installs are still recognised by `.tools/installed.json`. start.sh keeps the
extra on every later sync while `models/flux2-klein-4b` or the download folder exists
(`uv sync` is exact and would remove mflux otherwise). The mflux pin is the one that knows
the model id `flux2-klein-4b` and pins the same repo revision itself (`ModelRevisions`);
mflux pins `huggingface-hub<2`, so the lock moved from 2.x to 1.x for everyone (whisper,
parakeet and the model downloads use only `snapshot_download`). `videocut doctor`: "image
generation" (optional): ready with the mflux version, half installed, not installed, or not
on this Mac.

**In the app** (`server/models.py`, token-guarded like every route; Settings → Material,
card `LocalImageModel` in `components/Material.tsx`):

| Route | Does |
|---|---|
| `GET /api/models/image` | `{state, reason, download_gb, disk_gb, need_gb, free_gb, disk_ok, memory_gb, min_memory_gb, installed_gb, partial_gb, mflux_version, progress, error, log_tail}` |
| `POST /api/models/image/install` | starts one install; 409 while one runs or a removal is under way, 400 when this Mac cannot run it or the disk is too small (install_image's message), 403 under `VIDEOCUT_AGENT` |
| `POST /api/models/image/cancel` | SIGTERM to the child's process group, SIGKILL after 10 s; finished parts stay |
| `DELETE /api/models/image` | removes `models/flux2-klein-4b/`, its `.part` and `models/.download/` (409 while busy); the `image` packages stay in the venv |

States, in this order: `removing`; `installing` (the child runs); `installed` (`model_ready()`
and mflux importable); `failed` (the last child exited non-zero and was not cancelled; the
error is its last `✗`/error line, kept in memory until the next action); `unsupported`
(not Apple Silicon, or memory below `min_memory_gb`, with the sentence); `partial` (a
download folder or `.part` with data, resumable, or the model without its packages);
`not_installed`. `need_gb` is install_image's formula (download + 8-bit copy + 2 GB, minus
what is downloaded already); `free_gb` is `shutil.disk_usage` of the models folder.

The install runs `[sys.executable, "-m", "videocut.cli", "models", "pull", "--image"]` as a
child process with `start_new_session` (its own process group, so cancel also stops `uv` and
the quantise worker), `tool_env()` without `VIDEOCUT_AGENT`, `PYTHONUNBUFFERED`,
`HF_HUB_DISABLE_PROGRESS_BARS`; stdout and stderr go to `$VIDEOCUT_HOME/logs/model-image.log`.
Never in the server process: the quantise step takes several GB of memory and must not take
the UI down. Progress: the step is the last of install_image's own step lines in the log
(`Installing the Python packages` → packages, `Downloading model` → download, `Preparing the
model` → preparing); during the download `pct` and `downloaded_gb` come from the size of
`models/.download/flux2-klein-4b/` (including huggingface_hub's `.incomplete` files) against
`approx_size_gb`, capped at 99 %; speed and time left from the samples of the last 20 s
(snapshot_download prints no line a program could parse, so install.py is unchanged). The
card polls every 2 s while installing or removing. After a server restart nothing runs: the
app's lifespan stops the child at shutdown (uvicorn re-raises SIGTERM, so `atexit` would not
run), and a new server stops a child that a killed one left behind (`logs/model-image.pid`
holds its pid and command line; a reused pid with another command is left alone). A
half-done folder then reads as `partial`; the next install
resumes through install_image (snapshot_download continues its part files, a finished
download is only quantised).

Generation (`LocalFlux.generate(prompt, width, height, seed, palette, style, dest)`):
refused inside an agent run (`VIDEOCUT_AGENT`), and when `available()` says no (Apple
Silicon, ≥ 16 GB memory, mflux importable, model complete at the pinned revision; the reason
is a plain sentence). Size: the nearest of 1024×1024, 1024×1792 and 1792×1024 by aspect
(< 0.8 portrait, > 1.25 landscape, else square; multiples of 16 as the model needs); the
material step crops to the output format. Prompt: the sentence, then the brand palette as
colour names (`colour_words`: never hex, which the model drew as literal text), the style
words and `NO_TEXT` last: `a tidy desk with a laptop; colours deep navy blue and warm
orange; clean editorial photo; no text, no letters, no logos` (`illustration` → `flat
illustration`; at most 5 valid `#RRGGBB` colours). Seed: the given one or a random one, always recorded. Each image
runs in a child process (`python -I -m videocut.tools._material_providers.local_flux`,
request as JSON on stdin, one JSON line back) with `HF_HUB_OFFLINE=1`, a timeout (240 s) and
a memory guard: the parent reads the child's physical footprint (`proc_pid_rusage`, which
includes MLX's unified-memory buffers) four times a second and stops it above the Mac's
memory minus 4 GB (at most 80 %); the child also sets `mx.set_memory_limit` to that value.
The child caps MLX's buffer cache at 1 GB (without it MLX keeps freed buffers up to its
limit: 27 GB footprint measured on a 36 GB Mac, no faster) and, on Macs under 20 GB, decodes
the VAE in tiles (1024×1792 needs a 15.4 GB footprint untiled, about 10–11 GB tiled; tiling
leaves a faint grid in fine detail, mean difference 7/255, invisible at video size, so only
where needed). The PNG is written without metadata. `GenResult`: model `flux2-klein-4b`, revision (the
pinned commit), seed, watermark `none` (the model adds none; the AI label and C2PA come from
our render), elapsed_s, steps 4, quantisation `8-bit`, the exact prompt, and in `extra` the
mflux version, load and generation seconds and the peak memory.

Measured on an M3 Pro with 36 GB (2026-10-19, mflux 0.23.0, 4 steps, 8-bit): download
16.0 GB in 25 min, quantise 15 s, 8.0 GB on disk. Per image (child start and model load
about 1 s included): 1024×1024 40–45 s, 1024×1792 75–81 s. Peak physical footprint of the
child: 12.6 GB square / 19.6 GB portrait untiled (36 GB Mac); with a 16 GB Mac's settings
(12 GB guard, tiled decode) 9.7 GB square / 10.3 GB portrait at about the same speed. Not yet
run on a real 16 GB Mac. Hex colours in the prompt are sometimes drawn as text into the
image (FLUX renders strings it is given); a prompt asking for "room for text" gets garbled
lettering.

## Decisions

- **Local first.** Recordings and audio never leave the machine. Only transcript text,
  downscaled stills and screen text read locally (OCR, `redact text`) go to Claude.
- **macOS on Apple Silicon first.** Transcription runs on MLX.
- **No Homebrew, no sudo.** Everything is installed into the repository folder, so
  `uninstall.sh` can remove it completely.
- **The code itself is not pinned by checksum.** The install and `videocut update` fetch
  Video Cut's own repository (the newest release tag, else `main`) over HTTPS from GitHub;
  the repository is the product the person chose to install. Everything it downloads in
  turn is pinned and verified (below). A signed or checksum-published release is a later
  step.
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
