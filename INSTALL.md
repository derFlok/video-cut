# Installing Video Cut / Video Cut installieren

**Deutsch weiter unten.**

## English

### What you need

- A Mac with Apple Silicon (M1 or newer) and macOS 14 or newer.
- About 8 GB of free disk space (15 GB recommended).
- Claude Code, already installed and signed in (the desktop app or the command line).
- About 10 minutes. The installation downloads about 2.5 GB.

You do not need a terminal, developer tools, Homebrew or an administrator password.

### What you do

Open Claude Code and send this one sentence:

> Install https://github.com/derFlok/video-cut for me and guide me through it.

### What Claude Code does for you

1. Checks that your Mac can run Video Cut (chip, macOS version, free disk space).
2. Tells you how much it will download and how long it takes.
3. Downloads Video Cut into the folder `VideoCut/app` in your home folder.
4. Installs everything Video Cut needs into that folder: Python, FFmpeg, a headless
   browser for the titles, a speech recognition model and a face tracking model. Every
   download is pinned to a fixed version and checked against a checksum. Nothing is
   installed system-wide, nothing asks for your password.
5. Creates the app "Video Cut" in the Applications folder of your home folder
   (`~/Applications`) and opens it.
6. The app shows a welcome screen: choose how Video Cut connects to Claude (your own Claude
   subscription for personal use, or an API key), test the connection, done.

Afterwards you start Video Cut by double-clicking "Video Cut" in your Applications folder.
"Quit" in the app stops it.

### Later

Ask Claude Code in plain words:

- "Video Cut does not start" — it runs the built-in check (`videocut doctor`) and fixes what
  it reports.
- "Update Video Cut" — it downloads the newest version and keeps your videos and settings.
- "Remove Video Cut" — it removes the program and tools; your videos in
  `~/VideoCut/jobs` stay unless you say you want them deleted.

### What stays on your Mac

Your recordings and their audio never leave your Mac. Only the transcript text, small still
images and text read from your screen recordings go to Claude. See `docs/AUTH.md` for the
two ways to connect and the terms that apply.

---

## Deutsch

### Was du brauchst

- Einen Mac mit Apple-Chip (M1 oder neuer) und macOS 14 oder neuer.
- Etwa 8 GB freien Speicher (15 GB empfohlen).
- Claude Code, schon installiert und angemeldet (die Desktop-App oder die Kommandozeile).
- Etwa 10 Minuten. Die Installation lädt etwa 2,5 GB herunter.

Du brauchst kein Terminal, keine Entwicklerwerkzeuge, kein Homebrew und kein
Administrator-Passwort.

### Was du tust

Öffne Claude Code und schicke diesen einen Satz:

> Installiere https://github.com/derFlok/video-cut für mich und führe mich durch die Einrichtung.

### Was Claude Code für dich erledigt

1. Prüft, ob dein Mac Video Cut ausführen kann (Chip, macOS-Version, freier Speicher).
2. Sagt dir, wie viel heruntergeladen wird und wie lange es dauert.
3. Lädt Video Cut in den Ordner `VideoCut/app` in deinem Benutzerordner.
4. Installiert alles, was Video Cut braucht, in diesen Ordner: Python, FFmpeg, einen
   unsichtbaren Browser für die Einblendungen, ein Spracherkennungs- und ein
   Gesichtserkennungsmodell. Jeder Download hat eine feste Version und wird per Prüfsumme
   kontrolliert. Nichts wird systemweit installiert, nichts fragt nach deinem Passwort.
5. Legt die App „Video Cut“ im Ordner „Programme“ deines Benutzerordners an
   (`~/Applications`) und öffnet sie.
6. Die App zeigt einen Willkommens-Bildschirm: Du wählst, wie Video Cut mit Claude arbeitet
   (dein eigenes Claude-Abo für die persönliche Nutzung oder ein API-Schlüssel), prüfst die
   Verbindung, fertig.

Danach startest du Video Cut mit einem Doppelklick auf „Video Cut“ im Ordner „Programme“.
„Beenden“ in der App hält es an.

### Später

Frag Claude Code in normalen Worten:

- „Video Cut startet nicht“ – es führt die eingebaute Prüfung aus (`videocut doctor`) und
  behebt, was sie meldet.
- „Video Cut aktualisieren“ – es lädt die neueste Version und behält deine Videos und
  Einstellungen.
- „Video Cut entfernen“ – es entfernt Programm und Werkzeuge; deine Videos in
  `~/VideoCut/jobs` bleiben, außer du sagst ausdrücklich, dass sie weg sollen.

### Was auf deinem Mac bleibt

Deine Aufnahmen und ihr Ton verlassen deinen Mac nie. An Claude gehen nur der Text der
Aufnahme, kleine Standbilder und Text, der aus Bildschirmaufnahmen gelesen wurde. Die beiden
Wege, Claude zu verbinden, und die geltenden Bedingungen stehen in `docs/AUTH.md`.

---

## For Claude Code (the person's helper)

Follow the skill in this repository: `.claude/skills/install-videocut/SKILL.md`
(online: https://github.com/derFlok/video-cut/blob/main/.claude/skills/install-videocut/SKILL.md).
It covers install, repair, update and uninstall. The short version of the install:

```sh
mkdir -p ~/VideoCut/app
curl -fsSL --retry 4 https://codeload.github.com/derFlok/video-cut/tar.gz/refs/heads/main \
  | tar -xzf - --strip-components=1 -C ~/VideoCut/app
cd ~/VideoCut/app && ./start.sh                      # long-running; relay the "Step n/7" lines
.tools/bin/uv run --frozen videocut launcher         # ~/Applications/Video Cut.app
open ~/Applications/"Video Cut.app"                  # the first-start screen takes over
```

Do not use `git`, `/usr/bin/python3`, `sudo` or Homebrew (on a fresh Mac `git` and `python3`
open the developer tools installer). Never read `~/.claude` or the person's API key. On
errors: `videocut doctor --json` and follow each failed check's `fix`.
