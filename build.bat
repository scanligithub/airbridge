@echo off
chcp 65001 >nul
setlocal

echo =======================================================
echo   AirBridge 单文件 EXE 构建流水线（GUI + MCP STDIO）
echo =======================================================

:: M2.5 MCP Python SDK 需要 Python 3.11+；构建版本与统一运行时保持一致。
py -3.11 --version
if errorlevel 1 (
    echo ERROR: Python 3.11 is required.
    exit /b 1
)

:: 安装统一 EXE 所需依赖。MCP SDK 与 PyInstaller 版本锁定。
py -3.11 -m pip install --upgrade pip
if errorlevel 1 exit /b 1

py -3.11 -m pip install pyserial PySide6 "mcp==2.3.0" "pyinstaller==6.22.3"
if errorlevel 1 exit /b 1

:: 同一个 EXE：无参数启动 GUI；mcp stdio 启动 MCP 子进程入口。
py -3.11 -m PyInstaller --clean --noconfirm AirBridge.spec
if errorlevel 1 exit /b 1

if not exist "dist\AirBridge.exe" (
    echo ERROR: dist\AirBridge.exe was not generated.
    exit /b 1
)

echo =======================================================
echo   构建完成: dist\AirBridge.exe
echo   GUI:      AirBridge.exe
echo   MCP:      AirBridge.exe mcp stdio
echo =======================================================

endlocal
