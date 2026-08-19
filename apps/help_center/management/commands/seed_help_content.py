"""Load the shipped Help Center manual.

    .\\.venv\\Scripts\\python.exe manage.py seed_help_content
    .\\.venv\\Scripts\\python.exe manage.py seed_help_content --update

By default the command only *creates* what is missing. A surf school corrects
these pages to match its own procedures — "meet at the blue container, not the
kiosk" — and a deployment must never quietly overwrite that. Pass ``--update``
to force the shipped text back over existing rows.

The prose below is the product manual, not sample data: every paragraph
describes a screen that exists and a task somebody actually performs.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.help_center.models import HelpArticle, HelpCategory

# ---------------------------------------------------------------------------
# The manual
# ---------------------------------------------------------------------------
CATEGORIES: list[dict] = [
    # ======================================================= getting started
    {
        "code": "getting-started",
        "name_en": "Getting started",
        "name_tr": "Başlarken",
        "icon": "compass",
        "sort_order": 10,
        "articles": [
            {
                "slug": "your-first-day",
                "related_module": "dashboard",
                "sort_order": 10,
                "keywords": "start, first day, login, sign in, orientation, giriş, başlangıç",
                "title_en": "Your first day with the system",
                "title_tr": "Sistemle ilk gününüz",
                "body_en": """
This system runs the whole school: the people, the water time, the kit and the
money. Everything you see is filtered by your role, so two colleagues signing in
side by side will not see the same menu. If a screen described here is missing
from your sidebar, your role does not include it — ask a manager rather than
assuming it is broken.

## Signing in

Sign in with the e-mail address or username your manager created for you. After
eight failed attempts the account locks for fifteen minutes; this protects the
customer data the school is legally responsible for, and a manager cannot shorten
the wait. If you have genuinely forgotten your password, use **Forgot password**
on the sign-in screen instead of guessing.

## Finding your way around

The sidebar is grouped the way the day runs: people first, then operations,
then equipment, then surf and safety, then the business screens. The bar at the
top carries the language switch, the light/dark toggle and your notifications.
Press `/` anywhere to jump straight into the search box on the current screen.

## The three habits that matter

Record things as they happen, not at the end of the day. A booking entered at
17:00 for a lesson that ran at 09:00 has already caused a double-booking
somewhere. Use the customer record rather than a name typed into a note: the
whole system — waivers, medical flags, package balances, invoices — hangs off
that one link. And when the water conditions change, update the lesson status;
the safety and reporting screens are only as honest as the person at the desk.

## When something looks wrong

Nothing in this system is permanently deleted by ordinary use. Records are
archived, and every change is written to the audit log with your name and the
time. So if you make a mistake, say so — it can be traced and corrected. What
cannot be corrected is a lesson that went into the water on a guess.
""",
                "body_tr": """
Bu sistem okulun tamamını yönetir: insanlar, sudaki zaman, ekipman ve para.
Gördüğünüz her şey rolünüze göre filtrelenir; bu yüzden yan yana oturan iki
çalışan aynı menüyü görmez. Burada anlatılan bir ekran sizin kenar çubuğunuzda
yoksa, rolünüz o ekranı kapsamıyordur — bozuk olduğunu varsaymak yerine bir
yöneticiye sorun.

## Giriş yapma

Yöneticinizin sizin için oluşturduğu e-posta adresi veya kullanıcı adıyla giriş
yapın. Sekiz başarısız denemeden sonra hesap on beş dakika kilitlenir; bu, okulun
yasal olarak sorumlu olduğu müşteri verilerini korur ve yönetici bu süreyi
kısaltamaz. Şifrenizi gerçekten unuttuysanız tahmin etmek yerine giriş ekranındaki
**Şifremi unuttum** bağlantısını kullanın.

## Ekranlarda gezinme

Kenar çubuğu günün akışına göre gruplanmıştır: önce insanlar, sonra operasyon,
ardından ekipman, deniz ve güvenlik, en sonda işletme ekranları. Üstteki çubukta
dil değiştirme, açık/koyu tema düğmesi ve bildirimleriniz bulunur. Herhangi bir
ekranda `/` tuşuna basarak doğrudan arama kutusuna geçebilirsiniz.

## Önemli olan üç alışkanlık

Kayıtları gün sonunda değil, olay olurken girin. Saat 09:00'daki bir ders için
17:00'de girilen rezervasyon, çoktan bir yerde çakışmaya yol açmıştır. Nota
yazılmış bir isim yerine müşteri kaydını kullanın: sorumluluk formları, sağlık
uyarıları, paket bakiyeleri ve faturalar — hepsi o tek bağlantıya asılıdır. Deniz
koşulları değiştiğinde ders durumunu güncelleyin; güvenlik ve raporlama ekranları
ancak masadaki kişinin dürüstlüğü kadar doğrudur.

## Bir şey yanlış göründüğünde

Bu sistemde normal kullanım sırasında hiçbir kayıt kalıcı olarak silinmez.
Kayıtlar arşivlenir ve her değişiklik adınız ve saatiyle denetim günlüğüne
yazılır. Yani bir hata yaptıysanız söyleyin — izi sürülüp düzeltilebilir.
Düzeltilemeyecek olan, tahminle suya girilmiş bir derstir.
""",
            },
            {
                "slug": "roles-and-permissions",
                "related_module": "accounts",
                "sort_order": 20,
                "keywords": "role, permission, capability, access, rol, yetki, izin",
                "title_en": "Roles and what each one can see",
                "title_tr": "Roller ve her rolün görebildikleri",
                "body_en": """
Access is decided by **capabilities**, not by job titles typed into a field. A
capability is a short string such as `bookings.add` or `finance.refund`, and each
of the fourteen roles holds a fixed set of them. The sidebar, the buttons on a
screen and the REST API all consult the same list, so the interface can never
offer an action the server would refuse.

## The roles

Reception, Rental Staff, Surf Instructor, Head Instructor, Lifeguard, Equipment
Manager, Maintenance Staff, Finance, Marketing, Photographer, Operations Manager,
Manager and Super Admin cover the school. Customers and Students also have
accounts, but they only ever see rows that belong to them — their own bookings,
their own invoices, their own progress.

## Why a button is missing

A missing button is a missing capability, and that is the system working. If you
open a URL directly without the capability you get a clear "your role does not
grant access to this screen" message rather than a blank page. Some capabilities
are never granted implicitly, whoever you are: restoring a backup, deleting an
audit entry, approving an AI terminal command, issuing a refund and changing
another user's permissions.

## Getting access changed

A Manager or Super Admin changes a role from **Users & Roles**. Every permission
change is written to the audit log, including who made it, so ask in writing
rather than borrowing a colleague's session. Sharing a login destroys the only
record of who did what, and that record is what protects you when an incident is
investigated.
""",
                "body_tr": """
Erişim, bir alana yazılmış unvanlara göre değil **yetkilere** göre belirlenir.
Yetki, `bookings.add` veya `finance.refund` gibi kısa bir metindir ve on dört
rolün her biri sabit bir yetki kümesine sahiptir. Kenar çubuğu, ekrandaki
düğmeler ve REST API aynı listeye bakar; bu yüzden arayüz, sunucunun reddedeceği
bir işlemi asla sunamaz.

## Roller

Resepsiyon, Kiralama Personeli, Sörf Eğitmeni, Baş Eğitmen, Cankurtaran, Ekipman
Sorumlusu, Bakım Personeli, Finans, Pazarlama, Fotoğrafçı, Operasyon Müdürü,
Müdür ve Süper Yönetici okulun tamamını kapsar. Müşterilerin ve öğrencilerin de
hesapları vardır, ancak yalnızca kendilerine ait kayıtları görürler: kendi
rezervasyonları, kendi faturaları, kendi gelişimleri.

## Bir düğme neden görünmüyor

Görünmeyen düğme, eksik bir yetkidir; yani sistem doğru çalışıyordur. Yetkiniz
olmadan bir adresi doğrudan açarsanız boş sayfa yerine "rolünüz bu ekrana erişim
vermiyor" uyarısını görürsünüz. Bazı yetkiler kim olursanız olun kendiliğinden
verilmez: yedek geri yükleme, denetim kaydı silme, AI terminal komutu onaylama,
iade yapma ve başka bir kullanıcının izinlerini değiştirme.

## Erişimin değiştirilmesi

Rolleri **Kullanıcılar ve Roller** ekranından yalnızca Müdür veya Süper Yönetici
değiştirir. Her izin değişikliği, kimin yaptığı bilgisiyle denetim günlüğüne
yazılır; bu yüzden bir meslektaşınızın oturumunu ödünç almak yerine yazılı olarak
talep edin. Ortak giriş kullanmak, kimin ne yaptığına dair tek kaydı yok eder ve
bir olay soruşturulurken sizi koruyan şey tam olarak o kayıttır.
""",
            },
            {
                "slug": "language-and-display",
                "related_module": "settings",
                "sort_order": 30,
                "keywords": "language, turkish, english, dark mode, theme, dil, türkçe, tema",
                "title_en": "Language, theme and working bilingually",
                "title_tr": "Dil, tema ve iki dilli çalışma",
                "body_en": """
The whole interface exists in Turkish and English. Switch with the language
control in the top bar; the choice follows your account, so the next time you
sign in — on any device — you get the same language. Nothing about the data
changes: a booking created in Turkish reads identically in English.

## What is translated and what is not

Screen labels, buttons, statuses and validation messages come from the
translation catalogue and always match your chosen language. Content **people
type** does not: a customer's note, an instructor's lesson debrief and an
equipment nickname stay in the language they were written in. Help Center
articles and Training Center steps are the exception — they are stored in both
languages and follow your switch.

## Working with international guests

Customers can have their own preferred language on their record, and notification
templates use it. A German family booking through reception will get their
confirmation in the language recorded on their customer profile, not in the
language the receptionist happened to be using.

## Dark mode and printing

The light/dark toggle is remembered on the device rather than the account, so a
sunlit counter screen and a back-office laptop can differ. Printing always uses
the light layout, and interface chrome — the sidebar, buttons, filters — is
dropped from printed output so an invoice or a lesson manifest comes out clean.
""",
                "body_tr": """
Arayüzün tamamı Türkçe ve İngilizce olarak mevcuttur. Üst çubuktaki dil
kontrolüyle geçiş yapın; seçim hesabınıza kaydedilir, böylece hangi cihazdan
girerseniz girin aynı dille karşılaşırsınız. Veriler bundan etkilenmez: Türkçe
arayüzde oluşturulan bir rezervasyon İngilizce arayüzde de aynıdır.

## Neler çevrilir, neler çevrilmez

Ekran etiketleri, düğmeler, durumlar ve doğrulama mesajları çeviri kataloğundan
gelir ve daima seçtiğiniz dille eşleşir. **İnsanların yazdığı** içerik çevrilmez:
müşteri notu, eğitmenin ders değerlendirmesi ve ekipman takma adı yazıldıkları
dilde kalır. Yardım Merkezi makaleleri ve Eğitim Merkezi adımları istisnadır —
her iki dilde saklanır ve dil seçiminizi takip eder.

## Yabancı misafirlerle çalışma

