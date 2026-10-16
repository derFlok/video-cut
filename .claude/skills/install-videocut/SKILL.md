---
name: install-videocut
description: Install, open, repair, update or uninstall Video Cut (the local video editing app from github.com/derFlok/video-cut) for a person on their Mac, in plain language. Use when someone asks you to install Video Cut, says "Video Cut startet nicht" / "Video Cut does not start", wants to update ("Video Cut aktualisieren") or remove it, or the launcher shows an error.
---

# Video Cut: install and look after it for a person

You are the person's own helper. They probably do not use a terminal and should not have to.
Talk to them in their language (German if they write German), in short plain sentences, no
jargon. Do the technical work yourself; tell them what is happening and how long it takes.

Fixed facts:

- Code lives in `~/VideoCut/app`. Videos, brands and models live in `~/VideoCut` (outside the
  code). The double-click app is `~/Applications/Video Cut.app`.
- Repository: `https://github.com/derFlok/video-cut`. Download it as a tarball with `curl`.
- Needs a Mac with Apple Silicon (M1 or newer), macOS 14+, about 8 GB free disk (15 GB
  recommended). A first install downloads about 2.5 GB (tools ~0.9 GB, speech model 1.6 GB)
  and takes about 5 minutes on 100 Mbit/s. On disk afterwards: about 4 GB.

Never:

- use `sudo`, Homebrew, MacPorts or Rosetta, or install the Xcode command line tools;
- run `git` or `/usr/bin/python3` on a Mac that may not have the command line tools: on a
  fresh Mac both are stubs that open a large "install developer tools" dialog. `curl`,
  `tar`, `shasum`, `plutil` and `open` are always there and are all you need;
- read, copy or print anything from `~/.claude` (sign-in data) or the API key; the person
  types a key only into Video Cut's own settings page;
- delete anything in `~/VideoCut` except `~/VideoCut/app` unless the person explicitly asks.

Ask the person only what is theirs to decide. A normal install needs no question at all:
announce the download size, then go.

## Install

1. **Check the Mac** (no questions):
   `uname -m` must be `arm64`, `sw_vers -productVersion` 14 or newer,
   `df -g ~ | tail -1` at least 8 GB free in the "Avail" column. If something fails, explain
   it plainly and stop (an Intel Mac cannot run Video Cut).
2. **Already installed?** If `~/VideoCut/app/start.sh` exists, this is a repair or an update:
   go to "Repair" below instead.
3. **Tell the person** in one or two sentences: "I am installing Video Cut now. It downloads
   about 2.5 GB (tools and a speech recognition model) and takes about 5 minutes on a
   normal connection. Your recordings never leave this Mac."
4. **Download the code** (the newest release, or `main` while there is none):

   ```sh
   tag=$(curl -fsSL https://api.github.com/repos/derFlok/video-cut/releases/latest 2>/dev/null \
     | plutil -extract tag_name raw -o - - 2>/dev/null || true)
   ref=${tag:+refs/tags/$tag}; ref=${ref:-refs/heads/main}
   mkdir -p ~/VideoCut/app
   curl -fsSL --retry 4 "https://codeload.github.com/derFlok/video-cut/tar.gz/$ref" \
     | tar -xzf - --strip-components=1 -C ~/VideoCut/app
   ```

   A 404 means the repository is not reachable (private, or no network): tell the person.
5. **Install** (long-running; run it in the background or with a long timeout, and relay the
   "Step n/7" lines in plain words, e.g. "Step 7 of 7: downloading the speech model, 1.6 GB"):

   ```sh
   cd ~/VideoCut/app && ./start.sh
   ```

   It is safe to run again at any time: finished steps are skipped, an interrupted download
   continues. It ends with a check list and a summary. On a failure see "Repair".
6. **Create the double-click app** and open it:

   ```sh
   cd ~/VideoCut/app && .tools/bin/uv run --frozen videocut launcher
   open ~/Applications/"Video Cut.app"
   ```

   Only if the person wants it in the main Applications folder: add `--system`. Start at
   login only if they ask: `videocut launcher --at-login` (off by default).
