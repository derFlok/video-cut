#!/bin/bash
# Video Cut — one entry point. Safe to run repeatedly: finished steps are skipped and an
# interrupted download continues where it stopped.
#
#   ./start.sh              install/refresh everything, then run the doctor
#   ./start.sh --selftest   same, then cut a generated test clip end to end
#   ./start.sh --ui         same, then open the app in the browser
#   ./start.sh --check      only run the doctor, install nothing
#   ./start.sh --dev        also install the developer tools (pytest, ruff); kept
#                           automatically in a git checkout or with VIDEOCUT_DEV=1
#
# Everything is installed into ./.tools and ./.venv: no Homebrew, no sudo, no Xcode command
# line tools, no Rosetta. Only programs that ship with macOS are used (curl, shasum, tar,
# plutil). User data (brands, jobs, models) lives in ~/VideoCut (override: VIDEOCUT_HOME).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$ROOT/.tools"
BIN="$TOOLS/bin"
MANIFEST="$ROOT/manifest/tools.json"
STEPS=7
PY_MB=450   # Python packages (download; about 1.5 GB on disk)
MODE="${1:-}"
DEV="${VIDEOCUT_DEV:-}"
if [[ "$MODE" == "--dev" ]]; then DEV=1; MODE="${2:-}"; fi
[[ -e "$ROOT/.git" ]] && DEV=1   # a developer checkout keeps pytest and ruff