Müşterilerin kayıtlarında kendi tercih ettikleri dil tutulabilir ve bildirim
şablonları bu dili kullanır. Resepsiyondan rezervasyon yaptıran bir Alman aile,
resepsiyonistin o an kullandığı dilde değil, müşteri profilinde kayıtlı dilde
onay alır.

## Koyu tema ve yazdırma

Açık/koyu tema tercihi hesapta değil cihazda saklanır; bu yüzden güneş altındaki
tezgâh ekranı ile ofisteki dizüstü bilgisayar farklı olabilir. Yazdırma her zaman
açık düzeni kullanır ve kenar çubuğu, düğmeler, filtreler gibi arayüz öğeleri
çıktıdan çıkarılır; böylece fatura veya ders listesi temiz basılır.
""",
            },
        ],
    },
    # ============================================================= dashboard
    {
        "code": "dashboard",
        "name_en": "Dashboard",
        "name_tr": "Kontrol paneli",
        "icon": "layout-dashboard",
        "sort_order": 20,
        "articles": [
            {
                "slug": "reading-the-dashboard",
                "related_module": "dashboard",
                "sort_order": 10,
                "keywords": "dashboard, home, today, kpi, overview, panel, özet, bugün",
                "title_en": "Reading the dashboard",
                "title_tr": "Kontrol panelini okumak",
                "body_en": """
The dashboard answers one question: *is today under control?* It is the first
screen after sign-in and it is built for a glance from behind the counter, not
for analysis. Anything that needs thinking about belongs in Analytics or Reports.

## The tiles

The top row carries today's numbers — lessons scheduled, students expected,
active rentals, money taken. Each tile shows the change against the same length
of period immediately before, so "42 bookings" becomes useful information rather
than a number without a scale. A tile you have no capability for simply is not
rendered.

## Today's timeline

Below the tiles is the day itself: lessons in start-time order with their
instructor, spot and headcount. Bookings that are still unconfirmed are marked,
because an unconfirmed booking at 08:00 for a 09:00 lesson is the single most
common cause of a group arriving one board short.

## Conditions and alerts

The surf panel shows the current reading for the school's default spot and the
suitability score for the levels you teach. Warnings — an expired instructor
certification, a piece of equipment overdue from a rental, an unresolved safety
incident, a backup that has not run — appear as alerts rather than being buried
in their own modules. Anything the AI contributes is shown on a distinct
background with an "AI Recommendation" chip, and it never decides a safety
question on its own.

## If the dashboard is empty

A new installation has no data, so the tiles read zero. That is expected. Run the
onboarding wizard first: it sets the school name, currency, timezone and the
primary surf spot, which is what most of these panels are keyed on.
""",
                "body_tr": """
Kontrol paneli tek bir soruyu yanıtlar: *bugün kontrol altında mı?* Girişten
sonraki ilk ekrandır ve analiz için değil, tezgâhın arkasından bir bakışta
okunmak için tasarlanmıştır. Üzerinde düşünmek gereken her şey Analitik veya
Raporlar ekranına aittir.

## Kutucuklar

Üst sıra bugünün rakamlarını taşır: planlanan dersler, beklenen öğrenciler, aktif
kiralamalar, tahsil edilen para. Her kutucuk, hemen öncesindeki eşit uzunluktaki
döneme göre değişimi gösterir; böylece "42 rezervasyon" ölçeksiz bir sayı olmaktan
çıkıp anlamlı bir bilgiye dönüşür. Yetkiniz olmayan bir kutucuk hiç
görüntülenmez.

## Günün akışı

Kutucukların altında günün kendisi vardır: başlangıç saatine göre sıralanmış
dersler, eğitmenleri, noktaları ve kişi sayıları. Henüz onaylanmamış rezervasyonlar
işaretlenir; çünkü 09:00 dersi için saat 08:00'de hâlâ onaylanmamış bir rezervasyon,
bir grubun bir tahta eksik gelmesinin en sık nedenidir.

## Koşullar ve uyarılar

Deniz paneli, okulun varsayılan noktası için güncel ölçümü ve ders verdiğiniz
seviyelere uygunluk puanını gösterir. Uyarılar — süresi dolmuş eğitmen sertifikası,
iade edilmemiş kiralama, kapatılmamış güvenlik olayı, çalışmamış yedekleme — kendi
modüllerinde gömülü kalmak yerine burada görünür. Yapay zekânın katkısı ayrı bir
zeminde ve "AI Önerisi" etiketiyle gösterilir; hiçbir güvenlik kararını tek başına
vermez.

## Panel boşsa

Yeni kurulmuş bir sistemde veri yoktur, bu yüzden kutucuklar sıfır gösterir. Bu
beklenen durumdur. Önce kurulum sihirbazını çalıştırın: okul adını, para birimini,
saat dilimini ve birincil sörf noktasını ayarlar — bu panellerin çoğu bu bilgilere
bağlıdır.
""",
            }
        ],
    },
    # ============================================================== students
    {
        "code": "students",
        "name_en": "Students",
        "name_tr": "Öğrenciler",
        "icon": "graduation-cap",
        "sort_order": 30,
        "articles": [
            {
                "slug": "registering-a-student",
                "related_module": "students",
                "sort_order": 10,
                "keywords": "student, register, waiver, minor, medical, öğrenci, kayıt, veli",
                "title_en": "Registering a student",
                "title_tr": "Öğrenci kaydı açmak",
                "body_en": """
A **customer** is who pays; a **student** is who goes in the water. They are
often the same person, but not always — a parent booking for two children is one
customer and two students. Create the customer first, then attach students to it,
and the invoices, packages and consent forms all line up correctly.

## What you must capture before the water

Four things are not optional: date of birth, swimming ability, any medical
condition or medication that matters in the water, and an emergency contact who
is not in the lesson. For anyone under eighteen you also need the guardian's name
and a signed waiver from that guardian — not from the student.

## Medical flags

Asthma, epilepsy, diabetes, heart conditions, recent surgery, a shoulder that
dislocates: record it in the medical field even if the customer waves it away.
The information appears on the instructor's lesson manifest and on the check-in
screen. It is confidential, it is only shown to staff who need it, and nobody is
ever refused a lesson for disclosing it — the point is that the person on the
beach knows.

## Skill level

Set an honest starting level: First Time, Beginner, Advanced Beginner,
Intermediate, Advanced or Competition. The level drives the instructor-to-student
ratio, the wave height the school will accept for that group and the board volume
recommendation. Optimism here turns into a genuine hazard three hours later.

## Waivers and photos

Upload the signed waiver to the student record rather than filing paper behind
the desk. Documents can carry an expiry date, and an expired waiver shows up as a
warning at check-in. If the school photographs lessons, record the photo consent
separately — consent to surf is not consent to appear on social media.
""",
                "body_tr": """
**Müşteri** ödeyen kişidir; **öğrenci** suya giren kişidir. Çoğu zaman aynı
kişidirler, ama her zaman değil — iki çocuğu için rezervasyon yaptıran bir ebeveyn
bir müşteri ve iki öğrencidir. Önce müşteriyi oluşturun, ardından öğrencileri ona
bağlayın; böylece faturalar, paketler ve onam formları doğru şekilde eşleşir.

## Suya girmeden önce mutlaka alınması gerekenler

Dört bilgi isteğe bağlı değildir: doğum tarihi, yüzme becerisi, suda önem taşıyan
hastalık veya ilaç bilgisi ve derste bulunmayan bir acil durum kişisi. On sekiz
yaş altındaki herkes için ayrıca velinin adı ve **velinin** imzaladığı bir
sorumluluk formu gerekir; öğrencinin imzası yeterli değildir.

## Sağlık uyarıları

Astım, epilepsi, diyabet, kalp rahatsızlığı, yakın zamanda geçirilmiş ameliyat,
çıkma eğilimi olan bir omuz: müşteri önemsiz görse bile sağlık alanına yazın. Bu
bilgi eğitmenin ders listesinde ve giriş ekranında görünür. Gizlidir, yalnızca
ihtiyacı olan personele gösterilir ve kimse bilgi verdiği için dersten
çevrilmez — amaç, kumsaldaki kişinin durumu bilmesidir.

## Seviye

Gerçekçi bir başlangıç seviyesi seçin: İlk Kez, Başlangıç, İleri Başlangıç, Orta,
İleri veya Yarışma. Seviye; eğitmen-öğrenci oranını, okulun o grup için kabul
edeceği dalga yüksekliğini ve önerilen tahta hacmini belirler. Buradaki iyimserlik
üç saat sonra gerçek bir tehlikeye dönüşür.

## Sorumluluk formları ve fotoğraflar

İmzalı formu masanın arkasında saklamak yerine öğrenci kaydına yükleyin. Belgelere
son geçerlilik tarihi verilebilir ve süresi dolmuş bir form giriş sırasında uyarı
olarak görünür. Okul ders fotoğrafı çekiyorsa fotoğraf onayını ayrıca kaydedin —
sörf yapma onayı, sosyal medyada yer alma onayı değildir.
""",
            },
            {
                "slug": "skill-assessments",
                "related_module": "students",
                "sort_order": 20,
                "keywords": "level, assessment, progress, skill, seviye, değerlendirme, gelişim",
                "title_en": "Skill levels and assessments",
                "title_tr": "Seviyeler ve değerlendirmeler",
                "body_en": """
A student's level is not a label typed once at registration — it is a record with
a history. After a lesson the instructor files a skill assessment, and that
assessment is what moves the level. Anyone can then see why a student is
Intermediate rather than merely being told that they are.

## Filing an assessment

Open the student, choose **New assessment**, and score the components the school
teaches: paddling, positioning, pop-up, trim, turning, wave selection and surf
etiquette. Add a short written note — "stands consistently on whitewater, still
late to her feet on green waves" is worth more than any number, both to the next
instructor and to the student.

## Why the level matters operationally

The level is not decoration. It sets the maximum group size per instructor (six
for first-timers, ten for intermediates, six again for competition coaching), it
sets the wave height above which the school will not put that student in the
water, and it decides which surf spots are offered. Promote a student one level
too early and every one of those guard rails moves with them.

## Promotions

Promote when the student demonstrates the skill in the conditions of the day, not
when they have attended a certain number of lessons. Attendance is not competence.
If two instructors disagree, the Head Instructor decides — and the disagreement
itself is worth recording in the note.

## What the student sees

Students with an account see their own level, their assessment history and their
lesson attendance. They do not see internal notes marked as internal. Write the
frank operational note in the internal field and the encouraging summary in the
assessment comment, and both audiences are served honestly.
""",
                "body_tr": """
Bir öğrencinin seviyesi, kayıt sırasında bir kez yazılan etiket değil, geçmişi olan
bir kayıttır. Ders sonrası eğitmen bir beceri değerlendirmesi girer ve seviyeyi
değiştiren şey bu değerlendirmedir. Böylece herkes, bir öğrencinin neden Orta
seviye olduğunu yalnızca duymakla kalmaz, görebilir.

## Değerlendirme girme

Öğrenciyi açın, **Yeni değerlendirme** seçin ve okulun öğrettiği bileşenleri
puanlayın: kürek çekme, konumlanma, ayağa kalkış, denge, dönüş, dalga seçimi ve
sörf görgü kuralları. Kısa bir yazılı not ekleyin — "köpükte istikrarlı kalkıyor,
yeşil dalgada hâlâ geç ayağa kalkıyor" cümlesi, hem sonraki eğitmen hem öğrenci
için her puandan değerlidir.

