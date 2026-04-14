$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$botPath = Join-Path $repoRoot "bot.py"
$botPathResolved = [System.IO.Path]::GetFullPath($botPath)

if (-not (Test-Path $pythonPath)) {
    throw "Python executable not found at $pythonPath"
}

if (-not (Test-Path $botPath)) {
    throw "bot.py not found at $botPath"
}

$botProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        ($_.Name -match "^python(?:w)?(?:\.exe)?$") -and
        $_.CommandLine -and
        $_.CommandLine.Contains($botPathResolved)
    }

foreach ($process in $botProcesses) {
    try {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Warning "Failed to stop process $($process.ProcessId): $($_.Exception.Message)"
    }
}

Set-Location $repoRoot
Start-Process -FilePath $pythonPath -ArgumentList $botPathResolved -WorkingDirectory $repoRoot
Write-Host "Jane restart requested."
