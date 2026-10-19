@echo off
title Batch BG Remover
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.10+ and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python batch_bg_remover.py %*
if errorlevel 1 (
    echo.
    echo [ERROR] Program failed. Missing dependencies?
    echo Run:  pip install pillow numpy scipy tkinterdnd2
    echo.
    pause
)