## Seviyenin operasyonel önemi

Seviye süs değildir. Eğitmen başına azami grup büyüklüğünü belirler (ilk kez
girenler için altı, orta seviye için on, yarışma antrenmanında yine altı), okulun
o öğrenciyi suya sokmayacağı dalga yüksekliğini belirler ve hangi noktaların
önerileceğine karar verir. Bir öğrenciyi bir seviye erken yükseltirseniz bu
güvenlik sınırlarının hepsi onunla birlikte kayar.

## Seviye yükseltme

Belirli sayıda derse katıldığı için değil, o günün koşullarında beceriyi
gösterdiği için yükseltin. Katılım, yeterlilik demek değildir. İki eğitmen aynı
fikirde değilse kararı Baş Eğitmen verir — ve bu görüş ayrılığının kendisi de nota
yazılmaya değer.

## Öğrencinin gördükleri

Hesabı olan öğrenciler kendi seviyelerini, değerlendirme geçmişlerini ve ders
katılımlarını görür. Dâhili olarak işaretlenmiş notları görmezler. Açık sözlü
operasyonel notu dâhili alana, cesaretlendirici özeti değerlendirme yorumuna
yazın; böylece her iki taraf da dürüstçe bilgilendirilmiş olur.
""",
            },
        ],
    },
    # =========================================================== instructors
    {
        "code": "instructors",
        "name_en": "Instructors",
        "name_tr": "Eğitmenler",
        "icon": "user-check",
        "sort_order": 40,
        "articles": [
            {
                "slug": "instructor-records",
                "related_module": "instructors",
                "sort_order": 10,
                "keywords": "instructor, certification, availability, time off, eğitmen, sertifika, izin",
                "title_en": "Instructor records, certifications and availability",
                "title_tr": "Eğitmen kayıtları, sertifikalar ve müsaitlik",
                "body_en": """
An instructor record is what the scheduler reasons about. It holds the levels
they are qualified to teach, the languages they speak, their certifications with
expiry dates, their weekly availability, their booked time off and their
commission rate.

## Certifications are dated, not ticked

Record ISA/Surfing England level, lifesaving and first aid separately, each with
its expiry date. An expired certification raises a warning on the dashboard and
in the assignment screen. The system will let a manager schedule that instructor
anyway — because reality sometimes requires it — but the decision is explicit,
recorded and visible, rather than an oversight nobody noticed until an insurer
asked.

## Availability and time off

Weekly availability is the normal pattern: which days, which hours, which spots.
Time off is the exception on top: holiday, illness, a competition, a course.
Booking a lesson against an instructor who is unavailable is refused rather than
warned about, because a soft warning at 08:00 in high season is a warning nobody
reads.

## Languages and specialities

Record languages honestly — "conversational German" is a different promise from
"fluent German". Reception uses this when matching a family to a coach, and a
guest who was promised their own language and did not get it will say so in the
review.

## Commission and performance

If the school pays commission, the rate lives on the instructor record and the
finance module calculates it from completed lessons; nobody retypes it. The
performance review section is for structured feedback over a season. Both are
visible only to roles holding the commission capability — an instructor cannot
browse a colleague's earnings.
""",
                "body_tr": """
Eğitmen kaydı, planlamanın üzerinde çalıştığı kayıttır. Ders verebileceği
seviyeleri, konuştuğu dilleri, son geçerlilik tarihleriyle sertifikalarını,
haftalık müsaitliğini, planlanmış izinlerini ve komisyon oranını içerir.

## Sertifikalar işaretlenmez, tarihlenir

ISA/Surfing England seviyesini, cankurtaranlık ve ilk yardım belgelerini ayrı ayrı,
her birinin bitiş tarihiyle kaydedin. Süresi dolmuş bir sertifika kontrol panelinde
ve görevlendirme ekranında uyarı üretir. Sistem yöneticinin yine de görevlendirme
yapmasına izin verir — çünkü gerçek hayat bazen bunu gerektirir — ama bu karar
açık, kayıtlı ve görünürdür; sigortacı sorana kadar kimsenin fark etmediği bir
ihmal değildir.

## Müsaitlik ve izinler

Haftalık müsaitlik normal düzendir: hangi günler, hangi saatler, hangi noktalar.
İzinler bunun üzerine gelen istisnadır: tatil, hastalık, yarışma, kurs. Müsait
olmayan bir eğitmene ders yazılması uyarıyla geçiştirilmez, reddedilir; çünkü
sezonun yoğun döneminde saat 08:00'de çıkan yumuşak bir uyarıyı kimse okumaz.

## Diller ve uzmanlıklar

Dilleri dürüst kaydedin — "günlük konuşma düzeyinde Almanca" ile "akıcı Almanca"
farklı vaatlerdir. Resepsiyon, bir aileyi eğitmenle eşleştirirken buna bakar ve
kendi dilinde eğitim sözü verilip alamayan misafir bunu değerlendirmesinde yazar.

## Komisyon ve performans

Okul komisyon ödüyorsa oran eğitmen kaydında durur ve finans modülü tamamlanan
derslerden hesaplar; kimse yeniden yazmaz. Performans değerlendirmesi bölümü bir
sezon boyunca yapılandırılmış geri bildirim içindir. İkisi de yalnızca komisyon
yetkisine sahip rollere görünür — bir eğitmen meslektaşının kazancını göremez.
""",
            }
        ],
    },
    # =============================================================== lessons
    {
        "code": "lessons",
        "name_en": "Lessons",
        "name_tr": "Dersler",
        "icon": "book-open",
        "sort_order": 50,
        "articles": [
            {
                "slug": "planning-a-lesson",
                "related_module": "lessons",
                "sort_order": 10,
                "keywords": "lesson, schedule, group, ratio, spot, ders, planlama, oran",
                "title_en": "Planning a lesson",
                "title_tr": "Ders planlamak",
                "body_en": """
A lesson is a time, a place, an instructor, a level and a group of students. The
system enforces the constraints that keep that combination safe, so planning is
mostly a matter of answering its questions honestly.

## Lesson types

Lesson types are the school's catalogue: private, semi-private, group, kids'
club, improver clinic, tow-in coaching. Each carries a default duration, a
default price and a maximum group size. Create the type once and every lesson
inherits it; change the price on the type and future lessons follow while past
ones keep what was actually charged.

## Group size is a safety rule

The maximum number of students per instructor is fixed by level: six for
first-timers, eight for beginners and advanced beginners, ten for intermediate
and advanced, six for competition coaching. If the group contains anyone under
eighteen the cap drops to six regardless of level. These are not preferences and
the booking screen will refuse to exceed them — add a second instructor instead.

## Choosing the spot

Each surf spot records the levels it suits, its break and bottom type, its ideal
tide and wind, and its open hazards. A spot whose minimum level is above your
group is not offered. A spot with an open critical hazard is not offered at all,
to anyone, until the hazard is cleared by someone who has been there.

## Before you save

Check three things: the instructor is actually available and certified, the
equipment for the group exists and is not already committed elsewhere, and the
forecast for that slot is inside the wave-height and wind limits for the group's
level. All three are shown on the lesson screen — the point of entering the
lesson in advance is that you get to see them before the students arrive.
""",
                "body_tr": """
Ders; bir zaman, bir yer, bir eğitmen, bir seviye ve bir öğrenci grubudur. Sistem
bu bileşimi güvenli kılan kısıtları uygular; bu yüzden planlama, esas olarak
sorularını dürüstçe yanıtlamaktan ibarettir.

## Ders türleri

Ders türleri okulun kataloğudur: özel, yarı özel, grup, çocuk kulübü, gelişim
kliniği, çekmeli antrenman. Her biri varsayılan bir süre, fiyat ve azami grup
büyüklüğü taşır. Türü bir kez oluşturun, her ders bunu devralsın; türdeki fiyatı
değiştirdiğinizde gelecekteki dersler yeni fiyatı alır, geçmiş dersler fiilen
tahsil edileni korur.

## Grup büyüklüğü bir güvenlik kuralıdır

Eğitmen başına azami öğrenci sayısı seviyeye göre sabittir: ilk kez girenler için
altı, başlangıç ve ileri başlangıç için sekiz, orta ve ileri için on, yarışma
antrenmanında altı. Grupta on sekiz yaş altı biri varsa seviye ne olursa olsun
sınır altıya düşer. Bunlar tercih değildir ve rezervasyon ekranı sınırın aşılmasını
reddeder — bunun yerine ikinci bir eğitmen ekleyin.

## Nokta seçimi

Her sörf noktası; uygun olduğu seviyeleri, kırılım ve zemin tipini, ideal gelgit ve
rüzgârını ve açık tehlikelerini kaydeder. Asgari seviyesi grubunuzun üzerinde olan
bir nokta önerilmez. Açık kritik tehlikesi olan bir nokta, tehlike sahada birisi
tarafından kaldırılana kadar hiç kimseye önerilmez.

## Kaydetmeden önce

Üç şeyi kontrol edin: eğitmen gerçekten müsait ve sertifikalı mı, grubun ekipmanı
mevcut ve başka bir yere tahsis edilmemiş mi, o saat için tahmin grubun seviyesine
ait dalga yüksekliği ve rüzgâr sınırlarının içinde mi. Üçü de ders ekranında
gösterilir — dersi önceden girmenin amacı, öğrenciler gelmeden bunları
görebilmektir.
""",
            },
            {
                "slug": "running-a-lesson",
                "related_module": "lessons",
                "sort_order": 20,
                "keywords": "attendance, check-in, debrief, cancel, katılım, yoklama, iptal",
                "title_en": "Running the lesson and taking attendance",
                "title_tr": "Dersi yürütmek ve yoklama almak",
                "body_en": """
Once the day starts, the lesson record becomes an operational log rather than a
plan. Four moments matter: check-in, going in the water, coming out, and the
debrief.

## Check-in

Open the lesson and check students in as they arrive. The screen shows the
medical flags and any missing waiver at that moment — this is the last point at
which a missing consent form is cheap to fix. Mark a genuine no-show as a no-show
rather than cancelling: the two mean different things for the customer's account,
their package balance and the school's statistics.

## Conditions at the time

Record the conditions you actually observed, not the forecast. Wave height, wind,
tide state and water temperature at the start of the session are what an incident
review will ask for, and the forecast from the night before is not evidence. If
the conditions are outside the limits for the group's level, postpone the lesson
with the "postponed — conditions" status; that status exists so the difference
between "we cancelled" and "the sea cancelled" survives into the reports.

## While in the water

Attendance can be adjusted after the fact. If a student leaves early, mark it and
say why in the note; if they got a hold of a wave nobody expected, that also
belongs in the note. The instructor's debrief is what the next instructor reads
first.

## Completing the lesson

Setting the lesson to Completed is what releases the equipment back to the
available pool, triggers the commission calculation and lets the student's skill
assessment be filed. A lesson left in progress overnight blocks all three, and it
is the most common reason a board appears to be missing the following morning.
""",
                "body_tr": """
Gün başladığında ders kaydı bir plan olmaktan çıkıp operasyonel bir günlüğe
dönüşür. Dört an önemlidir: giriş, suya giriş, sudan çıkış ve değerlendirme.

## Giriş (yoklama)