7. **Remember how to help later** (tell the person in one sentence that you are adding a small
   note so you can help with Video Cut later): link this skill into the person's skills:

   ```sh
   mkdir -p ~/.claude/skills && ln -sfn ~/VideoCut/app/.claude/skills/install-videocut ~/.claude/skills/install-videocut
   ```

8. **Hand over to the app.** The browser shows the first-start screen. Explain:
   - "Video Cut needs a connection to Claude. If you have a Claude subscription and the
     `claude` program on this Mac is signed in, the screen shows 'Claude-Abo erkannt' and you
     choose it. Otherwise you can paste an API key from the Anthropic Console there."
   - The subscription way is for the person's own, personal use (Anthropic's terms); for a
     team or a company an API key is the right way. That is their choice; do not decide it.
   - If the screen says no `claude` program was found and they want to use their
     subscription: offer to install Anthropic's official program
     (`curl -fsSL https://claude.ai/install.sh | bash`, no sudo, see
     https://code.claude.com/docs) and then have them sign in with `claude auth login` (it
     opens the browser). Then "Erneut prüfen" / "Check again" on the screen.
   - "From now on you start Video Cut by double-clicking 'Video Cut' in your Applications
     folder. 'Beenden' in the app stops it. For problems or updates, just ask me."
   - First-video tip: record in 4K landscape or straight in portrait.

## Repair ("Video Cut startet nicht", errors, the launcher shows an alert)

1. Run the doctor and read its JSON:

   ```sh
   cd ~/VideoCut/app && .tools/bin/uv run --frozen videocut doctor --json
   ```

   Each entry in `checks` has `ok`, `required`, `detail` and a `fix`. Do what the `fix` of
   each failed check says (most often: run `./start.sh` again). If `.tools/bin/uv` itself is
   missing, run `./start.sh` first.
2. Look at the logs: `~/VideoCut/state/launcher.log` and `~/VideoCut/state/server.log`
   (last 50 lines).
3. If the app does not open: `cd ~/VideoCut/app && .tools/bin/uv run --frozen videocut open`
   prints what happens. Port 4300 taken by another program is fine (the next free port is
   used); `VIDEOCUT_PORT=4400` forces another one.
4. Rebuild the double-click app if it is gone or moved: `videocut launcher` (step 6 above).
5. Still broken: re-download the code (Install step 4, it overwrites the code only) and run
   `./start.sh`. Never delete `~/VideoCut/jobs`.

## Update ("Video Cut aktualisieren")

```sh
cd ~/VideoCut/app && .tools/bin/uv run --frozen videocut update
```

It stops the running app (not while a video is being processed; then tell the person to
wait, or add `--force` if they agree to stop it), downloads the newest release, keeps the
installed tools, runs `./start.sh` and opens the app again. Tell the person what changed if
`CHANGELOG.md` says so (in one or two sentences).

## Uninstall

Tell the person what will be removed and what stays, then do it:

- removed: the code and tools `~/VideoCut/app` (about 3 GB), the double-click app
  (`videocut launcher --remove` removes it and a login item; run it before deleting the
  code), and the skill link `~/.claude/skills/install-videocut`;
- the speech models `~/VideoCut/models` (about 1.6–4 GB): remove them too unless the person
  wants to reinstall later;
- **kept unless the person explicitly wants them gone**: their videos in `~/VideoCut/jobs`,
  brands in `~/VideoCut/brands`, settings `~/VideoCut/settings.json`;
- an API key stored by Video Cut is in the macOS keychain under "videocut"; remove it only if
  the person asks: `security delete-generic-password -s videocut -a anthropic-api-key`.

```sh
cd ~/VideoCut/app && .tools/bin/uv run --frozen videocut launcher --remove
rm -rf ~/VideoCut/app ~/.claude/skills/install-videocut
```
