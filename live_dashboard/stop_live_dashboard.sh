#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${1:-$SCRIPT_DIR/live_dashboard.env}"
PID_FILE="$SCRIPT_DIR/live_service_data/server.pid"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
  OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/live_service_data}"
  PID_FILE="$OUTPUT_DIR/server.pid"
fi

if [[ ! -f "$PID_FILE" ]]; then
  echo "No pid file: $PID_FILE" >&2
  exit 1
fi

PID="$(cat "$PID_FILE")"
if kill "$PID" >/dev/null 2>&1; then
  rm -f "$PID_FILE"
  echo "Stopped PID $PID"
else
  echo "Failed to stop PID $PID" >&2
  exit 1
fi

