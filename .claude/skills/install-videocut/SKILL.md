---
name: install-videocut
description: Install, open, repair, update or uninstall Video Cut (the local video editing app from github.com/derFlok/video-cut) for a person on their Mac, in plain language. Use when someone asks you to install Video Cut, says "Video Cut startet nicht" / "Video Cut does not start", wants to update ("Video Cut aktualisieren") or remove it, or the launcher shows an error.
---

# Video Cut: install and look after it for a person

You are the person's own helper. They probably do not use a terminal and should not have to.
**Everything you write is read by them.** Use their language (German if they write German,
with "du"), short plain sentences, no jargon: never mention archives, tarballs, ports,
Python, FFmpeg, Node, background jobs, logs, permissions or what something costs you. Do the
technical work yourself; tell them what is happening and how long it takes.

**Your first message, before any command,** is the announcement in Install step 1 (in their
language). Then work without narrating each command.

Fixed facts:

- Code lives in `~/VideoCut/app`. Videos, brands and models live in `~/VideoCut` (outside the
  code). The double-click app is `~/Applications/Video Cut.app` (the Applications folder
  inside the home folder, "Programme" in German Finder).
- Repository: `https://github.com/derFlok/video-cut`. Download it as a tarball with `curl`.
- Needs a Mac with Apple Silicon (M1 or newer), macOS 14+, about 8 GB free disk. A first
  install downloads about 2.5 GB, uses about 4 GB of disk and takes 5 to 10 minutes.
- Later conversations (repair, update) may start in another folder: `cd ~/VideoCut/app` first;
  this skill and the project's `CLAUDE.md` are there.

Never:

- use `sudo`, Homebrew, MacPorts or Rosetta, or install the Xcode command line tools;
- run `git`, `python3`, `pip`, `make`, `clang` or `brew` at all, not even with `--version`
  to look around: on a fresh Mac `git` and `python3` are stubs that open a large "install
  developer tools" dialog. `curl`, `tar`, `plutil`, `uname`, `sw_vers`, `df` and `open` are
  always there and are all you need;
- read, write or create anything in `~/.claude` (Claude Code's own settings and sign-in), and
  never read the person's API key; they type a key only into Video Cut's own settings page;
- send the person to the Terminal or give them a command to type;
- cut, approve or process a video during an install or a repair (`videocut run`, `--approve`,
  `--demo-data`): a video is made by the person in the app, and the plan is theirs to approve;
- delete anything in `~/VideoCut` except `~/VideoCut/app` unless the person explicitly asks.

Ask the person only what is theirs to decide. A normal install needs no question at all.

## Install

1. **Announce, before anything else.** Send one short paragraph, for example:

   > I'm installing Video Cut for you now. It downloads about 2.5 GB (tools and a speech
   > recognition model), uses about 4 GB on your Mac and takes about 5 to 10 minutes. Nothing
   > else on your Mac is changed: everything goes into the folder "VideoCut" in your home folder
   > and the app "Video Cut" in your Applications folder. Your recordings never leave your Mac.

   German:

   > Ich installiere Video Cut jetzt für dich. Es lädt etwa 2,5 GB herunter (Werkzeuge und ein
   > Spracherkennungsmodell), belegt etwa 4 GB auf deinem Mac und dauert etwa 5 bis 10 Minuten.
   > Sonst wird an deinem Mac nichts verändert: Alles kommt in den Ordner „VideoCut“ in deinem
   > Benutzerordner und die App „Video Cut“ in deinen Programme-Ordner. Deine Aufnahmen
   > verlassen deinen Mac nie.

2. **Check the Mac** (no questions): `uname -m` must be `arm64`, `sw_vers -productVersion` 14
   or newer, `df -g ~ | tail -1` at least 8 GB free in the "Avail" column. If something fails,
   explain it plainly and stop (an Intel Mac cannot run Video Cut).
