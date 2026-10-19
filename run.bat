@echo off
chcp 65001 >nul
title AI Delivery Console
cd /d "D:\WorkBuddyWorkspace\2026-10-17-20-26-43\ai-deliver-kit"

set "PY=C:\Users\xuanx\AppData\Local\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=C:\Users\xuanx\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" (
    echo.
    echo   Python not found.
    echo   Open run.bat in Notepad and fix the PY path.
    echo.
    pause
    exit /b 1
)

rem --- already running? just open the browser, never start a second copy ---
curl -s --max-time 2 http://127.0.0.1:8100/api/targets >nul 2>&1
if not errorlevel 1 (
    start "" http://127.0.0.1:8100
    exit
)

start "Delivery Console - keep this window" /min "%PY%" -u server.py 8100
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8100
exit
