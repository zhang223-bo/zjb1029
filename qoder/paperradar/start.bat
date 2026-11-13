@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo 正在检查环境并启动 PaperRadar...
echo.
"%PY%" run.py %*
echo.
echo 服务已退出。按任意键关闭窗口。
pause >nul
