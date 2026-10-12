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

## Brands

Video Cut ships without any branding. You upload your own (logo, colours, fonts, optionally a brand guide and example posts) and every video adapts to it. You can keep several brands side by side and pick one per video. See [`docs/BRIEF.md`](docs/BRIEF.md) §9.

## Privacy

- Recordings, audio and renders stay on your Mac.
- Sent to Anthropic: transcript text and downscaled still frames.
- Downloads at install time: the tools and models listed in `manifest/tools.json`, nothing else.
- Do not film other people without their consent, and do not show customer data on screen.

## License

Apache-2.0, see [`LICENSE`](LICENSE). Third-party components and their licences: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Video Cut is an independent project. It is not affiliated with or endorsed by Anthropic.
