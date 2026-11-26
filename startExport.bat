@echo off
rem Run the exporter from source. Others can use dist\CapCut导出器 instead, which needs no Python.
chcp 65001 >nul
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo 没有找到 Python。请安装 Python 3.10 以上版本，或者直接使用 dist\CapCut导出器\CapCut导出器.exe。
    pause
    exit /b 1
)
start "" pythonw gui.py
