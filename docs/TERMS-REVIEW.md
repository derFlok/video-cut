# Terms review: Video Cut and Anthropic's terms

Read on **2026-10-16**. Every quote below is copied word for word from the page named above it,
fetched on that date. This is a reading of the documents with the quotes that support it, not
legal advice. Terms change: re-read the sources before relying on this page, and update the date.

What is being checked: Video Cut is a free, open-source (Apache-2.0) Mac app. It starts the
official, unmodified `claude` program (Claude Code) that the person installed and signed in to
themselves, headless (`claude -p`) with a restricted tool allow-list. There are two ways to give
it access: (1) the person's own Anthropic API key, stored in the macOS keychain and passed to
`claude` as `ANTHROPIC_API_KEY`; (2) "local mode": the person's own Claude Code sign-in with
their own Pro or Max plan. Video Cut never reads, stores or forwards login tokens, has no
servers and makes no API calls of its own. To find out whether a sign-in exists it runs the
official program (`claude auth status`; today in `./start.sh --check`, in the app's first start
from Phase 4 on).

## Short answers

| Question | What the quoted text says | Based on |
|---|---|---|
| (a) May a person use their own Pro/Max sign-in through Video Cut on their own Mac? | K1 and L7 speak for it; A2 can be read against it → **unclear, ask Anthropic (Q1)** | K1, L7, L2 vs. A2, L6, U3 |
| Running `claude -p` headless on a subscription | No conflict found: K1/K2 name `claude -p` with subscription limits; T4 excepts uses "we otherwise explicitly permit" | K1, K2, T4 |
| Detecting the sign-in in the background with `claude auth status` | No prohibition found; documented command, no credentials read | C1, L6 |
| Using an API key | No conflict found (Commercial Terms) | T2, M2, L7 |
| Product name "Video Cut" | No conflict found (no Anthropic name in it) | L3 |
| Saying in plain text that Video Cut runs Claude Code | Explicitly named as permitted | L3 |
| Plain-text lines such as "Claude cuts your videos", "Claude-Abo erkannt" | **Unclear, ask Anthropic (Q2)**; the explicit permission names only "runs Claude Code" | L3, T8, G2, A3 |
| Business videos on a Pro/Max plan (EEA consumers) | **Unclear, ask Anthropic (Q3)**; the EEA Consumer Terms say "Non-commercial use only" | T1, T7 |
| Do we as the open-source project have to accept the Commercial Terms? | **Unclear, ask Anthropic (Q4)**; we meet both listed conditions either way | L2 |
| One subscription cutting videos for other people (e.g. an agency for clients) | Quoted text speaks against it | T3, L6, L4 |

## (b) Wording for the README and the app

Wording we derive from the quotes below:

1. The product is called **Video Cut**; "Claude", "Claude Code" and "Anthropic" never appear in the
   product, feature or company name, and no Anthropic or Claude logo or Claude Code-style visual is
   used (L3, A3).
2. It may say in plain text that Video Cut **runs Claude Code** (L3).
3. "Video Cut is an independent project. It is not affiliated with or endorsed by Anthropic." Not
   required by any quote, but it matches L3, T8 and G3 (no implied endorsement).
4. Local mode is for **one person using their own subscription on their own Mac**; for a team, a
   company or work for other people, use an API key (L4, L6, T3). Usage counts against the
   person's plan limits (K4).
