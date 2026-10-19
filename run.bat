@echo off
chcp 65001 >nul
title AI Delivery Kit
cd /d "D:\WorkBuddyWorkspace\2026-10-17-20-26-43\ai-deliver-kit"

set "PY=C:\Users\xuanx\AppData\Local\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=C:\Users\xuanx\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" (
    echo.
    echo   Python not found.
    echo   Open run.bat in Notepad and fix the PY path on line 6.
    echo.
    pause
    exit /b 1
)

"%PY%" -m deliverkit menu

echo.
pause
