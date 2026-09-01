"""Paketlenmiş uygulamanın giriş noktası (PyInstaller).

Bu dosya yalnızca `.exe` içinde çalışır. Görevleri:

1. Yazılabilir veri dizinini hazırlar (exe'nin yanı — bkz. config/settings/base.py)
2. İlk çalıştırmada veritabanını oluşturur ve ilk kurulum sihirbazını sunar
3. WSGI sunucusunu (waitress) başlatır — Celery/Redis gerekmez, her şey
   süreç içinde çalışır
4. Tarayıcıyı açar
5. Konsolda anlaşılır durum bilgisi gösterir

Geliştirme sırasında bu dosyaya gerek yoktur; `manage.py runserver` veya
`scripts/start.ps1` kullanın.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

APP_NAME = "Smart Surf School Yonetim Sistemi"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


# ------------------------------------------------------------------
#  Konsol yardımcıları
# ------------------------------------------------------------------
def _make_console_tolerant() -> None:
    """Konsolun kodlayamadığı bir karakter uygulamayı düşürmesin.

    Windows konsolu Türkçe sistemlerde cp1254/cp857 kullanır. Bir çıktı
    metni bu tabloda olmayan bir işaret içerirse Python ``UnicodeEncodeError``
    yükseltir; standart çıktıya yazan her yer buna karşı savunmasızdır.
    Kodlamayı değiştirmiyoruz — yalnızca hata davranışını
    "yerine soru işareti koy"a çekiyoruz.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


_make_console_tolerant()


def _supports_unicode() -> bool:
    try:
        "─".encode(sys.stdout.encoding or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError, TypeError, AttributeError):
        return False


BOX = "═" if _supports_unicode() else "="


def banner(text: str) -> None:
    line = BOX * 62
    print()
    print(f"  {line}")
    print(f"    {text}")
    print(f"  {line}")
    print()


def step(text: str) -> None:
    print(f"  [*] {text}")


def ok(text: str) -> None:
    print(f"  [OK] {text}")


def warn(text: str) -> None:
    print(f"  [!]  {text}")


def fail(text: str) -> None:
    print(f"  [X]  {text}")


def stdin_is_interactive() -> bool:
    """Kullanıcıdan girdi alınabilir mi? (Servis/otomasyon modunda alınamaz.)"""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def pause_before_exit(message: str = "Kapatmak için Enter'a basın...") -> None:
    if stdin_is_interactive():
        try:
            input(f"  {message}")
        except (EOFError, KeyboardInterrupt):
            pass


# ------------------------------------------------------------------
#  Ağ
# ------------------------------------------------------------------
def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


def find_free_port(host: str, start: int, attempts: int = 20) -> int | None:
    for offset in range(attempts):
        candidate = start + offset
        if not port_in_use(host, candidate):
            return candidate
    return None