5. The person signs in **inside Claude Code itself**, never in Video Cut (L6: "sign-in to a Claude
   account must complete through Anthropic's own flow").
6. If `ANTHROPIC_API_KEY` is set, Claude Code uses the key and bills it instead of the plan (K3, C2).
7. EEA consumers: the Consumer Terms say "Non-commercial use only" (T7); until Q3 is answered the
   README points business use to an API key.

## (c) Is anything in our flow not allowed?

- **Background detection** with `claude auth status`: documented, with a documented exit code and
  JSON output (C1). Video Cut reads no credential file and no keychain entry of Claude Code, which
  is what L6 forbids ("may not collect, store, or intermediate Claude.ai credentials or session
  tokens"). No prohibition found. Do **not** use `claude setup-token` or `CLAUDE_CODE_OAUTH_TOKEN`
  to move a token around: that would come close to "intermediate [...] session tokens" (L6).
- **`claude -p` headless on a subscription:** K1/K2 name it explicitly. T4 forbids automated access
  "Except when you are accessing our Services via an Anthropic API Key or where we otherwise
  explicitly permit it"; K1 reads as such a permission for `claude -p` and third-party apps (it is a help-centre note, not part of the terms; Q1 asks for confirmation).
- **Technical notes found while reading** (for the code, not legal points):
  - H4: `--bare` "will become the default for `-p` in a future release", and H2/H3: bare mode
    does not use the subscription login. Local mode would then stop working unless it opts out of
    bare mode explicitly. Video Cut today runs local mode without `--bare` and with
    `--setting-sources project` and `--strict-mcp-config`.
  - H5: without `--bare`, `claude -p` loads what is configured in the working directory or
    `~/.claude`, with no trust prompt. Video Cut limits this with `--setting-sources project` and
    `--strict-mcp-config`; whether hooks from user settings are fully excluded should be
    re-checked on every Claude Code update (CLAUDE.md rule 8).
  - K3/C2: a set `ANTHROPIC_API_KEY` always wins in `-p`. Video Cut removes it from the
    environment in local mode.
- **Product naming:** no conflict found for "Video Cut" (L3). Taglines that use "Claude" as the subject are the
  open point Q2.

## Sources and quotes

### S1. Claude Code docs: Legal and compliance
<https://code.claude.com/docs/en/legal-and-compliance>, read 2026-10-16 (page has no date).

- **L1:** "Your use of Claude Code is subject to:" / "[Commercial Terms of Service] - for Team,
  Enterprise, and Claude API users" / "[Consumer Terms of Service] - for Free, Pro, and Max users"
- **L2:** "Unless we've mutually agreed otherwise, preinstalling or running Claude Code in your
  products or services (e.g. in hosted sandboxes or other agent infrastructure) requires agreeing
  to our [Commercial Terms of Service] and complying with the conditions below:"
  - "**The Claude Code binary must not be modified.** Claude Code must be installed and run as
    published by Anthropic, and customers may not remove, disable, or restrict any authentication
    method built into it (including methods that permit signing in with a Claude account or the
    user's own API key)."
  - "**Customers may not pay for, resell, or intermediate Claude usage on their end users'
    behalf.** Each end user must authenticate with their own Anthropic API key, Claude
    subscription plan credentials, or 3P inference provider credential (Amazon Bedrock, Google
    Cloud's Agent Platform, Microsoft Foundry). That usage is billed directly to the end user under
    their own agreement with Anthropic or, for third-party inference providers, with the applicable
    provider."
- **L3:** "**Using the Claude Code name and logo.** You can accurately say, in plain text, that
  your product has Claude Code preinstalled or that it runs Claude Code. But you can't use the
  Claude Code or Anthropic names or logos as part of your own product, feature, or company name,
  in your own logo, or in a way that suggests Anthropic built, endorses, or is partnered with your
  product. Any other use of Anthropic's names or logos is governed by our [Trademark Guidelines]
  and requires our written permission."
- **L4:** "Advertised usage limits for Pro and Max plans assume ordinary, individual usage of
  Claude Code and the Agent SDK."
- **L5:** "**OAuth authentication** is intended exclusively for purchasers of Claude Free, Pro,
  Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude
  Code and other native Anthropic applications."
- **L6:** "**Developers** building products or services that interact with Claude's capabilities,
  including those using the [Agent SDK], should use API key authentication through [Claude Console]
  or a supported cloud provider. Anthropic does not permit third-party developers to offer
  Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan
  credentials on behalf of their users. Moreover, developers may not collect, store, or
  intermediate Claude.ai credentials or session tokens — sign-in to a Claude account must complete
  through Anthropic's own flow."
- **L7:** "This does not restrict how customers provision and manage their own API keys or
  third-party inference provider credentials [...] provided the resulting usage is billed to the
  key owner under their agreement with Anthropic (or the applicable provider) and is not resold or
  intermediated as described above. Nor does it prevent an end user from signing in to the
  unmodified Claude Code binary with their own Claude subscription, including where a platform
  hosts Claude Code as described under *Can customers offer Claude Code in their products?* above."
- **L8:** "Anthropic reserves the right to take measures to enforce these restrictions and may do
  so without prior notice."
- **L9:** "For questions about permitted authentication methods for your use case, please
  [contact sales]."

### S2. Agent SDK overview
<https://code.claude.com/docs/en/agent-sdk/overview>, read 2026-10-16.

- **A1:** "To drive the same agent loop from a language other than Python or TypeScript, [run the
  CLI as a subprocess] with the `-p` flag and `--output-format json`."
- **A2:** "Unless previously approved, Anthropic does not allow third party developers to offer
  claude.ai login or rate limits for their products, including agents built on the Claude Agent
  SDK. Use the API key authentication methods described in the [Quickstart] instead."
- **A3:** "For partners integrating the Claude Agent SDK, use of Claude branding is optional."
  Not permitted: "\"Claude Code\" or \"Claude Code Agent\"" and "Claude Code-branded ASCII art or
  visual elements that mimic Claude Code". "Your product should maintain its own branding and not
  appear to be Claude Code or any Anthropic product."
- **A4:** "Use of the Claude Agent SDK is governed by [Anthropic's Commercial Terms of Service],
  including when you use it to power products and services that you make available to your own
  customers and end users [...]"

### S3. Claude Code docs: Run Claude Code programmatically
<https://code.claude.com/docs/en/headless>, read 2026-10-16.

- **H1:** "The [Agent SDK] gives you the same tools, agent loop, and context management that power
  Claude Code. It's available as a CLI for scripts and CI/CD, or as [Python] and [TypeScript]
  packages for full programmatic control."
- **H2:** "Set `ANTHROPIC_API_KEY` before running it, because bare mode doesn't use your
  subscription login:"
- **H3:** "In bare mode, Claude Code never reads OAuth credentials or the system keychain."
- **H4:** "`--bare` is the recommended mode for scripted and SDK calls, and will become the
  default for `-p` in a future release."
- **H5:** "Without it, `claude -p` discovers and loads them as an interactive session would,
  including anything configured in the working directory or `~/.claude`." / "A `-p` session shows
  no workspace trust dialog and no per-server approval prompt."

### S4. Claude Code docs: Authentication, CLI reference
<https://code.claude.com/docs/en/authentication>, <https://code.claude.com/docs/en/cli-reference>,
read 2026-10-16.

- **C1:** "`claude auth status` | Show authentication status as JSON. Use `--text` for
  human-readable output. Exits with code 0 if logged in, 1 if not."
- **C2:** "`ANTHROPIC_API_KEY` environment variable. [...] In non-interactive mode (`-p`), the key
  is always used when present."
- **C4:** "On macOS, credentials are stored in the encrypted macOS Keychain."

### S5. Claude Help Center
<https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan>,
read 2026-10-16.

- **K1:** "Update October 7, 2026: Claude Max and Team plans now include monthly API credits,
  which cover the Claude Agent SDK, claude -p, the Claude API, and Claude Managed Agents. You can
  still use the Claude Agent SDK, claude -p, and third-party apps with your subscription limits."
- **K2:** "Update June 15, 2026: We’ve paused the previously-announced changes to Claude Agent SDK
  usage. For now, nothing has changed: Claude Agent SDK, claude -p, and third-party app usage still
  draw from your subscription limits."

<https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan>,
read 2026-10-16.

- **K3:** "Important: If you have an ANTHROPIC_API_KEY environment variable set on your system,
  Claude Code will use this API key for authentication instead of your Claude subscription (Pro,
  Max, Team, or Enterprise plans), resulting in API usage charges rather than using your
  subscription's included usage."
- **K4:** "These limits are shared across Claude and Claude Code, meaning all activity in both
  tools counts against the same limits."

### S6. Consumer Terms of Service
<https://www.anthropic.com/legal/consumer-terms>, read 2026-10-16, "Effective October 8, 2025".
The page served from here was the **EEA/Switzerland version** (Anthropic Ireland, Limited). The
version for other regions (Anthropic, PBC) could not be fetched and may be worded differently.

- **T1:** "These Terms apply to you if you are a consumer who is resident in the European Economic
  Area or Switzerland. You are a consumer if you are acting wholly or mainly outside your trade,
  business, craft or profession in using our Services."
- **T2:** "Please note: Our Commercial Terms of Service govern your use of any Anthropic API key,
  the Anthropic Console, or any other Anthropic offerings that reference the Commercial Terms of
  Service. For clarity, this does not include Claude.ai or Claude Pro use for individuals or
  entities."
- **T3:** "You may not share your Account login information, Anthropic API key, or Account
  credentials with anyone else or make your Account available to anyone else."
- **T4:** "You may not access or use, or help another person to access or use, our Services in the
  following ways:" [...] "Except when you are accessing our Services via an Anthropic API Key or
  where we otherwise explicitly permit it, to access the Services through automated or non-human
  means, whether through a bot, script, or otherwise."
- **T7:** "Non-commercial use only. You agree that you will not use our Services for any
  commercial or business purposes and we and our Providers have no liability to you for any loss
  of profit, loss of business, business interruption, or loss of business opportunity."
- **T8:** "Use of our brand. You may not, without our prior written permission, use our name,
  logos, or other trademarks in connection with products or services other than the Services, or
  in any other way that implies our affiliation, endorsement, or sponsorship. To seek permission,
  please email us at marketing@anthropic.com."

### S7. Usage Policy
<https://www.anthropic.com/legal/aup>, read 2026-10-16. The page shows "Effective November 12,
2026" (a date after the reading date).

- **U2:** "Intentionally bypass or interfere with capabilities, restrictions, or guardrails
  established within our products, including by circumvention of the account sign-up or log-in
  process [...] without prior authorization from Anthropic"
- **U3:** "Resell, proxy, or otherwise provide access to Claude through unauthorized means,
  including services that route requests through consumer subscriptions or misrepresent the
  product or client being used"

### S8. Commercial Terms of Service
<https://www.anthropic.com/legal/commercial-terms>, read 2026-10-16, "Effective June 17, 2025".

- **M1:** "They govern Customer’s use of Anthropic API keys and any other Anthropic offerings that
  references these Terms [...]"
- **M2:** "A.1. Overview. Subject to these Terms, Anthropic gives Customer permission to use the
  Services, including to power products and services Customer makes available to its own customers
  and end users (“Users”)."

### S9. Trademark Guidelines
<https://www.anthropic.com/legal/trademark-guidelines>, read 2026-10-16, "Effective August 1, 2024".

- **G2:** "You may only use our trademarks as specifically permitted by us and only in materials we
  approve beforehand."
- **G3:** "You may not use our trademarks in a manner that implies Anthropic’s sponsorship or
  endorsement, or a relationship or affiliation with Anthropic, except as we expressly authorize."

## Questions to send to Anthropic

Via the "contact sales" link on the Legal and compliance page (L9), or marketing@anthropic.com for
Q2 (T8).

**Q1: local mode with the person's own subscription**

> I maintain a free, open-source (Apache-2.0) Mac app. It starts the official, unmodified `claude`
> CLI on the user's own Mac (`claude -p` with a restricted tool allow-list). The user installed
> Claude Code themselves and signed in through Claude Code's own login with their own Pro or Max
> plan. Our app never sees, reads or stores credentials or tokens, has no servers and makes no API
> calls of its own; it checks whether a sign-in exists with `claude auth status`. Your support
> article 15036540 (October 7, 2026) says users "can still use [...] claude -p, and third-party
> apps with your subscription limits". The Agent SDK overview says third-party developers may not
> "offer claude.ai login or rate limits for their products" unless previously approved. Is our
> setup permitted for an individual using their own subscription, or do we need approval or
> API-key-only operation?

**Q2: the name "Claude" in plain-text copy**

> Our open-source app is called "Video Cut" and runs the user's own Claude Code. Your Legal and
> compliance page allows saying in plain text that a product "runs Claude Code". May we also use
> plain-text lines such as "Claude cuts your videos" and "Claude subscription detected" in the app
> and README, together with the note "not affiliated with or endorsed by Anthropic"? We use no
> logos or Anthropic visual elements.

**Q3: business videos on a consumer plan**

> Users of our app may cut videos for their own small business. Under the EEA Consumer Terms
> ("Non-commercial use only"), may a Pro or Max subscriber use Claude Code for such work, or should
> we tell business users to use their own Console API key?

**Q4: Commercial Terms for the open-source project**

> The Legal and compliance page says "preinstalling or running Claude Code in your products or
> services" requires agreeing to the Commercial Terms. Does that apply to an unpaid open-source
> project that only starts the copy of Claude Code the user installed on their own computer, with
> no hosting or redistribution of the binary?
