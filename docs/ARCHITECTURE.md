# Architecture (current state)

`docs/BRIEF.md` is the target design. This file describes what exists and records deviations.

## Layout

| Path | Purpose |
|---|---|
| `start.sh` | entry point: platform checks, bootstraps `uv`, syncs Python, runs `videocut setup` + `doctor` |
| `manifest/tools.json` | pinned downloads: version, URL, SHA-256, licence |
| `src/videocut/` | Python package and CLI (`setup`, `doctor`, `selftest`, `models pull`, `ui`, `auth`, `run`, `tool`) |
| `src/videocut/run.py` | `videocut run`: prep → plan run → human approval → cut run |
| `src/videocut/agent.py` | the restricted `claude` runner (see `SECURITY.md`) |
| `claude-plugin/` | Claude Code plugin `video-cut` with the skills `ping`, `plan`, `cut` |
| `src/videocut/tools/` | `videocut tool <name>`: one module per pipeline tool, used by Claude inside a job |
| `src/videocut/edl.py` | EDL schema (dataclasses), validation with JSON paths, load/save |
| `tests/clips.py` | generated test clips (macOS `say` + ffmpeg test pattern), no third-party media |
| `renderer/` | Node + `playwright-core`: canvas frames in headless Chromium, piped to FFmpeg |
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
| `job new <video> [--type] [--brief] [--id]`, `job show [job]`, `job approve [job]` | `job.py` |
| `ingest [--denoise]` | `ingest.py` |
| `transcribe [--model turbo\|parakeet] [--language]`, `candidates` | `transcribe.py` |
| `sheet contact [--every]`, `sheet burst <t> [--span]`, `sheet timeline <t0> <t1>` | `sheets.py` |
| `edl check [file]` | `edl.py` |
| `render [--preview]` | `render.py` |
| `verify` | `verify.py` |

Helpers: `_transcribe_energy.py` (pauses, voiced gaps), `_transcribe_pack.py`
(`takes_packed.md`), `_image.py` (ffmpeg-drawn pictures, peak-normalised waveforms),
`_render_plan.py` (frame grid, crop windows, filter graph), `_color.py` (HDR → SDR).

## Job folder (`$VIDEOCUT_HOME/jobs/<id>/`)

```
job.json   id, created, source (relative), type, brief, status, approval
input/     source recording, hard-linked (copied across volumes), never moved
work/      probe.json, audio_raw.wav, audio.wav (+ audio.json), proxy.mp4        ingest
           words.json, transcribe.json, candidates.json, takes_packed.md         transcribe
           sheets/contact_NN.jpg, burst_*.jpg, timeline_*.jpg                    sheet
           contract.md (reel contract of the type, copied by `run` if one exists)
           project.md                                                            plan run
           edl.json, render_filter.txt, cut_report.md                            cut run
           runs.jsonl, claude-plan.jsonl, claude-cut.jsonl                       videocut run
out/       preview.mp4 | reel.mp4, cover.jpg, render.json                        render
           verify.json, verify/cut_NN.jpg                                        verify
```

Lifecycle (`status` in job.json): `new` → `ingested` (ingest) → `approved` (`job approve`, by a
human). Approval stores the SHA-256 of `work/project.md`; `job show` reports `approved: true`
only while the plan is unchanged, so a new plan needs a new approval.

Ingest is idempotent: an output newer than its input is kept. DeepFilterNet runs with delay
compensation (`-D`) so cleaned audio stays in sync with the picture. Transcribe keeps a
`words.json` made from the same audio, model and language.

## Skills and `videocut run`

Claude only ever sees the job folder and `videocut tool *`. Two skills, two runs, a human in
between (BRIEF §5):

| Skill | Reads | Writes | Images |
|---|---|---|---|
| `plan` | `takes_packed.md`, `job.json`, `contract.md`, 1–2 contact sheets | `work/project.md`: hook, core message, target length, keep/cut list in source time, emphasis words, risks | ≤ 4 |
| `cut` | `project.md` (must be approved), `words.json`, flagged verify pictures | `work/edl.json`, renders, `work/cut_report.md` | ~8, only at doubts |

`videocut run <video|job>`:

1. **prep** (no Claude): `job new` when a file is given, `ingest --denoise`, `transcribe`
   (also candidates and the packed transcript), `sheet contact`; copies `templates/<type>.md`
   to `work/contract.md` when it exists.
2. **plan run**: `claude -p` with the `plan` skill. Fails if `project.md` is missing or
   `edl.json` was touched. An existing plan is offered again instead (`--replan` redoes it).
3. **approval**: prompt in the terminal (`--approve` skips it, for tests), or later
   `videocut tool job approve <id>` followed by `videocut run <id>`.
4. **cut run**: `claude -p` with the `cut` skill; resumes the plan session when the plan was
   approved unchanged in the same invocation, otherwise starts fresh. Loop inside the skill:
   `edl check` → `render --preview` → `verify` → fix, max. 3 rounds → `render` → `verify`.
5. prints `out/`. Exit code 0 when verify is ok, 3 when not approved, 4 when verify is not ok.

Every Claude run appends `{phase, wall_s, turns, cost_usd, usage, duration_ms, session_id,
resumed, auth}` to `work/runs.jsonl` (the token and runtime log of BRIEF §11).

Guardrails of an agent run (`agent.py`): tools `Bash,Read,Write,Edit,Skill` (no subagents, no
web, no MCP); Bash only `videocut tool *`; Read/Write/Edit only `./**` (the job folder);
`job.json` is read-only; `VIDEOCUT_AGENT=1` is set, so `job approve` refuses.

**Deviation from BRIEF §5/§6:** pictures are not handed to subagents (the restricted runner
has no Task tool); the skills keep an image budget instead.

## Verify

Per inner cut: a luma jump score, audio click numbers and a picture. A jump cut in a talking
head is expected: a clear jump is info `jump_cut` (plus `reframe` when the EDL changes crop or
zoom at that cut). The flag `visual_jump` is raised only for a jump ≥ 3× the median jump score
of the reel's other cuts without a reframe; `click` for an audio step. Global checks: duration,
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