Dersi açın ve öğrenciler geldikçe girişlerini işaretleyin. Ekran o anda sağlık
uyarılarını ve eksik sorumluluk formlarını gösterir — eksik bir onam formunun ucuza
düzeltilebileceği son andır. Gerçekten gelmeyen kişiyi iptal olarak değil "gelmedi"
olarak işaretleyin: ikisi müşterinin hesabı, paket bakiyesi ve okulun istatistikleri
için farklı anlamlara gelir.

## O andaki koşullar

Tahmini değil, fiilen gözlemlediğiniz koşulları kaydedin. Seans başındaki dalga
yüksekliği, rüzgâr, gelgit durumu ve su sıcaklığı bir olay incelemesinde sorulacak
verilerdir; önceki akşamın tahmini kanıt değildir. Koşullar grubun seviyesine ait
sınırların dışındaysa dersi "ertelendi — koşullar" durumuyla erteleyin; bu durum,
"biz iptal ettik" ile "denizi iptal etti" farkının raporlara yansıması için vardır.

## Su içindeyken

Yoklama sonradan düzeltilebilir. Bir öğrenci erken çıktıysa işaretleyin ve nedenini
nota yazın; kimsenin beklemediği bir dalgayı yakaladıysa o da nota yazılmalıdır.
Eğitmenin değerlendirmesi, bir sonraki eğitmenin ilk okuduğu şeydir.

## Dersi tamamlamak

Dersi "Tamamlandı" durumuna almak; ekipmanı tekrar müsait havuzuna döndürür,
komisyon hesabını tetikler ve beceri değerlendirmesinin girilebilmesini sağlar.
Gece boyunca "devam ediyor" durumunda bırakılan bir ders üçünü de engeller ve
ertesi sabah bir tahtanın kayıp görünmesinin en yaygın nedenidir.
""",
            },
        ],
    },
    # ============================================================== bookings
    {
        "code": "bookings",
        "name_en": "Bookings",
        "name_tr": "Rezervasyonlar",
        "icon": "calendar-days",
        "sort_order": 60,
        "articles": [
            {
                "slug": "taking-a-booking",
                "related_module": "bookings",
                "sort_order": 10,
                "keywords": "booking, reserve, deposit, confirm, rezervasyon, kapora, onay",
                "title_en": "Taking a booking",
                "title_tr": "Rezervasyon almak",
                "body_en": """
A booking connects a customer to something the school sells — a lesson, a camp
place, a rental — and holds capacity while payment is arranged. Every booking has
a short code; read it out to the customer, because that is what they will quote
when they phone back.

## The flow

Draft, Pending confirmation, Confirmed, Checked in, Completed. Cancelled and
No-show are the two ways out. Only Pending, Confirmed and Checked-in occupy a
seat, so a cancelled booking releases its capacity immediately and a draft never
held any.

## Capacity and conflicts

The booking screen checks four things before it will confirm: the lesson has room
inside the instructor ratio for that level, the instructor is available and not on
time off, the spot is open, and the equipment the booking needs is not already
promised. A conflict is refused with the specific reason, not with a generic
error, so you can fix the actual problem instead of guessing.

## Deposits and payment status

Payment status is tracked separately from booking status, because a confirmed
lesson can be unpaid and a paid lesson can be cancelled. Take the deposit against
the booking and the balance appears on the customer's account. If the customer has
a lesson package, use it as the payment method — the package balance decrements
automatically and nobody has to remember how many lessons are left.

## Groups and families

For a family, create one booking per student against the same customer rather
than one booking for four people. It looks like more typing, but it is what lets
you check in three children while the fourth is still in the car park, and it is
what makes the attendance and the invoice agree at the end.
""",
                "body_tr": """
Rezervasyon, bir müşteriyi okulun sattığı bir şeye — ders, kamp yeri, kiralama —
bağlar ve ödeme düzenlenirken kapasiteyi tutar. Her rezervasyonun kısa bir kodu
vardır; müşteriye okuyun, çünkü telefonla aradığında söyleyeceği şey odur.

## Akış

Taslak, Onay bekliyor, Onaylandı, Giriş yapıldı, Tamamlandı. İptal ve Gelmedi ise
iki çıkış yoludur. Yalnızca Onay bekliyor, Onaylandı ve Giriş yapıldı durumları
kontenjan tutar; bu yüzden iptal edilen rezervasyon kapasitesini anında serbest
bırakır, taslak ise hiç tutmamıştır.

## Kapasite ve çakışmalar

Rezervasyon ekranı onaylamadan önce dört şeyi kontrol eder: derste o seviyenin
eğitmen oranı içinde yer var mı, eğitmen müsait ve izinde değil mi, nokta açık mı ve
ihtiyaç duyulan ekipman başkasına söz verilmiş mi. Çakışma, genel bir hata yerine
somut nedeniyle reddedilir; böylece tahmin etmek yerine gerçek sorunu çözersiniz.

## Kapora ve ödeme durumu

Ödeme durumu, rezervasyon durumundan ayrı izlenir; çünkü onaylanmış bir ders
ödenmemiş, ödenmiş bir ders iptal edilmiş olabilir. Kaporayı rezervasyon üzerinden
alın, kalan bakiye müşterinin hesabında görünsün. Müşterinin ders paketi varsa
ödeme yöntemi olarak paketi seçin — paket bakiyesi otomatik düşer ve kaç ders
kaldığını kimsenin hatırlaması gerekmez.

## Gruplar ve aileler

Bir aile için dört kişilik tek rezervasyon yerine aynı müşteriye bağlı öğrenci
başına bir rezervasyon oluşturun. Daha çok yazmak gibi görünür, ama dördüncüsü hâlâ
otoparktayken üç çocuğun girişini yapmanızı sağlayan ve sonunda yoklama ile
faturanın birbirini tutmasını sağlayan şey budur.
""",
            },
            {
                "slug": "cancellations-and-waitlist",
                "related_module": "bookings",
                "sort_order": 20,
                "keywords": "cancel, no show, waitlist, refund, iptal, gelmedi, bekleme listesi",
                "title_en": "Cancellations, no-shows and the waitlist",
                "title_tr": "İptaller, gelmeyenler ve bekleme listesi",
                "body_en": """
How a booking ends decides what the customer is charged, whether a package lesson
is consumed and what the reports say about the season. Choose the right ending.

## Cancelled versus no-show

**Cancelled** means somebody told the school in advance. **No-show** means the
seat was held, the instructor waited and nobody came. A no-show normally consumes
the lesson or the deposit under the school's terms; a cancellation inside the
notice period usually does not. Recording a no-show as a cancellation to be kind
is a decision with money attached — take it deliberately, and note why.

## Cancelled by the sea

If the school called the session off because of conditions, that is not the
customer's cancellation and should not cost them anything. Use the lesson's
"postponed — conditions" status and rebook. Reports separate weather losses from
commercial losses, and a season where a quarter of lessons are lost to wind is a
scheduling problem, not a sales problem — but only if the data says so.

## The waitlist

When a lesson is full, add the customer to the waitlist instead of turning them
away. If a place opens, the waitlist entry surfaces on the bookings screen with
the customer's contact details in the order they joined. Working the waitlist in
order is both fairer and easier to defend when two people wanted the same slot.

## Refunds

Refunds are a separate, capability-gated action in the finance module — cancelling
a booking never silently moves money. The refund is recorded against the original
payment, appears in the audit log with the name of whoever authorised it, and
shows on the customer's statement rather than vanishing into a cash drawer.
""",
                "body_tr": """
Bir rezervasyonun nasıl sonlandığı; müşteriden ne tahsil edileceğini, paket dersinin
harcanıp harcanmayacağını ve raporların sezon hakkında ne söyleyeceğini belirler.
Doğru sonu seçin.

## İptal ile gelmedi arasındaki fark

**İptal**, birinin okula önceden haber vermesidir. **Gelmedi**, yerin tutulduğu,
eğitmenin beklediği ve kimsenin gelmediği durumdur. Okulun koşullarına göre
"gelmedi" genellikle dersi veya kaporayı harcar; ihbar süresi içindeki iptal
genelde harcamaz. İyi niyetle "gelmedi"yi iptal olarak kaydetmek, parasal sonucu
olan bir karardır — bilerek verin ve nedenini not edin.

## Denizin iptali

Okul seansı koşullar nedeniyle iptal ettiyse bu müşterinin iptali değildir ve ona
maliyet çıkarmamalıdır. Dersin "ertelendi — koşullar" durumunu kullanın ve yeniden
planlayın. Raporlar hava kaynaklı kayıpları ticari kayıplardan ayırır; derslerin
dörtte birinin rüzgâra gittiği bir sezon satış sorunu değil planlama sorunudur —
ama bunu ancak veri söylüyorsa görebilirsiniz.

## Bekleme listesi

Ders dolduğunda müşteriyi geri çevirmek yerine bekleme listesine ekleyin. Yer
açıldığında bekleme kaydı, katılım sırasına göre müşterinin iletişim bilgileriyle
rezervasyon ekranında görünür. Listeyi sırayla işlemek hem daha adildir hem de aynı
saati iki kişi istediğinde savunması kolaydır.

## İadeler

İadeler finans modülünde ayrı ve yetkiye bağlı bir işlemdir — rezervasyon iptali
hiçbir zaman sessizce para hareketi yaratmaz. İade, orijinal ödemeye karşı
kaydedilir, onaylayan kişinin adıyla denetim günlüğüne yazılır ve kasada
kaybolmak yerine müşterinin ekstresinde görünür.
""",
            },
        ],
    },
    # ============================================================ surf camps
    {
        "code": "surf-camps",
        "name_en": "Surf Camps",
        "name_tr": "Sörf kampları",
        "icon": "tent",
        "sort_order": 70,
        "articles": [
            {
                "slug": "building-a-surf-camp",
                "related_module": "surf_camps",
                "sort_order": 10,
                "keywords": "camp, multi day, itinerary, participants, kamp, program, katılımcı",
                "title_en": "Building a surf camp",
                "title_tr": "Sörf kampı oluşturmak",
                "body_en": """
A surf camp is a multi-day package: accommodation, a run of sessions, meals,
transfers and usually a video-analysis evening. It is modelled as a camp with
days, each day holding activities, and participants attached to the camp as a
whole.

## Setting up

Create the camp with its dates, capacity, level range and price. Then build the
days. A day is not required to contain surfing — a rest day, a boat trip or a
theory morning is a legitimate day and putting it in the plan is what stops a
guest arriving at the beach at 09:00 on the one morning nothing was scheduled.

## Activities inside a day

Each activity carries a time, a type, a spot and an instructor. Sessions created
here behave like ordinary lessons: the same ratio limits, the same equipment
reservation, the same conditions check. This matters because a camp's third
morning is exactly when a tired group and a rising swell meet.

## Participants

Add participants as students, not as names in a list. That gives every camper the
level, the medical flags, the waiver and the equipment sizing that the ordinary
lesson machinery needs. Camp capacity is enforced, and a camp that suits
Beginner–Intermediate will not accept a first-timer without an explicit override
by someone with the capability to grant it.

## Money and reporting

