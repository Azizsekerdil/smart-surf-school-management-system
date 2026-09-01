<#
.SYNOPSIS
    Tek dosyalik Windows uygulamasi (.exe) uretir.

.DESCRIPTION
    Sira onemlidir:
      1. Ikon uretilir (yoksa)
      2. Statik dosyalar MASAUSTU AYARLARIYLA toplanir
         DEBUG=False iken WhiteNoise hash'li dosya adlari ve
         staticfiles.json manifestosu uretir. Bu manifesto pakete
         girmezse uygulama her sayfada 500 hatasi verir.
      3. PyInstaller paketi olusturur (surf_school.spec)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
    .\scripts\build_exe.ps1 -SkipStatic     # statikleri yeniden toplama
#>

[CmdletBinding()]
param(
    [switch]$SkipStatic
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[X] Sanal ortam bulunamadi. Once scripts\setup.ps1 calistirin." -ForegroundColor Red
    exit 1
}

function Write-Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    [OK] $m" -ForegroundColor Green }

Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Blue
Write-Host "    SMART SURF SCHOOL - EXE PAKETLEME" -ForegroundColor Blue
Write-Host "  ============================================================" -ForegroundColor Blue

# ------------------------------------------------------------------ 1) Bagimliliklar
Write-Step "PyInstaller ve waitress kontrol ediliyor"
& $venvPython -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    PyInstaller kuruluyor..." -ForegroundColor Yellow
    & $venvPython -m pip install pyinstaller --quiet
}
& $venvPython -c "import waitress" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    waitress kuruluyor..." -ForegroundColor Yellow
    & $venvPython -m pip install waitress --quiet
}
$pyiVersion = & $venvPython -c "import PyInstaller; print(PyInstaller.__version__)"
Write-Ok "PyInstaller $pyiVersion"

# ------------------------------------------------------------------ 2) Ikon
Write-Step "Uygulama ikonu"
if (Test-Path "assets\surf_school.ico") {
    Write-Ok "Mevcut ikon kullanilacak"
} else {
    & $venvPython scripts\make_icon.py | Out-Null
    Write-Ok "Ikon uretildi"
}

# ------------------------------------------------------------------ 3) Statik dosyalar
if (-not $SkipStatic) {
    Write-Step "Statik dosyalar toplaniyor (masaustu ayarlariyla)"

    # KRITIK: masaustu ayarlarinda DEBUG=False oldugu icin WhiteNoise
    # manifest depolamasi kullanilir ve staticfiles.json uretilir. Bu
    # manifest uretilmezse paketlenmis uygulama calisma aninda
    # "Missing staticfiles manifest entry" hatasi verir.
    $env:DJANGO_SETTINGS_MODULE = "config.settings.desktop"
    $env:DJANGO_SECRET_KEY = "build-time-only-not-a-real-secret-key-1234567890"

    # NOT: native komutlarda "2>&1" kullanmayin. Windows PowerShell 5.1
    # stderr satirlarini ErrorRecord'a sarar ve komut basarili donse bile
    # $ErrorActionPreference="Stop" ile script'i durdurur.
    & $venvPython manage.py collectstatic --noinput --clear | Select-Object -Last 1

    if (-not (Test-Path "staticfiles\staticfiles.json")) {
        Write-Host "    [X] staticfiles.json uretilmedi." -ForegroundColor Red
        Write-Host "        Paketlenmis uygulama statik dosyalari sunamaz." -ForegroundColor Red
        exit 1
    }
    $entries = ((Get-Content "staticfiles\staticfiles.json" -Raw | ConvertFrom-Json).paths | Get-Member -MemberType NoteProperty).Count
    Write-Ok "Manifest uretildi ($entries dosya)"
}

# ------------------------------------------------------------------ 4) Paketleme
Write-Step "Paket olusturuluyor (birkac dakika surebilir)"

# PyInstaller'in Django kancasi ayar modulunu ice aktarir; spec dosyasi bu
# degiskenleri kendisi de tanimlar (setdefault), burada acikca veriyoruz.
$env:DJANGO_SETTINGS_MODULE = "config.settings.desktop"
$env:DJANGO_SECRET_KEY = "build-time-only-not-a-real-secret-key-1234567890"

# PyInstaller ilerleme bilgisini stderr'e yazar; bu bir hata degildir.
# Ciktiyi dosyaya alip yalnizca sonucu gosteriyoruz.
$buildLog = Join-Path $ProjectRoot "build\pyinstaller.log"
New-Item -ItemType Directory -Force -Path (Split-Path $buildLog) | Out-Null

$prevPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $venvPython -m PyInstaller surf_school.spec --noconfirm --log-level WARN *> $buildLog
$pyiExit = $LASTEXITCODE
$ErrorActionPreference = $prevPreference
Remove-Item Env:\DJANGO_SETTINGS_MODULE, Env:\DJANGO_SECRET_KEY -ErrorAction SilentlyContinue

if ($pyiExit -ne 0) {
    Write-Host "    [X] PyInstaller hata verdi (cikis kodu $pyiExit)." -ForegroundColor Red
    Get-Content $buildLog -Tail 25
    exit 1
}

$warnings = @(Get-Content $buildLog | Where-Object { $_ -match "^\d+ (WARNING|ERROR):" })
if ($warnings.Count -gt 0) {
    Write-Host "    $($warnings.Count) uyari (ayrinti: build\pyinstaller.log)" -ForegroundColor Yellow
}
Write-Ok "Derleme tamam"

$exe = Join-Path $ProjectRoot "dist\Smart Surf School.exe"
if (-not (Test-Path $exe)) {
    Write-Host "    [X] Paketleme basarisiz." -ForegroundColor Red
    exit 1
}

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)

Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "    PAKETLEME TAMAMLANDI" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "    Dosya : $exe"
Write-Host "    Boyut : $sizeMb MB"
Write-Host ""
Write-Host "    Bu tek dosya calisir; Python kurulumu gerekmez." -ForegroundColor White
Write-Host "    Veritabani, medya ve gunlukler exe'nin YANINDA olusur," -ForegroundColor White
Write-Host "    bu yuzden exe'yi bos bir klasore koyun." -ForegroundColor White
Write-Host ""
