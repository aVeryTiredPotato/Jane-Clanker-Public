@echo off
setlocal

set "REPO_ROOT=%~dp0"
set "PS_SCRIPT=%REPO_ROOT%tools\restartBot.ps1"

if not exist "%PS_SCRIPT%" (
    echo Restart script not found: "%PS_SCRIPT%"
    pause
    exit /b 1
)

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%"

if errorlevel 1 (
    echo.
    echo Jane restart failed.
    pause
    exit /b 1
)

echo Jane restart requested.
