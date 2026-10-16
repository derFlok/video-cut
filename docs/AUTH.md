# Connecting Claude

To do the editing, Video Cut runs the official Claude Code
program (`claude`) on your Mac, the same one you used to install Video Cut. There are two ways to
give it access. You choose at the first start and can change it later in the settings; the
editing is the same either way.

## How Video Cut finds out what you have

At the first start Video Cut asks the official program whether it is signed in (it runs
`claude auth status`, a documented command that only answers "yes/no and how"). It never opens
Claude Code's own files or keychain entry and never sees your password or login token.

- **Signed in with a Claude plan (Pro or Max):** Video Cut tells you it found a signed-in Pro or Max plan and lets you choose
  whether to use it or to store an API key instead.
- **Not signed in:** Video Cut explains both ways below, with a button that opens the Claude
  Console (for a key) and a button that shows how to sign in to Claude Code.

## Way 1: your Claude subscription (Pro or Max), for yourself

You already pay for Claude Pro or Max and Claude Code is signed in with it. Video Cut starts that
Claude Code for you. Nothing extra to pay; each video uses part of your plan's usage limits, the
same limits your chats with Claude and your other Claude Code work share.

Please keep to these rules (from Anthropic's terms, see [`TERMS-REVIEW.md`](TERMS-REVIEW.md)):

- **Only for yourself, on your own Mac, with your own account.** Do not cut videos for other
  people with your subscription, and do not share your account.
- **Sign in inside Claude Code**, never anywhere else. Video Cut has no Claude login of its own.
- **Business videos:** in the EEA and Switzerland, Anthropic's consumer terms say "Non-commercial use only". Until
  Anthropic has answered our question on this, use an API key (Way 2) for videos for your business,
  a team or a company.
- If an `ANTHROPIC_API_KEY` is set on your Mac, Claude Code always uses that key and bills it
  instead of your plan. Video Cut removes it for its own runs in subscription mode.

## Way 2: an API key (pay per video)

1. Open the [Claude Console](https://platform.claude.com) (formerly Anthropic Console), create an account, add a payment
   method and create a key.
2. Paste the key into Video Cut (settings, or at the first start). It is stored in the macOS
   keychain (entry "videocut") and nowhere else; Video Cut passes it to Claude Code only for its
   own runs.

You pay Anthropic per use. Video Cut shows the cost of every video while it works and on the
result page.

**What it costs** (measured on our own test videos, October 2026, every run is logged in the
job's `work/runs.jsonl`):

| | Cost |
|---|---|
| One video up to the first finished version (plan, cut, captions and graphics) | about USD 1–3.50 |
| Each correction afterwards | about USD 0.40–1.50 |
| A long screen demo with several correction rounds | about USD 6–9 |

## What leaves your Mac

Only to Anthropic, only through your own Claude access (subscription or key):

- the **transcript text** of your recording,
- **small, downscaled still images** at the points where Claude has to decide something,
- **screen text read on your Mac** (OCR, Apple's own text recognition) when you add a screen
  recording, screenshots or slides.

Your recordings, the audio and the finished videos never leave your Mac. Video Cut itself sends
nothing to anyone else: no telemetry, no usage statistics, no crash reports, no account. The only
other network traffic from Video Cut is the installation and updates, which download the tools and
models listed in [`manifest/tools.json`](../manifest/tools.json) and the packages pinned in the
lock files. Claude Code is Anthropic's program and handles its own data under Anthropic's terms
and privacy policy (see Anthropic's
[data usage page](https://code.claude.com/docs/en/data-usage) for what it sends and how to turn
optional reporting off).

## What Video Cut never does

- It never reads, copies, stores or forwards Claude login tokens or Claude Code's credentials.
- It never changes the `claude` program or its settings.
- It never sends your recordings or audio anywhere.
- It never offers a Claude sign-in of its own.

`./start.sh --check` (or asking your Claude Code "Is Video Cut connected to Claude?") shows which
way is active.

Video Cut is an independent project. It is not affiliated with or endorsed by Anthropic.