Camps are usually sold as one price covering everything, so the invoice sits on
the camp booking rather than on each session. Instructor commission is still
calculated per session from the activities, which is why filling in the day plan
properly is worth the ten minutes it takes. At the end you can report on camp
occupancy, revenue per camper and how many sessions the weather actually allowed.
""",
                "body_tr": """
Sörf kampı çok günlü bir pakettir: konaklama, ardışık seanslar, yemekler,
transferler ve genellikle bir video analiz akşamı. Sistemde kamp; günlerden, her
gün etkinliklerden oluşur ve katılımcılar kampın tamamına bağlanır.

## Kurulum

Kampı tarihleri, kontenjanı, seviye aralığı ve fiyatıyla oluşturun. Ardından
günleri kurun. Bir günün sörf içermesi zorunlu değildir — dinlenme günü, tekne
turu veya teori sabahı da geçerli bir gündür ve planda yer alması, hiçbir şeyin
planlanmadığı o tek sabah misafirin 09:00'da kumsalda beklemesini önler.

## Gün içindeki etkinlikler

Her etkinlik bir saat, tür, nokta ve eğitmen taşır. Burada oluşturulan seanslar
normal dersler gibi davranır: aynı oran sınırları, aynı ekipman rezervasyonu, aynı
koşul kontrolü. Bu önemlidir; çünkü kampın üçüncü sabahı, yorgun bir grupla
yükselen dalganın tam olarak buluştuğu andır.

## Katılımcılar

Katılımcıları bir listedeki isimler olarak değil, öğrenci olarak ekleyin. Böylece
her kampçının seviyesi, sağlık uyarıları, sorumluluk formu ve ekipman ölçüsü normal
ders mekanizmasına dâhil olur. Kamp kontenjanı zorunludur ve Başlangıç–Orta
seviyeye uygun bir kamp, yetkili biri açıkça izin vermeden ilk kez sörf yapacak
birini kabul etmez.

## Para ve raporlama

Kamplar genellikle her şeyi kapsayan tek fiyatla satılır; bu yüzden fatura tek tek
seanslarda değil kamp rezervasyonunda durur. Eğitmen komisyonu yine etkinliklerden
seans bazında hesaplanır — gün planını düzgün doldurmanın on dakikaya değmesinin
nedeni budur. Sonunda kamp doluluğu, kampçı başına gelir ve havanın gerçekte kaç
seansa izin verdiği raporlanabilir.
""",
            }
        ],
    },
    # ============================================================= equipment
    {
        "code": "equipment",
        "name_en": "Equipment",
        "name_tr": "Ekipman",
        "icon": "package",
        "sort_order": 80,
        "articles": [
            {
                "slug": "adding-equipment",
                "related_module": "equipment",
                "sort_order": 10,
                "keywords": "board, wetsuit, inventory, qr, volume, tahta, mayo, envanter",
                "title_en": "Adding boards, wetsuits and the rest of the fleet",
                "title_tr": "Tahtalar, mayolar ve filonun geri kalanı",
                "body_en": """
Every physical item the school lends or rents belongs in the inventory: boards,
wetsuits, leashes, impact vests, roof racks, the van. If it can go missing or
break, it needs a record — otherwise the first time anyone counts is after it is
gone.

## Categories and specifications

Group items into categories (soft-top, funboard, shortboard, wetsuit 3/2, wetsuit
4/3, accessory) and fill in the specifications that matter for matching. For a
board that is length, width, thickness and volume in litres; for a wetsuit it is
size and thickness. Volume is the field that lets the system recommend the right
board for a rider's weight and level — roughly one litre per kilogram for a total
beginner, dropping to under half that for an advanced surfer.

## Status and condition

Status is where the item *is*: available, rented, in a lesson, reserved, in
maintenance, damaged, lost, retired. Condition is what *state* it is in: new,
excellent, good, fair, poor, unusable. They move independently — a board can be
available and in fair condition, which is fine for a whitewater lesson and wrong
for a demo. Items in maintenance, damaged, lost or retired status are never
offered to a customer.

## Labels

Each item gets a QR code you can print and stick on the tail pad or the wetsuit
tag. Scanning it at the rental desk opens that exact item, which removes the
entire class of error where board 14 goes out and board 41 is recorded.

## Purchase and depreciation

Record what the item cost and when it was bought. Over a season this is what
turns "we seem to buy a lot of leashes" into a number, and it feeds the equipment
utilisation report — the one that tells you which half of the fleet earns its
storage space.
""",
                "body_tr": """
Okulun ödünç verdiği veya kiraladığı her fiziksel eşya envantere aittir: tahtalar,
mayolar, leash'ler, darbe yelekleri, tavan barları, minibüs. Kaybolabiliyor veya
kırılabiliyorsa kaydı olmalıdır — aksi hâlde ilk sayım, eşya gittikten sonra
yapılır.

## Kategoriler ve teknik özellikler

Eşyaları kategorilere ayırın (soft-top, funboard, shortboard, 3/2 mayo, 4/3 mayo,
aksesuar) ve eşleştirmede işe yarayan özellikleri doldurun. Tahta için bu boy, en,
kalınlık ve litre cinsinden hacimdir; mayo için beden ve kalınlıktır. Hacim, sistemin
bir binicinin kilosuna ve seviyesine uygun tahtayı önermesini sağlayan alandır —
tam yeni başlayan için kilogram başına yaklaşık bir litre, ileri seviyede bunun
yarısının altına iner.

## Durum ve kondisyon

Durum, eşyanın *nerede* olduğudur: müsait, kirada, derste, rezerve, bakımda,
hasarlı, kayıp, emekli. Kondisyon ise *hâlidir*: yeni, mükemmel, iyi, orta, kötü,
kullanılamaz. Bunlar bağımsız hareket eder — bir tahta müsait ve orta kondisyonda
olabilir; köpük dersi için uygundur, deneme sürüşü için değildir. Bakımda, hasarlı,
kayıp veya emekli durumundaki eşyalar müşteriye asla önerilmez.

## Etiketler

Her eşya için yazdırıp kuyruk pedine veya mayo etiketine yapıştırabileceğiniz bir
QR kod üretilir. Kiralama masasında okutmak doğrudan o eşyayı açar; böylece 14
numaralı tahtanın çıkıp 41 numaranın kaydedildiği hata sınıfı tamamen ortadan
kalkar.

## Alım ve amortisman

Eşyanın maliyetini ve alım tarihini kaydedin. Bir sezon boyunca bu, "galiba çok
leash alıyoruz" cümlesini bir sayıya dönüştürür ve ekipman kullanım raporunu
besler — filonun hangi yarısının deposunun hakkını verdiğini söyleyen rapor.
""",
            }
        ],
    },
    # =============================================================== rentals
    {
        "code": "rentals",
        "name_en": "Rentals",
        "name_tr": "Kiralamalar",
        "icon": "arrow-left-right",
        "sort_order": 90,
        "articles": [
            {
                "slug": "renting-equipment",
                "related_module": "rentals",
                "sort_order": 10,
                "keywords": "rental, hire, return, deposit, damage, kiralama, iade, depozito",
                "title_en": "Renting equipment out and taking it back",
                "title_tr": "Ekipman kiralamak ve geri almak",
                "body_en": """
The rental desk is the busiest transactional screen in the school and the easiest
place to lose an item. The flow is deliberately short: identify the customer, add
the items, agree the period, take the deposit, hand it over.

## Opening a rental

Start from the customer — a walk-in becomes a customer record with a phone number
and an ID reference before anything leaves the rack. Add items by scanning their
QR codes. Only items whose status is available can be added; anything in
maintenance, damaged, lost or retired is refused, which is the whole reason those
statuses exist.

## Pricing the period

Rental pricing is hourly, daily or weekly, taken from the item's category. The
system calculates the amount; if you override it, the override is recorded with
your name. Take the deposit as part of the rental so the amount held is visible
when a different colleague handles the return three days later.

## Overdue items

An item past its return time shows on the rentals screen and in the dashboard
alerts. Chase early: the difference between a two-hour and a two-day conversation
with a guest about a missing board is almost entirely how quickly the call was
made.

## Taking it back

On return, check the item and set its condition honestly. Damage is recorded
against the rental — with the damage type, a photo and whether the customer is
being charged — and the item moves to the maintenance queue automatically if it
is no longer usable. Closing the rental releases the deposit and returns the item
to the available pool. A rental left open keeps the board out of circulation, so
close it at the counter, not at the end of the week.
""",
                "body_tr": """
Kiralama masası okulun en yoğun işlem ekranı ve eşya kaybetmenin en kolay olduğu
yerdir. Akış bilinçli olarak kısadır: müşteriyi tanımla, eşyaları ekle, süreyi
belirle, depozitoyu al, teslim et.

## Kiralama açmak

Müşteriden başlayın — rafından bir şey çıkmadan önce, gelen kişi telefon numarası
ve kimlik referansı olan bir müşteri kaydına dönüşür. Eşyaları QR kodlarını
okutarak ekleyin. Yalnızca durumu "müsait" olan eşyalar eklenebilir; bakımda,
hasarlı, kayıp veya emekli olanlar reddedilir — bu durumların var olma nedeni de
budur.

## Sürenin fiyatlandırılması

Kiralama fiyatı, eşyanın kategorisine göre saatlik, günlük veya haftalıktır. Tutarı
sistem hesaplar; elle değiştirirseniz bu değişiklik adınızla kaydedilir. Depozitoyu
kiralamanın parçası olarak alın; böylece üç gün sonra iadeyi başka bir çalışan
yaptığında tutulan tutar görünür olur.

## Geciken eşyalar

İade saati geçen eşya, kiralama ekranında ve kontrol paneli uyarılarında görünür.
Erken arayın: kayıp bir tahta konusunda misafirle iki saatlik mi yoksa iki günlük
mü bir konuşma yapacağınızı belirleyen şey, neredeyse tamamen aramanın ne kadar
çabuk yapıldığıdır.

## Geri alım

İadede eşyayı kontrol edin ve kondisyonunu dürüstçe güncelleyin. Hasar; türü,
fotoğrafı ve müşteriden tahsil edilip edilmediğiyle birlikte kiralamaya kaydedilir
ve eşya kullanılamaz hâldeyse otomatik olarak bakım kuyruğuna geçer. Kiralamayı
kapatmak depozitoyu serbest bırakır ve eşyayı müsait havuzuna döndürür. Açık kalan
bir kiralama tahtayı dolaşımdan alıkoyar; bu yüzden hafta sonunda değil, tezgâhta
kapatın.
""",
            }
        ],
    },
    # =========================================================== maintenance
    {
        "code": "maintenance",
        "name_en": "Maintenance",
        "name_tr": "Bakım",
        "icon": "wrench",
        "sort_order": 100,
        "articles": [
            {
                "slug": "repairs-and-schedules",
                "related_module": "maintenance",
                "sort_order": 10,
                "keywords": "repair, ding, service, schedule, cost, tamir, bakım, onarım",
                "title_en": "Repairs, dings and the maintenance schedule",
                "title_tr": "Onarımlar, ding'ler ve bakım planı",
                "body_en": """
Maintenance covers two different things: the repair that happens because
something broke, and the service that happens because time passed. Both live
here, and keeping them together is what makes an equipment budget predictable.

## Reporting a problem

