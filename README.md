# Video Cut

**Claude cuts your videos, on your Mac.** Record yourself talking, drop the file in, pick a video type and a brand, and Video Cut turns it into a finished short video: filler words and pauses removed, vertical 9:16 framing (or 16:9, 1:1, 4:5 per video), animated captions, graphics that back up what you say, clean audio, music underneath.

Video and audio never leave your machine. Claude only receives text (the transcript and the screen text read locally on your Mac) and small still images at decision points.

> **Status: early development (0.0.x).** Installer, editing pipeline and the local app work; expect rough edges. See [`docs/BRIEF.md`](docs/BRIEF.md) for the full design and [`CHANGELOG.md`](CHANGELOG.md) for progress.

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
| `./start.sh --ui` | same, then open the app in your browser |
| `./start.sh --check` | only check, install nothing |
| `./uninstall.sh` | remove installed tools (keeps your data) |
| `./uninstall.sh --all` | also offer to delete your data folder |

Your brands, projects and downloaded models live in `~/VideoCut` (change with `VIDEOCUT_HOME`), never inside the repository.

## Using the app

```bash
./start.sh --ui            # or, once installed: .tools/bin/uv run videocut ui
```

The app runs only on your Mac (`127.0.0.1`) and opens in your browser with a one-time link;
close the terminal window to stop it. German by default, English via the switch at the top.

1. **New video:** pick a video type, drag your camera recording onto the page (optionally a
   screen recording of the same take, or screenshots), choose a brand, an output format and
   a cut style, and write a one-line instruction for Claude. The upload starts the preparation.
2. **Watch the progress:** the steps, a live log, the time and the Claude cost so far.
3. **Read the plan:** hook, core message, what stays in (with source times and why), the
   framing with stills, overlays and risks. Ask Claude to change it in plain words, for
   example *"make the ending shorter, the last sentence is enough"* or *"drop the question
   about the calls, keep the example longer"*. You can also edit the plan text directly.
4. **Personal data:** if the plan found names, e-mail addresses or numbers on screen, you
   decide per item what is redacted (default: everything) and how: blurred, cut off (a
   browser bar or sidebar along the edge of the screen) or a solid block; "auto" cuts off
   full-width bars and blurs the rest. Then **approve**: only now
   Claude cuts, adds captions and overlays, renders and checks its own result.
5. **Result:** play the reel, read the automatic check in plain words, and give feedback at
   the current player position, for example *"zoom out less here"*, *"the caption at 0:12
   says the wrong word"* or *"drop the question about the calls"*. Only the affected parts
   change; every version stays listed (the last five can be played again).
6. **Download** the MP4 and the cover.

On the job page you can rename a video and **duplicate** it (the "⋯" menu), e.g. to try
another cut style or format without losing the first result; the copy needs its own
approval unless you take the approval over.

**Brands** (top navigation): preview sheets per format, colours, fonts, caption pace and
form of address, logo and font upload, new brands start from the neutral example. The
app checks a brand the same way `videocut tool brand check` does.

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
   loudness), fixes up to three rounds and renders `out/reel.mp4` (in the job's format, e.g.
   1080×1920; −14 LUFS).
   `work/cut_report.md` lists what changed and any open doubts.
5. **Captions and overlays** (with `--brand`): Claude adds captions and a few overlays (key
   words, numbers, questions, screenshots) in your brand's look, using only what you say.
   Nothing on the face or in the app's UI zones, which is checked automatically. Then it
   renders the final reel. `work/overlay_report.md` lists the overlays.

Corrections: `videocut run <id> --plan-feedback "shorter ending"` revises the plan before
approval (approve it again afterwards); `videocut run <id> --feedback "zoom out less here" --at 12`
revises the finished reel at second 12 without a new approval (the previous reel stays in
`out/history/`, every correction is logged in `work/revisions.jsonl`).

