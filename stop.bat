@echo off
setlocal enabledelayedexpansion
set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%p /F >nul 2>&1
    set FOUND=1
)
if "!FOUND!"=="1" (
    echo アプリを停止しました。
) else (
    echo 起動しているアプリが見つかりませんでした。
)
pause
