@echo off
chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] 未找到虚拟环境，请先运行 install.bat
    pause
    exit /b 1
)

echo [NodePanel] 启动中... 访问 http://localhost:8300
echo [NodePanel] 按 Ctrl+C 停止服务
echo.
"%PYTHON%" "%SCRIPT_DIR%main.py"
pause
