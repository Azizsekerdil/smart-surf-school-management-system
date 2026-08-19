<#
.SYNOPSIS
    Run the test suite, coverage and the security checks.

.EXAMPLE
    .\scripts\test.ps1
    .\scripts\test.ps1 -Coverage
    .\scripts\test.ps1 -App bookings
    .\scripts\test.ps1 -Security
    .\scripts\test.ps1 -All
#>
[CmdletBinding()]
param(
    [string]$App,
    [switch]$Coverage,
    [switch]$Security,
    [switch]$Lint,
    [switch]$All,
    [switch]$Fast
)

$ErrorActionPreference = 'Continue'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) { throw "Run .\scripts\setup.ps1 first." }

if ($All) { $Coverage = $true; $Security = $true; $Lint = $true }

$failures = @()

function Write-Section($Title) {
    Write-Host "`n=== $Title ===" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
Write-Section "Django system checks"
& $VenvPython manage.py check --deploy --settings=config.settings.test 2>&1 | Out-String | Write-Host
if ($LASTEXITCODE -ne 0) { $failures += 'django-check' }

# ---------------------------------------------------------------------------
if ($Lint) {
    Write-Section "Lint (ruff)"
    & $VenvPython -m ruff check apps config --no-cache
    if ($LASTEXITCODE -ne 0) { $failures += 'ruff' }
}

# ---------------------------------------------------------------------------
Write-Section "Tests"

$pytestArgs = @('-m', 'pytest', '-q', '--no-header')
if ($App) { $pytestArgs += "apps/$App" }
if ($Fast) { $pytestArgs += '-x', '--ff' }
if ($Coverage) {
    $pytestArgs += '--cov=apps', '--cov-report=term-missing:skip-covered', '--cov-report=html:htmlcov'
}

& $VenvPython @pytestArgs
if ($LASTEXITCODE -ne 0) { $failures += 'pytest' }

if ($Coverage -and (Test-Path 'htmlcov\index.html')) {
    Write-Host "`nCoverage report: $ProjectRoot\htmlcov\index.html" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
if ($Security) {
    Write-Section "Security static analysis (bandit)"
    # B101 (assert) is noisy in tests; the AI terminal subprocess calls are
    # reviewed separately and carry explicit nosec justifications.
    & $VenvPython -m bandit -r apps config -ll -x "*/tests/*,*/migrations/*" -f screen
    if ($LASTEXITCODE -gt 1) { $failures += 'bandit' }

    Write-Section "Dependency vulnerability audit (pip-audit)"
    & $VenvPython -m pip_audit --skip-editable 2>&1 | Out-String | Write-Host
    if ($LASTEXITCODE -ne 0) { Write-Host "pip-audit reported findings (see above)." -ForegroundColor Yellow }
}

# ---------------------------------------------------------------------------
Write-Host "`n===============================" -ForegroundColor White
if ($failures.Count -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "Failed: $($failures -join ', ')" -ForegroundColor Red
    exit 1
}
