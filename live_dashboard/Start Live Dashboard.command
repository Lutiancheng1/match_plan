#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec zsh "$SCRIPT_DIR/start_live_dashboard.sh"