say() { printf '\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
step() { say "Step $1/$STEPS: $2"; }

# Settings from the person's shell must not leak into ours.
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV CONDA_PREFIX NODE_OPTIONS npm_config_prefix
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

if [[ "$MODE" == "--check" ]]; then
  [[ -x "$BIN/uv" ]] || fail "Not installed yet: run ./start.sh"
  export UV_PYTHON_INSTALL_DIR="$TOOLS/python" UV_PYTHON_PREFERENCE="only-managed"
  export UV_CACHE_DIR="$TOOLS/uv-cache" UV_PROJECT_ENVIRONMENT="$ROOT/.venv"
  cd "$ROOT"
  exec "$BIN/uv" run --frozen --no-sync --quiet videocut doctor
fi

# --- 1. This Mac --------------------------------------------------------------
step 1 "checking this Mac"
[[ "$(uname -s)" == "Darwin" ]] || fail "Video Cut currently supports macOS only."
# uname -m says x86_64 under Rosetta; the hardware flag does not lie.
if [[ "$(uname -m)" != "arm64" && "$(sysctl -n hw.optional.arm64 2>/dev/null)" != "1" ]]; then
  fail "Video Cut needs Apple Silicon (M1 or newer)."
fi
[[ "$(uname -m)" == "arm64" ]] || fail "This terminal runs under Rosetta. Open a normal (arm64) Terminal and try again."
macos_major="$(sw_vers -productVersion | cut -d. -f1)"
(( macos_major >= 14 )) || fail "macOS 14 or newer required (found $(sw_vers -productVersion))."

ram_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
(( ram_gb >= 16 )) || note "Warning: ${ram_gb} GB RAM. 16 GB or more is recommended."

free_gb=$(( $(df -k "$ROOT" | awk 'NR==2 {print $4}') / 1024 / 1024 ))
(( free_gb >= 8 )) || fail "Only ${free_gb} GB free disk space. At least 8 GB needed (15 GB recommended)."
(( free_gb >= 15 )) || note "Warning: ${free_gb} GB free disk space. 15 GB recommended."
note "macOS $(sw_vers -productVersion), Apple Silicon, ${ram_gb} GB RAM, ${free_gb} GB free."
if [[ ! -f "$TOOLS/installed.json" ]]; then
  note "A first install downloads about 2.3 GB (tools ~0.7 GB, speech model ~1.6 GB) and"
  note "uses about 5-6 GB of disk: roughly 5 minutes on 100 Mbit/s. Nothing needs sudo."
fi

# --- 2. uv (Python toolchain), pinned + checksum-verified ---------------------
mf() { plutil -extract "$1" raw -o - "$MANIFEST"; }

install_uv() {
  local url sha tmp part
  url="$(mf tools.uv.url)"; sha="$(mf tools.uv.sha256)"
  mkdir -p "$TOOLS/downloads" "$BIN"
  part="$TOOLS/downloads/uv-$(mf tools.uv.version).tar.gz"
  # -C -: continue a partial download; --retry: survive a flaky connection
  curl -fsSL --retry 4 --retry-delay 2 -C - -o "$part" "$url" \
    || curl -fsSL --retry 4 --retry-delay 2 -o "$part" "$url" \
    || fail "Could not download uv. Check the internet connection and run ./start.sh again."
  if ! echo "$sha  $part" | shasum -a 256 -c --status; then
    rm -f "$part"
    fail "uv checksum mismatch (download damaged). Run ./start.sh again."
  fi
  tmp="$(mktemp -d)"
  tar -xzf "$part" -C "$tmp"
  mv "$tmp"/uv-*/uv "$tmp"/uv-*/uvx "$BIN/"
  rm -rf "$tmp" "$part"
}

want_uv="$(mf tools.uv.version)"
if [[ ! -x "$BIN/uv" ]] || [[ "$("$BIN/uv" --version | awk '{print $2}')" != "$want_uv" ]]; then
  step 2 "uv $want_uv (installs Python), $(mf tools.uv.approx_size_mb) MB"
  install_uv
else
  step 2 "uv $want_uv (already done)"
fi

export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_PYTHON_PREFERENCE="only-managed"   # same Python on every machine
export UV_CACHE_DIR="$TOOLS/uv-cache"
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv"
export UV_NO_PROGRESS=1
[[ -n "$DEV" ]] || export UV_NO_DEV=1   # end users get no developer tools

# --- 3. Python (pinned build, checksum-verified) + packages --------------------
cd "$ROOT"
py_ver="$(mf tools.python.version)"
export UV_PYTHON="$py_ver"
if [[ -x "$ROOT/.venv/bin/python" ]] && compgen -G "$TOOLS/python/cpython-$py_ver-macos-aarch64-*" >/dev/null; then
  step 3 "Python $py_ver and packages (checking)"
else
  step 3 "Python $py_ver and packages, about $(( PY_MB + $(mf tools.python.approx_size_mb) )) MB, about 1 minute on 100 Mbit/s"
fi
if ! compgen -G "$TOOLS/python/cpython-$py_ver-macos-aarch64-*" >/dev/null; then
  # uv installs Python from a local mirror holding exactly the pinned, verified archive.
  mirror="$TOOLS/downloads/python-mirror"
  py_file="$mirror/$(mf tools.python.release)/$(mf tools.python.filename)"
  mkdir -p "$(dirname "$py_file")"
  curl -fsSL --retry 4 --retry-delay 2 -C - -o "$py_file" "$(mf tools.python.url)" \
    || curl -fsSL --retry 4 --retry-delay 2 -o "$py_file" "$(mf tools.python.url)" \
    || fail "Could not download Python. Check the internet connection and run ./start.sh again."
  if ! echo "$(mf tools.python.sha256)  $py_file" | shasum -a 256 -c --status; then
    rm -f "$py_file"
    fail "Python checksum mismatch (download damaged). Run ./start.sh again."
  fi
  UV_PYTHON_INSTALL_MIRROR="file://$mirror" "$BIN/uv" python install --quiet "$py_ver"
  rm -rf "$mirror"
fi
export UV_PYTHON_DOWNLOADS=never   # never another Python than the pinned one
sync_ok=0
for attempt in 1 2 3; do
  if "$BIN/uv" sync --frozen --quiet; then sync_ok=1; break; fi
  note "Python packages: attempt $attempt failed; retrying (finished downloads are kept)."
  sleep 3
done
(( sync_ok )) || fail "Could not install the Python packages. Check the internet connection and run ./start.sh again."
# The venv holds its own copies: keep only what a re-sync cannot download again (~1.4 GB less).
# Another uv process (a running `uv run`) holds the cache lock: skip, next run prunes.
UV_LOCK_TIMEOUT=5 "$BIN/uv" cache prune --ci --quiet 2>/dev/null || true

# --- 4.-7. tools, renderer, app, models (src/videocut/install.py) -------------
"$BIN/uv" run --frozen --quiet videocut setup
echo
say "Checking the installation"
doctor_ok=1
"$BIN/uv" run --frozen --quiet videocut doctor || doctor_ok=0

# --- summary ------------------------------------------------------------------
kb() { { du -sk "$@" 2>/dev/null || true; } | awk '{s += $1} END {print s + 0}'; }
DATA="${VIDEOCUT_HOME:-$HOME/VideoCut}"
tools_kb=$(kb "$TOOLS" "$ROOT/.venv" "$ROOT/renderer/node_modules" "$ROOT/app/node_modules")
models_kb=$(kb "$DATA/models")
echo
say "Summary"
note "Time:        $(( SECONDS / 60 )) min $(( SECONDS % 60 )) s"
note "Code+tools:  $ROOT ($(( tools_kb / 1024 )) MB)"
note "Models:      $DATA/models ($(( models_kb / 1024 )) MB)"
note "Your videos: $DATA/jobs"
if (( ! doctor_ok )); then
  fail "Some required checks failed (see the lines with ✗ and the → fix below each)."
fi

if [[ "$MODE" == "--selftest" ]]; then
  "$BIN/uv" run --frozen --quiet videocut selftest
elif [[ "$MODE" == "--ui" ]]; then
  exec "$BIN/uv" run --frozen --quiet videocut ui
else
  note "Ready. Open the app with: ./start.sh --ui   (or create the double-click app: .tools/bin/uv run videocut launcher)"
fi
