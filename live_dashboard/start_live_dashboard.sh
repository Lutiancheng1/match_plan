#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${1:-$SCRIPT_DIR/live_dashboard.env}"
cd "$SCRIPT_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  echo "Copy $SCRIPT_DIR/live_dashboard.env.example to $SCRIPT_DIR/live_dashboard.env and fill it." >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

# Require either auto-login credentials or manual cookie/body
if [[ -z "${LOGIN_USERNAME:-}" || -z "${LOGIN_PASSWORD:-}" ]]; then
  : "${GET_GAME_LIST_COOKIE:?Need LOGIN_USERNAME/LOGIN_PASSWORD or GET_GAME_LIST_COOKIE}"
  : "${GET_GAME_LIST_BODY:?Need LOGIN_USERNAME/LOGIN_PASSWORD or GET_GAME_LIST_BODY}"
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
TITLE="${TITLE:-全部比赛实时看板}"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/live_service_data}"
if [[ "$OUTPUT_DIR" != /* ]]; then
  OUTPUT_DIR="$SCRIPT_DIR/$OUTPUT_DIR"
fi
POLL_INTERVAL="${POLL_INTERVAL:-10}"
REFRESH_MS="${REFRESH_MS:-1000}"
TIMEOUT="${TIMEOUT:-30}"
MORE_FILTER="${MORE_FILTER:-All}"
GTYPES="${GTYPES:-ft,bk,es,tn,vb,bm,tt,bs,sk,op}"
INCLUDE_MORE="${INCLUDE_MORE:-1}"

mkdir -p "$OUTPUT_DIR"

args=(
  python3 "$SCRIPT_DIR/serve_live_dashboard.py"
  --host "$HOST"
  --port "$PORT"
  --title "$TITLE"
  --output-dir "$OUTPUT_DIR"
  --interval "$POLL_INTERVAL"
  --refresh-ms "$REFRESH_MS"
  --timeout "$TIMEOUT"
  --more-filter "$MORE_FILTER"
  --gtypes "$GTYPES"
)

if [[ "$INCLUDE_MORE" == "1" ]]; then
  args+=(--include-more)
fi

echo "Starting local dashboard at http://$HOST:$PORT"
nohup "${args[@]}" >"$OUTPUT_DIR/server.log" 2>&1 &
echo $! >"$OUTPUT_DIR/server.pid"
sleep 1
open "http://$HOST:$PORT" >/dev/null 2>&1 || true
echo "PID $(cat "$OUTPUT_DIR/server.pid")"
echo "Log $OUTPUT_DIR/server.log"
