#!/usr/bin/env bash
# Video Cut — one entry point. Safe to run repeatedly.
#
#   ./start.sh              install/refresh everything, then run the doctor
#   ./start.sh --selftest   same, then cut a generated test clip end to end
#   ./start.sh --check      only run the doctor, install nothing
#
# Everything is installed into ./.tools (no Homebrew, no sudo).
# User data (brands, jobs, models) lives in ~/VideoCut (override: VIDEOCUT_HOME).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$ROOT/.tools"
BIN="$TOOLS/bin"
MANIFEST="$ROOT/manifest/tools.json"

say() { printf '\033[1m▸ %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. Platform -------------------------------------------------------------
[[ "$(uname -s)" == "Darwin" ]] || fail "Video Cut currently supports macOS only."
[[ "$(uname -m)" == "arm64" ]] || fail "Video Cut needs Apple Silicon (M1 or newer)."
macos_major="$(sw_vers -productVersion | cut -d. -f1)"
(( macos_major >= 14 )) || fail "macOS 14 or newer required (found $(sw_vers -productVersion))."

ram_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
(( ram_gb >= 16 )) || say "Warning: ${ram_gb} GB RAM. 16 GB or more is recommended."

free_gb=$(( $(df -k "$ROOT" | awk 'NR==2 {print $4}') / 1024 / 1024 ))
(( free_gb >= 8 )) || fail "Only ${free_gb} GB free disk space. At least 8 GB needed (15 GB recommended)."
(( free_gb >= 15 )) || say "Warning: ${free_gb} GB free disk space. 15 GB recommended."

# --- 2. uv (Python toolchain), pinned + checksum-verified ---------------------
mf() { plutil -extract "$1" raw -o - "$MANIFEST"; }

install_uv() {
  local url sha tmp
  url="$(mf tools.uv.url)"; sha="$(mf tools.uv.sha256)"
  tmp="$(mktemp -d)"
  say "Installing uv $(mf tools.uv.version)"
  curl -fsSL -o "$tmp/uv.tar.gz" "$url"
  echo "$sha  $tmp/uv.tar.gz" | shasum -a 256 -c --status || fail "uv checksum mismatch."
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  mkdir -p "$BIN"
  mv "$tmp"/uv-*/uv "$tmp"/uv-*/uvx "$BIN/"
  rm -rf "$tmp"
}

want_uv="$(mf tools.uv.version)"
if [[ ! -x "$BIN/uv" ]] || [[ "$("$BIN/uv" --version | awk '{print $2}')" != "$want_uv" ]]; then
  install_uv
fi

export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_PYTHON_PREFERENCE="only-managed"   # same Python on every machine
export UV_CACHE_DIR="$TOOLS/uv-cache"
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv"

# --- 3. Python environment + the rest ----------------------------------------
cd "$ROOT"
if [[ "${1:-}" == "--check" ]]; then
  exec "$BIN/uv" run --frozen --quiet videocut doctor
fi

say "Syncing Python environment"
"$BIN/uv" sync --frozen --quiet

"$BIN/uv" run --frozen --quiet videocut setup
"$BIN/uv" run --frozen --quiet videocut doctor

if [[ "${1:-}" == "--selftest" ]]; then
  "$BIN/uv" run --frozen --quiet videocut selftest
fi
