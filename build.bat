@echo off
chcp 65001 >nul
echo =======================================================
echo   AirBridge 通用串口数据传输通道 EXE 打包流水线
echo =======================================================

:: 1. 确保安装依赖环境
pip install pyserial PySide6 pyinstaller

:: 2. 使用增强 spec 规范编译单文件绿色版 EXE
pyinstaller --clean AirBridge.spec

echo =======================================================
echo   打包完成！生成绿色单文件位于: dist\AirBridge.exe
echo   请将 dist\AirBridge.exe 拷贝至物理隔离主机 B 直接运行！
echo =======================================================
pause