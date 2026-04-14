@echo off
setlocal

set "REPO_ROOT=%~dp0"
set "PS_SCRIPT=%REPO_ROOT%tools\stopBot.ps1"

if not exist "%PS_SCRIPT%" (
    echo Stop script not found: "%PS_SCRIPT%"
    pause
    exit /b 1
)

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%"

if errorlevel 1 (
    echo.
    echo Jane stop failed.
    pause
    exit /b 1
)
