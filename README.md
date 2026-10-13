# Video Cut

**Claude cuts your videos, on your Mac.** Record yourself talking, drop the file in, pick a video type and a brand, and Video Cut turns it into a finished short video: filler words and pauses removed, vertical 9:16 framing, animated captions, graphics that back up what you say, clean audio, music underneath.

Video and audio never leave your machine. Claude only receives text (the transcript) and small still images at decision points.

> **Status: early development (0.0.x).** Installer, toolchain and self-test work. The editing pipeline and the app are being built. See [`docs/BRIEF.md`](docs/BRIEF.md) for the full design and [`CHANGELOG.md`](CHANGELOG.md) for progress.

[Deutsch](README.de.md)

## How it works

1. A local model transcribes your recording word by word, with timestamps.
2. Claude reads the transcript, looks at contact sheets only where a decision is unclear, and proposes a plan (hook, key message, length, what goes).
3. You approve the plan.
4. Claude writes an edit decision list. ffmpeg renders it, and graphics are rendered in your brand.
5. Claude inspects its own render at every cut and fixes problems (up to three rounds).
6. You review, give feedback in plain words, and download the result.

There is no third-party AI editor involved. Every step is an open tool (ffmpeg, Whisper, MediaPipe, DeepFilterNet, headless Chromium) that Claude drives and checks.

## Requirements

- macOS 14 or newer on Apple Silicon (M1 or newer)
- 16 GB RAM recommended
- About 15 GB free disk space (tools ~1 GB, speech model ~1.6 GB, rest for your projects)
- Claude: either [Claude Code](https://docs.claude.com/en/docs/claude-code) signed in with your own account, or an Anthropic API key. See [`docs/AUTH.md`](docs/AUTH.md).

## Install

```bash
git clone https://github.com/derFlok/video-cut.git
cd video-cut
./start.sh --selftest
```

`start.sh` installs everything into `./.tools` (no Homebrew, no admin rights). Every download is pinned to an exact version and checked against a SHA-256 checksum ([`manifest/tools.json`](manifest/tools.json)). Running it again is safe and takes seconds.

| Command | What it does |
|---|---|
| `./start.sh` | install or refresh, then check the installation |
| `./start.sh --selftest` | same, then run the pipeline on a generated test clip |
| `./start.sh --check` | only check, install nothing |
| `./uninstall.sh` | remove installed tools (keeps your data) |
| `./uninstall.sh --all` | also offer to delete your data folder |

Your brands, projects and downloaded models live in `~/VideoCut` (change with `VIDEOCUT_HOME`), never inside the repository.

## Usage (command line)

```bash
.venv/bin/videocut run ~/Movies/take.mov --type thought --brand my-brand --brief "one idea, strong first sentence"
```

1. **Prep** (local, no Claude): job folder in `~/VideoCut/jobs/<id>`, noise reduction,
   transcription with word times, filler and pause candidates, contact sheets.
2. **Plan:** Claude reads the transcript and a few stills and writes `work/project.md`: hook,
   core message, target length, what to keep and cut, words to emphasise.
3. **You approve** the plan in the terminal (edit `project.md` first if you like). Nothing is
   cut before that. To decide later: `videocut tool job approve <id>`, then `videocut run <id>`.
4. **Cut:** Claude writes the edit list, renders a preview, checks every cut (clicks, jumps,
   loudness), fixes up to three rounds and renders `out/reel.mp4` (1080×1920, −14 LUFS).
   `work/cut_report.md` lists what changed and any open doubts.
5. **Captions and overlays** (with `--brand`): Claude adds captions and a few overlays (key
   words, numbers, questions, screenshots) in your brand's look, using only what you say.
   Nothing on the face or in the app's UI zones, which is checked automatically. Then it
   renders the final reel. `work/overlay_report.md` lists the overlays.

Options: `--approve` (no prompt), `--replan`, `--recut`, `--brand <id>`, `--screen FILE` (a
screen recording made at the same time, for a new job), `--asset FILE...` (screenshots for a
new job; put it last), `--demo-data` (personal data the plan found is demo data), `--max-turns N`. Time, turns, cost and tokens of every Claude run are
logged in `work/runs.jsonl`.

## Recording a product demo

```bash
.venv/bin/videocut run ~/Movies/me.mov --screen ~/Movies/screen.mov --brand my-brand
.venv/bin/videocut run ~/Movies/me.mov --brand my-brand --asset ~/Desktop/shot-1.png ~/Desktop/shot-2.png
```

1. **Screen:** press Cmd+Shift+5, choose "Record Entire Screen" (or a part), and under
   *Options* pick your **microphone** and turn on **Show Mouse Clicks**. The microphone track
   lets Video Cut line the screen up with your camera recording.
2. **Yourself:** start the camera recording of yourself **at the same time** (phone or
   webcam), then start the screen recording. Optional: clap once in view of the camera at
   the start; it helps when the room is noisy.
3. Do the demo and talk as usual; stop both recordings at the end.
4. Run the first command above. Prep syncs the two recordings (`work/sync.json`) and finds
   clicks and changes on the screen. The plan says which parts show you, the screen, or
   both (a small picture of you below the screen, or screen on top and you below), where to
   zoom in, and which places may show personal data (e-mail addresses, phone numbers,
   names). **Demo data?** You are asked at approval: all demo, redact all, or pick numbers
   (or pass `--demo-data`). Without an answer everything listed is covered with solid
   blocks.
5. If the sync is unsure, the run stops and tells you how to set it by hand:
   `videocut tool sync --job <id> --offset <seconds>` (screen time minus camera time of one
   moment you can see or hear in both), then `videocut run <id>`.

**Screenshots only:** take the screenshots (Cmd+Shift+4), record yourself talking about
them, and run the second command. Claude matches each screenshot to the sentence about it
and shows it with a zoom onto the part you mention; personal data in it is covered.

## Brands

Video Cut ships without any branding. You upload your own (logo, colours, fonts, optionally a brand guide and example posts) and every video adapts to it. You can keep several brands side by side and pick one per video. See [`docs/BRIEF.md`](docs/BRIEF.md) §9.

A brand is a folder `~/VideoCut/brands/<id>/` with a `brand.json` and its font and logo
files (start from `brands/_example`).

```bash
.venv/bin/videocut tool brand check my-brand   # validate; contrast table (caption colours ≥ WCAG AA)
.venv/bin/videocut tool brand sheet my-brand   # preview sheet of every scene → brands/my-brand/preview.jpg
```

## Privacy

- Recordings, audio and renders stay on your Mac.
- Sent to Anthropic: transcript text and downscaled still frames.
- Downloads at install time: the tools and models listed in `manifest/tools.json`, nothing else.
- Do not film other people without their consent, and do not show customer data on screen.

## License

Apache-2.0, see [`LICENSE`](LICENSE). Third-party components and their licences: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Video Cut is an independent project. It is not affiliated with or endorsed by Anthropic.