3. **Already installed?** If `~/VideoCut/app/start.sh` exists, this is a repair or an update:
   go to "Repair" below instead.
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
5. **Install.** Run it in the foreground with the longest timeout your Bash tool allows
   (10 minutes); never as a background job, and do not end your turn while it runs:

   ```sh
   cd ~/VideoCut/app && ./start.sh
   ```

   It usually finishes in 3 to 6 minutes. If the timeout hits first (slow connection), run it
   again: finished steps are skipped and the download continues where it stopped. Between runs
   tell the person the step in plain words ("Schritt 7 von 7: das Spracherkennungsmodell wird
   geladen, 1,6 GB"). It ends with a check list and a summary. On a failure see "Repair".
6. **Create the double-click app** and open it:

   ```sh
   cd ~/VideoCut/app && .tools/bin/uv run --frozen videocut launcher
   open ~/Applications/"Video Cut.app"
   ```

   Only if the person asks for it in the main Applications folder: add `--system`. Start at
   login only if they ask: `--at-login` (off by default).
7. **Hand over to the app.** The browser shows Video Cut's welcome screen; it takes care of
   connecting Claude. Do not sign anything in yourself and do not install anything else.
   - Run `claude auth status --text` (if a `claude` program is on PATH) only to know what the
     screen will show. If it says not signed in, or there is no such program, tell the person
     in one sentence: the welcome screen explains the two ways to connect Claude and how.
   - The two ways, if they ask: their own Claude subscription (for personal use, Anthropic's
     terms; for a team or a company an API key is the right way) or an API key from the
     Anthropic Console. That is their choice; do not decide it.
8. **Final message.** Send exactly this, in their language, and nothing else (no paths,
   ports, tool names, sizes or lists). German:

   > Video Cut ist installiert und im Browser geöffnet. Zum nächsten Start: Programme → Video
   > Cut. Bei Fragen oder Problemen sag mir einfach: „Video Cut startet nicht“ oder „Video Cut
   > aktualisieren“.

   English:

   > Video Cut is installed and open in your browser. To start it next time: Applications →
   > Video Cut. If you have questions or problems, just tell me: "Video Cut does not start" or
   > "Update Video Cut".

   At most one more sentence: "The welcome screen in the browser now shows how to connect
   Claude." / „Der Willkommens-Bildschirm im Browser zeigt dir jetzt, wie du Claude
   verbindest.“

## Repair ("Video Cut startet nicht", errors, the launcher shows an alert)

1. `cd ~/VideoCut/app`, then run the doctor and read its JSON:

   ```sh
   .tools/bin/uv run --frozen videocut doctor --json
   ```

   Each entry in `checks` has `ok`, `required`, `detail` and a `fix`. Do what the `fix` of
   each failed check says (most often: run `./start.sh` again, in the foreground). If
   `.tools/bin/uv` itself is missing, run `./start.sh` first. A failed `claude auth` check is
   for the app's welcome/settings screen, not for you to fix.
2. Look at the logs: `~/VideoCut/state/launcher.log` and `~/VideoCut/state/server.log`
   (last 50 lines).
3. If the app does not open: `.tools/bin/uv run --frozen videocut open` prints what happens.
   Another program on port 4300 is fine (the next free port is used).
4. Rebuild the double-click app if it is gone or moved: `videocut launcher` (Install step 6).
5. Still broken: download the code again (Install step 4, it overwrites the code only) and run
   `./start.sh`. Never delete `~/VideoCut/jobs`.

Tell the person the result in one or two plain sentences.

## Update ("Video Cut aktualisieren")

```sh
cd ~/VideoCut/app && .tools/bin/uv run --frozen videocut update
```

It downloads the newest release, then stops the running app (not while a video is being
processed: then tell the person to wait, or add `--force` if they agree to stop it), keeps the
installed tools, runs `./start.sh` and opens the app again. Run it in the foreground like
`start.sh`. Tell the person what changed if `CHANGELOG.md` says so (one or two sentences).

## Uninstall

Tell the person what will be removed and what stays, then do it:

- removed: the program and its tools in `~/VideoCut/app` and the double-click app
  (`uninstall.sh` stops the app and removes the launcher and a login item first);
- the speech models in `~/VideoCut/models` (about 1.6 GB): remove them too unless the person
  wants to reinstall later;
- **kept unless the person explicitly wants them gone**: their videos in `~/VideoCut/jobs`,
  brands in `~/VideoCut/brands`, settings `~/VideoCut/settings.json`;
- an API key stored by Video Cut is in the macOS keychain under "videocut"; remove it only if
  the person asks: `security delete-generic-password -s videocut -a anthropic-api-key`.

```sh
cd ~/VideoCut/app && ./uninstall.sh
cd ~ && rm -rf ~/VideoCut/app
```
