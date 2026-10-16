#!/bin/bash
# Fresh-install test: install Video Cut in a clean environment and measure it.
#
#   scripts/fresh-install-test.sh                  start.sh on a copy of this checkout, downloads
#                                                  everything (the real time)
#   scripts/fresh-install-test.sh --keep-models    same, models seeded from the model cache
#   scripts/fresh-install-test.sh --claude SOURCE [--lang de]
#                                                  the person's own Claude Code installs from
#                                                  SOURCE (URL or checkout path); `claude -p`
#                                                  with the person's sentence, transcript kept
# Options:
#   --keep-models        seed the models from the model cache (fast repeat runs)
#   --model-cache DIR    model cache (default: ~/.cache/videocut-fresh-test/models); filled
#                        after every successful run
#   --report FILE        also write the markdown report to FILE
#   --keep               keep the temporary directory (default: removed at the end)
#   --claude SOURCE      Claude-driven install (needs the official `claude`, signed in)
#   --lang en|de         language of the person's sentence (default en)
#   --transcript FILE    stream-json transcript (default: <report or temp dir>/claude-<lang>.jsonl);
#                        a readable summary is written next to it as FILE.md
#   --budget USD         cost cap per Claude run (default 4); --turns N (default 60)
#
# The install runs under `env -i` with a temporary HOME and VIDEOCUT_HOME and the system PATH
# only (no Homebrew, no ~/.local, no node or python from this machine). Nothing touches
# ~/VideoCut or this checkout. In --claude mode the `claude` program itself keeps the real HOME
# for its own sign-in (nothing of it is read here); every command it runs gets the temporary
# HOME and the system PATH through CLAUDE_ENV_FILE, and only project settings are loaded.
# Exit code: 0 when every step passed, 1 otherwise.
#
# Not simulated (this machine already has them): Xcode Command Line Tools (the /usr/bin/git and
# /usr/bin/python3 shims open an install dialog on a fresh Mac), a fresh macOS user account,
# Gatekeeper prompts. See docs/fresh-install-report.md for the checklist a human runs.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP_MODELS=0
KEEP_TMP=0
REPORT_FILE=""
MODEL_CACHE="$HOME/.cache/videocut-fresh-test/models"
REAL_VIDEOCUT="$HOME/VideoCut"
CLAUDE_SRC=""
LANG_RUN=en
TRANSCRIPT=""
BUDGET=4
TURNS=60

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-models) KEEP_MODELS=1 ;;
    --model-cache) MODEL_CACHE="$2"; shift ;;
    --report) REPORT_FILE="$2"; shift ;;
    --keep) KEEP_TMP=1 ;;
    --claude) CLAUDE_SRC="$2"; shift ;;
    --lang) LANG_RUN="$2"; shift ;;
    --transcript) TRANSCRIPT="$2"; shift ;;
    --budget) BUDGET="$2"; shift ;;
    --turns) TURNS="$2"; shift ;;
    -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$LANG_RUN" in en|de) ;; *) echo "--lang must be en or de" >&2; exit 2 ;; esac

TMP="$(mktemp -d "${TMPDIR:-/tmp}/videocut-fresh.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
FHOME="$TMP/home"
LOGDIR="$TMP/logs"
if [ -n "$CLAUDE_SRC" ]; then
  VHOME="$FHOME/VideoCut"   # the default location, as on the person's Mac
  APPDIR="$FHOME/VideoCut/app"
else
  VHOME="$TMP/VideoCut"
  APPDIR="$TMP/app"
fi
mkdir -p "$FHOME" "$VHOME" "$LOGDIR" "$TMP/tmp"
if [ -n "$CLAUDE_SRC" ] && [ -z "$TRANSCRIPT" ]; then
  TRANSCRIPT="$(dirname "${REPORT_FILE:-$TMP/x}")/claude-$LANG_RUN.jsonl"
fi

cleanup() {
  if [ "$KEEP_TMP" = 1 ]; then
    echo "kept: $TMP" >&2
  else
    chmod -R u+w "$TMP" 2>/dev/null
    rm -rf "$TMP"
  fi
}
trap cleanup EXIT

CLEAN_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
clean() {
  env -i HOME="$FHOME" VIDEOCUT_HOME="$VHOME" PATH="$CLEAN_PATH" TERM=xterm \
    LANG=en_US.UTF-8 TMPDIR="$TMP/tmp/" "$@"
}

