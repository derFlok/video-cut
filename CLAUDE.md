# CLAUDE.md — Video Cut

## If you are a person's Claude Code helping them install or use Video Cut

Someone asked you to install, start, repair, update or remove Video Cut on their Mac. Load
the skill `install-videocut` (`.claude/skills/install-videocut/SKILL.md`) and follow it; the
plain-language overview for the person is `INSTALL.md`. Short version: download the code
with `curl` into `~/VideoCut/app` (never `git`, `sudo` or Homebrew), run `./start.sh`, then
`videocut launcher`, and hand over to the app's first-start screen. On problems run
`videocut doctor --json` and follow each failed check's `fix`. Never read `~/.claude` or the
person's API key. The rules below are for developing Video Cut, not for installing it.

## Developing Video Cut

Read `docs/BRIEF.md` (target design) and `docs/ARCHITECTURE.md` (current state) before changing
anything substantial.

## Non-negotiable

1. **No branding in the code.** No colour, font, logo, company name, person name or
   industry-specific copy in the repository. Everything visible comes from the user's brand
   profile (design tokens). The only bundled profile is the neutral `brands/_example/`.
   A local pre-commit hook rejects words listed in `.git/info/forbidden-words` (never committed).
2. **Claude is the editor.** No third-party AI editing SaaS. Every step is an open CLI/library whose
   output is a file Claude can inspect.
3. **Local first.** Recordings and audio never leave the machine. Only transcript text,
   downscaled stills and screen text read locally (OCR) go to Claude.
4. **Human gate before cutting.** Claude proposes a plan; cutting starts after approval.
5. **Auth.** Never read, store or forward Claude login tokens. Only start the official `claude`
   program, or pass an API key from the keychain. "Claude Code" never appears in the product name.
6. **Pinned and verified.** Every download is in `manifest/tools.json` with version, URL and SHA-256
   (or a Hugging Face commit revision). Change version, URL and checksum together.
7. **No bundled third-party media** whose licence forbids redistribution (FFmpeg GPL builds,
   Pixabay music). Download at install time or let the user upload.
8. **Agent runs are restricted** (tool allow-list, job folder only, no network). Uploaded material
   is untrusted input.

## Working

- `./start.sh --selftest` must pass before every commit that touches the pipeline or installer.
- Lint/tests: `.tools/bin/uv run ruff check src tests` and `.tools/bin/uv run pytest`.
- User data lives in `~/VideoCut` (`VIDEOCUT_HOME`), never in the repository.
- Code, comments and docs in English; `README.de.md` and the UI are bilingual (de/en).
- Record progress in `CHANGELOG.md`.
