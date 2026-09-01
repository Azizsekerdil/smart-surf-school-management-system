"""Masaüstü paketi için uygulama ikonu üretir.

Depoda hazır bir logo PNG'si yok; ikon, static/img/favicon.svg ile aynı
görsel dili kullanarak programatik olarak çizilir (Pillow ile): yuvarlatılmış
mavi zemin üzerinde iki beyaz dalga. Böylece harici bir görsel dosyasına
bağımlılık oluşmaz ve ikon her boyutta net görünür.

Kullanım:
    python scripts/make_icon.py
Çıktı:
    assets/surf_school.ico   (16, 32, 48, 64, 128, 256 piksel)
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "surf_school.ico"

# Marka rengi — static/img/favicon.svg ile aynı (#0083ce)
BRAND = (0, 131, 206)
BRAND_DEEP = (0, 90, 156)
WHITE = (255, 255, 255)


def rounded_background(size: int) -> Image.Image:
    """Köşeleri yuvarlatılmış, dikey degrade mavi zemin."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(gradient)

    for y in range(size):
        ratio = y / max(size - 1, 1)
        color = tuple(int(BRAND[i] + (BRAND_DEEP[i] - BRAND[i]) * ratio) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color + (255,))

    mask = Image.new("L", (size, size), 0)
    radius = max(int(size * 0.22), 2)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    image.paste(gradient, (0, 0), mask)
    return image


def draw_waves(image: Image.Image, size: int) -> None:
    """favicon.svg'deki gibi iki yatay dalga çizgisi çizer."""
    draw = ImageDraw.Draw(image)
    unit = size / 100.0
    width = max(int(unit * 7), 1)

    def wave(center_y: float, alpha: int) -> None:
        points = []
        for step in range(0, 201):
            x = 12 * unit + (76 * unit) * (step / 200.0)
            # 2,5 tam dalga; genlik yüksekliğin ~%7'si
            y = center_y * unit + math.sin(step / 200.0 * math.pi * 5) * (7 * unit)
            points.append((x, y))
        draw.line(points, fill=WHITE + (alpha,), width=width, joint="curve")

    wave(38, 255)
    wave(64, 210)


def build() -> Path:
    sizes = [256, 128, 64, 48, 32, 16]
    frames = []
    for size in sizes:
        # Kenar yumuşatma için büyük çizip küçült
        scale = 4 if size >= 32 else 8
        canvas = rounded_background(size * scale)
        draw_waves(canvas, size * scale)
        frames.append(canvas.resize((size, size), Image.LANCZOS))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUTPUT, format="ICO", sizes=[(s, s) for s in sizes])
    return OUTPUT


if __name__ == "__main__":
    path = build()
    print(f"Ikon olusturuldu: {path}  ({path.stat().st_size} bayt)")
