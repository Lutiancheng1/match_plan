#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec zsh "$SCRIPT_DIR/stop_live_dashboard.sh"
