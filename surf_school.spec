# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller yapılandırması — Smart Surf School Yönetim Sistemi.

Kullanım:
    python -m PyInstaller surf_school.spec --noconfirm
    (veya scripts/build_exe.ps1)

Taşınabilirlik: tüm yollar SPECPATH'e görelidir; aynı spec dosyası Windows'ta
ve macOS CI'da değişiklik gerektirmeden çalışır. Platforma özgü tek şey ikon
seçimidir (aşağıdaki sys.platform dallanması).

Django'yu paketlerken üç konu özel dikkat ister:

1. **Veri dosyaları**: şablonlar, statik dosyalar ve staticfiles manifestosu
   Python modülü olmadıkları için otomatik toplanmaz; açıkça eklenir.
   collectstatic, paketlemeden ÖNCE masaüstü ayarlarıyla çalıştırılmalıdır
   (build_exe.ps1 bunu yapar) — aksi halde staticfiles.json eksik kalır ve
   her sayfa 500 döner.
2. **Gizli içe aktarmalar**: Django uygulamaları, veritabanı arka uçları,
   Celery görevleri ve dize olarak referanslanan sınıflar (ör. WhiteNoise
   depolaması) dinamik yüklenir; PyInstaller'ın statik çözümleyicisi
   bunları göremez.
3. **Yazılabilir veri**: veritabanı, medya, günlükler ve yedekler paketin
   geçici açılma dizinine değil exe'nin yanına yazılır
   (bkz. config/settings/base.py DATA_DIR).
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT = Path(SPECPATH)  # noqa: F821 - SPECPATH, PyInstaller tarafından sağlanır

# PyInstaller'ın kendi Django kancası (hook-django), ayar modülünü içe
# aktararak dize referanslarını çözer. Ortamda ayar modülü tanımlı değilse
# kanca config/settings paketini (boş __init__) bulur ve çöker. Spec'i hem
# yerelde hem CI'da tek başına yeterli kılmak için burada tanımlanır.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.desktop")
os.environ.setdefault("DJANGO_SECRET_KEY", "build-time-only-not-a-real-secret-key-1234567890")

# ------------------------------------------------------------------
#  Veri dosyaları
# ------------------------------------------------------------------
datas = [
    (str(PROJECT / "templates"), "templates"),
    (str(PROJECT / "static"), "static"),
    (str(PROJECT / "staticfiles"), "staticfiles"),
    (str(PROJECT / ".env.example"), "."),
]

# Çeviri katalogları (varsa)
if (PROJECT / "locale").is_dir():
    datas.append((str(PROJECT / "locale"), "locale"))

# Django'nun ve üçüncü taraf uygulamaların kendi şablon/çeviri/statik
# dosyaları (admin arayüzü, DRF browsable API, Swagger sayfası, axes)
for package in ("django", "rest_framework", "drf_spectacular", "django_filters", "axes"):
    datas += collect_data_files(package, include_py_files=False)

# ------------------------------------------------------------------
#  Gizli içe aktarmalar
# ------------------------------------------------------------------
hiddenimports = [
    # Veritabanı arka uçları (dize ile seçilir)
    "django.db.backends.sqlite3",
    "django.db.backends.sqlite3.base",
    "django.db.backends.postgresql",
    "psycopg",
    # Şifreleme / oturum / önbellek
    "django.contrib.auth.hashers",
    "django.contrib.sessions.backends.db",
    "django.contrib.staticfiles.storage",
    "django.core.cache.backends.locmem",
    "django.core.cache.backends.redis",
    "whitenoise.storage",
    # WSGI sunucusu
    "waitress",
    # Celery'nin çalışma zamanı bağımlılıkları
    "billiard",
    "vine",
    # Raporlama / dışa aktarma
    "reportlab.pdfbase._fontdata",
    "openpyxl",
    "xlsxwriter",
    # QR / barkod
    "segno",
    "barcode",
    # HTTP istemcileri (AI ve hava durumu sağlayıcıları)
    "httpx",
    "httpcore",
]

# Proje uygulamaları ve alt modülleri (migration'lar, sinyaller, admin,
# management komutları, Celery görevleri)
for app in ("apps", "config"):
    hiddenimports += collect_submodules(app)

# Dinamik yüklenen Django ve üçüncü taraf paketler
for package in (
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "django_htmx",
    "axes",
    # WhiteNoise ara katmanı ayarlarda dize olarak referanslanır
    "whitenoise",
    # Celery eager modda süreç içinde çalışır; görev modülleri onu içe aktarır
    "celery",
    "kombu",
    # django-environ
    "environ",
):
    hiddenimports += collect_submodules(package)

hiddenimports = sorted(set(hiddenimports))

# ------------------------------------------------------------------
#  İkon — platforma göre (spec taşınabilir kalır)
# ------------------------------------------------------------------
icon_file = None
if sys.platform == "win32":
    _candidate = PROJECT / "assets" / "surf_school.ico"
    if _candidate.is_file():
        icon_file = str(_candidate)
elif sys.platform == "darwin":
    _candidate = PROJECT / "assets" / "surf_school.icns"
    if _candidate.is_file():
        icon_file = str(_candidate)

# ------------------------------------------------------------------
#  Analiz
# ------------------------------------------------------------------
a = Analysis(
    ["launcher.py"],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Paketi gereksiz büyüten, çalışma zamanında kullanılmayan bileşenler.
    # DİKKAT: numpy BURAYA EKLENMEZ — apps/analytics ve apps/ai onu kullanır.
    excludes=[
        "tkinter",
        "matplotlib",
        "pandas",
        "scipy",
        "IPython",
        "jupyter",
        "pytest",
        "_pytest",
        "mypy",
        "black",
        "ruff",
        "bandit",
        "pip_audit",
        "debug_toolbar",
        "setuptools._distutils",
        # Sunum/dokümantasyon araçları yalnızca geliştirme tarafındadır
        # (scripts/generate_presentation.py). lxml, python-pptx'in
        # bağımlılığıdır ve pakete girerse ~4 MB gereksiz yer kaplar.
        "pptx",
        "lxml",
        "win32com",
        "pythoncom",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Smart Surf School",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX bazı virüs tarayıcılarında yanlış alarm üretir
    runtime_tmpdir=None,
    console=True,  # sunucu durumu, kurulum sihirbazı ve Ctrl+C için gerekli
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)
