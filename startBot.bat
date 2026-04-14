@echo off
setlocal

set "REPO_ROOT=%~dp0"
set "PYTHON_EXE=%REPO_ROOT%.venv\Scripts\python.exe"
set "BOT_FILE=%REPO_ROOT%bot.py"

if not exist "%PYTHON_EXE%" (
    echo Python executable not found: "%PYTHON_EXE%"
    pause
    exit /b 1
)

if not exist "%BOT_FILE%" (
    echo bot.py not found: "%BOT_FILE%"
    pause
    exit /b 1
)

cd /d "%REPO_ROOT%"
"%PYTHON_EXE%" "%BOT_FILE%"

if errorlevel 1 (
    echo.
    echo Jane stopped with an error.
    pause
)
