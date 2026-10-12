# Connecting Claude

Video Cut does the editing with Claude, run headless through the official Claude Code program
(`claude -p`). There are two ways to give it access. You choose in the settings; nothing else in
the pipeline changes.

## 1. API key (supported for everyone)

Create a key in the [Anthropic Console](https://console.anthropic.com). Video Cut stores it in the
macOS keychain and passes it to `claude` as `ANTHROPIC_API_KEY` for each run. You pay per use;
the app shows the cost of every job. Rough estimate: USD 0.50–1.50 per short video (not yet
measured).

## 2. Local mode: your own Claude Code sign-in (personal use only)

If you have Claude Code installed and signed in with your own Pro or Max plan, Video Cut can start
that `claude` program for you. Your usage then counts against your own plan limits, which are
designed for ordinary individual use.

Please note Anthropic's terms: third-party products may not offer claude.ai sign-in or route
requests through Pro/Max credentials on behalf of their users without prior approval. Local mode
is therefore meant for using Video Cut yourself on your own machine. If you deploy Video Cut for a
team or a company, use an API key.

## What Video Cut never does

- It never reads, copies, stores or forwards Claude login tokens.
- It never modifies the `claude` program.
- It never sends your recordings or audio to Anthropic, only transcript text and downscaled
  still images.

`./start.sh --check` shows which mode is active.
