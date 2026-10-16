#!/usr/bin/env bash
# Removes everything start.sh installed into this folder.
# Your data in ~/VideoCut (brands, jobs, models) is kept unless you pass --all.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${VIDEOCUT_HOME:-$HOME/VideoCut}"

# The double-click app and an optional login item (only our own bundle is removed).
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  (cd "$ROOT" && "$ROOT/.venv/bin/python" -m videocut.cli stop --force) || true
  (cd "$ROOT" && "$ROOT/.venv/bin/python" -m videocut.cli launcher --remove) || true
fi
rm -rf "$ROOT/.tools" "$ROOT/.venv" "$ROOT/renderer/node_modules" "$ROOT/app/node_modules"
echo "Removed installed tools from $ROOT."

if [[ "${1:-}" == "--all" ]]; then
  read -r -p "Also delete $DATA (brands, jobs, downloaded models)? [y/N] " answer
  if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
    rm -rf "$DATA"
    echo "Removed $DATA."
  fi
else
  echo "Kept your data in $DATA (use --all to remove it too)."
fi
