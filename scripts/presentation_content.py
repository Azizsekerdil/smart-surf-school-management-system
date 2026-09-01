"""Slide content for the Smart Surf School introduction decks.

Single source of truth for both languages. The renderer in
``generate_presentation.py`` reads this and produces the PPTX, PDF and HTML
files, so the wording lives in exactly one place.

A rule for anything written here
--------------------------------
**Every number on a slide must be one that was actually measured**, and the
deck says where it came from. No "100% secure", no "world first", no
"guaranteed compliant" — claims like that cannot be checked and would make the
honest numbers look like marketing too.

The figures below were counted mechanically from this source tree on
2026-08-19 -- from the Django app registry, the DRF router, the capability
matrix and a real ``pytest`` run:

    28 apps · 86 models · 1861 fields · 97 tables · 75 REST resources
    15 roles · 203 capabilities · 13 AI tools · 359 templates
    2130 tests collected, 2128 passing, 2 skipped
    37 signed-in screens + 2 public · 20 acceptance steps

**Code coverage is deliberately absent.** It was not measured for this release,
so quoting a percentage would be quoting a number nobody could check. Say
nothing rather than say something unverifiable.

The two skipped tests need a live AI provider, which the test settings forbid.
Acceptance steps 18 and 19 self-report SKIPPED when LM Studio or NVIDIA are
unreachable, so "20/20" only holds with both providers running -- the slides
say so.

AI latency figures, where shown, are single-run measurements from one machine.
They are not benchmarks.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Design tokens — shared by both languages and both variants
# ---------------------------------------------------------------------------
BRAND = "0083CE"
BRAND_DARK = "065889"
BRAND_DEEP = "072E4B"
BRAND_LIGHT = "75D5FF"
SAND = "CDA568"

INK = "0F172A"
INK_SOFT = "475569"
MUTED = "94A3B8"
PAPER = "FFFFFF"
PAPER_SOFT = "F8FAFC"

OK = "10B981"
WARN = "F59E0B"
RISK = "F43F5E"
VIOLET = "8B5CF6"

ACCENTS = (BRAND, OK, WARN, VIOLET, "06B6D4", SAND)

APP_VERSION = "1.0.0"
REPO_URL = "github.com/Azizsekerdil/smart-surf-school-management-system"


# ---------------------------------------------------------------------------
# Slide grammar
# ---------------------------------------------------------------------------
# Every slide is a dict with a "kind" the renderer knows how to draw:
#
#   title      : the opening slide
#   metrics    : a row/grid of big numbers with captions
#   cards      : 2x2 or 2x3 cards, each with a heading and body
#   bullets    : a heading plus a list, optionally with a side panel
#   split      : left prose, right list of labelled rows
#   screens    : one or two real UI screenshots with captions; paths are
#                relative to the repo root (see scripts/capture_screenshots.py)
#   table      : a simple header + rows table
#   quote      : one large statement, used for the design principles
#   closing    : the final slide
#
# Nothing here knows about PowerPoint; the renderer owns all geometry.


def _tr() -> list[dict]:
    """Turkish deck."""
    return [
        {
            "kind": "title",
            "eyebrow": "SÖRF OKULU YÖNETİM YAZILIMI",
            "title": "Akıllı Sörf Okulu\nYönetim Sistemi",
            "subtitle": "Bir sörf okulunun bütün günlük işleri tek uygulamada:\n"
            "dersler, rezervasyon, ekipman, deniz koşulları, güvenlik, finans ve yapay zekâ.",
            "meta": f"Sürüm {APP_VERSION}  ·  Django 5.2 LTS  ·  Türkçe / English",
            "highlights": [
                ("28", "modül, tek veri tabanı"),
                ("2.128", "geçen test, 0 başarısız"),
                ("39/39", "ekran çalışır durumda"),
            ],
        },
        {
            "kind": "bullets",
            "eyebrow": "PROBLEM",
            "title": "Bir sörf okulu kaç ayrı yerde çalışır?",
            "lead": "Tipik bir okulda gün şöyle geçer: rezervasyonlar WhatsApp'ta, "
            "öğrenci bilgileri deftere yazılı, ekipman kimde belli değil, "
            "tahsilat fişte, deniz durumu başka bir uygulamada.",
            "items": [
                ("Aynı saate iki ders", "Çakışmayı kimse görmez; iki eğitmen aynı öğrenciyi bekler."),
                ("Kayıp ekipman", "Board kimde? Ne zaman dönecek? Depozito ne oldu?"),
                ("Eksik güvenlik bilgisi", "Öğrencinin astımı olduğu eğitmene dersten sonra söylenir."),
                ("Görünmeyen para", "Ay sonunda ne kazanıldığı ancak tahmin edilir."),
                ("Dağılan kurum hafızası", "Bir çalışan ayrılınca bilgi de gider."),
            ],
            "note": "Bu yazılım bu beş problemi tek veri tabanında birleştirir.",
        },
        {
            "kind": "metrics",
            "eyebrow": "ÖLÇÜLEN DURUM",
            "title": "Rakamlarla sistem",
            "lead": "Aşağıdaki değerler projeden okundu — hiçbiri tahmin değil.",
            "metrics": [
                ("28", "modül"),
                ("86", "veri modeli"),
                ("75", "REST kaynağı"),
                ("15", "rol"),
                ("203", "yetki"),
                ("2.128", "geçen test"),
                ("39/39", "ekran çalışıyor"),
                ("20", "kabul adımı"),
            ],
            "note": "Testler `pytest` ile çalıştırıldı: 2.130 test toplandı, 2.128 geçti, "
            "2 tanesi canlı bir YZ sağlayıcısı gerektirdiği için atlandı. Kod kapsamı bu "
            "sürüm için ÖLÇÜLMEDİ, bu yüzden burada bir yüzde verilmiyor. Ekranlar "
            "çalışan sunucuya gerçek HTTP isteğiyle doğrulandı.",
        },
        {
            "kind": "cards",
            "eyebrow": "KAPSAM",
            "title": "Tek uygulamada altı alan",
            "cards": [
                ("İnsanlar", "Müşteriler, öğrenciler, eğitmenler, CRM. Sörf seviyesi, "
                 "sağlık notu, sertifika geçerliliği, komisyon."),
                ("Operasyon", "10 ders tipi, rezervasyon takvimi, bekleme listesi, "
                 "sörf kampları, günlük program."),
                ("Ekipman", "QR etiketli envanter, saatlik/günlük/haftalık kiralama, "
                 "hasar ve bakım geçmişi."),
                ("Deniz ve güvenlik", "Canlı dalga/rüzgâr/gelgit, seviyeye göre Surf Score, "
                 "olay kaydı, cankurtaran çizelgesi."),
                ("Para", "Ödeme, iade, fatura, gider, komisyon, ders paketi ve "
                 "satış noktası (POS)."),
                ("Karar desteği", "İstatistik motoru, panolar, PDF/Excel/CSV rapor, "
                 "yedekleme ve denetim kaydı."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "ARAYÜZ",
            "title": "Ekranda nasıl görünür: ana pano",
            "shots": [
                ("assets/screenshots/tr/dashboard.png",
                 "Günün dersleri, ciro, bekleyen tahsilat, ekipman uyarıları ve deniz "
                 "durumu — tek bakışta."),
            ],
            "note": "Bu sunumdaki bütün ekran görüntüleri 18.08.2026'da çalışan "
            "uygulamadan, sentetik demo verisiyle alındı — hiçbiri taslak ya da "
            "tasarım görseli değildir.",
        },
        {
            "kind": "split",
            "eyebrow": "YETKİ",
            "title": "Kim neyi görebilir?",
            "lead": "15 rol ve 203 yetki tek bir tablodan yönetilir. Menü, ekranlar, "
            "REST API ve yapay zekâ asistanı aynı tabloyu okur — bu yüzden bir ekran "
            "hiçbir zaman API'nin reddedeceği bir işlemi teklif edemez.",
            "rows": [
                ("Süper Yönetici", "Her şey, yedek geri yükleme dâhil"),
                ("Yönetici", "Operasyon + finans + personel"),
                ("Baş eğitmen", "Dersler, eğitmenler, güvenlik onayı"),
                ("Eğitmen", "Kendi dersleri ve öğrencileri"),
                ("Cankurtaran", "Güvenlik modülü, olay bildirimi"),
                ("Resepsiyon", "Müşteri, rezervasyon, tahsilat"),
                ("Kiralama personeli", "Ekipman çıkış/iade, depozito"),
                ("Finans", "Ödeme, iade, rapor, komisyon"),
                ("Müşteri / Öğrenci", "Yalnızca kendi kayıtları — satır düzeyinde"),
            ],
            "note": "Tahsilat (finance.view) ile ciroyu okumak (finance.revenue) ayrı yetkilerdir; kiralama personelinde ikincisi yoktur, sorgu hiç çalıştırılmaz. apps/accounts/tests/test_object_scoping.py bunu test eder.",
        },
        {
            "kind": "bullets",
            "eyebrow": "REZERVASYON",
            "title": "Çakışmayı sistem yakalar, insan değil",
            "lead": "Rezervasyon yazılırken kurallar canlı çalışır. Her biri ayrı ayrı "
            "test edilmiştir.",
            "items": [
                ("Boş kontenjan", "Ders dolu mu, kaç kişilik yer kaldı."),
                ("Öğrencinin kendi çakışması", "Aynı saatte başka bir dersi var mı."),
                ("Seviye uyumu", "Ders tipi bu seviyeyi kabul ediyor mu."),
                ("Eğitmen müsaitliği", "Haftalık çalışma saati, izin, başka ders."),
                ("Eğitmen–öğrenci oranı", "18 yaş altı grupta daha katı sınır uygulanır."),
                ("Güvenlik kısıtı", "Öğrenciye tanımlı sağlık/beceri kısıtı var mı."),
            ],
            "note": "Çakışma bulunursa neyin yanlış olduğu açıkça yazılır — "
            "kural aşılamaz, çünkü aşıldığında birileri yaralanır.",
        },
        {
            "kind": "screens",
            "eyebrow": "ARAYÜZ — REZERVASYON",
            "title": "Takvim ve öğrenci kartoteği",
            "shots": [
                ("assets/screenshots/tr/bookings.png",
                 "Rezervasyon takvimi: ders türüne göre renkli, satılan koltuk sayısı üstünde."),
                ("assets/screenshots/tr/students.png",
                 "Öğrenci listesi: seviye, yüzme yeterliliği ve son ders bir arada."),
            ],
        },
        {
            "kind": "cards",
            "eyebrow": "EKİPMAN",
            "title": "Board kimde, ne zaman dönecek",
            "cards": [
                ("QR etiket", "Her ekipmanın benzersiz kodu ve QR'ı var. "
                 "Tezgâhta okutulur, satır otomatik eklenir."),
                ("Doğru board önerisi", "Kilo ve seviyeye göre litre hacmi; "
                 "su sıcaklığına göre wetsuit kalınlığı önerilir."),
                ("Şeffaf fiyat", "Saatlik/günlük/haftalık. Bir haftadan uzun kiralamada "
                 "haftalık tarife daha ucuzsa otomatik uygulanır."),
                ("Gecikme ve hasar", "Gecikme ücreti kira bedelinin 3 katıyla sınırlıdır; "
                 "hasar bildirilirse bakım kaydı otomatik açılır."),
                ("Bakım geçmişi", "Ding, çatlak, fin, leash, wetsuit yırtığı — "
                 "her müdahale maliyetiyle birlikte kayıtlı."),
                ("Öngörülen bakım", "Servis geçmişinden hesaplanan risk sıralaması. "
                 "İstatistik — yapay zekâ tahmini değil."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "ARAYÜZ — EKİPMAN",
            "title": "Envanter ve kiralama tezgâhı",
            "shots": [
                ("assets/screenshots/tr/equipment.png",
                 "QR kodlu envanter: durum, kondisyon ve günlük tarife kartın üstünde."),
                ("assets/screenshots/tr/rentals.png",
                 "Kiralama tezgâhı: tek okutmayla iade, depozito ve gecikme takibi."),
            ],
        },
        {
            "kind": "split",
            "eyebrow": "DENİZ KOŞULLARI",
            "title": "Surf Score: 0–100, ve neden o puan olduğu",
            "lead": "Dalga, rüzgâr, periyot, gelgit, su sıcaklığı ve hava; her seviye için "
            "ayrı ayrı puanlanır. Puan HESAPLANIR — bir dil modeline sorulmaz — ve her "
            "bileşeni ayrı ayrı gösterilir.",
            "rows": [
                ("Dalga yüksekliği uyumu", "%35"),
                ("Rüzgâr kalitesi (offshore/onshore)", "%25"),
                ("Swell periyodu", "%15"),
                ("Gelgit uyumu", "%10"),
                ("Hava ve yağış", "%10"),
                ("Su sıcaklığı konforu", "%5"),
            ],
            "note": "Sert güvenlik sınırı: seviyenin dalga ya da rüzgâr limiti aşılırsa "
            "puan 25'te kapatılır ve «uygun değil» işaretlenir. Dalga verisi yoksa puan "
            "hiç üretilmez — bilinmeyen deniz güvenli deniz değildir.",
        },
        {
            "kind": "screens",
            "eyebrow": "ARAYÜZ — DENİZ",
            "title": "Canlı deniz durumu, hesaplanan puan",
            "shots": [
                ("assets/screenshots/tr/surf_conditions.png",
                 "Open-Meteo'dan canlı okuma: dalga, rüzgâr, gelgit ve su sıcaklığı — "
                 "her seviye için hesaplanan Surf Score, gerekçesiyle birlikte."),
            ],
        },
        {
            "kind": "quote",
            "eyebrow": "GÜVENLİK İLKESİ",
            "quote": "Yapay zekâ hiçbir güvenlik kararının son mercii değildir.",
            "body": "Sistem koşulları, eşikleri ve geçmişi özetler. Dersin yapılıp "
            "yapılmayacağına, bir öğrencinin suya girip giremeyeceğine ve bir ekipmanın "
            "kullanıma uygun olup olmadığına yetkili personel karar verir.\n\n"
            "Veri modelinde de böyledir: yapay zekânın önerdiği bir hava uyarısı, "
            "adı kayıtlı bir personel onaylayana kadar «etkin uyarı» sayılmaz.",
        },
        {
            "kind": "cards",
            "eyebrow": "PARA",
            "title": "Kuruşu kuruşuna, geçmişi bozmadan",
            "cards": [
                ("Decimal aritmetik", "Para hiçbir yerde ondalıklı kayan sayı değildir. "
                 "12 hane, 2 ondalık — toplama ve karşılaştırma tam."),
                ("İade geçmişi bozmaz", "İade, karşıt işaretli yeni bir kayıt oluşturur; "
                 "orijinal ödeme asla değiştirilmez."),
                ("Atomik işlemler", "Rezervasyon ile derse katılım kaydı ya birlikte "
                 "oluşur ya hiç oluşmaz."),
                ("Ders paketleri", "İndirimli çoklu ders satılır, kullandıkça düşer, "
                 "kalan hak müşteri kartında görünür."),
                ("Satış noktası", "Barkodla ürün ekleme, stok defteri, fiş yazdırma. "
                 "İptal edilen satış stoğu geri yükler, kaydı silmez."),
                ("Denetim kaydı", "Her ödeme, iade ve yetki değişikliği kim/ne/ne zaman "
                 "bilgisiyle değiştirilemez şekilde saklanır."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "ARAYÜZ — PARA",
            "title": "Finans panosu ve analitik",
            "shots": [
                ("assets/screenshots/tr/finance.png",
                 "Net ciro, gider, alacak ve vadesi geçen faturalar — son 30 gün."),
                ("assets/screenshots/tr/analytics.png",
                 "Doluluk, iptal oranı, tekrar gelen müşteri ve ciro eğilimi."),
            ],
        },
        {
            "kind": "metrics",
            "eyebrow": "YAPAY ZEKÂ",
            "title": "Ölçülmüş yapay zekâ, iddia edilmiş değil",
            "lead": "Model seçimleri belgeden değil, bu makineden yapılan gerçek "
            "çağrılardan çıkarıldı.",
            "metrics": [
                ("857 ms", "Nemotron 3 Super\n(asistan)"),
                ("535 ms", "Nemotron 3.5 Lightning\n(hızlı yönlendirme)"),
                ("389 ms", "Riva Translate\n(TR ↔ EN)"),
                ("311 ms", "Embedding 1B\n(2048 boyut)"),
            ],
            "note": "Kritik bulgu: Nemotron modelleri `thinking: false` gönderilmeden "
            "90 saniyeyi aşıyor ve düşünce zincirini yanıta sızdırıyordu. Bayrakla "
            "535 ms ve temiz yanıt. Ayrıntı: docs/research/VERIFIED_API_PROBES.md",
        },
        {
            "kind": "split",
            "eyebrow": "YAPAY ZEKÂ MİMARİSİ",
            "title": "Yerel önce, bulut isteğe bağlı",
            "lead": "Sağlayıcılar tek arayüzün arkasındadır. Çağrı yapan taraf model adı "
            "değil ROL ister. Bir sağlayıcı çökerse bu bir hata değil, bir değerdir — "
            "uygulama internetsiz de tam çalışır.",
            "rows": [
                ("LM Studio (yerel)", "Ücretsiz, çevrimdışı, veri makineden çıkmaz"),
                ("NVIDIA NIM", "Güçlü akıl yürütme, görsel, çeviri, embedding"),
                ("Anthropic Claude", "İsteğe bağlı"),
                ("OpenAI uyumlu", "Ollama, vLLM ve benzerleri"),
                ("Yalnızca yerel", "Müşteri verisi bilgisayardan çıkmasın"),
                ("Otomatik", "Kolay soru yerelde, zor soru bulutta"),
            ],
            "note": "Yerel embedding modeli sayesinde bilgi tabanı araması da "
            "internetsiz çalışır.",
        },
        {
            "kind": "bullets",
            "eyebrow": "YAPAY ZEKÂ GÜVENİLİRLİĞİ",
            "title": "Asistan veri uydurmaz — uyduramaz",
            "lead": "«Veri uydurmasın» bir temenni değil, bir mekanizmadır. Model bir "
            "rakamı ancak gerçek bir sorgu çalıştıran araçtan alabilir.",
            "items": [
                ("13 veri tabanı aracı", "Ders, rezervasyon, ciro, ekipman, bakım, "
                 "kiralama, eğitmen, müşteri, deniz durumu, güvenlik."),
                ("Yetki denetimi araçta", "Araç, soruyu SORAN kullanıcının yetkisiyle "
                 "çalışır. Asistan bir yetki yükseltme yolu değildir."),
                ("«Veri yok» ayrı bir cevap", "Sonuç boşsa model bunu bildirir; "
                 "makul görünen bir sayı uydurmaz."),
                ("Kaynak gösterimi", "Bilgi tabanından gelen yanıtlar alıntıladıkları "
                 "belgeyle birlikte gösterilir."),
                ("Şeffaf çalışma", "Her yanıtın altında hangi sağlayıcı, hangi model, "
                 "kaç ms, hangi sorgular çalıştı yazar."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "ARAYÜZ — YAPAY ZEKÂ",
            "title": "Asistan iş başında",
            "shots": [
                ("assets/screenshots/tr/ai.png",
                 "AI Asistan okulun kendi verisiyle konuşur: 13 veri tabanı aracı, hazır "
                 "örnek sorular ve her yanıtın altında sağlayıcı/model/süre dökümü."),
            ],
        },
        {
            "kind": "cards",
            "eyebrow": "GELİŞTİRME TERMİNALİ",
            "title": "Yapay zekâ önerir, kural karar verir, insan onaylar",
            "cards": [
                ("Kabuk yok", "Komutlar doğrulanmış argüman dizisi olarak çalışır. "
                 "Zincirleme ve yönlendirme filtrelenmez — imkânsızdır."),
                ("İzinli komut listesi", "git status evet, git push hayır. "
                 "manage.py test evet, manage.py flush asla."),
                ("Çalışma alanı hapsi", "UNC, sürücü göreli yol, 8.3 kısa ad, "
                 "CON/NUL aygıt adları ve NTFS akışları kapalı."),
                ("Onay kapısı", "Düzenlenen komut sıfırdan yeniden doğrulanır — "
                 "onay, kuralı atlama yetkisi değildir."),
                ("Temiz ortam", "Alt sürece hiçbir kimlik bilgisi geçmez; "
                 "zaman aşımında süreç ağacı komple sonlandırılır."),
                ("Tam kayıt", "Reddedilen komutlar dâhil her karar denetim kaydına yazılır."),
            ],
        },
        {
            "kind": "split",
            "eyebrow": "SÜREKLİLİK",
            "title": "Yedekleme: umut değil, plan",
            "lead": "Yedek almak kolay kısımdır; asıl mesele yanlış yedeği doğru verinin "
            "üzerine yazmamaktır. Geri yükleme bilerek zahmetli tasarlandı.",
            "rows": [
                ("1. Doğrula", "SHA-256 sağlama toplamı ve bütünlük kontrolü"),
                ("2. Kodu yaz", "Operatör yedek kodunu elle yazmadan devam etmez"),
                ("3. Yetki", "backups.restore yetkisi ayrıca aranır"),
                ("4. Güvenlik yedeği", "Mevcut durumun yedeği ÖNCE alınır"),
                ("5. Geri yükle", "Ve her adım denetim kaydına yazılır"),
                ("6. Hata olursa", "Otomatik olarak eski hâline döndürülür"),
            ],
            "note": "SQLite, dosya kopyalanarak değil sqlite3 yedek API'siyle alınır — "
            "çalışan bir veri tabanını kopyalamak bozuk yedek üretir.",
        },
        {
            "kind": "screens",
            "eyebrow": "ARAYÜZ — İŞLETİM",
            "title": "Yedekleme ve denetim kaydı",
            "shots": [
                ("assets/screenshots/tr/backups.png",
                 "Doğrulanmış yedekler, saklama politikası ve tek tıkla yeni yedek."),
                ("assets/screenshots/tr/audit.png",
                 "Denetim kaydı: kim, neyi, ne zaman — satırlar eklenir, değiştirilemez."),
            ],
        },
        {
            "kind": "cards",
            "eyebrow": "KULLANIM",
            "title": "Öğrenmesi kolay, iki dilli",
            "cards": [
                ("Türkçe ve İngilizce", "Menüler, bildirimler, doğrulama mesajları ve "
                 "Yardım ve Eğitim Merkezi içeriği iki dillidir. Arayüz metinleri şu an İngilizcedir: 337 şablon {% trans %} kullanır ancak derlenmiş Türkçe katalog henüz paketlenmemiştir. Türkçe biçimlendirme (tarih, sayı, para) uygulanır."),
                ("Yardım Merkezi", "16 kategori, 22 makale — her ekran için başvuru, "
                 "iki dilde."),
                ("Eğitim Merkezi", "10 interaktif kurs, 71 adım. İlk öğrenciden ilk "
                 "yedeğe kadar gerçek işler adım adım."),
                ("Kurulum sihirbazı", "İlk açılışta okul adı, para birimi, dil ve "
                 "sörf noktası sorulur; sonuçta gerçek kayıtlar oluşturulur."),
                ("Rol farkındalığı", "Eğitmenin panosu kendi dersleriyle başlar, "
                 "müşteri yalnızca kendi rezervasyonunu görür."),
                ("Koyu / açık tema", "Tezgâhta gündüz, ofiste gece — göz yormadan."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "ARAYÜZ — GİRİŞ",
            "title": "İki dilli giriş, rol bazlı ekranlar",
            "shots": [
                ("assets/screenshots/tr/login.png",
                 "Giriş ekranı: TR/EN dil seçimi; giriş sonrası menü ve panolar "
                 "kullanıcının rolüne göre şekillenir."),
            ],
        },
        {
            "kind": "split",
            "eyebrow": "TEKNOLOJİ",
            "title": "Sade, dayanıklı, taşınabilir",
            "lead": "Teknoloji seçimleri moda değil, bu işletmenin gerçek koşulları "
            "gözetilerek yapıldı: tek bilgisayar, zayıf internet, teknik personel yok.",
            "rows": [
                ("Python 3.11 + Django 5.2 LTS", "2028'e kadar destekli"),
                ("Django REST Framework", "75 kaynak, OpenAPI dokümanı"),
                ("HTMX + Alpine + Tailwind", "Ayrı derleme yok, CDN yok"),
                ("SQLite → PostgreSQL", "Tek ortam değişkeniyle geçiş"),
                ("Redis / Celery", "Varsa kullanılır, yoksa uygulama aynen çalışır"),
                ("Çevrimdışı çalışır", "Bütün varlıklar yerelde gömülü"),
            ],
            "note": "Bağımlılık taramasında bilinen güvenlik açığı yok; "
            "statik güvenlik analizinde yüksek önem dereceli bulgu yok.",
        },
        {
            "kind": "metrics",
            "eyebrow": "KALİTE",
            "title": "Ne iddia ediliyorsa ölçüldü",
            "lead": "Aşağıdakilerin hepsi bu makinede çalıştırıldı ve sonucu kaydedildi.",
            "metrics": [
                ("2.128", "geçen test\n0 başarısız"),
                ("90", "erişim denetimi\ntesti"),
                ("39/39", "ekran\naçılıyor"),
                ("20", "uçtan uca\nkabul adımı"),
            ],
            "note": "Kabul senaryosu gerçek servis katmanından geçer: eğitmen ve öğrenci "
            "oluşturma, ders, rezervasyon, tahsilat, dersi tamamlama, kiralama ve iade, "
            "rapor üretimi, yedek alma, yerel ve bulut yapay zekâ, denetim kaydı.",
        },
        {
            "kind": "bullets",
            "eyebrow": "DÜRÜSTLÜK",
            "title": "Bilinen sınırlar",
            "lead": "Bir tanıtım yalnızca güçlü yanları anlatırsa, zayıf yanları "
            "kullanıcı canlı ortamda keşfeder. Bilinenler burada:",
            "items": [
                ("Geliştirme SQLite üzerinde", "Üretim hedefi PostgreSQL; geçiş tek "
                 "ortam değişkeni ama bu makinede PostgreSQL kurulu değildi."),
                ("Tarayıcı otomasyon testi yok", "Ekranlar gerçek HTTP ile doğrulandı, "
                 "tıklama senaryoları elle test edildi."),
                ("Open-Meteo ücretsiz katmanı ticari değil", "Veri lisansı uygun, "
                 "ancak ücretsiz servis ticari kullanıma kapalı; met.no yedeği hazır."),
                ("Yük testi yapılmadı", "Hedef tek okul, tek makine."),
                ("Yapay zekâ sağlayıcıları elle test ediliyor", "Otomatik testler "
                 "ağa hiç çıkmaz — bu bilinçli bir karar."),
            ],
        },
        {
            "kind": "closing",
            "eyebrow": "ÖZET",
            "title": "Bir sörf okulunun günü,\ntek uygulamada",
            "points": [
                "Çakışmayı ve güvenlik kuralını sistem yakalar",
                "Para kuruşu kuruşuna, geçmiş değiştirilemez",
                "Deniz puanı hesaplanır, yapay zekâya sorulmaz",
                "Yapay zekâ yalnızca gerçek veriyi konuşur",
                "İnternet olmadan da tam çalışır",
            ],
            "meta": f"Sürüm {APP_VERSION}  ·  {REPO_URL}\n"
            f"Windows 10/11 (x64) + macOS (Apple Silicon)  ·  İndirme: {REPO_URL}/releases (v{APP_VERSION})\n"
            "macOS paketi Apple Silicon (arm64) içindir ve notarize edilmemiştir — ilk açılışta sağ tık → Aç.",
        },
    ]


def _en() -> list[dict]:
    """English deck."""
    return [
        {
            "kind": "title",
            "eyebrow": "SURF SCHOOL MANAGEMENT SOFTWARE",
            "title": "Smart Surf School\nManagement System",
            "subtitle": "Everything a surf school runs on, in one application:\n"
            "lessons, bookings, equipment, surf conditions, safety, finance and AI.",
            "meta": f"Version {APP_VERSION}  ·  Django 5.2 LTS  ·  Turkish / English",
            "highlights": [
                ("28", "modules, one database"),
                ("2,128", "tests passing, 0 failing"),
                ("39/39", "screens working"),
            ],
        },
        {
            "kind": "bullets",
            "eyebrow": "THE PROBLEM",
            "title": "How many places does a surf school run in?",
            "lead": "A typical day: bookings arrive on WhatsApp, student details live in "
            "a notebook, nobody is sure who has which board, takings are on paper receipts, "
            "and the forecast is in another app entirely.",
            "items": [
                ("Two lessons, one slot", "Nobody sees the clash until two coaches wait "
                 "for the same student."),
                ("Missing equipment", "Who has the board? When is it due? What about the deposit?"),
                ("Safety details too late", "The instructor learns about the asthma after "
                 "the lesson, not before."),
                ("Invisible money", "What the month actually earned is a guess."),
                ("Knowledge that walks out", "When someone leaves, what they knew leaves too."),
            ],
            "note": "This system puts all five in one database.",
        },
        {
            "kind": "metrics",
            "eyebrow": "MEASURED",
            "title": "The system in numbers",
            "lead": "Every figure below was read from the project — none is an estimate.",
            "metrics": [
                ("28", "modules"),
                ("86", "data models"),
                ("75", "REST resources"),
                ("15", "roles"),
                ("203", "capabilities"),
                ("2,128", "tests passing"),
                ("39/39", "screens working"),
                ("20", "acceptance steps"),
            ],
            "note": "Tests via `pytest`: 2,130 collected, 2,128 passing, 2 skipped "
            "because they need a live AI provider. Code coverage was NOT measured for "
            "this release, so no percentage is quoted. Screens verified with real HTTP "
            "requests against a running server.",
        },
        {
            "kind": "cards",
            "eyebrow": "SCOPE",
            "title": "Six areas, one application",
            "cards": [
                ("People", "Customers, students, instructors, CRM. Surf level, medical "
                 "notes, certification expiry, commission."),
                ("Operations", "10 lesson types, booking calendar, waitlist, surf camps, "
                 "the day's schedule."),
                ("Equipment", "QR-labelled inventory, hourly/daily/weekly rentals, "
                 "damage and service history."),
                ("Surf & safety", "Live wave, wind and tide, a Surf Score per level, "
                 "incident reports, lifeguard roster."),
                ("Money", "Payments, refunds, invoices, expenses, commission, lesson "
                 "packages and point of sale."),
                ("Decision support", "Statistics engine, dashboards, PDF/Excel/CSV "
                 "reports, backups and an audit trail."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "THE INTERFACE",
            "title": "What it looks like: the dashboard",
            "shots": [
                ("assets/screenshots/en/dashboard.png",
                 "Today's lessons, revenue, pending balances, equipment warnings and "
                 "sea conditions — at a glance."),
            ],
            "note": "Every screenshot in this deck was taken from the running "
            "application on 2026-08-18, showing synthetic demo data — none of them "
            "is a mock-up or a design rendering.",
        },
        {
            "kind": "split",
            "eyebrow": "ACCESS",
            "title": "Who can see what?",
            "lead": "15 roles and 203 capabilities come from one table. The menu, the "
            "screens, the REST API and the AI assistant all read it — which is why a "
            "screen can never offer an action the API would refuse.",
            "rows": [
                ("Super Admin", "Everything, including backup restore"),
                ("Manager", "Operations + finance + staff"),
                ("Head Instructor", "Lessons, instructors, safety sign-off"),
                ("Surf Instructor", "Their own lessons and students"),
                ("Lifeguard", "Safety module, incident reporting"),
                ("Reception", "Customers, bookings, taking payment"),
                ("Rental Staff", "Check-out and check-in, deposits"),
                ("Finance", "Payments, refunds, reports, commission"),
                ("Customer / Student", "Only their own records — enforced per row"),
            ],
            "note": "Taking money (finance.view) and reading the takings (finance.revenue) are separate privileges; rental staff hold the first and not the second, so the aggregate query is never run. Asserted in apps/accounts/tests/test_object_scoping.py.",
        },
        {
            "kind": "bullets",
            "eyebrow": "BOOKINGS",
            "title": "The system catches the clash, not the person",
            "lead": "Rules run live while a booking is typed. Each one is tested "
            "separately.",
            "items": [
                ("Free seats", "Is the lesson full, how many places are left."),
                ("The student's own clash", "Are they already booked at that time."),
                ("Level fit", "Does the lesson type accept this level."),
                ("Instructor availability", "Weekly hours, time off, another lesson."),
                ("Instructor-to-student ratio", "A stricter limit applies when a minor "
                 "is in the group."),
                ("Safety restrictions", "Any medical or skill restriction on the student."),
            ],
            "note": "When something clashes, the system says exactly what is wrong — "
            "and the rule cannot be worked around, because that is when people get hurt.",
        },
        {
            "kind": "screens",
            "eyebrow": "INTERFACE — BOOKINGS",
            "title": "The calendar and the student file",
            "shots": [
                ("assets/screenshots/en/bookings.png",
                 "Booking calendar: coloured by lesson type, seats sold on every entry."),
                ("assets/screenshots/en/students.png",
                 "Student list: level, water competence and last lesson side by side."),
            ],
        },
        {
            "kind": "cards",
            "eyebrow": "EQUIPMENT",
            "title": "Who has the board, and when it is due",
            "cards": [
                ("QR labels", "Every item has a unique code and a QR label. Scan it at "
                 "the counter and the line is added."),
                ("The right board", "Volume in litres from weight and level; wetsuit "
                 "thickness from the water temperature."),
                ("Honest pricing", "Hourly, daily or weekly — and a hire of a week or "
                 "more automatically takes the weekly rate when it is cheaper."),
                ("Late and damaged", "Late fees are capped at three times the hire; "
                 "reported damage opens a maintenance record automatically."),
                ("Service history", "Ding, crack, fin, leash, wetsuit tear — every "
                 "repair recorded with its cost."),
                ("Predicted maintenance", "A risk ranking computed from your own service "
                 "history. Statistics — not an AI guess."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "INTERFACE — EQUIPMENT",
            "title": "Inventory and the hire counter",
            "shots": [
                ("assets/screenshots/en/equipment.png",
                 "QR-tagged inventory: status, condition and daily rate on the card."),
                ("assets/screenshots/en/rentals.png",
                 "The hire counter: one scan to check gear back in, deposits and overdue tracking."),
            ],
        },
        {
            "kind": "split",
            "eyebrow": "SURF CONDITIONS",
            "title": "Surf Score: 0–100, and why it is that number",
            "lead": "Wave, wind, period, tide, water temperature and weather, scored "
            "separately for each surf level. The score is COMPUTED — never asked of a "
            "language model — and every component is shown.",
            "rows": [
                ("Wave height fit", "35%"),
                ("Wind quality (offshore/onshore)", "25%"),
                ("Swell period", "15%"),
                ("Tide match", "10%"),
                ("Weather and precipitation", "10%"),
                ("Water temperature comfort", "5%"),
            ],
            "note": "Hard safety gate: above the level's wave or wind limit the score is "
            "capped at 25 and marked unsafe. With no wave data no score is produced at "
            "all — an unknown ocean is not a safe ocean.",
        },
        {
            "kind": "screens",
            "eyebrow": "INTERFACE — THE SEA",
            "title": "Live sea state, computed score",
            "shots": [
                ("assets/screenshots/en/surf_conditions.png",
                 "A live Open-Meteo reading: wave, wind, tide and water temperature — "
                 "and the Surf Score computed per level, with its reasoning shown."),
            ],
        },
        {
            "kind": "quote",
            "eyebrow": "SAFETY PRINCIPLE",
            "quote": "The AI is never the final authority on a safety decision.",
            "body": "The system summarises conditions, thresholds and history. Whether a "
            "lesson runs, whether a student enters the water, and whether a piece of "
            "equipment is fit for use is decided by a qualified member of staff.\n\n"
            "The data model works the same way: a weather warning suggested by the AI is "
            "not an active warning until a named person acknowledges it.",
        },
        {
            "kind": "cards",
            "eyebrow": "MONEY",
            "title": "Exact to the cent, with the history intact",
            "cards": [
                ("Decimal arithmetic", "Money is never a floating-point number anywhere. "
                 "12 digits, 2 decimals — sums and comparisons are exact."),
                ("Refunds keep the record", "A refund creates a matching negative entry; "
                 "the original payment is never altered."),
                ("Atomic operations", "A booking and its lesson attendance are created "
                 "together or not at all."),
                ("Lesson packages", "Sell a discounted multi-lesson package; it draws "
                 "down as it is used and the balance shows on the customer's page."),
                ("Point of sale", "Barcode entry, a stock ledger, printed receipts. "
                 "Voiding a sale returns the stock rather than deleting the record."),
                ("Audit trail", "Every payment, refund and permission change is stored "
                 "immutably with who, what and when."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "INTERFACE — MONEY",
            "title": "The finance board and analytics",
            "shots": [
                ("assets/screenshots/en/finance.png",
                 "Net revenue, expenses, receivables and overdue invoices — last 30 days."),
                ("assets/screenshots/en/analytics.png",
                 "Occupancy, cancellation rate, repeat customers and the revenue trend."),
            ],
        },
        {
            "kind": "metrics",
            "eyebrow": "ARTIFICIAL INTELLIGENCE",
            "title": "Measured AI, not claimed AI",
            "lead": "Model choices came from real calls made on this machine, not from "
            "vendor documentation.",
            "metrics": [
                ("857 ms", "Nemotron 3 Super\n(assistant)"),
                ("535 ms", "Nemotron 3.5 Lightning\n(fast routing)"),
                ("389 ms", "Riva Translate\n(TR ↔ EN)"),
                ("311 ms", "Embedding 1B\n(2048 dimensions)"),
            ],
            "note": "A finding that changed the design: without `thinking: false` the "
            "Nemotron models exceeded 90 seconds and leaked chain-of-thought into the "
            "answer. With the flag: 535 ms and a clean reply. "
            "See docs/research/VERIFIED_API_PROBES.md",
        },
        {
            "kind": "split",
            "eyebrow": "AI ARCHITECTURE",
            "title": "Local first, cloud optional",
            "lead": "Providers sit behind one interface. Call sites ask for a ROLE, never "
            "a model name. A failed provider is a value rather than an exception — so the "
            "application stays fully usable with no internet at all.",
            "rows": [
                ("LM Studio (local)", "Free, offline, data never leaves the machine"),
                ("NVIDIA NIM", "Strong reasoning, vision, translation, embeddings"),
                ("Anthropic Claude", "Optional"),
                ("OpenAI-compatible", "Ollama, vLLM and others"),
                ("Local only", "For when customer data must not leave the building"),
                ("Automatic", "Cheap questions stay local, hard ones go to the cloud"),
            ],
            "note": "A local embedding model means knowledge-base search works offline too.",
        },
        {
            "kind": "bullets",
            "eyebrow": "AI TRUSTWORTHINESS",
            "title": "The assistant cannot invent data",
            "lead": "«Do not make things up» is a mechanism here, not a hope. The model "
            "can only obtain a figure from a tool that runs a real query.",
            "items": [
                ("13 database tools", "Lessons, bookings, revenue, equipment, maintenance, "
                 "rentals, instructors, customers, surf conditions, safety."),
                ("Permissions checked in the tool", "A tool runs with the capabilities of "
                 "the person ASKING. The assistant is not a privilege-escalation path."),
                ("«No data» is a real answer", "When a query returns nothing the model "
                 "says so rather than filling the gap with a plausible number."),
                ("Citations", "Answers drawn from the knowledge base show the document "
                 "they came from."),
                ("Visible working", "Under every answer: which provider, which model, how "
                 "many milliseconds, and which lookups ran."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "INTERFACE — AI",
            "title": "The assistant at work",
            "shots": [
                ("assets/screenshots/en/ai.png",
                 "The AI assistant talks to the school's own data: 13 database tools, "
                 "ready-made questions, and a provider/model/latency line under every answer."),
            ],
        },
        {
            "kind": "cards",
            "eyebrow": "DEVELOPMENT TERMINAL",
            "title": "The AI proposes, policy decides, a human approves",
            "cards": [
                ("No shell", "Commands run as a validated argument vector. Chaining and "
                 "redirection are not filtered — they are impossible."),
                ("Allowlist", "git status yes, git push no. manage.py test yes, "
                 "manage.py flush never."),
                ("Workspace jail", "UNC paths, drive-relative paths, 8.3 short names, "
                 "CON/NUL device names and NTFS streams are all closed off."),
                ("Approval gate", "An edited command is re-validated from scratch — "
                 "approval is not permission to bypass the policy."),
                ("Clean environment", "No credential reaches the child process; on "
                 "timeout the whole process tree is killed."),
                ("Full record", "Every decision is audited, including the refusals."),
            ],
        },
        {
            "kind": "split",
            "eyebrow": "CONTINUITY",
            "title": "Backups: a plan, not a hope",
            "lead": "Taking a backup is the easy part; the real risk is restoring the "
            "wrong one over good data. Restore is deliberately made difficult.",
            "rows": [
                ("1. Verify", "SHA-256 checksum and an integrity check"),
                ("2. Type the code", "The operator types the backup code to continue"),
                ("3. Permission", "backups.restore is required separately"),
                ("4. Safety backup", "The current state is backed up FIRST"),
                ("5. Restore", "With every step written to the audit log"),
                ("6. On failure", "Everything is put back automatically"),
            ],
            "note": "SQLite is captured through the sqlite3 backup API rather than by "
            "copying the file — copying a live database produces a corrupt backup.",
        },
        {
            "kind": "screens",
            "eyebrow": "INTERFACE — OPERATIONS",
            "title": "Backups and the audit log",
            "shots": [
                ("assets/screenshots/en/backups.png",
                 "Verified backups, the retention policy and a one-click new copy."),
                ("assets/screenshots/en/audit.png",
                 "The audit log: who, what, when — rows are appended, never edited."),
            ],
        },
        {
            "kind": "cards",
            "eyebrow": "IN USE",
            "title": "Easy to learn, in two languages",
            "cards": [
                ("Turkish and English", "Menus, notifications, validation messages and "
                 "Help Center and Training Center content is genuinely bilingual. The interface chrome is English today: 337 templates use {% trans %} but no compiled Turkish catalogue ships yet. Turkish locale formatting -- dates, numbers, currency -- does apply."),
                ("Help Center", "16 categories, 22 articles — a reference for every "
                 "screen, in both languages."),
                ("Training Center", "10 interactive courses, 71 steps. Real tasks, from "
                 "your first student to your first backup."),
                ("Setup wizard", "First run asks for the school name, currency, language "
                 "and surf spot — and then creates the real records."),
                ("Role aware", "An instructor's dashboard leads with their own lessons; "
                 "a customer sees only their own bookings."),
                ("Dark and light", "Bright at the counter, dark in the office."),
            ],
        },
        {
            "kind": "screens",
            "eyebrow": "INTERFACE — SIGN-IN",
            "title": "Bilingual sign-in, role-aware screens",
            "shots": [
                ("assets/screenshots/en/login.png",
                 "The sign-in screen: TR/EN language switch; after sign-in the menu "
                 "and dashboards shape themselves to the user's role."),
            ],
        },
        {
            "kind": "split",
            "eyebrow": "TECHNOLOGY",
            "title": "Plain, durable, portable",
            "lead": "The technology was chosen for this business's real conditions — one "
            "computer, weak internet, no IT staff — rather than for fashion.",
            "rows": [
                ("Python 3.11 + Django 5.2 LTS", "Supported until 2028"),
                ("Django REST Framework", "75 resources, OpenAPI documentation"),
                ("HTMX + Alpine + Tailwind", "No separate build, no CDN"),
                ("SQLite → PostgreSQL", "One environment variable"),
                ("Redis / Celery", "Used when present; the app is identical without"),
                ("Works offline", "Every asset is vendored locally"),
            ],
            "note": "The dependency audit reports no known vulnerabilities, and static "
            "security analysis reports no high-severity findings.",
        },
        {
            "kind": "metrics",
            "eyebrow": "QUALITY",
            "title": "Whatever is claimed was measured",
            "lead": "All of the following were run on this machine and the results recorded.",
            "metrics": [
                ("2,128", "tests passing\n0 failing"),
                ("90", "access-control\nassertions"),
                ("39/39", "screens\nrendering"),
                ("20", "end-to-end\nacceptance steps"),
            ],
            "note": "The acceptance scenario runs through the real service layer: create "
            "an instructor and a student, a lesson, a booking, take payment, complete the "
            "lesson, hire equipment and take it back, generate reports, make a backup, "
            "ask local and cloud AI, and check the audit log.",
        },
        {
            "kind": "bullets",
            "eyebrow": "HONESTY",
            "title": "Known limits",
            "lead": "If an introduction only lists strengths, the buyer discovers the "
            "weaknesses in production. Here are the known ones:",
            "items": [
                ("Development runs on SQLite", "PostgreSQL is the production target and "
                 "the switch is one environment variable, but PostgreSQL was not "
                 "installed on this machine."),
                ("No browser automation suite", "Screens are verified over real HTTP; "
                 "click-through flows were tested by hand."),
                ("Open-Meteo's free tier is non-commercial", "The data licence is fine, "
                 "the free hosted service is not; a met.no fallback is built in."),
                ("No load testing", "The target is one school on one machine."),
                ("AI providers are tested by hand", "Automated tests never touch the "
                 "network — that is a deliberate decision."),
            ],
        },
        {
            "kind": "closing",
            "eyebrow": "IN SHORT",
            "title": "A surf school's day,\nin one application",
            "points": [
                "The system catches the clash and the safety rule",
                "Money is exact and the history cannot be rewritten",
                "The surf score is computed, never asked of an AI",
                "The assistant only ever talks about real data",
                "It works with no internet at all",
            ],
            "meta": f"Version {APP_VERSION}  ·  {REPO_URL}\n"
            f"Windows 10/11 (x64) & macOS (Apple Silicon)  ·  Download: GitHub Releases (v{APP_VERSION})\n"
            "The macOS build targets Apple Silicon (arm64) and is not notarized — first launch: right-click → Open.",
        },
    ]


DECKS = {
    "tr": {
        "slides": _tr(),
        "stem": "Surf_School_Tanitim",
        "print_suffix": "_Baski",
        "html_title": "Akıllı Sörf Okulu Yönetim Sistemi — Tanıtım",
        "nav_prev": "Önceki",
        "nav_next": "Sonraki",
        "of": "/",
        "hint": "Kaydırın veya ok tuşlarını kullanın",
    },
    "en": {
        "slides": _en(),
        "stem": "Surf_School_Intro_EN",
        "print_suffix": "_Print",
        "html_title": "Smart Surf School Management System — Introduction",
        "nav_prev": "Previous",
        "nav_next": "Next",
        "of": "/",
        "hint": "Swipe or use the arrow keys",
    },
}
