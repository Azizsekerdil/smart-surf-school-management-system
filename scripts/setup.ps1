<#
.SYNOPSIS
    One-command development setup for the Smart Surf School Management System.

.DESCRIPTION
    Creates the virtual environment, installs Python and Node dependencies,
    builds the front-end assets, applies migrations, compiles the translation
    catalogues and (optionally) loads demo data.

    Safe to re-run: every step detects work that is already done.

.EXAMPLE
    .\scripts\setup.ps1
    .\scripts\setup.ps1 -WithDemoData
    .\scripts\setup.ps1 -SkipNode
#>
[CmdletBinding()]
param(
    [switch]$WithDemoData,
    [switch]$SkipNode,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step($Message) { Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Write-Ok($Message)   { Write-Host "  [ok] $Message" -ForegroundColor Green }
function Write-Warn($Message) { Write-Host "  [!]  $Message" -ForegroundColor Yellow }

Write-Host "Smart Surf School Management System - setup" -ForegroundColor White
Write-Host "Project root: $ProjectRoot"

# ---------------------------------------------------------------------------
Write-Step "Checking prerequisites"

try {
    $pythonVersion = & python --version 2>&1
    Write-Ok "Python: $pythonVersion"
} catch {
    throw "Python 3.11+ is required but was not found on PATH. Install it from https://python.org"
}

if (-not $SkipNode) {
    try {
        $nodeVersion = & node --version 2>&1
        Write-Ok "Node: $nodeVersion"
    } catch {
        Write-Warn "Node.js not found - front-end assets will not be rebuilt. Use -SkipNode to silence this."
        $SkipNode = $true
    }
}

try { Write-Ok "Git: $(& git --version 2>&1)" } catch { Write-Warn "Git not found (optional)." }

# ---------------------------------------------------------------------------
Write-Step "Virtual environment"

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if ((Test-Path $VenvPython) -and (-not $Force)) {
    Write-Ok "Reusing existing .venv"
} else {
    if ($Force -and (Test-Path '.venv')) {
        Write-Warn "Removing existing .venv (-Force)"
        Remove-Item -Recurse -Force '.venv'
    }
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
    Write-Ok "Created .venv"
}

# ---------------------------------------------------------------------------
Write-Step "Python dependencies"
& $VenvPython -m pip install --upgrade pip setuptools wheel --quiet
& $VenvPython -m pip install -r requirements.txt -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
Write-Ok "Installed"

# ---------------------------------------------------------------------------
Write-Step "Environment file"

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    $secret = & $VenvPython -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
    (Get-Content '.env' -Raw) -replace 'DJANGO_SECRET_KEY=.*', "DJANGO_SECRET_KEY=$secret" |
        Set-Content '.env' -Encoding utf8 -NoNewline
    Write-Ok "Created .env from .env.example with a freshly generated SECRET_KEY"
    Write-Warn "Add your NVIDIA_API_KEY / ANTHROPIC_API_KEY to .env to enable cloud AI (optional)."
} else {
    Write-Ok ".env already exists - left untouched"
}

# ---------------------------------------------------------------------------
if (-not $SkipNode) {
    Write-Step "Front-end assets"
    if ((-not (Test-Path 'node_modules')) -or $Force) {
        & npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { Write-Warn "npm install failed - continuing with the committed CSS." }
    } else {
        Write-Ok "node_modules present"
    }
    & npm run build
    if ($LASTEXITCODE -eq 0) { Write-Ok "Vendored assets and built Tailwind CSS" }
    else { Write-Warn "Asset build failed - the committed static/css/app.css will be used." }
}

# ---------------------------------------------------------------------------
Write-Step "Database"
& $VenvPython manage.py makemigrations --noinput
& $VenvPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Migrations failed." }
Write-Ok "Migrations applied"

# ---------------------------------------------------------------------------
Write-Step "Roles and permissions"
& $VenvPython manage.py bootstrap_roles
Write-Ok "Role groups synchronised"

# ---------------------------------------------------------------------------
Write-Step "Translations"
# Pure-Python: GNU gettext is not required.
& $VenvPython manage.py i18n_compile
Write-Ok "Translation catalogues compiled"

# ---------------------------------------------------------------------------
Write-Step "Help and training content"
& $VenvPython manage.py seed_help_content 2>&1 | Out-Null
& $VenvPython manage.py seed_training_content 2>&1 | Out-Null
Write-Ok "Guidance content loaded"

# ---------------------------------------------------------------------------
if ($WithDemoData) {
    Write-Step "Demo data"
    & $VenvPython manage.py seed_demo_data
    if ($LASTEXITCODE -eq 0) { Write-Ok "Demo data loaded" } else { Write-Warn "Demo data failed." }
}

# ---------------------------------------------------------------------------
Write-Step "Administrator account"
$hasSuperuser = & $VenvPython -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');django.setup();from django.contrib.auth import get_user_model;print(get_user_model().objects.filter(is_superuser=True).exists())"
if ($hasSuperuser -match 'True') {
    Write-Ok "A superuser already exists"
} else {
    Write-Warn "No superuser yet. Create one with:"
    Write-Host "    .\.venv\Scripts\python.exe manage.py createsuperuser" -ForegroundColor White
}

# ---------------------------------------------------------------------------
Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Start the application with:" -ForegroundColor White
Write-Host "    .\scripts\start.ps1" -ForegroundColor Cyan
Write-Host "Then open http://127.0.0.1:8000/" -ForegroundColor White