now() { perl -MTime::HiRes=time -e 'printf "%.1f", time'; }
secs() { perl -e 'printf "%.0f", $ARGV[1] - $ARGV[0]' "$1" "$2"; }
fmt_s() { perl -e '$s = shift; if ($s >= 60) { printf "%dm %02ds", $s / 60, $s % 60 } else { printf "%ds", $s }' "$1"; }
mb() { du -sk "$1" 2>/dev/null | awk '{printf "%.0f", $1/1024}'; }
copy_tree() { mkdir -p "$2"; cp -Rc "$1"/. "$2"/ 2>/dev/null || cp -R "$1"/. "$2"/; }

# Machine-wide received bytes of the default interface (an upper bound for the download).
NETIF="$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}')"
rx_bytes() {
  [ -n "$NETIF" ] || { echo 0; return; }
  netstat -I "$NETIF" -b 2>/dev/null | awk 'NR==2 {print $7}'
}

STEPS=""      # lines: name<TAB>seconds<TAB>status<TAB>log
FAILED=0
add_step() { STEPS="${STEPS}$1	$2	$3	${4:-}
"; }

# Run a command in the clean environment inside $APPDIR, log it with a timestamp per line.
run_logged() {  # name logfile cmd...
  local name="$1" log="$2" t0 rc
  shift 2
  echo "== $name" >&2
  t0="$(now)"
  (cd "$APPDIR" && clean "$@") 2>&1 \
    | perl -MTime::HiRes=time -ne 'BEGIN{$|=1} printf STDOUT "%.1f\t%s", time, $_; print STDERR $_' \
      >"$log"
  rc=${PIPESTATUS[0]}
  LAST_RC=$rc
  if [ "$rc" = 0 ]; then
    add_step "$name" "$(secs "$t0" "$(now)")" ok "$log"
  else
    add_step "$name" "$(secs "$t0" "$(now)")" "FAILED (exit $rc)" "$log"
    FAILED=1
  fi
}

seed_models() {
  [ "$KEEP_MODELS" = 1 ] || return 0
  if [ ! -d "$MODEL_CACHE" ]; then
    echo "model cache $MODEL_CACHE is empty; downloading the models once" >&2
    KEEP_MODELS=0
    return 0
  fi
  local t0
  t0="$(now)"
  copy_tree "$MODEL_CACHE" "$VHOME/models"
  add_step "seed models from cache ($(mb "$VHOME/models") MB)" "$(secs "$t0" "$(now)")" ok
}

# --- Preflight: what the clean environment offers --------------------------------------------
PRE=""
pre() { PRE="${PRE}| $1 | $2 |
"; }
pre "macOS" "$(sw_vers -productVersion) ($(uname -m))"
pre "RAM" "$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 )) GB"
pre "Free disk (temp volume)" "$(( $(df -k "$TMP" | awk 'NR==2 {print $4}') / 1024 / 1024 )) GB"
if xcode-select -p >/dev/null 2>&1; then
  pre "Xcode Command Line Tools" "installed on this machine (a fresh Mac has none; not simulated)"
else
  pre "Xcode Command Line Tools" "not installed"
fi
for t in curl shasum plutil tar perl say git python3 node npm brew; do
  p="$(clean /bin/sh -c "command -v $t" 2>/dev/null)"
  pre "\`$t\` on the clean PATH" "${p:-missing}"
done

T_ALL0="$(now)"
SUB=""
LAUNCHERS=""

# --- A. Direct install: start.sh on a copy of this checkout ----------------------------------
install_flow() {
  local t0 t_install0 t_install1
  # tracked + untracked, not ignored: no .tools, .venv, node_modules
  echo "== copy checkout to $APPDIR" >&2
  t0="$(now)"
  mkdir -p "$APPDIR"
  (cd "$REPO" && git ls-files -z --cached --others --exclude-standard \
    | perl -0ne 'chomp; print "$_\0" if -e' \
    | tar --null -T - -cf -) | tar -xf - -C "$APPDIR"
  add_step "copy checkout ($(mb "$APPDIR") MB)" "$(secs "$t0" "$(now)")" ok
  seed_models

  RX0="$(rx_bytes)"
  t_install0="$(now)"
  run_logged "start.sh (total)" "$LOGDIR/start.log" ./start.sh
  local start_rc=$LAST_RC
  t_install1="$(now)"
  RX1="$(rx_bytes)"

  # Per step: from one ▸ line to the next (the first segment is the platform check, the last
  # one includes the doctor that start.sh runs at the end).
  SUB="$(perl -e '
    my ($t0, $t1, $log) = @ARGV; my @ev = (["platform checks", $t0]);
    open my $f, "<", $log or exit;
    while (<$f>) { my ($t, $l) = split /\t/, $_, 2; next unless defined $l;
      if ($l =~ /\x{e2}\x{96}\x{b8}\s*(.*?)(\e\[0m)?\s*$/) { push @ev, [$1, $t] } }
    push @ev, ["end", $t1];
    for my $i (0 .. $#ev - 1) { printf "%s\t%.0f\n", $ev[$i][0], $ev[$i+1][1] - $ev[$i][1] }
  ' "$t_install0" "$t_install1" "$LOGDIR/start.log")"

  if [ "$start_rc" = 0 ]; then
    run_logged "videocut doctor" "$LOGDIR/doctor.log" .tools/bin/uv run --frozen videocut doctor
    run_logged "videocut selftest" "$LOGDIR/selftest.log" .tools/bin/uv run --frozen videocut selftest
  else
    add_step "videocut doctor" 0 "skipped (install failed)"
    add_step "videocut selftest" 0 "skipped (install failed)"
  fi
}

# --- B. Claude-driven install: the person's own Claude Code follows the repo's instructions --
summarize_transcript() {  # jsonl -> markdown on stdout
  /usr/bin/python3 -I - "$1" "$FHOME" "$TMP" <<'PY'
import json, re, sys

path, fhome, tmp = sys.argv[1:4]
short = lambda s: s.replace(fhome, "~").replace(tmp, "$TMP")
risky = re.compile(r"\b(sudo|brew|xcode-select|softwareupdate|git\s|/usr/bin/python3|pip3?\s)|curl[^|]*\|\s*(ba|z)?sh")
texts, cmds, flags, results, denied = [], [], [], [], []
for line in open(path, encoding="utf-8"):
    try:
        e = json.loads(line)
    except ValueError:
        continue
    if e.get("type") == "assistant":
        for c in e["message"]["content"]:
            if c.get("type") == "text" and c["text"].strip():
                texts.append(short(c["text"].strip()))
            elif c.get("type") == "tool_use":
                inp = c.get("input", {})
                arg = inp.get("command") or inp.get("file_path") or ""
                cmds.append(f"{c['name']}: {short(arg)}")
                if c["name"] == "Bash" and risky.search(arg):
                    flags.append(short(arg))
    elif e.get("type") == "result":
        results.append(e)
        denied += e.get("permission_denials") or []

turns = sum(r.get("num_turns", 0) for r in results)
cost = sum(r.get("total_cost_usd", 0) for r in results)
secs = sum(r.get("duration_ms", 0) for r in results) / 1000
questions = [t for t in texts if "?" in t]
print(f"- Runs: {len(results)} (follow-ups: {max(len(results) - 1, 0)}), turns: {turns}, "
      f"cost: ${cost:.2f}, model time: {secs / 60:.1f} min")
print(f"- Stop reasons: {', '.join(r.get('subtype', '?') for r in results)}")
print(f"- Tool calls: {len(cmds)}; permission denials: {len(denied)}")
print(f"- Messages to the person with a question mark: {len(questions)}")
print(f"- sudo / brew / git / system python / curl|sh: {len(flags)}")
for f in flags:
    print(f"  - `{f[:160]}`")
print("\n### Tool calls\n")
for c in cmds:
    print(f"- `{c[:200]}`")
print("\n### What the person read\n")
for t in texts:
    print(t)
    print("\n---\n")
PY
}

claude_flow() {
  local work="$TMP/work" envfile="$TMP/claude-env.sh" sentence claude_bin stamp t0 rc sid i answer
  claude_bin="$(command -v claude)" || { echo "the official claude program is not installed" >&2; exit 2; }
  mkdir -p "$work"
  stamp="$TMP/stamp"; : >"$stamp"
  seed_models
  if [ "$LANG_RUN" = de ]; then
    sentence="Installiere $CLAUDE_SRC für mich und führe mich durch die Einrichtung."
    answer="Ja, bitte. Mach einfach weiter."
  else
    sentence="Install $CLAUDE_SRC for me and guide me through it."
    answer="Yes, please. Just go ahead."
  fi
  cat >"$envfile" <<EOF
export HOME="$FHOME" PATH="$CLEAN_PATH" TMPDIR="$TMP/tmp/" TERM=xterm
unset VIDEOCUT_HOME VIRTUAL_ENV PYTHONPATH PYTHONHOME NODE_OPTIONS NODE_PATH \
  UV_PYTHON_INSTALL_DIR UV_PYTHON_PREFERENCE UV_CACHE_DIR UV_PROJECT_ENVIRONMENT
EOF
  # The test must not stop the human's own running app.
  local flags=(--output-format stream-json --verbose --max-budget-usd "$BUDGET" --max-turns "$TURNS"
    --strict-mcp-config --setting-sources project,local
    --allowedTools Bash Read Write Edit
    --disallowedTools "Bash(kill:*)" "Bash(pkill:*)" "Bash(killall:*)")
  run_claude() {
    (cd "$work" && env -i HOME="$HOME" USER="$USER" LOGNAME="${LOGNAME:-$USER}" SHELL=/bin/zsh \
      PATH="$(dirname "$claude_bin"):$CLEAN_PATH" TERM=xterm-256color LANG=en_US.UTF-8 \
      CLAUDE_ENV_FILE="$envfile" "$claude_bin" -p "$@" "${flags[@]}") \
      >>"$TRANSCRIPT" 2>>"$LOGDIR/claude.err"
  }
  : >"$TRANSCRIPT"
  echo "== claude ($LANG_RUN): $sentence" >&2
  t0="$(now)"
  RX0="$(rx_bytes)"
  run_claude "$sentence"
  rc=$?
  # A person answers a question with a plain yes; resume while nothing is installed yet.
  for i in 1 2; do
    [ -x "$APPDIR/.tools/bin/uv" ] && break
    sid="$(perl -ne 'print "$1\n" if /"session_id":"([^"]+)"/' "$TRANSCRIPT" | tail -1)"
    [ -n "$sid" ] || break
    echo "== claude follow-up $i: $answer" >&2
    run_claude "$answer" --resume "$sid"
    rc=$?
  done
  RX1="$(rx_bytes)"
  add_step "claude -p ($LANG_RUN)" "$(secs "$t0" "$(now)")" "$([ "$rc" = 0 ] && echo ok || echo "exit $rc")"
  # Stop whatever the run left running (the app server, a launcher).
  pkill -f "$TMP/" 2>/dev/null

  if [ ! -x "$APPDIR/.tools/bin/uv" ]; then
    local found
    found="$(find "$FHOME" "$work" -maxdepth 4 -name start.sh -not -path '*/node_modules/*' 2>/dev/null | head -1)"
    [ -n "$found" ] && APPDIR="$(dirname "$found")"
    add_step "code in ~/VideoCut/app" 0 "FAILED (found: ${found:-nothing})"
    FAILED=1
  fi
  if [ -x "$APPDIR/.tools/bin/uv" ]; then
    run_logged "videocut doctor (after Claude)" "$LOGDIR/doctor.log" .tools/bin/uv run --frozen videocut doctor
  else
    add_step "videocut doctor (after Claude)" 0 "FAILED (nothing installed)"
    FAILED=1
  fi
  LAUNCHERS="$(find "$FHOME" -maxdepth 3 \( -name '*.app' -o -name '*.command' \) \
    -not -path '*/.tools/*' -not -path '*/node_modules/*' 2>/dev/null | sed "s#$FHOME#~#")"
  if [ -d "$FHOME/Applications/Video Cut.app" ]; then
    add_step "launcher ~/Applications/Video Cut.app" 0 ok
  else
    add_step "launcher ~/Applications/Video Cut.app" 0 "FAILED (found: ${LAUNCHERS:-nothing})"
    FAILED=1
  fi
  # The human's own data must be untouched.
  local touched
  touched="$(find "$REAL_VIDEOCUT" -maxdepth 2 -newer "$stamp" 2>/dev/null | head -3)"
  if [ -z "$touched" ]; then
    add_step "real ~/VideoCut untouched" 0 ok
  else
    add_step "real ~/VideoCut untouched" 0 "FAILED (changed: $(echo $touched))"
    FAILED=1
  fi
  summarize_transcript "$TRANSCRIPT" >"$TRANSCRIPT.md"
}

if [ -n "$CLAUDE_SRC" ]; then claude_flow; else install_flow; fi

# --- Sizes -----------------------------------------------------------------------------------
SIZES=""
size() { [ -e "$2" ] && SIZES="${SIZES}| $1 | $(mb "$2") MB |
"; }
for d in "$APPDIR"/.tools/node-*; do size "Node (.tools/$(basename "$d"))" "$d"; done
size "uv, ffmpeg, ffprobe, deep-filter (.tools/bin)" "$APPDIR/.tools/bin"
size "Python (.tools/python)" "$APPDIR/.tools/python"
size "uv cache (.tools/uv-cache)" "$APPDIR/.tools/uv-cache"
size "Headless Chromium (.tools/ms-playwright)" "$APPDIR/.tools/ms-playwright"
size "Python environment (.venv)" "$APPDIR/.venv"
size "renderer/node_modules" "$APPDIR/renderer/node_modules"
size "app/node_modules" "$APPDIR/app/node_modules"
size "npm cache (~/.npm)" "$FHOME/.npm"
size "~/.cache" "$FHOME/.cache"
size "~/Library/Caches" "$FHOME/Library/Caches"
size "Models (\$VIDEOCUT_HOME/models)" "$VHOME/models"
size "Selftest output (\$VIDEOCUT_HOME/selftest)" "$VHOME/selftest"
TOTAL_DISK="$(( $(mb "$FHOME") + $(mb "$VHOME") ))"
[ -n "$CLAUDE_SRC" ] || TOTAL_DISK="$(( TOTAL_DISK + $(mb "$APPDIR") ))"
NET_MB="$(perl -e 'printf "%.0f", ($ARGV[1] - $ARGV[0]) / 1e6' "${RX0:-0}" "${RX1:-0}")"

if [ "$FAILED" = 0 ] && [ -d "$VHOME/models" ]; then
  copy_tree "$VHOME/models" "$MODEL_CACHE"
fi
T_ALL1="$(now)"

# --- Report ----------------------------------------------------------------------------------
report() {
  echo "# Fresh install test$([ -n "$CLAUDE_SRC" ] && echo " (Claude-driven, $LANG_RUN)")"
  echo
  echo "- Date: $(date -u +%Y-%m-%dT%H:%MZ)"
  if [ -n "$CLAUDE_SRC" ]; then
    echo "- Source: \`$(echo "$CLAUDE_SRC" | sed "s#$HOME#~#")\`; transcript: \`$(basename "$TRANSCRIPT")\` (+ \`.md\` summary)"
  else
    echo "- Checkout: \`$(cd "$REPO" && git rev-parse --short HEAD)\`$(cd "$REPO" && git diff --quiet HEAD || echo ' + local changes')"
  fi
  echo "- Models: $([ "$KEEP_MODELS" = 1 ] && echo 'seeded from cache (--keep-models)' || echo 'downloaded')"
  echo "- Result: $([ "$FAILED" = 0 ] && echo '**passed**' || echo '**FAILED**')"
  echo "- Total wall clock: $(fmt_s "$(secs "$T_ALL0" "$T_ALL1")")"
  echo
  echo "## Environment"
  echo
  echo "| Check | Value |"
  echo "|---|---|"
  printf '%s' "$PRE"
  echo
  echo "## Steps (wall clock)"
  echo
  echo "| Step | Time | Status |"
  echo "|---|---|---|"
  printf '%s' "$STEPS" | while IFS='	' read -r n s st _; do
    [ -n "$n" ] && echo "| $n | $(fmt_s "$s") | $st |"
  done
  if [ -n "$SUB" ]; then
    echo
    echo "### Inside start.sh"
    echo
    echo "| Step | Time |"
    echo "|---|---|"
    printf '%s\n' "$SUB" | while IFS='	' read -r n s; do
      [ -n "$n" ] && echo "| $n | $(fmt_s "$s") |"
    done
  fi
  if [ -n "$CLAUDE_SRC" ] && [ -f "$TRANSCRIPT.md" ]; then
    echo
    echo "## Claude run"
    echo
    sed -n '1,/^### Tool calls/p' "$TRANSCRIPT.md" | sed '$d'
  fi
  echo
  echo "## Size"
  echo
  echo "| Part | On disk |"
  echo "|---|---|"
  printf '%s' "$SIZES"
  echo "| **Total on disk** (du; APFS clones may be counted twice) | **${TOTAL_DISK} MB** |"
  echo
  echo "Received during the install: ~${NET_MB} MB (all traffic on \`${NETIF:-?}\`, an upper bound)."
  if [ "$FAILED" != 0 ]; then
    echo
    echo "## Failure output (last 40 lines per failed step)"
    printf '%s' "$STEPS" | while IFS='	' read -r n _ st log; do
      case "$st" in FAILED*) ;; *) continue ;; esac
      [ -n "$log" ] && [ -f "$log" ] || continue
      echo
      echo "### $n"
      echo '```'
      cut -f2- "$log" | sed $'s/\033\\[[0-9;]*m//g' | tail -40
      echo '```'
    done
  fi
}

REPORT="$(report)"
printf '%s\n' "$REPORT"
[ -n "$REPORT_FILE" ] && printf '%s\n' "$REPORT" >"$REPORT_FILE"
exit "$FAILED"