Anyone who touches equipment can raise a maintenance record — instructors and
rental staff included. Describe what is wrong, pick the damage type (ding, crack,
fin, leash, wetsuit tear, zipper, delamination, snapped) and attach a photo. The
item's status moves to maintenance immediately, which takes it out of every
booking and rental screen. That is the point: a cracked board must become
unbookable in the same minute it is reported, not when somebody gets round to it.

## Working the queue

The queue is ordered by severity and age. Each record carries the work done, the
parts used, the cost and who did it — in-house or an external shop. Recording the
cost is what later answers "is repairing this board still cheaper than replacing
it?", which is a question every school asks a season too late.

## Scheduled servicing

A maintenance schedule generates recurring work: rinse and inspect wetsuits
weekly, check leash swivels monthly, service the van before the season, inspect
the rescue board and the first-aid kit on a fixed interval. Scheduled work
appears before it is due, so it can be done on a quiet morning rather than in a
crisis.

## Coming back into service

Closing a maintenance record asks for the resulting condition. Set it honestly —
"good" and "fair" mean different things for a beginner group and a demo day — and
the item returns to the available pool. If it is beyond repair, retire it rather
than deleting it: the purchase cost, the repair history and the reason it died
are exactly what next season's equipment budget is built from.
""",
                "body_tr": """
Bakım iki farklı şeyi kapsar: bir şey kırıldığı için yapılan onarım ve zaman
geçtiği için yapılan periyodik bakım. İkisi de burada durur ve birlikte tutulmaları
ekipman bütçesini öngörülebilir kılar.

## Sorun bildirmek

Ekipmana dokunan herkes bakım kaydı açabilir — eğitmenler ve kiralama personeli
dâhil. Neyin bozuk olduğunu yazın, hasar türünü seçin (ding, çatlak, fin, leash,
mayo yırtığı, fermuar, delaminasyon, kırılma) ve fotoğraf ekleyin. Eşyanın durumu
anında bakıma geçer ve tüm rezervasyon ile kiralama ekranlarından çıkar. Amaç
budur: çatlak bir tahta, birileri fırsat bulduğunda değil, bildirildiği dakikada
rezerve edilemez hâle gelmelidir.

## Kuyruğu yönetmek

Kuyruk, önem derecesine ve bekleme süresine göre sıralanır. Her kayıt; yapılan işi,
kullanılan parçaları, maliyeti ve işi kimin yaptığını (kendi atölyemiz veya dış
servis) taşır. Maliyeti kaydetmek, "bu tahtayı onarmak hâlâ yenisini almaktan ucuz
mu?" sorusunu yanıtlar — her okulun bir sezon geç sorduğu soru.

## Planlı bakım

Bakım planı tekrarlayan işleri üretir: mayoları haftalık durulama ve kontrol,
leash döner başlıklarını aylık kontrol, minibüsün sezon öncesi bakımı, kurtarma
tahtası ve ilk yardım çantasının sabit aralıkla denetimi. Planlı işler zamanı
gelmeden görünür; böylece kriz anında değil sakin bir sabahta yapılabilir.

## Yeniden hizmete alma

Bakım kaydını kapatırken ortaya çıkan kondisyon sorulur. Dürüstçe girin — "iyi" ile
"orta", başlangıç grubu ve deneme günü için farklı anlamlar taşır — ve eşya müsait
havuzuna döner. Onarılamaz durumdaysa silmek yerine emekliye ayırın: alım maliyeti,
onarım geçmişi ve neden bittiği, gelecek sezonun ekipman bütçesinin dayanağıdır.
""",
            }
        ],
    },
    # =============================================================== finance
    {
        "code": "finance",
        "name_en": "Finance",
        "name_tr": "Finans",
        "icon": "wallet",
        "sort_order": 110,
        "articles": [
            {
                "slug": "invoices-and-payments",
                "related_module": "finance",
                "sort_order": 10,
                "keywords": "invoice, payment, refund, package, fatura, ödeme, iade, paket",
                "title_en": "Invoices, payments and refunds",
                "title_tr": "Faturalar, ödemeler ve iadeler",
                "body_en": """
Money is recorded as exact decimal amounts, never as rounded floating point, and
every movement is attached to something real — a booking, a rental, a camp place,
a shop sale. There is no screen anywhere that changes a balance without leaving a
trace.

## Invoices

An invoice has lines, each line pointing at what was sold. Create it from the
booking rather than from a blank form and the customer, the amounts and the
description come across correctly. Payment status runs Unpaid, Partially paid,
Paid, Refunded, Overdue, and it is derived from the payments recorded against the
invoice — you do not set it by hand.

## Taking a payment

Record the method: cash, card, bank transfer, online, package or voucher. The
method matters at the end of the day, when the cash drawer is counted against
what the system says should be in it. A partial payment is normal — take the
deposit now and the balance at check-in, and the invoice tracks the remainder
itself.

## Packages

A lesson package is bought once and drawn down over a season. Selling a
ten-lesson package creates a customer package with a balance; using it as the
payment method on a booking decrements it. The balance is visible on the customer
record, which is what lets reception answer "how many have I got left?" without
opening a spreadsheet.

## Refunds

Refunding requires an explicit capability that most roles do not hold. The refund
references the original payment, records who authorised it and why, and appears
in the audit log as a sensitive action that is retained for compliance. Never
issue a refund by recording a negative payment — the reports and the tax figures
both depend on refunds being their own kind of record.
""",
                "body_tr": """
Para, yuvarlanmış kayan noktalı sayılarla değil, kesin ondalık tutarlarla
kaydedilir ve her hareket gerçek bir şeye bağlıdır: bir rezervasyon, kiralama,
kamp yeri veya mağaza satışı. Sistemde bakiyeyi iz bırakmadan değiştiren hiçbir
ekran yoktur.

## Faturalar

Fatura satırlardan oluşur ve her satır satılan şeyi işaret eder. Boş bir formdan
değil rezervasyondan oluşturun; böylece müşteri, tutarlar ve açıklama doğru
aktarılır. Ödeme durumu Ödenmedi, Kısmen ödendi, Ödendi, İade edildi, Gecikmiş
şeklinde ilerler ve faturaya kaydedilen ödemelerden türetilir — elle
ayarlanmaz.

## Ödeme almak

Yöntemi kaydedin: nakit, kart, havale, çevrimiçi, paket veya hediye çeki. Yöntem
gün sonunda önem kazanır; kasa sayılıp sistemin söylediğiyle karşılaştırılır. Kısmi
ödeme normaldir — kaporayı şimdi, kalanı girişte alın; fatura kalan tutarı kendisi
takip eder.

## Paketler

Ders paketi bir kez satın alınır ve sezon boyunca kullanılır. On derslik bir paket
satmak, bakiyesi olan bir müşteri paketi oluşturur; rezervasyonda ödeme yöntemi
olarak seçmek bakiyeden düşer. Bakiye müşteri kaydında görünür — resepsiyonun
"kaç dersim kaldı?" sorusunu tablo açmadan yanıtlamasını sağlayan şey budur.

## İadeler

İade, çoğu rolde bulunmayan açık bir yetki gerektirir. İade; orijinal ödemeye atıfta
bulunur, kimin neden onayladığını kaydeder ve uyum gereği saklanan hassas bir işlem
olarak denetim günlüğünde görünür. İadeyi asla eksi bir ödeme girerek yapmayın —
hem raporlar hem vergi rakamları, iadelerin kendine ait bir kayıt türü olmasına
bağlıdır.
""",
            },
            {
                "slug": "expenses-and-commission",
                "related_module": "finance",
                "sort_order": 20,
                "keywords": "expense, commission, cost, payroll, gider, komisyon, maliyet",
                "title_en": "Expenses and instructor commission",
                "title_tr": "Giderler ve eğitmen komisyonu",
                "body_en": """
Revenue on its own tells you almost nothing about a surf school. The interesting
number is what is left after the wax, the wetsuit repairs, the van diesel, the
beach licence and the instructors' commission.

## Recording an expense

Every expense belongs to a category — equipment, maintenance, fuel, rent,
salaries, marketing, insurance, licences — and to a date. Attach the receipt as a
document on the expense record; a photograph taken at the counter is worth more
in March than a promise to file it later. Recurring costs should be entered as
they fall rather than as one annual lump, otherwise every monthly comparison is
meaningless.

## Commission

If instructors are paid a share of the lessons they teach, the rate lives on the
instructor record and a commission record is generated from completed lessons.
Nobody retypes an amount, which removes both the arithmetic mistakes and the
awkward conversations about them. Commission is only visible to roles holding the
commission capability.

## Reading the result

The finance dashboard shows income, expenses and the balance for the selected
period against the same length of period before it. Look at the ratio, not the
total: a July that earned twice as much as June while spending three times as
much is not a good July, and the tiles are laid out so that is immediately
obvious.

## Closing a period

Before closing a month, check three things: every completed lesson has an invoice,
every invoice is either paid or genuinely outstanding, and every cash payment has
a matching entry. The overdue list is the fastest way to find the first; the
audit log is how you find who to ask about the third.
""",
                "body_tr": """
Ciro tek başına bir sörf okulu hakkında neredeyse hiçbir şey söylemez. Asıl önemli
sayı; vaks, mayo tamiri, minibüs mazotu, plaj ruhsatı ve eğitmen komisyonundan
sonra geriye kalandır.

## Gider kaydetmek

Her gider bir kategoriye — ekipman, bakım, yakıt, kira, maaş, pazarlama, sigorta,
ruhsat — ve bir tarihe aittir. Fişi gider kaydına belge olarak ekleyin; tezgâhta
çekilmiş bir fotoğraf, mart ayında "sonra dosyalarım" sözünden çok daha değerlidir.
Tekrarlayan maliyetleri yıllık tek kalem yerine gerçekleştikçe girin; aksi hâlde
her aylık karşılaştırma anlamsızlaşır.

## Komisyon

Eğitmenlere verdikleri derslerden pay ödeniyorsa oran eğitmen kaydında durur ve
tamamlanan derslerden komisyon kaydı üretilir. Kimse tutarı yeniden yazmaz; bu hem
hesap hatalarını hem de o hataların yol açtığı sıkıntılı konuşmaları ortadan
kaldırır. Komisyon yalnızca komisyon yetkisi olan rollere görünür.

## Sonucu okumak

Finans paneli; seçilen dönemin gelirini, giderini ve bakiyesini, hemen öncesindeki
eşit uzunluktaki dönemle karşılaştırarak gösterir. Toplama değil orana bakın:
haziranın iki katı kazanırken üç katı harcayan bir temmuz iyi bir temmuz değildir
ve kutucuklar bunu anında görebilesiniz diye yerleştirilmiştir.

## Dönem kapatmak

Bir ayı kapatmadan önce üç şeyi kontrol edin: tamamlanan her dersin faturası var mı,
her fatura ya ödenmiş ya gerçekten açık mı ve her nakit ödemenin karşılığı girilmiş
mi. Birincisini bulmanın en hızlı yolu gecikmiş listesidir; üçüncüsünü kime
soracağınızı ise denetim günlüğü söyler.
""",
            },
        ],
    },
    # =============================================================== reports
    {
        "code": "reports",
        "name_en": "Reports",
        "name_tr": "Raporlar",
        "icon": "file-text",
        "sort_order": 120,
        "articles": [
            {
                "slug": "building-a-report",
                "related_module": "reporting",
                "sort_order": 10,
                "keywords": "report, export, excel, pdf, schedule, rapor, dışa aktarma",
                "title_en": "Building and exporting a report",
                "title_tr": "Rapor oluşturmak ve dışa aktarmak",
                "body_en": """
