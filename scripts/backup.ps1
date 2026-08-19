<#
.SYNOPSIS
    Create a backup from the command line or Windows Task Scheduler.

.DESCRIPTION
    Wraps the `backup` management command so scheduled backups work without
    Celery or Redis. Register it with Task Scheduler:

        schtasks /Create /TN "SurfSchool Daily Backup" /TR ^
          "powershell.exe -ExecutionPolicy Bypass -File D:\Surf_School\scripts\backup.ps1 -Type daily" ^
          /SC DAILY /ST 03:00

.EXAMPLE
    .\scripts\backup.ps1
    .\scripts\backup.ps1 -Type daily -Scope full
    .\scripts\backup.ps1 -Verify
    .\scripts\backup.ps1 -ApplyRetention
#>
[CmdletBinding()]
param(
    [ValidateSet('manual', 'daily', 'weekly', 'monthly')]
    [string]$Type = 'manual',

    [ValidateSet('database', 'media', 'full', 'config')]
    [string]$Scope = 'full',

    [string]$Notes = '',
    [switch]$Verify,
    [switch]$ApplyRetention,
    [switch]$List
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) { throw "Virtual environment not found. Run .\scripts\setup.ps1 first." }

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

if ($List) {
    & $VenvPython manage.py backup --list
    exit $LASTEXITCODE
}

if ($ApplyRetention) {
    Write-Host "[$stamp] Applying the retention policy..." -ForegroundColor Cyan
    & $VenvPython manage.py backup --apply-retention
    exit $LASTEXITCODE
}

Write-Host "[$stamp] Creating $Type backup (scope: $Scope)..." -ForegroundColor Cyan

$arguments = @('manage.py', 'backup', '--type', $Type, '--scope', $Scope)
if ($Notes)  { $arguments += @('--notes', $Notes) }
if ($Verify) { $arguments += '--verify' }

& $VenvPython @arguments
$exitCode = $LASTEXITCODE

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
if ($exitCode -eq 0) {
    Write-Host "[$stamp] Backup completed." -ForegroundColor Green
} else {
    Write-Host "[$stamp] Backup FAILED (exit code $exitCode)." -ForegroundColor Red
    # Leave a breadcrumb for Task Scheduler runs that nobody watches.
    $logDirectory = Join-Path $ProjectRoot 'logs'
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    "[$stamp] Backup failed with exit code $exitCode (type=$Type scope=$Scope)" |
        Add-Content -Path (Join-Path $logDirectory 'backup.log') -Encoding utf8
}

exit $exitCode