def is_our_app(host: str, port: int) -> bool:
    """Porttaki sunucu bu uygulama mı?

    Port dolu olması tek başına "program zaten açık" anlamına gelmez;
    bambaşka bir yazılım da o portu kullanıyor olabilir. Sağlık uç
    noktasına (/api/health/) bakarak emin oluruz.
    """
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(  # noqa: S310 - sabit, yerel adres
            f"http://{host}:{port}/api/health/", timeout=2
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return "status" in payload and "version" in payload
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def open_browser_when_ready(url: str, host: str, port: int) -> None:
    """Sunucu gerçekten yanıt vermeye başlayınca tarayıcıyı açar."""
    for _ in range(60):
        time.sleep(0.5)
        if port_in_use(host, port):
            time.sleep(0.8)  # ilk isteğin hazır olması için kısa pay
            try:
                webbrowser.open(url)
            except Exception:
                warn(f"Tarayıcı açılamadı. Elle açın: {url}")
            return


# ------------------------------------------------------------------
#  Django kurulumu
# ------------------------------------------------------------------
def prepare_database() -> tuple[bool, int]:
    """Veritabanını hazırlar. (yeni_kurulum_mu, kullanıcı_sayısı) döndürür."""
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import connection

    tables_before = set(connection.introspection.table_names())
    is_new = "django_migrations" not in tables_before

    step("Veritabanı hazırlanıyor...")
    call_command("migrate", interactive=False, verbosity=0)
    ok("Veritabanı hazır")

    user_count = get_user_model().objects.count()
    return is_new, user_count


def seed_baseline_content() -> None:
    """Yeni kurulumda temel içeriği yükler (hepsi idempotent komutlardır)."""
    from django.core.management import call_command

    step("Temel içerik yükleniyor (roller, ekipman kategorileri, yardım, eğitim)...")
    for command in (
        "bootstrap_roles",
        "seed_equipment_categories",
        "seed_help_content",
        "seed_training_content",
    ):
        try:
            call_command(command, verbosity=0)
        except Exception as exc:  # noqa: BLE001 - içerik eksikliği ölümcül değil
            warn(f"{command} çalıştırılamadı: {exc}")
    ok("Temel içerik hazır")


def first_run_wizard() -> None:
    """Hiç kullanıcı yokken çalışan basit kurulum sihirbazı.

    Etkileşimsiz ortamda (stdin yok / kapalı) hiçbir şey sormadan atlar;
    yönetici hesabı daha sonra da oluşturulabilir.
    """
    from django.contrib.auth import get_user_model

    print()
    warn("Sistemde henüz kullanıcı yok — giriş yapamazsınız.")

    if not stdin_is_interactive():
        warn("Etkileşimli konsol yok; yönetici hesabı adımı atlandı.")
        return

    print()
    print("      1) Yönetici hesabı oluştur (önerilir)")
    print("      2) Atla (daha sonra kurarım)")
    print()

    try:
        choice = input("  Seçiminiz [1/2] (varsayılan 1): ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        choice = "2"

    if choice != "1":
        warn("Atlandı. Kullanıcı oluşturmadan giriş yapamazsınız.")
        return

    User = get_user_model()
    print()
    print("  Yönetici hesabı oluşturuluyor.")
    print("  (Parola en az 10 karakter olmalı ve yalnızca rakamdan oluşmamalı.)")
    print()
    while True:
        try:
            username = input("  Kullanıcı adı: ").strip()
            if not username:
                continue
            if User.objects.filter(username__iexact=username).exists():
                fail("Bu kullanıcı adı zaten var.")
                continue
            import getpass

            password = getpass.getpass("  Parola: ")
            confirm = getpass.getpass("  Parola (tekrar): ")
            if password != confirm:
                fail("Parolalar eşleşmiyor.")
                continue

            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError

            try:
                validate_password(password)
            except ValidationError as exc:
                for message in exc.messages:
                    fail(message)
                continue

            # create_superuser rolü SUPER_ADMIN olarak ayarlar
            # (bkz. apps/accounts/models.py UserManager).
            User.objects.create_superuser(username=username, email="", password=password)
            print()
            ok(f"Yönetici hesabı oluşturuldu: {username}")
            break
        except (EOFError, KeyboardInterrupt):
            print()
            warn("Atlandı.")
            break


def write_env_template(data_dir) -> None:
    """İlk çalıştırmada, kullanıcının düzenleyebileceği bir .env şablonu bırakır.

    Şablondaki her satır yorumdur; hiçbir ayar zorunlu değildir. Gizli anahtar
    .env'e yazılmaz — config/settings/desktop.py onu .secret.key dosyasında
    kendisi yönetir.
    """
    env_file = data_dir / ".env"
    if env_file.exists():
        return
    template = "\n".join(
        [
            "# =========================================================================",
            "# Smart Surf School — masaüstü ayar dosyası",
            "# Bir ayarı değiştirmek için satırın başındaki '#' işaretini kaldırın ve",
            "# programı yeniden başlatın. Tüm ayarlar isteğe bağlıdır.",
            "# =========================================================================",
            "",
            "# Okul adı ve para birimi",
            "# SCHOOL_NAME=Smart Surf School",
            "# SCHOOL_CURRENCY=TRY",
            "",
            "# Arayüz dili (tr | en) ve saat dilimi",
            "# DJANGO_LANGUAGE_CODE=tr",
            "# DJANGO_TIME_ZONE=Europe/Istanbul",
            "",
            "# Sunucu adresi ve portu",
            "# SURF_SCHOOL_HOST=127.0.0.1",
            "# SURF_SCHOOL_PORT=8000",
            "",
            "# Yapay zeka sağlayıcıları (tanımlanırsa etkinleşir)",
            "# LM_STUDIO_BASE_URL=http://localhost:1234/v1",
            "# NVIDIA_API_KEY=",
            "# ANTHROPIC_API_KEY=",
            "",
        ]
    )
    try:
        env_file.write_text(template, encoding="utf-8")
        ok(f"Ayar dosyası oluşturuldu: {env_file.name}")
    except OSError:
        warn(".env şablonu yazılamadı (disk salt okunur olabilir).")


# ------------------------------------------------------------------
#  Sunucu
# ------------------------------------------------------------------
def run_server(host: str, port: int) -> None:
    """Waitress (WSGI) ile sunucuyu başlatır.

    Waitress saf Python'dur, Windows'ta ek servis gerektirmez ve üretim
    kalitesinde bir WSGI sunucusudur. Uygulamada WebSocket olmadığı için
    ASGI/Daphne'ye gerek yoktur.
    """
    from waitress import serve

    from config.wsgi import application

    serve(application, host=host, port=port, threads=8, ident="SmartSurfSchool")


# ------------------------------------------------------------------
#  Ana akış
# ------------------------------------------------------------------
def main() -> int:
    # Paketlenmiş uygulama masaüstü ayarlarıyla çalışır: DEBUG kapalı,
    # Celery süreç içi, medya Django tarafından sunulur.
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.desktop"

    banner(APP_NAME)

    import django

    from config.settings.base import DATA_DIR

    print(f"  Veri klasörü : {DATA_DIR}")

    write_env_template(DATA_DIR)

    django.setup()

    # ---------- veritabanı ----------
    try:
        is_new, user_count = prepare_database()
    except Exception as exc:  # pragma: no cover
        fail(f"Veritabanı hazırlanamadı: {exc}")
        print()
        print(f"  Ayrıntı için: {DATA_DIR / 'logs' / 'surf_school.log'}")
        pause_before_exit()
        return 1

    if is_new:
        try:
            seed_baseline_content()
        except Exception as exc:  # noqa: BLE001 - pragma: no cover
            warn(f"Temel içerik yüklenemedi: {exc}")

    if user_count == 0:
        first_run_wizard()

    # ---------- port ----------
    host = os.environ.get("SURF_SCHOOL_HOST", DEFAULT_HOST)
    try:
        port = int(os.environ.get("SURF_SCHOOL_PORT", DEFAULT_PORT))
    except ValueError:
        port = DEFAULT_PORT

    if port_in_use(host, port):
        if is_our_app(host, port):
            url = f"http://{host}:{port}"
            ok("Program zaten çalışıyor.")
            print(f"       Tarayıcı açılıyor: {url}")
            webbrowser.open(url)
            time.sleep(2)
            return 0

        # Portu başka bir yazılım tutuyor; ona tarayıcı açmak yanlış olur.
        warn(f"{port} portunu başka bir program kullanıyor.")
        free = find_free_port(host, port + 1)
        if free is None:
            fail("Boş port bulunamadı.")
            pause_before_exit()
            return 1
        port = free
        print(f"       Bunun yerine {port} portu kullanılacak.")

    url = f"http://{host}:{port}"

    print()
    ok("Sunucu başlatılıyor")
    print()
    print(f"      Adres  : {url}")
    print("      Durdur : Bu pencerede Ctrl+C  (veya pencereyi kapatın)")
    print()
    print(f"  {'-' * 60}")
    print()

    threading.Thread(target=open_browser_when_ready, args=(url, host, port), daemon=True).start()

    try:
        run_server(host, port)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # pragma: no cover
        print()
        fail(f"Sunucu hatası: {exc}")
        pause_before_exit()
        return 1

    print()
    print("  Sunucu durduruldu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
