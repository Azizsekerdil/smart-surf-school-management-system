#!/usr/bin/env bash
# macOS paketleme betiği — Smart Surf School Yönetim Sistemi
#
# GitHub Actions'ta (macos-latest) veya bir Mac'te çalıştırılır:
#     chmod +x build_macos.sh && ./build_macos.sh
#
# Adımlar Windows'taki scripts/build_exe.ps1 ile aynı sırayı izler:
#   1. Bağımlılıklar (requirements.txt + PyInstaller)
#   2. İkon (.ico'daki çizim yardımcılarıyla PNG üret, sips+iconutil ile .icns)
#   3. collectstatic MASAÜSTÜ ayarlarıyla (staticfiles.json manifestosu şart)
#   4. PyInstaller (surf_school.spec — SPECPATH-göreli, platform dallanmalı)
#   5. dist/Smart Surf School-macOS.zip (ditto ile)
#
# NOT: "set -u" BİLEREK yok — macOS'un bash 3.2'si boş dizi açılımını
# (örn. "${ARGS[@]}") unbound sayar ve betiği düşürür.
set -eo pipefail

cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "HATA: macOS paketi bir Mac üzerinde oluşturulmalıdır."
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> Bağımlılıklar kuruluyor"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt
"$PYTHON_BIN" -m pip install pyinstaller

# Spec dosyası bu değişkenleri setdefault ile kendisi de tanımlar; collectstatic
# için burada açıkça veriyoruz (masaüstü ayarları .secret.key üretmesin diye
# derleme anahtarı sabittir, pakete girmez).
export DJANGO_SETTINGS_MODULE="config.settings.desktop"
export DJANGO_SECRET_KEY="build-time-only-not-a-real-secret-key-1234567890"

# ------------------------------------------------------------------ İkon
# Depoda logo PNG'si yok; scripts/make_icon.py ikonu Pillow ile programatik
# çizer. Aynı çizim yardımcılarıyla 1024px PNG üretip sips+iconutil ile
# .icns'e çeviriyoruz. Windows .ico macOS'ta geçersizdir; spec dosyası
# darwin'de assets/surf_school.icns arar, bulamazsa ikonsuz paketler.
if command -v sips >/dev/null && command -v iconutil >/dev/null; then
  echo "==> Uygulama ikonu (.icns) üretiliyor"
  mkdir -p build/macos
  "$PYTHON_BIN" - <<'PYEOF'
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from make_icon import draw_waves, rounded_background

size = 1024
canvas = rounded_background(size)
draw_waves(canvas, size)
out = Path("build/macos/surf_school_1024.png")
out.parent.mkdir(parents=True, exist_ok=True)
canvas.save(out)
print(f"PNG üretildi: {out}")
PYEOF
  ICONSET="build/macos/SurfSchool.iconset"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    sips -z "$size" "$size" build/macos/surf_school_1024.png \
      --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" build/macos/surf_school_1024.png \
      --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$PROJECT_ROOT/assets/surf_school.icns"
  echo "İkon hazır: assets/surf_school.icns"
else
  echo "UYARI: sips/iconutil bulunamadı; paket ikonsuz oluşturulacak."
fi

# ------------------------------------------------------------------ Statikler
# KRİTİK: masaüstü ayarlarında DEBUG=False olduğu için WhiteNoise manifest
# depolaması staticfiles.json üretir. Bu manifesto pakete girmezse uygulama
# her sayfada "Missing staticfiles manifest entry" ile 500 döner. staticfiles/
# .gitignore kapsamındadır — CI'da her derlemede burada üretilmek zorundadır.
echo "==> Statik dosyalar toplanıyor (masaüstü ayarlarıyla)"
"$PYTHON_BIN" manage.py collectstatic --noinput --clear
if [[ ! -f staticfiles/staticfiles.json ]]; then
  echo "HATA: staticfiles/staticfiles.json üretilemedi."
  exit 1
fi

# ------------------------------------------------------------------ Paketleme
# surf_school.spec SPECPATH-göreli ve taşınabilirdir: tüm --add-data eşdeğerleri
# ve hidden import'lar spec içindedir, Windows'a özel mutlak yol içermez.
echo "==> PyInstaller paketi oluşturuluyor"
rm -rf "dist/Smart Surf School" "dist/Smart Surf School-macOS.zip"
"$PYTHON_BIN" -m PyInstaller surf_school.spec --noconfirm

# Spec tek dosyalık KONSOL uygulaması üretir (sunucu durumu ve ilk kurulum
# sihirbazı konsol ister; --windowed kullanılmaz). Çıktı: dist/Smart Surf School
BIN_PATH="dist/Smart Surf School"
if [[ ! -f "$BIN_PATH" ]]; then
  echo "HATA: $BIN_PATH oluşturulamadı."
  exit 1
fi

ditto -c -k --keepParent "$BIN_PATH" "dist/Smart Surf School-macOS.zip"
echo "Tamamlandı: $BIN_PATH"
echo "Dağıtım ZIP'i: dist/Smart Surf School-macOS.zip"
