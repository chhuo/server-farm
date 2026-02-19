#!/usr/bin/env bash
# NodePanel 一键安装脚本 (Linux / macOS)
set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${CYAN}[NodePanel]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo ""
echo -e "${CYAN}╔══════════════════════════════════╗${NC}"
echo -e "${CYAN}║        NodePanel 安装程序        ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════╝${NC}"
echo ""

# ── 检查 Python ──
info "检查 Python 版本..."
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
        MAJOR=$("$cmd" -c "import sys; print(sys.version_info.major)")
        MINOR=$("$cmd" -c "import sys; print(sys.version_info.minor)")
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON="$cmd"
            success "找到 Python $MAJOR.$MINOR ($cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "未找到 Python 3.10+，请先安装 Python：https://www.python.org/downloads/"
fi

# ── 创建虚拟环境 ──
if [ ! -d "$VENV_DIR" ]; then
    info "创建虚拟环境..."
    "$PYTHON" -m venv "$VENV_DIR"
    success "虚拟环境已创建: $VENV_DIR"
else
    success "虚拟环境已存在，跳过创建"
fi

# ── 激活虚拟环境 ──
source "$VENV_DIR/bin/activate"

# ── 安装依赖 ──
info "安装依赖包..."
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q
success "依赖安装完成"

# ── 检查配置文件 ──
if [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
    warn "未找到 config.yaml，将使用内置默认配置"
fi

# ── 询问是否注册 systemd 服务 ──
REGISTER_SERVICE=false
if command -v systemctl &>/dev/null && [ "$(id -u)" -eq 0 ]; then
    echo ""
    read -r -p "是否注册为 systemd 服务（开机自启）？[y/N] " REPLY
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        REGISTER_SERVICE=true
    fi
elif command -v systemctl &>/dev/null && [ "$(id -u)" -ne 0 ]; then
    warn "注册 systemd 服务需要 root 权限，跳过（可稍后用 sudo bash install.sh 重新运行）"
fi

if [ "$REGISTER_SERVICE" = true ]; then
    SERVICE_FILE="/etc/systemd/system/nodepanel.service"
    VENV_PYTHON="$VENV_DIR/bin/python"
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=NodePanel - Distributed Server Management Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_PYTHON $SCRIPT_DIR/main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable nodepanel
    systemctl restart nodepanel
    success "systemd 服务已注册并启动"
    info "管理命令: systemctl {start|stop|restart|status} nodepanel"
else
    # ── 直接启动 ──
    echo ""
    info "启动 NodePanel..."
    echo ""
    exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/main.py"
fi
