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
`kill`/`pkill`/`killall` are denied so the test cannot stop an app already running on the machine.
When it stops before anything is installed, it gets up to two plain answers ("Yes, please. Just
go ahead."). Afterwards the harness runs the doctor on `~/VideoCut/app`, looks for
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

## Claude-driven install

Filled in after the runs against the installer branch.

## What a human must test on a fresh account

We have no second Mac. A fresh macOS user account on the same Mac is the closest thing: no
Homebrew in its PATH, no shell profile, its own keychain, its own `~`. It does share the Xcode
Command Line Tools with the main account, so the CLT dialog still cannot be seen there; only a
Mac that never had them (or a reinstall) shows it.

- [ ] System Settings → Users & Groups → Add User → Standard user, any name → log out, log in as
      the new user, finish the first-run screens.
- [ ] Install the Claude desktop app (or Claude Code) from claude.com and sign in with the
      subscription account.
- [ ] Paste the sentence exactly: "Install https://github.com/derFlok/video-cut for me and guide
      me through it." (and once more in German in a second fresh account, or after removing
      `~/VideoCut` and `~/Applications/Video Cut.app`).
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
