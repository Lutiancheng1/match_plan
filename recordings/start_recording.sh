#!/bin/bash
# 一键启动录制脚本
# 用法: ./start_recording.sh [gtypes] [max_streams] [segment_minutes]

GTYPES="${1:-FT}"
MAX_STREAMS="${2:-2}"
SEG_MIN="${3:-10}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 直播录制系统 ==="
echo "运动类型: $GTYPES"
echo "最大路数: $MAX_STREAMS"
echo "分段间隔: ${SEG_MIN}分钟"
echo ""

# 统一环境预检
python3 preflight_setup.py --auto-install || exit 1

# 检查Safari
if ! pgrep -x "Safari" > /dev/null; then
    echo "[!] Safari 未运行，正在启动..."
    open -a "Safari"
    sleep 3
fi

# 启动录制
python3 run_auto_capture.py \
    --browser safari \
    --gtypes "$GTYPES" \
    --max-streams "$MAX_STREAMS" \
    --segment-minutes "$SEG_MIN"
