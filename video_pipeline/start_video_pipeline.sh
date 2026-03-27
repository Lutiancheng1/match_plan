#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

exec /usr/bin/caffeinate -dimsu /opt/homebrew/bin/python3 -u "$SCRIPT_DIR/scripts/run_v6.py" \
  "$SCRIPT_DIR/tasks/tasks_v6.json" \
  --output-dir "$SCRIPT_DIR/data" \
  --max-workers 6
