# Security

Video Cut runs Claude on material you upload (recordings, screenshots, brand guides, reference
videos). Text inside that material could try to give Claude instructions. The design limits what
such text can do:

- Claude runs headless with an explicit tool allow-list: the Video Cut pipeline commands and file
  access inside the job folder. No free shell, no network access from the agent run.
- The web app binds to `127.0.0.1` only and requires a per-session token.
- One job at a time, each with a turn and time limit.
- Video Cut never reads, stores or forwards Claude login tokens. It only starts the official
  `claude` program, or passes an API key from the macOS keychain to it.

Please report vulnerabilities privately via GitHub Security Advisories on this repository,
not in public issues.