Options: `--approve` (no prompt), `--replan`, `--recut`, `--brand <id>`, `--screen FILE` (a
screen recording made at the same time, for a new job), `--asset FILE...` (screenshots for a
new job; put it last), `--demo-data` (personal data the plan found is demo data),
`--format 9:16|16:9|1:1|4:5` (output format; for an existing job it replaces the format and
cuts again), `--cut-style calm|standard|tight` (see below), `--target 90|5m|2:30|auto` (target
length, see below), `--max-turns N`. Time, turns, cost and tokens of every Claude run are
logged in `work/runs.jsonl`.

**Output format** (per video): `9:16` 1080×1920 (Reels, Shorts, Stories), `16:9` 1920×1080
(YouTube, website, presentations), `1:1` 1080×1080 and `4:5` 1080×1350 (feed posts).
Default: `16:9` when the job has a screen recording or screenshots (a product demo), else
`9:16`. Change it later with `videocut tool job format <id> 16:9`; `videocut tool job show
<id>` prints it.

**Cut style** (per video): how much of your natural rhythm the cut keeps.
`calm` keeps whole sentences and pauses up to a second, with a soft 120 ms audio crossfade
at every cut — for demos and explanations, where the viewer reads the screen while you
talk. `standard` removes fillers and false starts but keeps sentences whole otherwise — for
talking heads. `tight` removes every pause and filler — for fast reels. Default: `calm` when
the job has a screen recording or screenshots, else `standard`. Change it with
`videocut tool job cut-style <id> standard` or in the app (plan or result page); the
approved plan and your personal-data decision stay, and the app offers to cut again.
Every cut point is moved to a real pause in the audio and verify fails a reel where a word
is cut off.

**Target length** (per video): `auto` gives reels of 30–60 s (30–45 s for a thought) and lets
a product demo run as long as its content needs, tight but complete. Set a length (e.g.
`5m`) and the plan aims for it ± 15 %; it never pads — if there is not enough material it
says so and proposes the natural length. Choose it when creating the video or later with
`videocut tool job target <id> 5m` / on the plan page; changing it needs a new plan and a
new approval.

## Recording a product demo

```bash
.venv/bin/videocut run ~/Movies/me.mov --screen ~/Movies/screen.mov --brand my-brand
.venv/bin/videocut run ~/Movies/me.mov --brand my-brand --asset ~/Desktop/shot-1.png ~/Desktop/shot-2.png
```

1. **Screen:** press Cmd+Shift+5, choose "Record Entire Screen" (or a part), and under
   *Options* pick your **microphone** and turn on **Show Mouse Clicks**. The microphone track
   lets Video Cut line the screen up with your camera recording.
   **Keep the microphone on in both recordings** (camera and screen): without sound in one
   of them the two cannot be lined up.
2. **Yourself:** start the camera recording of yourself **at the same time** (phone or
   webcam), then start the screen recording. Optional: clap once in view of the camera at
   the start; it helps when the room is noisy.
3. Do the demo and talk as usual; stop both recordings at the end.

   Tip for a 16:9 demo (the default here): record the Mac screen at its native resolution —
   the screen then fills the video and stays sharp, zooms are only needed for small text.
   Record yourself with a separate camera (phone or webcam); a camera picture of you gives a
   much better round picture-in-picture bubble than a small video-call tile.
4. Run the first command above. Prep syncs the two recordings (`work/sync.json`) and finds
   clicks and changes on the screen. The plan says which parts show you, the screen, or
   both (in 16:9: the screen fills the frame with you as a round bubble in a corner, or the
   screen beside you; in 9:16: a small picture of you below the screen, or screen on top
   and you below), where to zoom in, and which places may show personal data (e-mail addresses, phone numbers,
   names). **Demo data?** You are asked at approval: all demo, redact all, or pick numbers
   (or pass `--demo-data`). Without an answer everything listed is hidden in the job's
   redaction style (`--redact-style auto|blur|crop|block`, default auto: browser bars at
   the top or bottom are cut off, the rest is blurred). Their position is measured on the recording with the Mac's own text
   recognition (on this machine), and the check reads the finished reel to confirm the
   text is covered.
