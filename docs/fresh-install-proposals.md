# Proposed changes to the install skill and INSTALL.md

From the Claude-driven install runs on `origin/installer` (`645fd78`), English and German, see
`docs/fresh-install-report.md`. Both runs installed Video Cut correctly with no question to the
person, no `sudo`, Homebrew or `git`. These changes fix what a person without IT knowledge
would stumble over. The file to change is `.claude/skills/install-videocut/SKILL.md` unless
noted. Each proposal gives the finding, then the text.

## 1. The person heard nothing useful before the install started

Finding: neither run sent the step-3 announcement (size, time, "recordings stay here"). The
first messages were "Clean archive, all paths stay under one top-level folder." /
"Ich schaue mir zuerst den Inhalt des Installers an, bevor ich etwas ausführe." and "I'll wait
for it to finish rather than poll, to keep this cheap."

Replace the paragraph after the opening heading ("You are the person's own helper. …") with:

> You are the person's own helper. They probably do not use a terminal and should not have to.
> **Everything you write is read by them.** Use their language (German if they write German, with
> "du"), short plain sentences, no jargon: never mention archives, tarballs, ports, Python,
> FFmpeg, Node, background jobs, logs, permissions or what something costs you. Do the technical
> work yourself; tell them what is happening and how long it takes.
>
> **Your first message, before any command**, is the announcement from Install step 3 (in their
> language). Then work without narrating each command.

and in Install step 3 add: "Send this as your first message, before step 1."

## 2. The skill note in `~/.claude/skills` hits a "sensitive file" permission prompt

Finding: Claude Code treats `~/.claude/skills` as sensitive. `mkdir -p ~/.claude/skills` and the
`ln -sfn` were refused in both runs ("Claude requested permissions to edit ~/.claude/skills which
is a sensitive file"). In the desktop app the person gets a permission dialog with that wording,
with no explanation. When it was refused, the English run handed the person a shell command to
run themselves.

Replace Install step 7 with:

> 7. **Remember how to help later** (optional). Claude Code asks the person before it writes into
>    its own settings folder. First say, in their language: "One more small thing: I'm saving a
>    short note in my own settings so I can help you with Video Cut later. Claude will ask you to
>    allow this. Click 'Allow'." Then run:
>
>    ```sh
>    mkdir -p ~/.claude/skills && ln -sfn ~/VideoCut/app/.claude/skills/install-videocut ~/.claude/skills/install-videocut
>    ```
>
>    If it is refused, drop it without comment: Video Cut works without it, and the person
>    must never be given a command to type.

## 3. The person was sent to the Terminal to sign in

Finding: both runs ended with "Run `claude auth login` in Terminal" / "Öffne dafür einmal das
Terminal und tippe `claude auth login`". That contradicts INSTALL.md ("You do not need a
terminal") and is the hardest step for this audience.

Replace the third bullet of Install step 8 ("If the screen says no `claude` program was found
…") with:

> - If they want to use their subscription and the screen says the `claude` program is missing
>   or not signed in: do it for them, never send them to the Terminal. Missing: install
>   Anthropic's official program yourself (`curl -fsSL https://claude.ai/install.sh | bash`, no
>   sudo, see https://code.claude.com/docs). Not signed in: run `claude auth login` yourself; it
>   opens their browser, where they sign in with their Claude account. Tell them: "Your browser
>   is opening now. Sign in there with your Claude account, then come back and click 'Check
>   again' / 'Erneut prüfen'."

## 4. Waiting for `start.sh`

Finding: both runs started `./start.sh` as a background job and ended their turn ("I'll pick up
as soon as it completes"). In the command-line print mode the turn ends there and the person
sees nothing until they write again; the test needed a nudge ("Yes, please. Just go ahead.") to
finish. The desktop app may wake the session when the job ends (not checked).

Replace the first sentence of Install step 5 with:

> 5. **Install.** Run it in the foreground with the longest timeout your Bash tool allows
>    (10 minutes). It usually finishes in 3 to 6 minutes; on a slow connection the timeout may
>    hit first. Then run it again: finished steps are skipped and the download continues. Do
>    not end your turn while it runs. Between runs tell the person the step it is on in plain
>    words ("Step 7 of 7: downloading the speech model, 1.6 GB").

## 5. The final message is too technical and too long

Finding: the final messages listed tools ("Python, FFmpeg, the headless browser for titles"),
"serving on local port 4417 because 4300 was taken, which is normal", disk sizes and
`~/VideoCut/jobs`. Useful for us, noise for the person.

Add at the end of Install step 8:

> Keep the final message short (about eight lines): it is ready and open in the browser; what the
> welcome screen asks and the two choices in one line each; start it later by double-clicking
> "Video Cut" in the Applications folder of their home folder; ask you when something does not
> work or for an update. No paths, ports, tool names or sizes unless they ask.

## 6. INSTALL.md

- "About 10 minutes" fits the measurements (3 to 4 minutes of Claude's work with the model
  cached; about 4 minutes of `start.sh` plus Claude's turns with a full download on fast
  broadband). Keep it.
- English section "What you need": add "an internet connection (about 2.5 GB is downloaded)".
- The sentence "Afterwards you start Video Cut by double-clicking 'Video Cut' in your
  Applications folder" should say **in the Applications folder of your home folder** (it is
  `~/Applications`, not `/Applications`; a person looking in Finder's "Applications" sidebar
  entry will not find it). Same in German: "im Ordner „Programme“ deines Benutzerordners".
  Alternatively make the launcher also add a Dock item; not checked.
