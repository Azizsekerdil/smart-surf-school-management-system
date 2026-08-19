<#
.SYNOPSIS
    Start the development server (and optionally the Tailwind watcher and a
    Celery worker).

.EXAMPLE
    .\scripts\start.ps1
    .\scripts\start.ps1 -Port 8080
    .\scripts\start.ps1 -WatchCss
    .\scripts\start.ps1 -WithCelery
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindAddress = '127.0.0.1',
    [switch]$WatchCss,
    [switch]$WithCelery,
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment not found. Run .\scripts\setup.ps1 first."
}

Write-Host "Smart Surf School Management System" -ForegroundColor Cyan

if (-not $SkipChecks) {
    Write-Host "Running system checks..." -ForegroundColor White
    & $VenvPython manage.py check
    if ($LASTEXITCODE -ne 0) { throw "System checks failed. Fix the errors above before starting." }

    $pending = & $VenvPython manage.py showmigrations --plan 2>&1 | Select-String -Pattern '^\[ \]'
    if ($pending) {
        Write-Host "There are unapplied migrations. Applying them now..." -ForegroundColor Yellow
        & $VenvPython manage.py migrate --noinput
    }
}

# --- LM Studio status (informational only) ---------------------------------
try {
    $null = Invoke-RestMethod -Uri 'http://localhost:1234/v1/models' -TimeoutSec 2 -ErrorAction Stop
    Write-Host "  Local AI: LM Studio is running" -ForegroundColor Green
} catch {
    Write-Host "  Local AI: LM Studio is not running (the app works without it)" -ForegroundColor DarkGray
}

# --- optional background processes -----------------------------------------
$jobs = @()

if ($WatchCss) {
    Write-Host "  Starting Tailwind watcher..." -ForegroundColor White
    $jobs += Start-Process -FilePath 'npm' -ArgumentList 'run', 'watch:css' -PassThru -NoNewWindow
}

if ($WithCelery) {
    Write-Host "  Starting Celery worker (solo pool - required on Windows)..." -ForegroundColor White
    $jobs += Start-Process -FilePath $VenvPython -ArgumentList `
        '-m', 'celery', '-A', 'config', 'worker', '-l', 'info', '--pool=solo' -PassThru -NoNewWindow
}

Write-Host "`nServer starting on http://${BindAddress}:${Port}/" -ForegroundColor Green
Write-Host "  Admin:    http://${BindAddress}:${Port}/admin/"
Write-Host "  API docs: http://${BindAddress}:${Port}/api/docs/"
Write-Host "  Health:   http://${BindAddress}:${Port}/api/health/"
Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor DarkGray

try {
    & $VenvPython manage.py runserver "${BindAddress}:${Port}"
} finally {
    foreach ($job in $jobs) {
        if ($job -and -not $job.HasExited) {
            Write-Host "Stopping background process $($job.Id)..." -ForegroundColor DarkGray
            Stop-Process -Id $job.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