5. If the sync is unsure, the run stops and tells you how to set it by hand:
   `videocut tool sync --job <id> --offset <seconds>` (screen time minus camera time of one
   moment you can see or hear in both), then `videocut run <id>`.

**Screenshots only:** take the screenshots (Cmd+Shift+4), record yourself talking about
them, and run the second command. Claude matches each screenshot to the sentence about it
and shows it with a zoom onto the part you mention; personal data in it is covered.

## Slides and animations

Claude can show what you say as slides: steps, a list, a big number, a comparison, a
timeline, a bar chart, a quote or the call to action, built from your brand's look
(nothing is generated as code; every slide is a template filled with words you said). Or
you bring your own slides (a PDF export or images) and Claude puts each page where you
talk about it. In the app: **"Folien & Animationen"** in *Neues Video* — none, upload your
own, Claude creates them, or both, plus "What should be shown?". The plan lists every
slide with a preview, the moment it belongs to and its layout (slide large with you in a
circle, side by side, or slide only); remove one or ask for a change before approving.

```bash
.venv/bin/videocut run ~/Movies/me.mov --brand my-brand --slides-mode auto \
  --slides-wish "the three steps as a flow"
.venv/bin/videocut tool job slides-import <id> ~/Desktop/deck.pdf   # your own pages
.venv/bin/videocut tool job slides <id> --mode both                 # then: videocut run <id>
```

PDF pages are turned into pictures on this Mac (its own PDF engine); nothing is uploaded.
Claude never puts a number or claim on a slide that you did not say or ask for. Changing
the slides after planning needs a new plan and a new approval.

## Brands

Video Cut ships without any branding. You upload your own (logo, colours, fonts, optionally a brand guide and example posts) and every video adapts to it. You can keep several brands side by side and pick one per video. See [`docs/BRIEF.md`](docs/BRIEF.md) §9.

A brand is a folder `~/VideoCut/brands/<id>/` with a `brand.json` and its font and logo
files (start from `brands/_example`).

```bash
.venv/bin/videocut tool brand check my-brand   # validate; contrast table (caption colours ≥ WCAG AA)
.venv/bin/videocut tool brand sheet my-brand   # preview sheet of every scene → brands/my-brand/preview.jpg
```

### Vocabulary

If the transcription keeps misspelling a name (your company, a product), add it to the
brand's **Wörterbuch** in the app (or for one video: "Begriffe für dieses Video"): the
correct spelling plus the misspellings you have seen. Whisper is told the correct spelling,
and every misspelling is corrected in the transcript and the captions; Claude writes the
name that way in titles and cards. For an existing video, "apply" corrects the transcript
without transcribing again and offers to set the captions again. The job page also shows
"Did you mean …?" for words close to a term.

```bash
.venv/bin/videocut tool brand vocab my-brand add Zentrovia Zentrowia "Sentro Via"
.venv/bin/videocut tool job vocab <job> add "Lumo Cloud" Lumocloud   # this video only
.venv/bin/videocut tool vocab apply <job>      # correct an existing transcript
.venv/bin/videocut tool vocab suggest <job>    # possible misspellings (never applied)
.venv/bin/videocut run take.mov --vocab "Zentrovia=Zentrowia,Sentro Via"
```

## Privacy

- Recordings, audio and renders stay on your Mac.
- Sent to Anthropic: transcript text, downscaled still frames and screen text read locally (OCR).
- Downloads at install time: the tools and models listed in `manifest/tools.json`, nothing else.
- Do not film other people without their consent, and do not show customer data on screen.

## License

Apache-2.0, see [`LICENSE`](LICENSE). Third-party components and their licences: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Video Cut is an independent project. It is not affiliated with or endorsed by Anthropic.
