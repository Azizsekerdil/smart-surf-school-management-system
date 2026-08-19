"""Load the shipped Training Center courses.

    .\\.venv\\Scripts\\python.exe manage.py seed_training_content
    .\\.venv\\Scripts\\python.exe manage.py seed_training_content --update

Courses are keyed on ``code``, lessons on ``(course, order)`` and steps on
``(lesson, order)``, so the command is safe to run again. As with the Help
Center seeder it *creates* by default and only overwrites with ``--update``: a
school edits these steps to match its own counter, and a redeploy must not throw
that away.

Each course walks one real task end to end. ``target_url`` holds the URL name of
the screen the step is about; a name that does not resolve in this deployment
simply renders without a link (see ``TrainingStep.target_link``).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.training.models import Difficulty, TrainingCourse, TrainingLesson, TrainingStep

COURSES: list[dict] = [
    # ================================================== 1. first student
    {
        "code": "first-student",
        "icon": "graduation-cap",
        "difficulty": Difficulty.BEGINNER,
        "estimated_minutes": 12,
        "required_capability": "students.view",
        "sort_order": 10,
        "title_en": "Create your first student",
        "title_tr": "İlk öğrencinizi oluşturun",
        "description_en": (
            "From a person standing at the counter to a student record that is safe to "
            "put in the water: customer, student, medical flags, level and waiver."
        ),
        "description_tr": (
            "Tezgâhın önündeki bir kişiden, suya sokulması güvenli bir öğrenci kaydına: "
            "müşteri, öğrenci, sağlık uyarıları, seviye ve sorumluluk formu."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 5,
                "title_en": "Create the customer",
                "title_tr": "Müşteriyi oluşturun",
                "summary_en": "The customer is who pays. Everything else hangs off this record.",
                "summary_tr": "Müşteri ödeyen kişidir. Diğer her şey bu kayda bağlanır.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "customers:list",
                        "title_en": "Open the Customers screen",
                        "title_tr": "Müşteriler ekranını açın",
                        "action_hint_en": "Sidebar → Operations → Customers",
                        "action_hint_tr": "Kenar çubuğu → Operasyon → Müşteriler",
                        "body_en": (
                            "Before creating anybody, **search first**. Half of all duplicate "
                            "customers are created by somebody who did not check whether the "
                            "family came last summer. Type the surname or the phone number."
                        ),
                        "body_tr": (
                            "Kimseyi oluşturmadan önce **arayın**. Mükerrer müşteri kayıtlarının "
                            "yarısı, ailenin geçen yaz gelip gelmediğine bakmayan biri tarafından "
                            "yaratılır. Soyadı veya telefon numarasını yazın."
                        ),
                    },
                    {
                        "order": 2,
                        "target_url": "customers:create",
                        "title_en": "Add the customer",
                        "title_tr": "Müşteriyi ekleyin",
                        "action_hint_en": "Press “New customer”",
                        "action_hint_tr": "“Yeni müşteri” düğmesine basın",
                        "body_en": (
                            "Name, phone and e-mail are the minimum. The phone number is what you "
                            "will actually use — a board that has not come back at 19:00 is found "
                            "by telephone, not by e-mail.\n\n"
                            "Record their preferred language too; confirmations and reminders go "
                            "out in it."
                        ),
                        "body_tr": (
                            "Ad, telefon ve e-posta asgari bilgilerdir. Gerçekte kullanacağınız "
                            "bilgi telefondur — saat 19:00'da dönmeyen bir tahta e-postayla değil "
                            "telefonla bulunur.\n\n"
                            "Tercih ettikleri dili de kaydedin; onay ve hatırlatmalar o dilde gider."
                        ),
                    },
                    {
                        "order": 3,
                        "target_url": "customers:list",
                        "title_en": "Check what you created",
                        "title_tr": "Oluşturduğunuzu kontrol edin",
                        "body_en": (
                            "Open the record you just saved. You should see an empty student list, "
                            "no bookings and no balance. That is the correct starting state — the "
                            "customer exists, but nobody can go in the water yet."
                        ),
                        "body_tr": (
                            "Az önce kaydettiğiniz kaydı açın. Boş bir öğrenci listesi, rezervasyon "
                            "ve bakiye görmemelisiniz. Doğru başlangıç durumu budur — müşteri var, "
                            "ama henüz kimse suya giremez."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 7,
                "title_en": "Add the student",
                "title_tr": "Öğrenciyi ekleyin",
                "summary_en": "The student is who surfs — with the details that keep them safe.",
                "summary_tr": "Öğrenci sörf yapan kişidir — güvenliğini sağlayan bilgilerle.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "students:create",
                        "title_en": "Start a new student on that customer",
                        "title_tr": "O müşteriye yeni öğrenci ekleyin",
                        "action_hint_en": "Students → New student → pick the customer",
                        "action_hint_tr": "Öğrenciler → Yeni öğrenci → müşteriyi seçin",
                        "body_en": (
                            "One customer can have several students. A parent booking for two "
                            "children is **one customer and two students** — do it that way and "
                            "the invoice, the attendance list and the waivers all line up."
                        ),
                        "body_tr": (
                            "Bir müşterinin birden fazla öğrencisi olabilir. İki çocuğu için "
                            "rezervasyon yaptıran bir ebeveyn **bir müşteri ve iki öğrencidir** — "
                            "böyle yaparsanız fatura, yoklama listesi ve formlar birbirini tutar."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Fill in date of birth and swimming ability",
                        "title_tr": "Doğum tarihi ve yüzme becerisini girin",
                        "body_en": (
                            "Both are mandatory, and neither is a formality. Age under eighteen "
                            "tightens the instructor ratio to six students; swimming ability decides "
                            "whether this person may be in water out of their depth at all."
                        ),
                        "body_tr": (
                            "İkisi de zorunludur ve hiçbiri formalite değildir. On sekiz yaş altı, "
                            "eğitmen oranını altı öğrenciye sıkılaştırır; yüzme becerisi ise bu "
                            "kişinin boyunu aşan suda bulunup bulunamayacağını belirler."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Record medical information and an emergency contact",
                        "title_tr": "Sağlık bilgisi ve acil durum kişisini kaydedin",
                        "body_en": (
                            "Asthma, epilepsy, diabetes, heart conditions, recent surgery, "
                            "medication. Write it down even when the guest waves it away — it "
                            "appears on the instructor's manifest and it is confidential.\n\n"
                            "The emergency contact must be somebody **not in the lesson**."
                        ),
                        "body_tr": (
                            "Astım, epilepsi, diyabet, kalp rahatsızlığı, yakın zamanda ameliyat, "
                            "ilaç. Misafir önemsemese bile yazın — eğitmenin listesinde görünür ve "
                            "gizlidir.\n\n"
                            "Acil durum kişisi **derste bulunmayan** biri olmalıdır."
                        ),
                    },
                    {
                        "order": 4,
                        "title_en": "Set an honest starting level",
                        "title_tr": "Gerçekçi bir başlangıç seviyesi seçin",
                        "body_en": (
                            "First Time, Beginner, Advanced Beginner, Intermediate, Advanced or "
                            "Competition. The level sets the group size, the wave height the school "
                            "will accept and the board volume. When in doubt, go one level down: "
                            "an under-called student has a good morning, an over-called one has an "
                            "incident."
                        ),
                        "body_tr": (
                            "İlk Kez, Başlangıç, İleri Başlangıç, Orta, İleri veya Yarışma. Seviye; "
                            "grup büyüklüğünü, okulun kabul edeceği dalga yüksekliğini ve tahta "
                            "hacmini belirler. Tereddüt ederseniz bir alt seviyeyi seçin: düşük "
                            "değerlendirilen öğrenci güzel bir sabah geçirir, yüksek değerlendirilen "
                            "bir olay yaşar."
                        ),
                    },
                    {
                        "order": 5,
                        "title_en": "Upload the signed waiver",
                        "title_tr": "İmzalı sorumluluk formunu yükleyin",
                        "body_en": (
                            "Photograph or scan the signed form and attach it to the student record "
                            "with an expiry date. For anyone under eighteen the signature must be "
                            "the guardian's.\n\n"
                            "A missing waiver shows as a warning at check-in — which is the last "
                            "moment it is cheap to fix."
                        ),
                        "body_tr": (
                            "İmzalı formu fotoğraflayın veya tarayın ve son geçerlilik tarihiyle "
                            "öğrenci kaydına ekleyin. On sekiz yaş altında imza velinin olmalıdır.\n\n"
                            "Eksik form, giriş sırasında uyarı olarak görünür — düzeltmenin hâlâ "
                            "ucuz olduğu son andır."
                        ),
                    },
                ],
            },
        ],
    },
    # =============================================== 2. first instructor
    {
        "code": "first-instructor",
        "icon": "user-check",
        "difficulty": Difficulty.BEGINNER,
        "estimated_minutes": 12,
        "required_capability": "instructors.view",
        "sort_order": 20,
        "title_en": "Add your first instructor",
        "title_tr": "İlk eğitmeninizi ekleyin",
        "description_en": (
            "Set up a coach so the scheduler can use them: certifications with expiry "
            "dates, weekly availability, languages and commission."
        ),
        "description_tr": (
            "Planlamanın kullanabilmesi için bir eğitmen kurun: bitiş tarihli "
            "sertifikalar, haftalık müsaitlik, diller ve komisyon."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 6,
                "title_en": "The instructor record",
                "title_tr": "Eğitmen kaydı",
                "summary_en": "Who they are and what they are qualified to teach.",
                "summary_tr": "Kim oldukları ve ne öğretmeye yetkili oldukları.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "instructors:list",
                        "title_en": "Open Instructors",
                        "title_tr": "Eğitmenler ekranını açın",
                        "action_hint_en": "Sidebar → Operations → Instructors",
                        "action_hint_tr": "Kenar çubuğu → Operasyon → Eğitmenler",
                        "body_en": (
                            "The list shows every coach with their level range and whether anything "
                            "about them has expired. Red here means the scheduler will warn before "
                            "assigning them."
                        ),
                        "body_tr": (
                            "Liste; her eğitmeni seviye aralığıyla ve süresi dolmuş bir belgesi olup "
                            "olmadığıyla gösterir. Buradaki kırmızı, planlamanın görevlendirmeden "
                            "önce uyaracağı anlamına gelir."
                        ),
                    },
                    {
                        "order": 2,
                        "target_url": "instructors:create",
                        "title_en": "Create the instructor",
                        "title_tr": "Eğitmeni oluşturun",
                        "body_en": (
                            "Name, contact details, start date and the levels they teach. Set the "
                            "maximum level honestly — a coach rated to Intermediate will not be "
                            "offered an Advanced group, and that is the point."
                        ),
                        "body_tr": (
                            "Ad, iletişim bilgileri, başlangıç tarihi ve öğrettiği seviyeler. Azami "
                            "seviyeyi dürüst girin — Orta seviyeye kadar yetkili bir eğitmene İleri "
                            "grup önerilmez; amaç da budur."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Record the languages they actually teach in",
                        "title_tr": "Gerçekten ders verdiği dilleri kaydedin",
                        "body_en": (
                            "“Conversational German” and “teaches in German” are different "
                            "promises. Reception matches families to coaches from this field, and "
                            "the guest who was promised their own language will mention it in the "
                            "review either way."
                        ),
                        "body_tr": (
                            "“Günlük Almanca” ile “Almanca ders verir” farklı vaatlerdir. Resepsiyon "
                            "aileleri eğitmenlerle bu alandan eşleştirir ve kendi dilinde ders sözü "
                            "verilen misafir her hâlükârda değerlendirmesinde bundan bahseder."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 6,
                "title_en": "Certifications and availability",
                "title_tr": "Sertifikalar ve müsaitlik",
                "summary_en": "The two things that decide whether they can be scheduled.",
                "summary_tr": "Görevlendirilip görevlendirilemeyeceğini belirleyen iki şey.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Add each certification with its expiry date",
                        "title_tr": "Her sertifikayı bitiş tarihiyle ekleyin",
                        "body_en": (
                            "Surf coaching level, lifesaving and first aid are separate records. "
                            "The expiry date is the whole value of the field: an expired "
                            "certificate raises a warning on the dashboard weeks before an insurer "
                            "would have found it."
                        ),
                        "body_tr": (
                            "Sörf antrenörlük seviyesi, cankurtaranlık ve ilk yardım ayrı "
                            "kayıtlardır. Alanın tüm değeri bitiş tarihindedir: süresi dolmuş bir "
                            "belge, sigortacının fark edeceğinden haftalar önce kontrol panelinde "
                            "uyarı üretir."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Set the weekly availability",
                        "title_tr": "Haftalık müsaitliği ayarlayın",
                        "body_en": (
                            "Which days, which hours, which spots. This is the normal pattern, not "
                            "the exception. A lesson cannot be booked against an unavailable "
                            "instructor — it is refused rather than warned about, because a soft "
                            "warning in August is a warning nobody reads."
                        ),
                        "body_tr": (
                            "Hangi günler, hangi saatler, hangi noktalar. Bu normal düzendir, "
                            "istisna değil. Müsait olmayan bir eğitmene ders yazılamaz — uyarıyla "
                            "geçiştirilmez, reddedilir; çünkü ağustostaki yumuşak bir uyarıyı kimse "
                            "okumaz."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Book known time off now",
                        "title_tr": "Bilinen izinleri şimdi girin",
                        "body_en": (
                            "Holiday, a competition, a course, a wedding. Entering it in March costs "
                            "one minute; discovering it in July costs a rescheduled group and a "
                            "refund conversation."
                        ),
                        "body_tr": (
                            "Tatil, yarışma, kurs, düğün. Martta girmek bir dakika; temmuzda fark "
                            "etmek yeniden planlanan bir grup ve bir iade görüşmesi demektir."
                        ),
                    },
                    {
                        "order": 4,
                        "title_en": "Set the commission rate if the school pays one",
                        "title_tr": "Okul komisyon ödüyorsa oranı girin",
                        "body_en": (
                            "The rate lives here and the finance module calculates the amount from "
                            "completed lessons. Nobody retypes a figure, which removes both the "
                            "arithmetic errors and the conversations about them."
                        ),
                        "body_tr": (
                            "Oran burada durur ve finans modülü tutarı tamamlanan derslerden "
                            "hesaplar. Kimse rakamı yeniden yazmaz; bu hem hesap hatalarını hem de "
                            "onlar üzerine yapılan konuşmaları ortadan kaldırır."
                        ),
                    },
                ],
            },
        ],
    },
    # =================================================== 3. first lesson
    {
        "code": "first-lesson",
        "icon": "book-open",
        "difficulty": Difficulty.BEGINNER,
        "estimated_minutes": 15,
        "required_capability": "lessons.view",
        "sort_order": 30,
        "title_en": "Schedule your first lesson",
        "title_tr": "İlk dersinizi planlayın",
        "description_en": (
            "Build a lesson type, put a session on the calendar and understand the "
            "ratio, spot and conditions checks that stand between you and the water."
        ),
        "description_tr": (
            "Bir ders türü oluşturun, takvime seans koyun ve sizinle deniz arasında "
            "duran oran, nokta ve koşul kontrollerini anlayın."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 5,
                "title_en": "Define a lesson type",
                "title_tr": "Bir ders türü tanımlayın",
                "summary_en": "The catalogue entry every session inherits from.",
                "summary_tr": "Her seansın devraldığı katalog kaydı.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "lessons:list",
                        "title_en": "Open Lessons",
                        "title_tr": "Dersler ekranını açın",
                        "action_hint_en": "Sidebar → Operations → Lessons",
                        "action_hint_tr": "Kenar çubuğu → Operasyon → Dersler",
                        "body_en": (
                            "Lesson **types** are the catalogue; lessons are the sessions on the "
                            "calendar. Create the type once and every session inherits its duration, "
                            "price and group cap."
                        ),
                        "body_tr": (
                            "Ders **türleri** katalogdur; dersler ise takvimdeki seanslardır. Türü "
                            "bir kez oluşturun; her seans süresini, fiyatını ve grup sınırını "
                            "devralsın."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Create a “Beginner group” type",
                        "title_tr": "“Başlangıç grubu” türü oluşturun",
                        "body_en": (
                            "Two hours, a group size of eight, the school's group price, level range "
                            "First Time to Advanced Beginner. Changing the price later affects "
                            "future sessions only — past lessons keep what was actually charged."
                        ),
                        "body_tr": (
                            "İki saat, sekiz kişilik grup, okulun grup fiyatı, İlk Kez'den İleri "
                            "Başlangıç'a seviye aralığı. Fiyatı sonra değiştirmek yalnızca gelecek "
                            "seansları etkiler — geçmiş dersler fiilen tahsil edileni korur."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Add a private lesson type as well",
                        "title_tr": "Bir de özel ders türü ekleyin",
                        "body_en": (
                            "Ninety minutes, group size one, the private price. Having both from the "
                            "start means reception never has to improvise a price at the counter, "
                            "which is where inconsistent pricing begins."
                        ),
                        "body_tr": (
                            "Doksan dakika, tek kişi, özel ders fiyatı. İkisinin de baştan olması, "
                            "resepsiyonun tezgâhta fiyat uydurmak zorunda kalmaması demektir — "
                            "tutarsız fiyatlandırma tam orada başlar."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 6,
                "title_en": "Put a session on the calendar",
                "title_tr": "Takvime bir seans koyun",
                "summary_en": "Time, spot, instructor, level — and what gets refused.",
                "summary_tr": "Zaman, nokta, eğitmen, seviye — ve neyin reddedildiği.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "lessons:create",
                        "title_en": "Create the lesson",
                        "title_tr": "Dersi oluşturun",
                        "body_en": (
                            "Pick the type, the date and the start time, then the instructor. Only "
                            "instructors who are available, not on time off and certified for that "
                            "level are offered."
                        ),
                        "body_tr": (
                            "Türü, tarihi ve başlangıç saatini seçin, sonra eğitmeni. Yalnızca "
                            "müsait olan, izinde olmayan ve o seviye için sertifikalı eğitmenler "
                            "önerilir."
                        ),
                    },
                    {
                        "order": 2,
                        "target_url": "locations:list",
                        "title_en": "Choose the spot",
                        "title_tr": "Noktayı seçin",
                        "body_en": (
                            "Each spot records the levels it suits and its open hazards. A spot "
                            "whose minimum level is above your group is not offered, and a spot with "
                            "an open **critical** hazard is not offered to anyone until somebody who "
                            "has been there clears it."
                        ),
                        "body_tr": (
                            "Her nokta uygun olduğu seviyeleri ve açık tehlikelerini kaydeder. "
                            "Asgari seviyesi grubunuzun üzerinde olan bir nokta önerilmez; açık "
                            "**kritik** tehlikesi olan bir nokta ise sahada birisi tehlikeyi "
                            "kaldırana kadar hiç kimseye önerilmez."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Watch the ratio guard",
                        "title_tr": "Oran kontrolünü gözleyin",
                        "body_en": (
                            "Try setting a group of ten first-timers with one instructor. The screen "
                            "refuses it: the cap is six for first-timers, eight for beginners, ten "
                            "for intermediate and above, and six whenever anybody in the group is "
                            "under eighteen. Add a second instructor instead."
                        ),
                        "body_tr": (
                            "Tek eğitmenle on kişilik ilk kez giren bir grup kurmayı deneyin. Ekran "
                            "reddeder: sınır ilk kez girenlerde altı, başlangıçta sekiz, orta ve "
                            "üzerinde on, grupta on sekiz yaş altı biri varsa daima altıdır. Bunun "
                            "yerine ikinci bir eğitmen ekleyin."
                        ),
                    },
                    {
                        "order": 4,
                        "target_url": "surf_conditions:dashboard",
                        "title_en": "Check the forecast for that slot",
                        "title_tr": "O saatin tahminini kontrol edin",
                        "body_en": (
                            "The conditions panel scores the slot for the group's level against the "
                            "wave-height and wind limits. A red score is not a veto — it is the "
                            "reason to plan a fallback spot now, while it costs nothing."
                        ),
                        "body_tr": (
                            "Koşul paneli, o saati grubun seviyesine göre dalga yüksekliği ve rüzgâr "
                            "sınırlarına karşı puanlar. Kırmızı puan bir veto değildir — hiçbir şeye "
                            "mal olmazken alternatif bir nokta planlamanın gerekçesidir."
                        ),
                    },
                ],
            },
            {
                "order": 3,
                "estimated_minutes": 4,
                "title_en": "Run it and close it",
                "title_tr": "Yürütün ve kapatın",
                "summary_en": "Check-in, real conditions, attendance, completion.",
                "summary_tr": "Giriş, gerçek koşullar, yoklama, tamamlama.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Check students in as they arrive",
                        "title_tr": "Öğrenciler geldikçe girişlerini yapın",
                        "body_en": (
                            "The check-in list shows medical flags and any missing waiver. Mark a "
                            "genuine no-show as a no-show, not a cancellation — the two mean "
                            "different things for the customer's account and the school's numbers."
                        ),
                        "body_tr": (
                            "Giriş listesi sağlık uyarılarını ve eksik formları gösterir. Gerçekten "
                            "gelmeyeni iptal değil “gelmedi” olarak işaretleyin — ikisi müşterinin "
                            "hesabı ve okulun rakamları için farklı anlamlara gelir."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Record the conditions you actually saw",
                        "title_tr": "Fiilen gördüğünüz koşulları kaydedin",
                        "body_en": (
                            "Wave height, wind, tide and water temperature at the start of the "
                            "session. An incident review asks for what was observed; last night's "
                            "forecast is not evidence."
                        ),
                        "body_tr": (
                            "Seans başındaki dalga yüksekliği, rüzgâr, gelgit ve su sıcaklığı. Bir "
                            "olay incelemesi gözlemleneni sorar; dün akşamki tahmin kanıt değildir."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Complete the lesson",
                        "title_tr": "Dersi tamamlayın",
                        "body_en": (
                            "Setting the lesson to **Completed** releases the equipment back to the "
                            "available pool, triggers the commission calculation and unlocks the "
                            "skill assessment. A lesson left in progress overnight blocks all "
                            "three — and is why a board looks missing the next morning."
                        ),
                        "body_tr": (
                            "Dersi **Tamamlandı** yapmak; ekipmanı müsait havuzuna döndürür, "
                            "komisyon hesabını tetikler ve beceri değerlendirmesini açar. Gece "
                            "boyunca “devam ediyor” kalan bir ders üçünü de engeller — ertesi sabah "
                            "bir tahtanın kayıp görünmesinin nedeni budur."
                        ),
                    },
                ],
            },
        ],
    },
    # ================================================== 4. first booking
    {
        "code": "first-booking",
        "icon": "calendar-days",
        "difficulty": Difficulty.BEGINNER,
        "estimated_minutes": 12,
        "required_capability": "bookings.view",
        "sort_order": 40,
        "title_en": "Make a booking",
        "title_tr": "Rezervasyon yapın",
        "description_en": (
            "Take a booking from the counter or the phone: capacity, deposit, "
            "confirmation, and what to do when the lesson is full."
        ),
        "description_tr": (
            "Tezgâhtan veya telefondan rezervasyon alın: kapasite, kapora, onay ve "
            "ders dolduğunda ne yapılacağı."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 6,
                "title_en": "Take the booking",
                "title_tr": "Rezervasyonu alın",
                "summary_en": "Customer, student, session, deposit, confirmation.",
                "summary_tr": "Müşteri, öğrenci, seans, kapora, onay.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "bookings:calendar",
                        "title_en": "Open the booking calendar",
                        "title_tr": "Rezervasyon takvimini açın",
                        "action_hint_en": "Sidebar → Operations → Bookings",
                        "action_hint_tr": "Kenar çubuğu → Operasyon → Rezervasyonlar",
                        "body_en": (
                            "The calendar shows every session with how many places are left. Free "
                            "capacity is calculated from the ratio for that level, not from a number "
                            "somebody typed."
                        ),
                        "body_tr": (
                            "Takvim her seansı kalan yer sayısıyla gösterir. Boş kapasite, birinin "
                            "yazdığı bir sayıdan değil o seviyenin oranından hesaplanır."
                        ),
                    },
                    {
                        "order": 2,
                        "target_url": "bookings:create",
                        "title_en": "Create the booking",
                        "title_tr": "Rezervasyonu oluşturun",
                        "body_en": (
                            "Pick the customer, then the student, then the session. For a family, "
                            "create **one booking per student** against the same customer. It looks "
                            "like more typing until three children are checked in and the fourth is "
                            "still in the car park."
                        ),
                        "body_tr": (
                            "Önce müşteriyi, sonra öğrenciyi, sonra seansı seçin. Bir aile için aynı "
                            "müşteriye bağlı **öğrenci başına bir rezervasyon** oluşturun. Üç çocuğun "
                            "girişi yapılıp dördüncüsü hâlâ otoparktayken bunun neden böyle olduğunu "
                            "anlarsınız."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Read the booking code back to the customer",
                        "title_tr": "Rezervasyon kodunu müşteriye okuyun",
                        "body_en": (
                            "Every booking gets a short code. It is what the customer will quote "
                            "when they phone, and it is far faster to find than a surname spelled "
                            "three different ways."
                        ),
                        "body_tr": (
                            "Her rezervasyon kısa bir kod alır. Müşteri telefonla aradığında "
                            "söyleyeceği şey odur ve üç farklı şekilde yazılmış bir soyadından çok "
                            "daha hızlı bulunur."
                        ),
                    },
                    {
                        "order": 4,
                        "title_en": "Take the deposit and confirm",
                        "title_tr": "Kaporayı alın ve onaylayın",
                        "body_en": (
                            "Payment status is tracked separately from booking status — a confirmed "
                            "lesson can be unpaid. Take the deposit against the booking; the balance "
                            "shows on the customer's account for whoever is on the desk at check-in."
                        ),
                        "body_tr": (
                            "Ödeme durumu, rezervasyon durumundan ayrı izlenir — onaylı bir ders "
                            "ödenmemiş olabilir. Kaporayı rezervasyon üzerinden alın; kalan tutar, "
                            "girişte masada kim varsa görsün diye müşteri hesabında görünür."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 6,
                "title_en": "When things change",
                "title_tr": "Bir şeyler değiştiğinde",
                "summary_en": "Full sessions, cancellations, no-shows and the weather.",
                "summary_tr": "Dolu seanslar, iptaller, gelmeyenler ve hava.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Use the waitlist instead of turning people away",
                        "title_tr": "İnsanları geri çevirmek yerine bekleme listesini kullanın",
                        "body_en": (
                            "When a session is full, add the customer to the waitlist. If a place "
                            "opens it surfaces with their contact details, in the order they joined "
                            "— which is both fairer and easier to defend."
                        ),
                        "body_tr": (
                            "Seans dolduğunda müşteriyi bekleme listesine ekleyin. Yer açılırsa "
                            "iletişim bilgileriyle, katılım sırasına göre karşınıza çıkar — hem daha "
                            "adil hem de savunması kolay."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Cancel properly",
                        "title_tr": "Doğru iptal edin",
                        "body_en": (
                            "**Cancelled** means somebody told the school in advance. **No-show** "
                            "means the seat was held and nobody came. Recording a no-show as a "
                            "cancellation to be kind is a decision with money attached — take it "
                            "deliberately and note why."
                        ),
                        "body_tr": (
                            "**İptal**, birinin okula önceden haber vermesidir. **Gelmedi**, yerin "
                            "tutulup kimsenin gelmemesidir. İyi niyetle “gelmedi”yi iptal olarak "
                            "kaydetmek parasal sonucu olan bir karardır — bilerek verin ve nedenini "
                            "not edin."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Separate the sea's cancellation from the customer's",
                        "title_tr": "Denizin iptalini müşterinin iptalinden ayırın",
                        "body_en": (
                            "If the school called it off for conditions, use the lesson's "
                            "**postponed — conditions** status and rebook. Reports split weather "
                            "losses from commercial ones, and that distinction is how you find out "
                            "whether you have a sales problem or a scheduling problem."
                        ),
                        "body_tr": (
                            "Okul koşullar nedeniyle iptal ettiyse dersin **ertelendi — koşullar** "
                            "durumunu kullanın ve yeniden planlayın. Raporlar hava kaynaklı kayıpları "
                            "ticari kayıplardan ayırır; satış sorununuz mu yoksa planlama sorununuz "
                            "mu olduğunu bu ayrım gösterir."
                        ),
                    },
                ],
            },
        ],
    },
    # ================================================== 5. add a surfboard
    {
        "code": "add-surfboard",
        "icon": "package",
        "difficulty": Difficulty.BEGINNER,
        "estimated_minutes": 10,
        "required_capability": "equipment.view",
        "sort_order": 50,
        "title_en": "Add a surfboard to the inventory",
        "title_tr": "Envantere sörf tahtası ekleyin",
        "description_en": (
            "Get a board into the system so it can be assigned, rented, repaired and "
            "counted: category, dimensions, volume, status and its QR label."
        ),
        "description_tr": (
            "Bir tahtayı sisteme alın ki görevlendirilebilsin, kiralanabilsin, "
            "onarılabilsin ve sayılabilsin: kategori, ölçüler, hacim, durum ve QR etiketi."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 5,
                "title_en": "Create the item",
                "title_tr": "Eşyayı oluşturun",
                "summary_en": "Category first, then the board itself.",
                "summary_tr": "Önce kategori, sonra tahtanın kendisi.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "equipment:list",
                        "title_en": "Open the equipment inventory",
                        "title_tr": "Ekipman envanterini açın",
                        "action_hint_en": "Sidebar → Equipment → Inventory",
                        "action_hint_tr": "Kenar çubuğu → Ekipman → Envanter",
                        "body_en": (
                            "Everything the school lends or rents belongs here: boards, wetsuits, "
                            "leashes, impact vests, roof racks. If it can go missing or break, it "
                            "needs a record."
                        ),
                        "body_tr": (
                            "Okulun ödünç verdiği veya kiraladığı her şey buraya aittir: tahtalar, "
                            "mayolar, leash'ler, darbe yelekleri, tavan barları. Kaybolabiliyor veya "
                            "kırılabiliyorsa kaydı olmalıdır."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Make sure a category exists",
                        "title_tr": "Bir kategorinin var olduğundan emin olun",
                        "body_en": (
                            "Soft-top, funboard, shortboard, wetsuit 3/2, wetsuit 4/3, accessory. "
                            "The category carries the rental price band, so getting it right once "
                            "saves pricing every item by hand."
                        ),
                        "body_tr": (
                            "Soft-top, funboard, shortboard, 3/2 mayo, 4/3 mayo, aksesuar. Kategori "
                            "kiralama fiyat bandını taşır; bir kez doğru kurmak her eşyayı tek tek "
                            "fiyatlandırmaktan kurtarır."
                        ),
                    },
                    {
                        "order": 3,
                        "target_url": "equipment:create",
                        "title_en": "Add the board",
                        "title_tr": "Tahtayı ekleyin",
                        "body_en": (
                            "Give it a name people will actually say out loud — “Blue 8'0 #3” beats "
                            "a serial number. Record length, width, thickness and, above all, "
                            "**volume in litres**."
                        ),
                        "body_tr": (
                            "İnsanların gerçekten söyleyeceği bir ad verin — “Mavi 8'0 #3”, seri "
                            "numarasından iyidir. Boy, en, kalınlık ve her şeyden önce **litre "
                            "cinsinden hacmi** kaydedin."
                        ),
                    },
                    {
                        "order": 4,
                        "title_en": "Understand why volume matters",
                        "title_tr": "Hacmin neden önemli olduğunu anlayın",
                        "body_en": (
                            "Volume is what lets the system recommend a board for a rider's weight "
                            "and level — roughly one litre per kilogram for a complete beginner, "
                            "dropping to under half that for an advanced surfer. A board with no "
                            "volume recorded can never be matched automatically."
                        ),
                        "body_tr": (
                            "Hacim, sistemin bir binicinin kilosuna ve seviyesine göre tahta "
                            "önermesini sağlar — tam yeni başlayan için kilogram başına yaklaşık bir "
                            "litre, ileri seviyede bunun yarısının altı. Hacmi girilmemiş bir tahta "
                            "asla otomatik eşleştirilemez."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 5,
                "title_en": "Status, condition and the label",
                "title_tr": "Durum, kondisyon ve etiket",
                "summary_en": "Two different fields that people constantly confuse.",
                "summary_tr": "Sürekli karıştırılan iki farklı alan.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Set status and condition separately",
                        "title_tr": "Durum ve kondisyonu ayrı ayrı ayarlayın",
                        "body_en": (
                            "**Status** is where the item is: available, rented, in a lesson, "
                            "reserved, in maintenance, damaged, lost, retired. **Condition** is what "
                            "state it is in: new through to unusable. A board can be available and "
                            "in fair condition — fine for whitewater, wrong for a demo."
                        ),
                        "body_tr": (
                            "**Durum**, eşyanın nerede olduğudur: müsait, kirada, derste, rezerve, "
                            "bakımda, hasarlı, kayıp, emekli. **Kondisyon** ise hâlidir: yeniden "
                            "kullanılamaza kadar. Bir tahta müsait ve orta kondisyonda olabilir — "
                            "köpük için uygun, deneme için değil."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Record the purchase price and date",
                        "title_tr": "Alım fiyatını ve tarihini kaydedin",
                        "body_en": (
                            "This is what later turns “we seem to buy a lot of leashes” into a "
                            "number, and it feeds the utilisation report that tells you which half "
                            "of the fleet earns its storage space."
                        ),
                        "body_tr": (
                            "“Galiba çok leash alıyoruz” cümlesini bir sayıya dönüştüren şey budur "
                            "ve filonun hangi yarısının deposunun hakkını verdiğini söyleyen kullanım "
                            "raporunu besler."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Print the QR label and stick it on",
                        "title_tr": "QR etiketini yazdırın ve yapıştırın",
                        "body_en": (
                            "Each item gets a QR code. On the tail pad or the wetsuit tag, it "
                            "removes the entire class of error where board 14 goes out and board 41 "
                            "is written down."
                        ),
                        "body_tr": (
                            "Her eşya bir QR kod alır. Kuyruk pedine veya mayo etiketine "
                            "yapıştırıldığında, 14 numaralı tahtanın çıkıp 41 numaranın yazıldığı "
                            "hata sınıfını tamamen ortadan kaldırır."
                        ),
                    },
                ],
            },
        ],
    },
    # ================================================= 6. rent equipment
    {
        "code": "rent-equipment",
        "icon": "arrow-left-right",
        "difficulty": Difficulty.BEGINNER,
        "estimated_minutes": 10,
        "required_capability": "rentals.view",
        "sort_order": 60,
        "title_en": "Rent equipment out",
        "title_tr": "Ekipman kiralayın",
        "description_en": (
            "The full counter flow: identify the customer, scan the kit out, price the "
            "period, take the deposit — then take it all back and close the rental."
        ),
        "description_tr": (
            "Tezgâhın tam akışı: müşteriyi tanımlayın, ekipmanı okutup çıkarın, süreyi "
            "fiyatlandırın, depozitoyu alın — sonra hepsini geri alıp kiralamayı kapatın."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 5,
                "title_en": "Hand it over",
                "title_tr": "Teslim edin",
                "summary_en": "Customer, items, period, deposit.",
                "summary_tr": "Müşteri, eşyalar, süre, depozito.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "rentals:list",
                        "title_en": "Open Rentals",
                        "title_tr": "Kiralamalar ekranını açın",
                        "action_hint_en": "Sidebar → Equipment → Rentals",
                        "action_hint_tr": "Kenar çubuğu → Ekipman → Kiralamalar",
                        "body_en": (
                            "The list shows what is out, who has it and what is overdue. Overdue "
                            "items also appear in the dashboard alerts, because a board that is two "
                            "hours late is a phone call and a board that is two days late is a "
                            "police report."
                        ),
                        "body_tr": (
                            "Liste; dışarıda ne var, kimde ve ne gecikmiş gösterir. Geciken eşyalar "
                            "kontrol paneli uyarılarında da görünür; çünkü iki saat geciken bir tahta "
                            "bir telefon görüşmesi, iki gün geciken bir tahta bir tutanaktır."
                        ),
                    },
                    {
                        "order": 2,
                        "target_url": "rentals:create",
                        "title_en": "Start from the customer",
                        "title_tr": "Müşteriden başlayın",
                        "body_en": (
                            "A walk-in becomes a customer record with a phone number and an ID "
                            "reference **before** anything leaves the rack. This is the single "
                            "cheapest piece of insurance the desk has."
                        ),
                        "body_tr": (
                            "Raftan bir şey çıkmadan **önce** gelen kişi, telefon numarası ve kimlik "
                            "referansı olan bir müşteri kaydına dönüşür. Masanın sahip olduğu en ucuz "
                            "sigorta budur."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Scan the items out",
                        "title_tr": "Eşyaları okutarak çıkarın",
                        "body_en": (
                            "Scan each QR code. Only items whose status is *available* can be "
                            "added — anything in maintenance, damaged, lost or retired is refused. "
                            "That refusal is the whole reason those statuses exist."
                        ),
                        "body_tr": (
                            "Her QR kodu okutun. Yalnızca durumu *müsait* olan eşyalar eklenebilir — "
                            "bakımda, hasarlı, kayıp veya emekli olanlar reddedilir. Bu red, o "
                            "durumların var olma nedenidir."
                        ),
                    },
                    {
                        "order": 4,
                        "title_en": "Set the period and take the deposit",
                        "title_tr": "Süreyi belirleyin ve depozitoyu alın",
                        "body_en": (
                            "Hourly, daily or weekly, priced from the item's category. If you "
                            "override the amount, the override is recorded with your name. Take the "
                            "deposit as part of the rental so a different colleague can see what is "
                            "held when the customer comes back on Thursday."
                        ),
                        "body_tr": (
                            "Saatlik, günlük veya haftalık; fiyat eşyanın kategorisinden gelir. "
                            "Tutarı elle değiştirirseniz bu değişiklik adınızla kaydedilir. "
                            "Depozitoyu kiralamanın parçası olarak alın ki müşteri perşembe günü "
                            "döndüğünde başka bir çalışan tutulan tutarı görebilsin."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 5,
                "title_en": "Take it back",
                "title_tr": "Geri alın",
                "summary_en": "Inspect, record damage, release the deposit, close.",
                "summary_tr": "İnceleyin, hasarı kaydedin, depozitoyu iade edin, kapatın.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Inspect before you accept the return",
                        "title_tr": "İadeyi kabul etmeden önce inceleyin",
                        "body_en": (
                            "Fins, leash plug, rails, zip. Thirty seconds at the counter with the "
                            "customer still present is worth more than a discovery the next morning "
                            "with nobody to ask."
                        ),
                        "body_tr": (
                            "Finler, leash yuvası, kenarlar, fermuar. Müşteri hâlâ oradayken "
                            "tezgâhta harcanan otuz saniye, ertesi sabah soracak kimse yokken yapılan "
                            "keşiften değerlidir."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Record damage honestly",
                        "title_tr": "Hasarı dürüstçe kaydedin",
                        "body_en": (
                            "Pick the damage type, attach a photo and say whether the customer is "
                            "being charged. If the item is no longer usable it moves to the "
                            "maintenance queue automatically and stops being offered."
                        ),
                        "body_tr": (
                            "Hasar türünü seçin, fotoğraf ekleyin ve müşteriden tahsil edilip "
                            "edilmediğini belirtin. Eşya artık kullanılamaz durumdaysa otomatik "
                            "olarak bakım kuyruğuna geçer ve önerilmez olur."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Close the rental at the counter",
                        "title_tr": "Kiralamayı tezgâhta kapatın",
                        "body_en": (
                            "Closing releases the deposit and returns the item to the available "
                            "pool. A rental left open keeps the board out of circulation, so close "
                            "it now — not at the end of the week when the fleet looks half its size."
                        ),
                        "body_tr": (
                            "Kapatmak depozitoyu serbest bırakır ve eşyayı müsait havuzuna döndürür. "
                            "Açık kalan bir kiralama tahtayı dolaşımdan alıkoyar; bu yüzden hemen "
                            "kapatın — filonun yarı yarıya küçük göründüğü hafta sonunda değil."
                        ),
                    },
                ],
            },
        ],
    },
    # ==================================================== 7. take payment
    {
        "code": "take-payment",
        "icon": "wallet",
        "difficulty": Difficulty.INTERMEDIATE,
        "estimated_minutes": 12,
        "required_capability": "finance.view",
        "sort_order": 70,
        "title_en": "Take a payment",
        "title_tr": "Ödeme alın",
        "description_en": (
            "Invoice, payment, package and refund — the four money movements a surf "
            "school makes, and the rules that keep them auditable."
        ),
        "description_tr": (
            "Fatura, ödeme, paket ve iade — bir sörf okulunun yaptığı dört para hareketi "
            "ve bunları denetlenebilir kılan kurallar."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 6,
                "title_en": "Invoice and payment",
                "title_tr": "Fatura ve ödeme",
                "summary_en": "Create it from the booking, never from a blank form.",
                "summary_tr": "Boş formdan değil, rezervasyondan oluşturun.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "finance:dashboard",
                        "title_en": "Open Finance",
                        "title_tr": "Finans ekranını açın",
                        "action_hint_en": "Sidebar → Business → Finance",
                        "action_hint_tr": "Kenar çubuğu → İşletme → Finans",
                        "body_en": (
                            "Money is stored as exact decimal amounts, never rounded floating "
                            "point, and every movement is attached to something real: a booking, a "
                            "rental, a camp place, a shop sale."
                        ),
                        "body_tr": (
                            "Para; yuvarlanmış kayan noktalı sayı olarak değil, kesin ondalık "
                            "tutarlarla saklanır ve her hareket gerçek bir şeye bağlıdır: bir "
                            "rezervasyon, kiralama, kamp yeri veya mağaza satışı."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Create the invoice from the booking",
                        "title_tr": "Faturayı rezervasyondan oluşturun",
                        "body_en": (
                            "Starting from the booking brings the customer, the amounts and the "
                            "description across correctly. Starting from a blank form is how an "
                            "invoice ends up with the wrong customer on it."
                        ),
                        "body_tr": (
                            "Rezervasyondan başlamak; müşteriyi, tutarları ve açıklamayı doğru "
                            "aktarır. Boş formdan başlamak, faturanın üzerinde yanlış müşterinin "
                            "yer almasının yoludur."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Record the payment with its method",
                        "title_tr": "Ödemeyi yöntemiyle kaydedin",
                        "body_en": (
                            "Cash, card, transfer, online, package or voucher. The method matters at "
                            "the end of the day, when the drawer is counted against what the system "
                            "says should be in it.\n\n"
                            "Never set payment status by hand — it is derived from the payments."
                        ),
                        "body_tr": (
                            "Nakit, kart, havale, çevrimiçi, paket veya hediye çeki. Yöntem gün "
                            "sonunda önem kazanır; kasa sayılıp sistemin söylediğiyle "
                            "karşılaştırılır.\n\n"
                            "Ödeme durumunu asla elle ayarlamayın — ödemelerden türetilir."
                        ),
                    },
                    {
                        "order": 4,
                        "title_en": "Take a partial payment on purpose",
                        "title_tr": "Bilerek kısmi ödeme alın",
                        "body_en": (
                            "Deposit now, balance at check-in is the normal pattern. The invoice "
                            "tracks the remainder itself and the customer's account shows what is "
                            "still owed — nobody has to keep it in their head."
                        ),
                        "body_tr": (
                            "Şimdi kapora, girişte kalan — normal düzen budur. Fatura kalanı kendisi "
                            "takip eder ve müşterinin hesabı borcu gösterir; kimsenin aklında "
                            "tutması gerekmez."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 6,
                "title_en": "Packages and refunds",
                "title_tr": "Paketler ve iadeler",
                "summary_en": "Prepaid lessons, and the one movement that needs authorisation.",
                "summary_tr": "Ön ödemeli dersler ve onay gerektiren tek hareket.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Sell a lesson package",
                        "title_tr": "Ders paketi satın",
                        "body_en": (
                            "A package is bought once and drawn down over a season. Selling a "
                            "ten-lesson package creates a customer package with a balance that is "
                            "visible on the customer record."
                        ),
                        "body_tr": (
                            "Paket bir kez satın alınır ve sezon boyunca kullanılır. On derslik bir "
                            "paket satmak, müşteri kaydında görünen bakiyeli bir müşteri paketi "
                            "oluşturur."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Use the package as the payment method",
                        "title_tr": "Paketi ödeme yöntemi olarak kullanın",
                        "body_en": (
                            "On the next booking, choose **Package**. The balance decrements "
                            "automatically, so reception can answer “how many have I got left?” "
                            "without opening a spreadsheet or trusting anyone's memory."
                        ),
                        "body_tr": (
                            "Bir sonraki rezervasyonda **Paket**'i seçin. Bakiye otomatik düşer; "
                            "böylece resepsiyon “kaç dersim kaldı?” sorusunu tablo açmadan ve "
                            "kimsenin hafızasına güvenmeden yanıtlar."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Understand how refunds work",
                        "title_tr": "İadelerin nasıl işlediğini anlayın",
                        "body_en": (
                            "Refunding needs an explicit capability that most roles do not hold. The "
                            "refund references the original payment and is retained in the audit log "
                            "as a sensitive action.\n\n"
                            "**Never** issue a refund by recording a negative payment — the reports "
                            "and the tax figures both depend on refunds being their own record type."
                        ),
                        "body_tr": (
                            "İade, çoğu rolde bulunmayan açık bir yetki gerektirir. İade, orijinal "
                            "ödemeye atıfta bulunur ve hassas işlem olarak denetim günlüğünde "
                            "saklanır.\n\n"
                            "İadeyi **asla** eksi bir ödeme girerek yapmayın — hem raporlar hem vergi "
                            "rakamları, iadelerin kendine ait bir kayıt türü olmasına bağlıdır."
                        ),
                    },
                ],
            },
        ],
    },
    # ================================================= 8. generate report
    {
        "code": "generate-report",
        "icon": "file-text",
        "difficulty": Difficulty.INTERMEDIATE,
        "estimated_minutes": 10,
        "required_capability": "reporting.view",
        "sort_order": 80,
        "title_en": "Generate a report",
        "title_tr": "Rapor oluşturun",
        "description_en": (
            "Ask a question you can ask again next season: define it, run it, compare "
            "it against the previous period and export it."
        ),
        "description_tr": (
            "Gelecek sezon yeniden sorabileceğiniz bir soru sorun: tanımlayın, "
            "çalıştırın, önceki dönemle karşılaştırın ve dışa aktarın."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 5,
                "title_en": "Define and run",
                "title_tr": "Tanımlayın ve çalıştırın",
                "summary_en": "A saved definition beats a one-off query.",
                "summary_tr": "Kaydedilmiş bir tanım, tek seferlik sorgudan iyidir.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "reporting:list",
                        "title_en": "Open Reports",
                        "title_tr": "Raporlar ekranını açın",
                        "action_hint_en": "Sidebar → Business → Reports",
                        "action_hint_tr": "Kenar çubuğu → İşletme → Raporlar",
                        "body_en": (
                            "Reports are separated from the dashboard on purpose: the dashboard is "
                            "for a glance from behind the counter, reports are for questions that "
                            "need thinking about."
                        ),
                        "body_tr": (
                            "Raporlar bilerek kontrol panelinden ayrılmıştır: panel tezgâhın "
                            "arkasından bir bakış içindir, raporlar ise üzerinde düşünmek gereken "
                            "sorular içindir."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Create a definition and name it properly",
                        "title_tr": "Bir tanım oluşturun ve düzgün adlandırın",
                        "body_en": (
                            "Pick the subject, the period and the filters. Then name it so a "
                            "colleague understands it: “Weekend group lessons, by instructor” beats "
                            "“report 3” by a distance."
                        ),
                        "body_tr": (
                            "Konuyu, dönemi ve filtreleri seçin. Sonra bir meslektaşınızın "
                            "anlayacağı şekilde adlandırın: “Hafta sonu grup dersleri, eğitmen "
                            "bazında”, “rapor 3”ten çok daha iyidir."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Use the standard periods",
                        "title_tr": "Standart dönemleri kullanın",
                        "body_en": (
                            "Today, 7, 30, 90, 180, 365 days or a custom range. The comparison "
                            "against the previous **equal-length** period is calculated for you — "
                            "comparing a 31-day month against a 28-day one by hand is where most "
                            "imaginary growth comes from."
                        ),
                        "body_tr": (
                            "Bugün, 7, 30, 90, 180, 365 gün veya özel aralık. Önceki **eşit "
                            "uzunluktaki** dönemle karşılaştırma sizin için hesaplanır — 31 günlük "
                            "bir ayı 28 günlükle elle karşılaştırmak, hayali büyümenin başlıca "
                            "kaynağıdır."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 5,
                "title_en": "Export and schedule",
                "title_tr": "Dışa aktarın ve zamanlayın",
                "summary_en": "Getting the answer to the people who need it.",
                "summary_tr": "Cevabı ihtiyacı olanlara ulaştırmak.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Choose the right export format",
                        "title_tr": "Doğru dışa aktarma biçimini seçin",
                        "body_en": (
                            "Excel for anything that will be worked on further, CSV for anything "
                            "going into another system, PDF for anything read as-is — an "
                            "accountant's pack, a board summary, a lender's file."
                        ),
                        "body_tr": (
                            "Üzerinde çalışılacaklar için Excel, başka bir sisteme girecekler için "
                            "CSV, olduğu gibi okunacaklar için PDF — mali müşavir dosyası, yönetim "
                            "özeti, kredi başvurusu."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Know that exports are logged",
                        "title_tr": "Dışa aktarmaların kaydedildiğini bilin",
                        "body_en": (
                            "Exporting is a capability-gated action and each export is written to "
                            "the audit log. A full customer list leaving the building is a "
                            "data-protection event whether or not it was innocent — so export what "
                            "you need, not everything."
                        ),
                        "body_tr": (
                            "Dışa aktarma yetkiye bağlı bir işlemdir ve her aktarım denetim "
                            "günlüğüne yazılır. Tam bir müşteri listesinin binadan çıkması, iyi "
                            "niyetli olsun olmasın bir veri koruma olayıdır — bu yüzden her şeyi "
                            "değil, ihtiyacınız olanı aktarın."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Schedule the one people actually read",
                        "title_tr": "İnsanların gerçekten okuduğunu zamanlayın",
                        "body_en": (
                            "A saved report can run on a schedule and land in the notifications of "
                            "the people who need it. A Monday-morning report everybody reads beats a "
                            "perfect dashboard nobody opens."
                        ),
                        "body_tr": (
                            "Kaydedilmiş bir rapor belirli aralıklarla çalışıp ihtiyacı olanların "
                            "bildirimlerine düşebilir. Herkesin okuduğu bir pazartesi sabahı raporu, "
                            "kimsenin açmadığı kusursuz bir panelden iyidir."
                        ),
                    },
                ],
            },
        ],
    },
    # =================================================== 9. take a backup
    {
        "code": "take-backup",
        "icon": "database-backup",
        "difficulty": Difficulty.ADVANCED,
        "estimated_minutes": 12,
        "required_capability": "backups.view",
        "sort_order": 90,
        "title_en": "Take a backup",
        "title_tr": "Yedek alın",
        "description_en": (
            "Run a backup, understand what is inside it, get a copy off the machine "
            "and — the part everybody skips — prove it can be restored."
        ),
        "description_tr": (
            "Yedek alın, içinde ne olduğunu anlayın, bir kopyasını makine dışına "
            "çıkarın ve — herkesin atladığı kısım — geri yüklenebildiğini kanıtlayın."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 6,
                "title_en": "Run one",
                "title_tr": "Bir tane çalıştırın",
                "summary_en": "What a backup contains and how often it should run.",
                "summary_tr": "Yedeğin içeriği ve ne sıklıkla çalışması gerektiği.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "backups:list",
                        "title_en": "Open Backup & Restore",
                        "title_tr": "Yedekleme ve Geri Yükleme ekranını açın",
                        "action_hint_en": "Sidebar → System → Backup & Restore",
                        "action_hint_tr": "Kenar çubuğu → Sistem → Yedekleme ve Geri Yükleme",
                        "body_en": (
                            "Everything the school knows lives in one database: customers, waivers, "
                            "incident reports, invoices. A backup is the only thing between a failed "
                            "disk and starting the season again from paper."
                        ),
                        "body_tr": (
                            "Okulun bildiği her şey tek bir veritabanında durur: müşteriler, "
                            "sorumluluk formları, olay raporları, faturalar. Yedek; bozulan bir disk "
                            "ile sezona kâğıttan yeniden başlamak arasındaki tek şeydir."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Run a backup and read the result",
                        "title_tr": "Yedek alın ve sonucu okuyun",
                        "body_en": (
                            "Each run records its size, duration and result. That record is the "
                            "point: a backup that has been silently failing for three weeks is "
                            "visible here rather than assumed to be fine."
                        ),
                        "body_tr": (
                            "Her çalıştırma boyutunu, süresini ve sonucunu kaydeder. Asıl mesele bu "
                            "kayıttır: üç haftadır sessizce başarısız olan bir yedekleme, sorunsuz "
                            "varsayılmak yerine burada görünür."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Decide whether media is included",
                        "title_tr": "Medyanın dâhil olup olmayacağına karar verin",
                        "body_en": (
                            "Media — signed waivers, certificates, equipment and incident photos — "
                            "is usually most of the size. That is why the setting exists. But a "
                            "backup without the signed waivers is not a backup of a surf school."
                        ),
                        "body_tr": (
                            "Medya — imzalı formlar, sertifikalar, ekipman ve olay fotoğrafları — "
                            "genelde boyutun büyük kısmıdır. Ayarın var olma nedeni budur. Ancak "
                            "imzalı formları içermeyen bir yedek, bir sörf okulunun yedeği değildir."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 6,
                "title_en": "Make it a real backup",
                "title_tr": "Onu gerçek bir yedeğe dönüştürün",
                "summary_en": "Off the machine, and tested.",
                "summary_tr": "Makine dışında ve test edilmiş.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Copy it off the machine",
                        "title_tr": "Makine dışına kopyalayın",
                        "body_en": (
                            "A backup sitting next to the database protects you from a mistake — not "
                            "from a fire, a theft or ransomware. External disk taken home, or object "
                            "storage, on a schedule somebody is named as responsible for."
                        ),
                        "body_tr": (
                            "Veritabanının yanında duran bir yedek sizi hatadan korur — yangından, "
                            "hırsızlıktan veya fidye yazılımından korumaz. Eve götürülen harici disk "
                            "veya nesne depolama; sorumlusu adıyla belirlenmiş bir program dâhilinde."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Understand what restoring costs",
                        "title_tr": "Geri yüklemenin bedelini anlayın",
                        "body_en": (
                            "Restore is a privileged action no ordinary role holds, because it "
                            "overwrites current data with older data. Always take a fresh backup of "
                            "the current state first, so a bad restore is itself reversible."
                        ),
                        "body_tr": (
                            "Geri yükleme, hiçbir sıradan rolün sahip olmadığı ayrıcalıklı bir "
                            "işlemdir; çünkü güncel veriyi eski veriyle değiştirir. Önce mevcut "
                            "durumun taze bir yedeğini alın ki hatalı bir geri yükleme de geri "
                            "alınabilsin."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Test a restore once a season",
                        "title_tr": "Sezonda bir kez geri yüklemeyi test edin",
                        "body_en": (
                            "A backup that has never been restored is a hypothesis. Restore into a "
                            "scratch copy and check that a recent booking, an uploaded waiver and an "
                            "invoice are all there. Ten minutes turns a hope into a fact."
                        ),
                        "body_tr": (
                            "Hiç geri yüklenmemiş bir yedek yalnızca bir varsayımdır. Ayrı bir "
                            "kopyaya geri yükleyin ve yakın tarihli bir rezervasyonun, yüklenmiş bir "
                            "formun ve bir faturanın orada olduğunu kontrol edin. On dakika, bir "
                            "umudu gerçeğe çevirir."
                        ),
                    },
                ],
            },
        ],
    },
    # =============================================== 10. use AI assistant
    {
        "code": "use-ai-assistant",
        "icon": "sparkles",
        "difficulty": Difficulty.INTERMEDIATE,
        "estimated_minutes": 10,
        "required_capability": "ai.view",
        "sort_order": 100,
        "title_en": "Use the AI assistant",
        "title_tr": "Yapay zekâ asistanını kullanın",
        "description_en": (
            "Ask good questions, read the answers correctly, and learn the one rule "
            "that never bends: the AI is not the final authority on safety."
        ),
        "description_tr": (
            "İyi sorular sorun, cevapları doğru okuyun ve hiç esnemeyen tek kuralı "
            "öğrenin: güvenlik konusunda son söz yapay zekânın değildir."
        ),
        "lessons": [
            {
                "order": 1,
                "estimated_minutes": 5,
                "title_en": "Asking well",
                "title_tr": "İyi soru sormak",
                "summary_en": "What it is genuinely good at.",
                "summary_tr": "Gerçekten iyi olduğu işler.",
                "steps": [
                    {
                        "order": 1,
                        "target_url": "ai:chat",
                        "title_en": "Open the AI assistant",
                        "title_tr": "Yapay zekâ asistanını açın",
                        "action_hint_en": "Sidebar → Artificial Intelligence → AI Assistant",
                        "action_hint_tr": "Kenar çubuğu → Yapay Zekâ → AI Asistanı",
                        "body_en": (
                            "The assistant reads the school's own records and answers in plain "
                            "language. It respects your capabilities: it will not surface figures "
                            "your role could not open elsewhere."
                        ),
                        "body_tr": (
                            "Asistan, okulun kendi kayıtlarını okur ve gündelik dille yanıtlar. "
                            "Yetkilerinize saygı gösterir: rolünüzün başka yerde açamayacağı "
                            "rakamları önünüze getirmez."
                        ),
                    },
                    {
                        "order": 2,
                        "title_en": "Ask three real questions",
                        "title_tr": "Üç gerçek soru sorun",
                        "body_en": (
                            "Try: “Which boards have not gone out this month?”, “Summarise this "
                            "month's safety incidents”, “Which customers have not booked since last "
                            "season?”\n\n"
                            "Specific questions get specific answers. “How are we doing?” gets "
                            "something that sounds good and means nothing."
                        ),
                        "body_tr": (
                            "Deneyin: “Bu ay hangi tahtalar hiç çıkmadı?”, “Bu ayın güvenlik "
                            "olaylarını özetle”, “Geçen sezondan beri rezervasyon yapmayan "
                            "müşteriler kim?”\n\n"
                            "Belirli sorular belirli yanıtlar alır. “Nasıl gidiyoruz?” kulağa hoş "
                            "gelen ve hiçbir şey ifade etmeyen bir cevap alır."
                        ),
                    },
                    {
                        "order": 3,
                        "title_en": "Verify before you act",
                        "title_tr": "Harekete geçmeden önce doğrulayın",
                        "body_en": (
                            "Every answer is shown on a distinct background with an **AI "
                            "Recommendation** chip. Before acting on a number, open the screen it "
                            "came from. Before sending drafted text to a customer, read it as if you "
                            "wrote it — because as far as the customer is concerned, you did."
                        ),
                        "body_tr": (
                            "Her yanıt ayrı bir zeminde ve **AI Önerisi** etiketiyle gösterilir. Bir "
                            "rakama göre hareket etmeden önce geldiği ekranı açın. Taslak bir metni "
                            "müşteriye göndermeden önce siz yazmışsınız gibi okuyun — çünkü müşteri "
                            "açısından siz yazdınız."
                        ),
                    },
                ],
            },
            {
                "order": 2,
                "estimated_minutes": 5,
                "title_en": "The limits",
                "title_tr": "Sınırlar",
                "summary_en": "Safety, privacy and cost.",
                "summary_tr": "Güvenlik, gizlilik ve maliyet.",
                "steps": [
                    {
                        "order": 1,
                        "title_en": "Learn the safety rule",
                        "title_tr": "Güvenlik kuralını öğrenin",
                        "body_en": (
                            "**The AI is never the final authority on a safety decision.** It may "
                            "recommend postponing a session or flag that conditions look marginal — "
                            "and a named staff member must approve the call before anything changes "
                            "in the water. The approval is recorded with that person's name."
                        ),
                        "body_tr": (
                            "**Yapay zekâ hiçbir güvenlik kararında son merci değildir.** Bir seansın "
                            "ertelenmesini önerebilir veya koşulların sınırda göründüğünü "
                            "belirtebilir — ancak suda bir şey değişmeden önce adı belli bir "
                            "personelin onaylaması gerekir. Onay, o kişinin adıyla kaydedilir."
                        ),
                    },
                    {
                        "order": 2,
                        "target_url": "ai:control_center",
                        "title_en": "Know where the model runs",
                        "title_tr": "Modelin nerede çalıştığını bilin",
                        "body_en": (
                            "The school can run a local model, a cloud provider, or route "
                            "automatically. **Local** means nothing leaves the building — the right "
                            "default for anything touching customer or medical data."
                        ),
                        "body_tr": (
                            "Okul yerel bir model, bir bulut sağlayıcı veya otomatik yönlendirme "
                            "kullanabilir. **Yerel**, hiçbir verinin binadan çıkmaması demektir — "
                            "müşteri veya sağlık verisine dokunan her şey için doğru varsayılan."
                        ),
                    },
                    {
                        "order": 3,
                        "target_url": "ai:usage",
                        "title_en": "Watch what it costs",
                        "title_tr": "Maliyetini izleyin",
                        "body_en": (
                            "Cloud calls cost money. The usage screen shows what was spent, by whom "
                            "and on what. Checking it monthly is how a useful tool stays a useful "
                            "tool instead of becoming a line item nobody can explain."
                        ),
                        "body_tr": (
                            "Bulut çağrıları para harcar. Kullanım ekranı neyin, kim tarafından, ne "
                            "için harcandığını gösterir. Aylık kontrol etmek, yararlı bir aracın "
                            "kimsenin açıklayamadığı bir gider kalemine dönüşmesini önler."
                        ),
                    },
                ],
            },
        ],
    },
]


class Command(BaseCommand):
    help = "Load the built-in Training Center courses, lessons and steps (EN + TR)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--update",
            action="store_true",
            help="Overwrite existing courses, lessons and steps with the shipped text.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        update_existing: bool = options["update"]
        counters = {"courses": 0, "lessons": 0, "steps": 0, "skipped": 0}

        for course_data in COURSES:
            course = self._sync_course(course_data, update_existing, counters)
            for lesson_data in course_data["lessons"]:
                lesson = self._sync_lesson(course, lesson_data, update_existing, counters)
                for step_data in lesson_data["steps"]:
                    self._sync_step(lesson, step_data, update_existing, counters)

        self.stdout.write(
            self.style.SUCCESS(
                "Training content loaded: "
                f"{counters['courses']} courses, {counters['lessons']} lessons and "
                f"{counters['steps']} steps written; {counters['skipped']} left as-is."
            )
        )
        if counters["skipped"] and not update_existing:
            self.stdout.write(
                "Existing rows were kept. Re-run with --update to replace them with "
                "the shipped text."
            )

    # -- helpers -----------------------------------------------------------
    def _sync_course(self, data: dict, update_existing: bool, counters: dict) -> TrainingCourse:
        defaults = {
            "title_en": data["title_en"],
            "title_tr": data["title_tr"],
            "description_en": data["description_en"],
            "description_tr": data["description_tr"],
            "icon": data["icon"],
            "estimated_minutes": data["estimated_minutes"],
            "difficulty": data["difficulty"],
            "required_capability": data["required_capability"],
            "sort_order": data["sort_order"],
            "is_active": True,
        }
        course = TrainingCourse.all_objects.filter(code=data["code"]).first()
        if course is None:
            course = TrainingCourse.objects.create(code=data["code"], **defaults)
            counters["courses"] += 1
        elif update_existing:
            for field, value in defaults.items():
                setattr(course, field, value)
            course.is_deleted = False
            course.deleted_at = None
            course.save()
            counters["courses"] += 1
        else:
            counters["skipped"] += 1
        return course

    def _sync_lesson(
        self, course: TrainingCourse, data: dict, update_existing: bool, counters: dict
    ) -> TrainingLesson:
        defaults = {
            "title_en": data["title_en"],
            "title_tr": data["title_tr"],
            "summary_en": data["summary_en"],
            "summary_tr": data["summary_tr"],
            "estimated_minutes": data["estimated_minutes"],
        }
        lesson = TrainingLesson.all_objects.filter(course=course, order=data["order"]).first()
        if lesson is None:
            lesson = TrainingLesson.objects.create(course=course, order=data["order"], **defaults)
            counters["lessons"] += 1
        elif update_existing:
            for field, value in defaults.items():
                setattr(lesson, field, value)
            lesson.is_deleted = False
            lesson.deleted_at = None
            lesson.save()
            counters["lessons"] += 1
        else:
            counters["skipped"] += 1
        return lesson

    def _sync_step(
        self, lesson: TrainingLesson, data: dict, update_existing: bool, counters: dict
    ) -> TrainingStep:
        defaults = {
            "title_en": data["title_en"],
            "title_tr": data["title_tr"],
            "body_en": data.get("body_en", ""),
            "body_tr": data.get("body_tr", ""),
            "target_url": data.get("target_url", ""),
            "action_hint_en": data.get("action_hint_en", ""),
            "action_hint_tr": data.get("action_hint_tr", ""),
        }
        step = TrainingStep.all_objects.filter(lesson=lesson, order=data["order"]).first()
        if step is None:
            step = TrainingStep.objects.create(lesson=lesson, order=data["order"], **defaults)
            counters["steps"] += 1
        elif update_existing:
            for field, value in defaults.items():
                setattr(step, field, value)
            step.is_deleted = False
            step.deleted_at = None
            step.save()
            counters["steps"] += 1
        else:
            counters["skipped"] += 1
        return step
