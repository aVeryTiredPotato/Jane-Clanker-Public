$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$botPath = Join-Path $repoRoot "bot.py"

if (-not (Test-Path $pythonPath)) {
    throw "Python executable not found at $pythonPath"
}

if (-not (Test-Path $botPath)) {
    throw "bot.py not found at $botPath"
}

Set-Location $repoRoot
& $pythonPath $botPath
