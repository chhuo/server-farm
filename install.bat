@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo.
echo ╔══════════════════════════════════╗
echo ║        NodePanel 安装程序        ║
echo ╚══════════════════════════════════╝
echo.

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"

:: ── 检查 Python ──
echo [NodePanel] 检查 Python 版本...
set "PYTHON="

for %%c in (python python3) do (
    if "!PYTHON!"=="" (
        %%c --version >nul 2>&1
        if !errorlevel! == 0 (
            for /f "tokens=2" %%v in ('%%c --version 2^>^&1') do (
                for /f "tokens=1,2 delims=." %%a in ("%%v") do (
                    if %%a geq 3 (
                        if %%b geq 10 (
                            set "PYTHON=%%c"
                            echo [OK] 找到 Python %%v
                        )
                    )
                )
            )
        )
    )
)

if "!PYTHON!"=="" (
    echo [ERROR] 未找到 Python 3.10+，请先安装：https://www.python.org/downloads/
    pause
    exit /b 1
)

:: ── 创建虚拟环境 ──
if not exist "!VENV_DIR!" (
    echo [NodePanel] 创建虚拟环境...
    !PYTHON! -m venv "!VENV_DIR!"
    echo [OK] 虚拟环境已创建
) else (
    echo [OK] 虚拟环境已存在，跳过创建
)

:: ── 安装依赖 ──
echo [NodePanel] 安装依赖包...
call "!VENV_DIR!\Scripts\activate.bat"
python -m pip install --upgrade pip -q
python -m pip install -r "!SCRIPT_DIR!requirements.txt" -q
echo [OK] 依赖安装完成

:: ── 检查配置文件 ──
if not exist "!SCRIPT_DIR!config.yaml" (
    echo [!] 未找到 config.yaml，将使用内置默认配置
)

:: ── 启动 ──
echo.
echo [NodePanel] 启动 NodePanel...
echo [NodePanel] 访问地址: http://localhost:8300
echo [NodePanel] 按 Ctrl+C 停止服务
echo.
python "!SCRIPT_DIR!main.py"

pause
