#!/bin/bash
# 启动 live dashboard 数据服务
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查env配置
if [ ! -f "live_dashboard.env" ]; then
    cp live_dashboard.env.example live_dashboard.env
    echo "[!] 已创建 live_dashboard.env，请编辑填入用户名密码"
fi

python3 serve_live_dashboard.py
