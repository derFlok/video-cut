# Fresh-install report

Measured with `scripts/fresh-install-test.sh`. Numbers only; no personal data.

## How to run

```sh
scripts/fresh-install-test.sh                       # full install, downloads everything (real time)
scripts/fresh-install-test.sh --keep-models         # repeat run, models from ~/.cache/videocut-fresh-test
scripts/fresh-install-test.sh --claude <URL|path> --lang de --keep-models --report out/de.md
```

The direct mode copies the checkout (tracked and untracked files, nothing ignored) to a temporary
folder and runs `./start.sh`, `videocut doctor` and `videocut selftest` under
`env -i HOME=<tmp> VIDEOCUT_HOME=<tmp>/VideoCut PATH=/usr/bin:/bin:/usr/sbin:/sbin TERM=xterm`:
no Homebrew, no `~/.local`, no node or python of the machine, no shell profile.

The `--claude` mode gives the official `claude` program the person's sentence
("Install <source> for me and guide me through it." / "Installiere <source> für mich und führe
mich durch die Einrichtung.") in an empty folder. The program keeps its own sign-in; every command
it runs gets the temporary HOME and the system PATH (`CLAUDE_ENV_FILE`), only project settings
load (`--setting-sources project,local`, no MCP servers), tools are Bash, Read, Write, Edit, and
`kill`/`pkill`/`killall` are denied so the test cannot stop an app already running on the machine,
and Write/Edit are denied for the real `~/.claude` and `~/VideoCut`.
When it stops before the launcher exists, it gets up to two plain answers ("Yes, please. Just
go ahead."). The transcript goes to `$TMPDIR`, never into the repository (it names real paths). Afterwards the harness runs the doctor on `~/VideoCut/app`, looks for
`~/Applications/Video Cut.app`, checks that the real `~/VideoCut` was not touched, and writes the
stream-json transcript plus a readable summary (tool calls, risky commands, every message the
person would read).

## Baseline: direct install on main (`ca937da`, 2026-10-16)

Apple Silicon, macOS 26.3, 36 GB RAM, home broadband. Result: **passed**.

| Step | Wall clock |
|---|---|
| `start.sh` total | 4 min 08 s |
| of which: uv download | 2 s |
| of which: Python environment (`uv sync`, managed Python + wheels) | 42 s |
| of which: Node, ffmpeg, ffprobe, deep-filter | 17 s |
| of which: renderer packages + headless Chromium | 17 s |
| of which: app packages + app build | 7 s |
| of which: Whisper model (1.6 GB) | 2 min 38 s |
| `videocut doctor` | < 1 s |
| `videocut selftest` | 1 min 33 s |
| **Total (copy, install, doctor, selftest)** | **5 min 44 s** |

Repeat run with `--keep-models`: `start.sh` 2 min 18 s (no model download; the machine was busier,
so the other steps were slower), about 0.8 GB received.

| Size | |
|---|---|
| Received over the network during `start.sh` (interface counter, upper bound) | ~2.5 GB |
| On disk, all parts summed by `du` | 5.3 GB |
| of which: models (`~/VideoCut/models`) | 1.5 GB |
| of which: Python environment `.venv` | 1.5 GB |
| of which: uv cache `.tools/uv-cache` | 1.5 GB (APFS clones: likely shared with `.venv`, `du` counts both) |
| of which: Node 0.2 GB, Chromium 0.2 GB, ffmpeg/ffprobe/deep-filter/uv 0.2 GB, managed Python 0.07 GB, node_modules 0.1 GB | |

What broke in the clean environment: **nothing**. No certificate problem (uv's managed Python and
Node download over TLS without `SSL_CERT_FILE`), no missing node or python, `start.sh` never calls
`git`, Homebrew or `/usr/bin/python3`.

What the clean environment cannot show (this Mac has them):

- **Xcode Command Line Tools.** On a fresh Mac, `/usr/bin/git`, `/usr/bin/python3`, `make`,
  `clang` are shims that open the "install the command line developer tools" dialog (about 1 GB,
  several minutes). `start.sh` does not need them; the danger is the step before it: a Claude
  Code that runs `git clone` triggers the dialog. The install instructions must fetch the code
  as a tarball with `curl` (present on every Mac) and never call `git` or `python3` from
  `/usr/bin`.
- A fresh macOS user account (empty keychain, no shell profile, first-run prompts).
- Gatekeeper / "app from the internet" prompts for the launcher.
- A slow network: the Whisper model is 1.6 GB of the ~2.5 GB.

## Direct install on the installer branch (`origin/installer` `645fd78`, 2026-10-16)

Same machine and harness, full download. Result: **passed**.

| Step (start.sh now prints "Step n/7") | Wall clock |
|---|---|
| 1/7 checking this Mac, 2/7 uv | 2 s |
| 3/7 Python and packages (~474 MB) | 59 s |
| 4/7 Node, FFmpeg, FFprobe, DeepFilterNet (135 MB) | 21 s |
| 5/7 renderer + headless Chromium (120 MB) | 16 s |
| 6/7 app packages + build (60 MB) | 7 s |
| 7/7 models: Whisper 1.6 GB + face model | 3 min 21 s |
| **`start.sh` total** | **5 min 05 s** |
| `videocut doctor` / `videocut selftest` | 2 s / 1 min 26 s |

Received ~2.4 GB; on disk **3.9 GB** (was 5.3 GB on main: the uv cache is now pruned after the
sync, 1.5 GB → 0). The model download speed varies between runs (2 min 38 s on main, 3 min 16 s
here); the "about 2 minutes on 100 Mbit/s" estimate in step 7 is right for 100 Mbit/s.

## Claude-driven install (`origin/installer` `645fd78`, 2026-10-16)

The repository is private, so the source in the sentence was a local tarball of the branch with
the same layout as GitHub's (`video-cut-installer/…`) instead of the URL. Models were seeded from
the cache (`--keep-models`), so the times leave out the 1.6 GB model download (about 2.5 min on
fast broadband). Model: the CLI default (Opus).

| | English | German |
|---|---|---|
| Sentence | "Install <tarball> for me and guide me through it." | "Installiere <tarball> für mich und führe mich durch die Einrichtung." |
| Result | **installed**, doctor green, launcher created and opened | **installed**, doctor green, launcher created and opened |
| Wall clock | 3 min 15 s | 3 min 48 s |
| Turns / cost | 17 / $2.25 | 17 / $1.87 |
| Questions to the person | 0 | 0 |
| sudo / Homebrew / git / system python | none | none (one harmless `which … git`) |
| Found the instructions | read INSTALL.md, then the skill | read the skill |
| Code in `~/VideoCut/app`, app `~/Applications/Video Cut.app` | yes | yes |
| Port | 4300 taken by another app → 4417 (`VIDEOCUT_PORT`) | same |
| Permission prompts hit | `~/.claude/skills` (sensitive path), twice | same |
| Language and tone | English, plain but with jargon in places | German with "du", plain but with jargon in places |
| Explained the next step | yes: the welcome screen and both ways to connect Claude | yes |
| Off-script | sent the person to the Terminal for `claude auth login`; offered a shell command for the skill note | sent the person to the Terminal for `claude auth login` |
| Nothing outside the sandbox touched | yes | yes |

Judged as a person without IT knowledge: the install itself is right and quick, with no question
and no password. Four things would confuse or block them: (1) the first message is about
"the archive", not "this takes 5 minutes and downloads 2.5 GB"; (2) a permission dialog about
a "sensitive file" in Claude's settings comes with no explanation; (3) to use their subscription
they are told to open the Terminal; (4) the final message names ports and tools. Concrete text
changes: `docs/fresh-install-proposals.md`.

Test artefacts, not product findings: in print mode (`claude -p`) the turn ended while
`start.sh` ran in the background, so the harness answered once ("Yes, please. Just go ahead.")
to let it finish; the `claude` program was "not signed in" because the commands ran with a
temporary HOME.

### Control run: the same sentence on `main` (no install skill)

Before the skill existed, one English run on `main` (`ca937da`) showed why it matters: 39 turns,
$4.67, 21 minutes, and it never created `~/VideoCut/app` or a launcher. It installed in the
download folder, went looking for the real sign-in (`HOME=<real home> claude auth status`, put
`/opt/homebrew/bin` on PATH), wrote a note into its own auto-memory, and then cut a test video
end to end with `videocut run … --approve`, approving its own plan. After that run the harness
was hardened: auto-memory off, the Write/Edit tools denied for the real `~/.claude` and
`~/VideoCut`, and every command that names the real home folder is listed in the summary (Bash
itself cannot be fenced in by an environment file). The EN/DE runs above named the real home
folder in no command. Note: the deny rules in those two runs used a single leading slash, which
Claude Code reads as relative to the project, so they had no effect there; the refusals of
`~/.claude/skills` in both runs came from Claude Code's built-in protection of its settings
folder. Fixed in the harness (`//` for absolute paths).

## What a human must test on a fresh account

We have no second Mac. A fresh macOS user account on the same Mac is the closest thing: no
Homebrew in its PATH, no shell profile, its own keychain, its own `~`. It does share the Xcode
Command Line Tools with the main account, so the CLT dialog still cannot be seen there; only a
Mac that never had them (or a reinstall) shows it.

- [ ] System Settings → Users & Groups → Add User → Standard user, any name → log out, log in as
      the new user, finish the first-run screens.
- [ ] Install the Claude desktop app (or Claude Code) from claude.com and sign in with the
      subscription account.
- [ ] Paste the sentence from INSTALL.md exactly ("Install <repository URL> for me and guide me
      through it."), and once more the German one in a second fresh account, or after removing
      `~/VideoCut` and `~/Applications/Video Cut.app`.
- [ ] When the install runs: does a "Claude wants to edit a sensitive file" dialog appear for
      `~/.claude/skills`? Note its exact wording and whether Claude explained it first.
- [ ] Desktop app only: does the welcome screen find the `claude` program the desktop app brings,
      or does it say none was found?
- [ ] Note the clock time when you press Enter and when Video Cut opens in the browser.
- [ ] Count every question Claude asks you. Each one you could not answer without IT knowledge is
      a finding (write down its exact wording).
- [ ] Count every permission prompt ("Allow Claude to run …?"). Note which ones looked scary.
- [ ] No password prompt (no `sudo`), no Homebrew, no "install command line developer tools"
      dialog appeared. If the dialog appeared: note what Claude ran just before it.
- [ ] `~/Applications/Video Cut.app` exists, has a neutral icon, and double-clicking it opens
      Video Cut (the first time macOS may ask whether to open it: note the exact wording).
- [ ] The app shows "Claude-Abo erkannt" (or the two ways to connect Claude) without asking for
      a password or a key you do not have.
- [ ] Quit the browser tab, log out and in again, double-click the launcher: Video Cut opens again.
- [ ] Tell Claude "Video Cut startet nicht" while the app is closed: it starts or explains it in
      plain words.
- [ ] Tell Claude "Video Cut aktualisieren": it updates without git and without deleting
      `~/VideoCut/jobs`.
- [ ] Optional: disconnect the network halfway through the model download, reconnect, paste the
      sentence again: it resumes and finishes.
- [ ] Afterwards: delete the test user (System Settings → Users & Groups → Delete user → delete
      the home folder).
