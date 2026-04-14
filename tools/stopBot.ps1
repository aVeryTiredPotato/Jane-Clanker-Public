$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$botPath = Join-Path $repoRoot "bot.py"
$botPathResolved = [System.IO.Path]::GetFullPath($botPath)

if (-not (Test-Path $botPath)) {
    throw "bot.py not found at $botPath"
}

$botProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        ($_.Name -match "^python(?:w)?(?:\.exe)?$") -and
        $_.CommandLine -and
        $_.CommandLine.Contains($botPathResolved)
    }

if (-not $botProcesses) {
    Write-Host "Jane is not currently running."
    exit 0
}

foreach ($process in $botProcesses) {
    try {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        Write-Host "Stopped Jane process $($process.ProcessId)."
    } catch {
        Write-Warning "Failed to stop process $($process.ProcessId): $($_.Exception.Message)"
    }
}