Reports answer questions that need thinking about, and they are separated from
the dashboard for exactly that reason. A report definition is saved, so the
question you asked in August can be asked again in identical form next August.

## Defining a report

Pick the subject — bookings, lessons, revenue, expenses, equipment utilisation,
instructor performance, customer retention — then the period and the filters. Save
the definition with a name a colleague will understand: "Weekend group lessons,
by instructor" beats "report 3". Every generated run is kept, so you can see the
figures as they stood when a decision was made, not as they look after later
corrections.

## Periods and comparison

Use the standard periods (today, 7, 30, 90, 180, 365 days, or a custom range).
The comparison against the previous equal-length period is calculated for you.
Comparing a 31-day month against a 28-day month by hand is where most seasonal
"growth" comes from, and the system deliberately does not let that happen.

## Exporting

Export to Excel for anything that will be worked on further, to CSV for anything
that goes into another system, and to PDF for anything that will be read as-is —
an accountant's pack, a board summary, a lender's file. Exports are a
capability-gated action and each export is written to the audit log, because a
full customer list leaving the building is a data-protection event whether or not
it was innocent.

## Scheduling

A saved report can run on a schedule and land in the notifications of the people
who need it. A Monday-morning report that everybody reads beats a perfect
dashboard that nobody opens.
""",
                "body_tr": """
Raporlar, üzerinde düşünmek gereken soruları yanıtlar ve tam bu nedenle kontrol
panelinden ayrılmıştır. Rapor tanımı kaydedilir; böylece ağustosta sorduğunuz soru
gelecek ağustos aynı biçimde tekrar sorulabilir.

## Rapor tanımlamak

Konuyu seçin — rezervasyonlar, dersler, gelir, giderler, ekipman kullanımı, eğitmen
performansı, müşteri sadakati — sonra dönemi ve filtreleri belirleyin. Tanımı, bir
meslektaşınızın anlayacağı bir adla kaydedin: "Hafta sonu grup dersleri, eğitmen
bazında", "rapor 3"ten iyidir. Üretilen her çalıştırma saklanır; böylece rakamları
karar verildiği andaki hâliyle görebilirsiniz, sonraki düzeltmelerden sonraki
hâliyle değil.

## Dönemler ve karşılaştırma

Standart dönemleri kullanın (bugün, 7, 30, 90, 180, 365 gün veya özel aralık).
Önceki eşit uzunluktaki dönemle karşılaştırma sizin için hesaplanır. 31 günlük bir
ayı 28 günlük bir ayla elle karşılaştırmak, mevsimsel "büyüme"nin çoğunun
kaynağıdır ve sistem buna bilerek izin vermez.

## Dışa aktarma

Üzerinde çalışılacak her şey için Excel'e, başka bir sisteme girecek her şey için
CSV'ye, olduğu gibi okunacak her şey için PDF'e aktarın — mali müşavir dosyası,
yönetim özeti, kredi başvurusu. Dışa aktarma yetkiye bağlı bir işlemdir ve her
aktarım denetim günlüğüne yazılır; çünkü tam bir müşteri listesinin binadan çıkması,
iyi niyetli olsun olmasın bir veri koruma olayıdır.

## Zamanlama

Kaydedilmiş bir rapor belirli aralıklarla çalışıp ihtiyacı olanların bildirimlerine
düşebilir. Herkesin okuduğu bir pazartesi sabahı raporu, kimsenin açmadığı kusursuz
bir panelden iyidir.
""",
            }
        ],
    },
    # ================================================================ backup
    {
        "code": "backup",
        "name_en": "Backup",
        "name_tr": "Yedekleme",
        "icon": "database-backup",
        "sort_order": 130,
        "articles": [
            {
                "slug": "backup-and-restore",
                "related_module": "backups",
                "sort_order": 10,
                "keywords": "backup, restore, disaster, retention, yedek, geri yükleme",
                "title_en": "Taking a backup and restoring one",
                "title_tr": "Yedek almak ve geri yüklemek",
                "body_en": """
Everything the school knows — customers, waivers, incident reports, invoices —
lives in one database. A backup is the only thing standing between a failed disk
and starting the season again from paper.

## What a backup contains

A backup holds the database and, if enabled, the uploaded media: waivers,
certificates, equipment photos, incident photographs. Media is usually the bulk
of the size, which is why the setting exists — but a backup without the signed
waivers is not a backup of a surf school.

## Taking one

Run a backup from the Backup & Restore screen. Each run is recorded with its
size, duration and result, so a backup that has been silently failing for three
weeks is visible rather than assumed. Retention keeps daily, weekly and monthly
copies on a rolling window; older ones are removed automatically so the disk does
not fill in August.

## Where the copy lives

A backup on the same machine as the database protects you from a mistake, not
from a fire, a theft or ransomware. Copy backups off the machine — an external
disk taken home, or object storage — and do it on a schedule somebody is
responsible for. This is the single most valuable ten minutes a week anyone in
the school spends.

## Restoring

Restore is a privileged action that no ordinary role holds, and for good reason:
it overwrites current data with older data. Before restoring, take a fresh backup
of the current state, so a bad restore is itself reversible. Restores are logged
as sensitive actions and retained.

## Test it

A backup that has never been restored is a hypothesis. Once a season, restore
into a scratch copy and check that a recent booking, an uploaded waiver and an
invoice are all there. Ten minutes of testing turns a hope into a fact.
""",
                "body_tr": """
Okulun bildiği her şey — müşteriler, sorumluluk formları, olay raporları, faturalar
— tek bir veritabanında durur. Yedek, bozulan bir diskle sezona kâğıttan yeniden
başlamak arasındaki tek şeydir.

## Yedeğin içeriği

Yedek; veritabanını ve etkinse yüklenen medyayı içerir: sorumluluk formları,
sertifikalar, ekipman fotoğrafları, olay fotoğrafları. Boyutun büyük kısmı genelde
medyadır; ayarın var olma nedeni budur — ama imzalı formları içermeyen bir yedek,
bir sörf okulunun yedeği değildir.

## Yedek almak

Yedeklemeyi Yedekleme ve Geri Yükleme ekranından çalıştırın. Her çalıştırma boyutu,
süresi ve sonucuyla kaydedilir; böylece üç haftadır sessizce başarısız olan bir
yedekleme varsayım değil görünür bir gerçektir. Saklama politikası günlük, haftalık
ve aylık kopyaları kayan bir pencerede tutar; eskiler otomatik silinir ki ağustosta
disk dolmasın.

## Kopyanın yeri

Veritabanıyla aynı makinedeki bir yedek sizi hatadan korur; yangından, hırsızlıktan
veya fidye yazılımından korumaz. Yedekleri makine dışına kopyalayın — eve götürülen
harici disk veya nesne depolama — ve bunu sorumlusu belli bir program dâhilinde
yapın. Okuldaki herhangi birinin haftada harcadığı en değerli on dakikadır.

## Geri yükleme

Geri yükleme, hiçbir sıradan rolün sahip olmadığı ayrıcalıklı bir işlemdir ve bunun
iyi bir nedeni vardır: güncel veriyi eski veriyle değiştirir. Geri yüklemeden önce
mevcut durumun taze bir yedeğini alın; böylece hatalı bir geri yükleme de geri
alınabilir olur. Geri yüklemeler hassas işlem olarak kaydedilir ve saklanır.

## Test edin

Hiç geri yüklenmemiş bir yedek yalnızca bir varsayımdır. Sezonda bir kez, ayrı bir
kopyaya geri yükleyin ve yakın tarihli bir rezervasyonun, yüklenmiş bir sorumluluk
formunun ve bir faturanın orada olduğunu kontrol edin. On dakikalık test, bir
umudu gerçeğe çevirir.
""",
            }
        ],
    },
    # ========================================================== ai assistant
    {
        "code": "ai-assistant",
        "name_en": "AI Assistant",
        "name_tr": "Yapay zekâ asistanı",
        "icon": "sparkles",
        "sort_order": 140,
        "articles": [
            {
                "slug": "using-the-ai-assistant",
                "related_module": "ai",
                "sort_order": 10,
                "keywords": "ai, assistant, chat, recommendation, yapay zeka, asistan, öneri",
                "title_en": "Asking the AI assistant",
                "title_tr": "Yapay zekâ asistanına sormak",
                "body_en": """
The assistant reads the school's own data and answers questions in plain
language: which boards are idle this week, which customers have not booked since
last season, whether tomorrow's forecast suits a beginner group. It is a fast way
to get a first look at something — and it is not a decision-maker.

## What it is good at

Summarising, drafting and finding. "Summarise this month's incidents", "draft a
reminder e-mail for customers with an unpaid balance", "which spots suit an
Advanced Beginner group in an offshore wind at low tide". It answers from the
records the system holds, and it respects your capabilities — it will not surface
figures your role cannot open elsewhere.

## Reading its answers

Everything the assistant produces is shown on a distinct background and carries
an "AI Recommendation" chip. That marking is not decoration: it is the boundary
between the system of record and a model's opinion. Before acting on a number,
open the screen it came from. Before sending drafted text to a customer, read it
as if you had written it, because as far as the customer is concerned you did.

## The safety rule

**The AI is never the final authority on a safety decision.** It may recommend
postponing a session, flag that conditions look marginal for a group's level or
notice an expired certification — and a named staff member must approve any of
those calls before it changes what happens in the water. This is a rule of the
system, not a matter of preference, and the approval is recorded with the name of
whoever gave it.

## Local and cloud models, and cost

The school can run a local model, a cloud provider, or route automatically
between them. Local means nothing leaves the building, which is the right default
for anything touching customer or medical data. Cloud calls cost money, and the
AI Usage screen shows what was spent, by whom and on what.
""",
                "body_tr": """
Asistan, okulun kendi verilerini okur ve soruları gündelik dille yanıtlar: bu hafta
hangi tahtalar boşta, hangi müşteriler geçen sezondan beri rezervasyon yapmadı,
yarınki tahmin başlangıç grubuna uygun mu. Bir konuya ilk bakışı hızlandırır — ve
karar verici değildir.

## İyi olduğu işler

Özetlemek, taslak yazmak ve bulmak. "Bu ayın olaylarını özetle", "ödenmemiş
bakiyesi olan müşteriler için hatırlatma e-postası taslağı hazırla", "alçak gelgitte
kara rüzgârında İleri Başlangıç grubuna hangi noktalar uygun". Yanıtlarını sistemin
tuttuğu kayıtlardan üretir ve yetkilerinize saygı gösterir — rolünüzün başka yerde
açamayacağı rakamları önünüze getirmez.

## Yanıtlarını okumak

Asistanın ürettiği her şey ayrı bir zeminde gösterilir ve "AI Önerisi" etiketi
taşır. Bu işaret süs değildir: kayıt sistemi ile bir modelin görüşü arasındaki
sınırdır. Bir rakama göre hareket etmeden önce geldiği ekranı açın. Taslak bir
metni müşteriye göndermeden önce kendiniz yazmışsınız gibi okuyun; çünkü müşteri
açısından siz yazdınız.

