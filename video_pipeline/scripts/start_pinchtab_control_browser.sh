#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PINCHTAB_CONFIG_PATH="${PINCHTAB_CONFIG_PATH:-$HOME/.pinchtab/config.json}"
PINCHTAB_REMOTE_DEBUG_PORT="${PINCHTAB_REMOTE_DEBUG_PORT:-9222}"
PINCHTAB_PROXY_SERVER="${PINCHTAB_PROXY_SERVER:-http://127.0.0.1:1082}"
PINCHTAB_INITIAL_URL="${PINCHTAB_INITIAL_URL:-about:blank}"
PINCHTAB_CONTROL_PROFILE="${PINCHTAB_CONTROL_PROFILE:-$PROJECT_DIR/browser_profile/pinchtab_control}"
CHROME_APP="${CHROME_APP:-/Applications/Google Chrome.app}"
CHROME_BIN="$CHROME_APP/Contents/MacOS/Google Chrome"

if [[ ! -x "$CHROME_BIN" ]]; then
  echo "未找到 Google Chrome: $CHROME_BIN" >&2
  exit 1
fi

mkdir -p "$PINCHTAB_CONTROL_PROFILE"

/opt/homebrew/bin/python3 - "$PINCHTAB_CONFIG_PATH" <<'PY'
import json
import os
import sys

config_path = os.path.expanduser(sys.argv[1])
os.makedirs(os.path.dirname(config_path), exist_ok=True)

if os.path.exists(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
else:
    config = {}

security = config.setdefault("security", {})
attach = security.setdefault("attach", {})

changed = False
if attach.get("enabled") is not True:
    attach["enabled"] = True
    changed = True

for key, required_values in {
    "allowHosts": ["127.0.0.1", "localhost", "::1"],
    "allowSchemes": ["ws", "wss"],
}.items():
    current_values = attach.get(key) or []
    merged_values = list(dict.fromkeys([*current_values, *required_values]))
    if current_values != merged_values:
        attach[key] = merged_values
        changed = True

if changed:
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

print(config_path)
PY

/opt/homebrew/bin/pinchtab daemon restart >/dev/null 2>&1 || true

open -na "$CHROME_APP" --args \
  --remote-debugging-port="$PINCHTAB_REMOTE_DEBUG_PORT" \
  --user-data-dir="$PINCHTAB_CONTROL_PROFILE" \
  --proxy-server="$PINCHTAB_PROXY_SERVER" \
  --new-window \
  "$PINCHTAB_INITIAL_URL"

echo "PinchTab 受控 Chrome 已启动。"
echo "下一步：在这个专用 Chrome 里手动登录 sftraders.live，并停在 /schedules 页面。"
echo "控制资料目录：$PINCHTAB_CONTROL_PROFILE"
