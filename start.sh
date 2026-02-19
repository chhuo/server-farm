#!/usr/bin/env bash
# NodePanel 快速启动脚本 (Linux / macOS)
# 前提：已运行过 install.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "未找到虚拟环境，请先运行 bash install.sh"
    exit 1
fi

echo "[NodePanel] 启动中... 访问 http://localhost:8300"
exec "$PYTHON" "$SCRIPT_DIR/main.py"