## Güvenlik kuralı

**Yapay zekâ hiçbir güvenlik kararında son merci değildir.** Bir seansın
ertelenmesini önerebilir, koşulların grubun seviyesi için sınırda göründüğünü
belirtebilir veya süresi dolmuş bir sertifikayı fark edebilir — ancak bunların
suda olan biteni değiştirmesi için adı belli bir personelin onaylaması gerekir. Bu,
tercih değil sistemin kuralıdır ve onay, veren kişinin adıyla kaydedilir.

## Yerel ve bulut modeller, maliyet

Okul yerel bir model, bir bulut sağlayıcı veya ikisi arasında otomatik yönlendirme
kullanabilir. Yerel model, hiçbir verinin binadan çıkmaması demektir; müşteri veya
sağlık verisine dokunan her şey için doğru varsayılan budur. Bulut çağrıları para
harcar ve AI Kullanım ekranı neyin, kim tarafından, ne için harcandığını gösterir.
""",
            }
        ],
    },
    # =========================================================== ai terminal
    {
        "code": "ai-terminal",
        "name_en": "AI Terminal",
        "name_tr": "AI terminali",
        "icon": "terminal",
        "sort_order": 150,
        "articles": [
            {
                "slug": "ai-development-terminal",
                "related_module": "ai_terminal",
                "sort_order": 10,
                "keywords": "terminal, command, approve, patch, developer, komut, onay",
                "title_en": "The AI development terminal",
                "title_tr": "AI geliştirme terminali",
                "body_en": """
The AI development terminal lets a technical operator ask the model to inspect
the installation and propose changes to it. It is the most powerful screen in the
product and the one with the narrowest audience: if you are not responsible for
maintaining this installation, you do not need it.

## How it is fenced in

Commands run inside a fixed workspace directory, against an allow-list, with a
time limit and a cap on how much output is returned. Anything outside the
allow-list is refused rather than run and reported. The unsafe override that
disables the allow-list exists for a developer's own machine and must stay off
anywhere else — turning it on hands a language model a shell on the school's
server.

## Approval, not autonomy

The model proposes; a human approves. Executing a command and applying a code
change are separate privileged capabilities, and neither is granted implicitly to
any role. Every proposal shows the exact command or patch before you approve it.
Read it. "It looked like a migration" is not a defence when the school's booking
table is empty.

## Sessions and history

Work happens in a session so a sequence of related commands stays together with
its output. Every command, its result and every approval is written to the audit
log and retained — this is one of the sensitive action types that is never pruned.

## When to use it and when not to

Use it to diagnose: read a log, check a migration state, inspect a configuration
value, explain an error. Do not use it during business hours to change a running
system. Take a backup first, work outside the season's peak, and have a way back.
If you would not do it by hand on a Saturday morning in August, do not ask a model
to do it for you.
""",
                "body_tr": """
AI geliştirme terminali, teknik bir sorumlunun modelden kurulumu incelemesini ve
üzerinde değişiklik önermesini istemesine izin verir. Üründeki en güçlü ve hedef
kitlesi en dar ekrandır: bu kurulumun bakımından sorumlu değilseniz ihtiyacınız
yoktur.

## Nasıl sınırlandırılmıştır

Komutlar sabit bir çalışma dizini içinde, bir izin listesine karşı, süre sınırı ve
döndürülen çıktı miktarı sınırıyla çalışır. İzin listesi dışındaki her şey
çalıştırılıp raporlanmaz, reddedilir. İzin listesini devre dışı bırakan güvensiz
mod geliştiricinin kendi makinesi içindir ve başka her yerde kapalı kalmalıdır —
açmak, bir dil modeline okulun sunucusunda kabuk erişimi vermek demektir.

## Onay, özerklik değil

Model önerir; insan onaylar. Komut çalıştırmak ve kod değişikliği uygulamak ayrı
ayrıcalıklı yetkilerdir ve hiçbir role kendiliğinden verilmez. Her öneri,
onaylamadan önce tam komutu veya yamayı gösterir. Okuyun. Okulun rezervasyon tablosu
boşaldığında "migration gibi görünüyordu" bir savunma değildir.

## Oturumlar ve geçmiş

İş bir oturum içinde yürür; böylece birbirine bağlı komut dizisi çıktısıyla birlikte
durur. Her komut, sonucu ve her onay denetim günlüğüne yazılır ve saklanır — bu,
hiçbir zaman budanmayan hassas işlem türlerinden biridir.

## Ne zaman kullanılır, ne zaman kullanılmaz

Teşhis için kullanın: günlük okuma, migration durumu kontrolü, yapılandırma değeri
inceleme, hata açıklama. Çalışan bir sistemi mesai saatlerinde değiştirmek için
kullanmayın. Önce yedek alın, sezonun yoğun saatleri dışında çalışın ve geri dönüş
yolunuz olsun. Ağustosta bir cumartesi sabahı elle yapmayacağınız bir şeyi bir
modelden de istemeyin.
""",
            }
        ],
    },
    # ============================================================== settings
    {
        "code": "settings",
        "name_en": "Settings",
        "name_tr": "Ayarlar",
        "icon": "settings",
        "sort_order": 160,
        "articles": [
            {
                "slug": "school-settings",
                "related_module": "settings",
                "sort_order": 10,
                "keywords": "settings, currency, timezone, school name, ayar, para birimi, saat dilimi",
                "title_en": "School settings",
                "title_tr": "Okul ayarları",
                "body_en": """
Settings hold the facts about *this* school that every other screen reads: the
name on invoices, the currency, the timezone, the default language and the
primary surf spot. Most were set by the onboarding wizard on the first day; this
screen is where they are corrected later.

## Currency and timezone

Currency affects every amount displayed and every report. Changing it does not
convert existing figures — a price recorded as 1200 stays 1200 with a different
symbol in front of it — so change it only if it was wrong from the start, and
audit the historical figures if you do. Timezone decides what "today" means for
lesson times, the dashboard and every scheduled job. Set it once, to where the
school physically is.

## The default surf spot

Exactly one spot is the default, and it is used whenever a lesson, camp day or
rental does not name one. Surf conditions and the forecast on the dashboard are
fetched for that spot. If the school moves its main operation to another break
for a season, move the default flag rather than renaming the spot — the history
attached to the old record has to stay attached to it.

## Secrets

API keys and passwords are never stored here in readable form. They come from the
server's environment configuration, they are masked in the interface and they are
redacted from the logs. If a screen ever shows you a full key, treat that as a
defect and report it.

## Who can change what

Viewing settings is broadly available; changing them is not, and the privileged
settings capability is deliberately narrow. Every settings change is written to
the audit log as a sensitive action, because "the currency changed and nobody
knows when" is a genuinely expensive question to answer afterwards.
""",
                "body_tr": """
Ayarlar, diğer tüm ekranların okuduğu *bu* okula ait bilgileri tutar: faturalardaki
ad, para birimi, saat dilimi, varsayılan dil ve birincil sörf noktası. Çoğu ilk gün
kurulum sihirbazı tarafından ayarlanmıştır; bu ekran onların sonradan düzeltildiği
yerdir.

## Para birimi ve saat dilimi

Para birimi, gösterilen her tutarı ve her raporu etkiler. Değiştirmek mevcut
rakamları dönüştürmez — 1200 olarak kaydedilmiş bir fiyat, önünde farklı bir sembolle
1200 kalır — bu yüzden yalnızca baştan yanlışsa değiştirin ve değiştirirseniz geçmiş
rakamları gözden geçirin. Saat dilimi; ders saatleri, kontrol paneli ve zamanlanmış
görevler için "bugün"ün ne demek olduğunu belirler. Bir kez, okulun fiziksel olarak
bulunduğu yere göre ayarlayın.

## Varsayılan sörf noktası

Tam olarak bir nokta varsayılandır ve bir ders, kamp günü veya kiralama nokta
belirtmediğinde o kullanılır. Deniz koşulları ve panel tahmini bu nokta için
çekilir. Okul bir sezonluğuna ana operasyonunu başka bir kırılıma taşıyorsa noktayı
yeniden adlandırmak yerine varsayılan işaretini taşıyın — eski kayda bağlı geçmişin
o kayda bağlı kalması gerekir.

## Gizli bilgiler

API anahtarları ve şifreler burada hiçbir zaman okunabilir biçimde saklanmaz.
Sunucunun ortam yapılandırmasından gelir, arayüzde maskelenir ve günlüklerden
temizlenir. Bir ekran size tam bir anahtarı gösteriyorsa bunu bir hata olarak kabul
edip bildirin.

## Kim neyi değiştirebilir

Ayarları görüntülemek geniş bir kesime açıktır; değiştirmek değildir ve ayrıcalıklı
ayar yetkisi bilinçli olarak dardır. Her ayar değişikliği hassas işlem olarak
denetim günlüğüne yazılır; çünkü "para birimi değişmiş ama ne zaman belli değil"
sorusunu sonradan yanıtlamak gerçekten pahalıdır.
""",
            }
        ],
    },
]


class Command(BaseCommand):
    help = "Load the built-in Help Center categories and articles (EN + TR)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--update",
            action="store_true",
            help=(
                "Overwrite existing categories and articles with the shipped text. "
                "Without this flag, locally edited content is left untouched."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        update_existing: bool = options["update"]

        created_categories = updated_categories = 0
        created_articles = updated_articles = skipped_articles = 0

        for category_data in CATEGORIES:
            articles = category_data["articles"]
            defaults = {
                "name_en": category_data["name_en"],
                "name_tr": category_data["name_tr"],
                "icon": category_data["icon"],
                "sort_order": category_data["sort_order"],
                "is_active": True,
            }

            category = HelpCategory.objects.filter(code=category_data["code"]).first()
            if category is None:
                category = HelpCategory.objects.create(code=category_data["code"], **defaults)
                created_categories += 1
            elif update_existing:
                for field, value in defaults.items():
                    setattr(category, field, value)
                category.save(update_fields=[*defaults.keys(), "updated_at"])
                updated_categories += 1

            for article_data in articles:
                article_defaults = {
                    "category": category,
                    "title_en": article_data["title_en"].strip(),
                    "title_tr": article_data["title_tr"].strip(),
                    "body_en": article_data["body_en"].strip(),
                    "body_tr": article_data["body_tr"].strip(),
                    "keywords": article_data["keywords"],
                    "related_module": article_data["related_module"],
                    "sort_order": article_data["sort_order"],
                    "is_published": True,
                }

                article = HelpArticle.all_objects.filter(slug=article_data["slug"]).first()
                if article is None:
                    HelpArticle.objects.create(slug=article_data["slug"], **article_defaults)
                    created_articles += 1
                elif update_existing:
                    for field, value in article_defaults.items():
                        setattr(article, field, value)
                    # A previously archived article comes back when re-seeded.
                    article.is_deleted = False
                    article.deleted_at = None
                    article.save()
                    updated_articles += 1
                else:
                    skipped_articles += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Help content loaded: {created_categories} categories created, "
                f"{updated_categories} updated; {created_articles} articles created, "
                f"{updated_articles} updated, {skipped_articles} left as-is."
            )
        )
        if skipped_articles and not update_existing:
            self.stdout.write(
                "Existing articles were kept. Re-run with --update to replace them "
                "with the shipped text."
            )
