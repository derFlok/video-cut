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

What leaves with a finished video: besides the picture and sound, the MP4 carries a
container comment naming the AI models when AI-generated material is in it, and a C2PA
provenance manifest (when `c2patool` is installed): "Video Cut" and its version as the
generator, that the video was edited, and the names of the image models used. It holds no
prompts, no person's name, no file paths and no account data; it is signed with a
certificate made on this Mac ("Video Cut local"), which checking tools show as an unknown
signer. Prompts and stock credits stay in the job folder (`work/disclosure.json`).

Please report vulnerabilities privately via GitHub Security Advisories on this repository,
not in public issues.
