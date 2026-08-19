# Kullanım Kılavuzu

Bu sistemle bir sörf okulu gününü nasıl yöneteceğinizin pratik anlatımı.
İngilizce sürüm: [USER_GUIDE_EN.md](USER_GUIDE_EN.md)

---

## 1. Giriş

`http://127.0.0.1:8000/` adresine gidin ve **kullanıcı adınız veya e-posta
adresinizle** giriş yapın.

*Oturumumu açık tut* seçeneğini yalnızca kendi bilgisayarınızda işaretleyin.
Resepsiyondaki ortak bilgisayarda işaretlemeyin — böylece tarayıcı kapandığında
oturum sona erer.

Şifrenizi profil menüsünden → *Şifre değiştir* ile değiştirebilirsiniz. Üst
çubuktaki 🌐 simgesiyle istediğiniz an Türkçe ve İngilizce arasında geçiş yapın.

**Gördükleriniz rolünüze göre değişir.** Kiralama personeli ciro rakamlarını
görmez; bir eğitmenin panosu kendi derslerini öne çıkarır. Bir menü öğesi
görünmüyorsa rolünüzde o yetki yoktur — arıza sanmadan önce yöneticinize sorun.

---

## 2. Ana pano (Dashboard)

Her sabahki başlangıç noktanız.

| Kart | Ne söyler |
|---|---|
| Bugünkü Dersler | Kaç ders var ve ne kadar dolu |
| Bugünkü Öğrenciler | Bugün suya girecek herkes |
| Bugünkü Gelir | Bugün şu ana kadar alınan |
| Aktif Kiralamalar | Dışarıdaki ekipman; gecikenler işaretli |
| Ekipman Uyarıları | Teslim etmeden önce ilgilenilmesi gerekenler |
| Deniz Koşulları | Anlık dalga, rüzgâr ve seviyeye göre Surf Score |
| Eğitmen Müsaitliği | Kimler çalışıyor |
| Bekleyen Ödemeler | Tahsil edilmemiş tutarlar |
| YZ Uyarıları | Asistanın dikkat çektiği konular |

Kartların altında solda günün programı, sağda uyarılar ve son işlemler yer alır.
Herhangi bir derse tıklayarak açabilirsiniz.

Her yerden `/` tuşuna basarak genel aramaya geçebilirsiniz; müşteri, öğrenci,
rezervasyon, ekipman ve kiralama arar. Tam bir kod yazarsanız (`EQ00042`,
`BK000123`) doğrudan o kayda gider.

---

## 3. İlk kez gelen bir müşteri

**Adım 1 — müşteriyi oluşturun.** Müşteriler → *Yeni müşteri*. Başlamak için ad,
telefon ve e-posta yeterlidir. **Acil durum kişisini mutlaka girin** — ihtiyaç
duyduğunuzda sormaya vaktiniz olmayacak.

**Adım 2 — öğrenci profilini oluşturun.** Öğrenciler → *Yeni öğrenci*, o
müşteriye bağlı olarak. Önemli alanlar:

- **Sörf seviyesi** — dürüst başlayın. "İlk kez" bir eksiklik değildir ve
  güvenlik kurallarını belirleyen alandır.
- **Yüzme biliyor mu** ve yüzebildiği mesafe — bu bir güvenlik alanıdır, formalite
  değil.
- **Kilo** — sistem buna göre board hacmi önerir.
- **Sağlık durumu, kullandığı ilaçlar, alerjiler** — astım, epilepsi, yeni bir
  sakatlık. Eğitmenin bunu dersten sonra değil, önce bilmesi gerekir.

**Adım 3 — dersi rezerve edin.** Bkz. §4.

**Adım 4 — ödemeyi alın.** Bkz. §7.

Daha önce gelmiş bir müşteride adını aratıp doğrudan 3. adıma geçin.

---

## 4. Rezervasyonlar

Rezervasyonlar → *Takvim* aylık görünümü açar. Her blok ders tipini, saati,
eğitmeni ve doluluğu (`4/8`) gösterir. Renkler ders tipinden gelir.

### Rezervasyon oluşturma

1. *Yeni rezervasyon*.
2. Müşteriyi arayın — yazmaya başladığınızda sonuçlar gelir.
3. Öğrenciyi seçin.
4. Dersi seçin. Yalnızca boş kontenjanı olan dersler listelenir.
5. Katılımcı sayısını girin.
6. **Çakışma panelini izleyin.** Anlık güncellenir ve şunları denetler:
   - boş kontenjan
   - öğrencinin o saatte başka rezervasyonu olup olmadığı
   - seviyesinin derse uygunluğu
   - eğitmen müsaitliği
   - eğitmen–öğrenci oranı (18 yaş altı gruplarda daha katı)
   - öğrenciye tanımlı güvenlik kısıtları
7. Onaylayın.

Panelde bir çakışma görünüyorsa neyin yanlış olduğunu açıkça yazar. **Bunu aşmaya
çalışmayın** — oran ve seviye kuralları, göz ardı edildiğinde birileri
yaralandığı için vardır.

### Ders doluysa

Müşteriyi **bekleme listesine** ekleyin. Bir iptal olduğunda listedeki ilk kişi
otomatik olarak yükseltilir ve size bildirim gelir.

### İptal

Rezervasyonu açın → *İptal et*, gerekçe yazın. 24 saatten önceki iptaller
ücretsizdir; 24 saat içindeki iptallerde sistem bir ücret önerir, değiştirebilir
veya kaldırabilirsiniz. Kontenjan hemen serbest kalır ve bekleme listesi ilerler.

Gelmeyen biri için iptal yerine **Gelmedi** işaretleyin — bu ayrım hem
istatistikler hem de bir sonraki sefer nasıl davranacağınız için önemlidir.

---

## 5. Dersi yürütmek

Dersi panodan veya takvimden açın.

**Suya girmeden önce:**
1. Grubun seviyesi için **Surf Score**'a bakın. Bu hesaplanmış bir sayıdır;
   genişleterek hangi etkenlerden oluştuğunu görebilirsiniz.
2. **Güvenlik brifingi** kutusunu işaretleyin — kimin onayladığı kaydedilir.
3. Her öğrenciye **board** ve **wetsuit** atayın. Sistem kilo ve seviyeden board
   hacmini, su sıcaklığından wetsuit kalınlığını önerir.
4. Gelen her öğrenciyi **giriş yaptırın** (check-in).

**Ders sonrası:**
1. *Dersi tamamla*. Giriş yapmış herkes katıldı olarak işaretlenir; ders sayıları
   ve son ders tarihi güncellenir.
2. İlerleyen öğrenciler için **beceri değerlendirmesi** girin. Seviyesini
   yükseltirseniz profili güncellenir ve sonraki rezervasyonlar yeni seviyeyi
   kullanır.

> Surf Score koşulların grubun seviyesi için uygun olmadığını söylüyorsa, dersi
> yapma ya da erteleme kararı **sizindir**. Sistem size rakamları ve gerekçeleri
> verir; sizin yerinize karar vermez ve kararınızı geçersiz kılmaz.

---

## 6. Ekipman ve kiralama

### Envanter

Ekipman → her kaydın bir varlık kodu ve QR etiketi vardır. Etiketleri kayıt
sayfasından yazdırabilirsiniz. Kategori, durum veya kondisyona göre filtreleyin;
varlık kodu, marka veya seri numarasıyla arayın.

Durumlar: *Müsait*, *Kirada*, *Derste*, *Rezerve*, *Bakımda*, *Hasarlı*,
*Kayıp*, *Emekli*. Müsait olmayan hiçbir ekipman teslim edilemez.

### Ekipman kiralama

Kiralama → *Yeni kiralama*:

1. Müşteriyi bulun.
2. **Varlık kodlarını okutun veya yazın.** Her kod bir satır ekler ve toplamı
   günceller.
3. Saatlik, günlük veya haftalık seçin. Bir hafta ve üzeri kiralamalarda haftalık
   tarife daha ucuzsa sistem onu otomatik uygular.
4. Depozitoyu alın.
5. Onaylayın — tüm ekipmanlar *Kirada* olarak işaretlenir.

### Ekipman teslim alma

Kiralama → kaydı bulun → *İade*. Her kalem için geri geldiği kondisyonu seçin.
Bir hasar varsa:

- hasar tipini seçin (ding, çatlak, fin, leash, wetsuit yırtığı…),
- açıklayın,
- uygunsa bir ücret girin.

Sistem varsa gecikme ücretini hesaplar, hasar bedellerini uygular, depozitoyu
mahsup eder ve hasar bildirildiğinde otomatik olarak bir bakım kaydı açıp
ekipmanı hizmet dışına alır.

Tezgâhta hızlı iade için *Varlık koduyla hızlı iade* kullanın.

### Bakım

Bakım ekranı açık işleri gösterir. **Öngörülen bakım** panosu ekipmanları riske
göre sıralar: son bakımdan bu yana geçen gün, o tarihten sonraki kiralama sayısı,
toplam kullanım saati, yaş ve geçmiş arızalar. Bu, kendi servis geçmişinizden
çıkarılan bir istatistiktir — tahmin değil, yapay zekâ görüşü de değil. Her kayıt
neden o puanı aldığını açıklar.

---

## 7. Para

### Ödeme alma

Rezervasyon veya kiralama ekranından *Ödeme kaydet*: tutar ve yöntem girilir,
sistem bakiyeyi ve ödeme durumunu günceller. Her şey müşteri kaydına işlenir.

### İade

Ödemeyi açın → *İade*. İade, eşleşen negatif bir kayıt oluşturur; orijinal kayıt
hiçbir zaman değiştirilmez, böylece geçmiş dürüst kalır. İade için
`finance.refund` yetkisi gerekir.

### Ders paketleri

İndirimli çoklu ders paketi satmak için: Finans → Paketler. Dersler kullanıldıkça
düşülür, kalan hak müşteri sayfasında görünür.

### Satış noktası (POS)

POS → *Terminal*. Ürüne dokunun veya barkodu okutun, adedi ayarlayın, indirim
uygulayın, ödemeyi alın, fişi yazdırın. Stok otomatik düşer ve satış finans
rakamlarına yansır.

Bir satışı iptal etmek stoğu geri yükler — kaydı asla silmez.

---

## 8. Raporlar ve analitik

**Analitik** eğilimleri gösterir: gelir, rezervasyon, doluluk, müşteri sadakati,
ekipman kullanımı, en yoğun saatler. Her rakam, aynı uzunluktaki bir önceki
dönemle karşılaştırmalı verilir. Dönemi filtreyle değiştirin: Bugün, 7 / 30 / 90 /
180 / 365 gün veya özel tarih aralığı.

Veri az olduğunda sistem tahmini kendinden emin bir çizgi gibi sunmaz, bunu açıkça
belirtir. Düşük güvenilirlikli bir tahmini bir ipucu olarak değerlendirin.

**Raporlar** belge üretir: günlük operasyon, gelir, ödemeler, giderler, kâr-zarar,
rezervasyonlar, iptaller, öğrenci listesi, eğitmen performansı ve komisyonu,
ekipman envanteri ve kullanımı, bakım, kiralama, kamp listeleri, güvenlik olayları.

Raporu seçin, filtreleri ayarlayın, **PDF**, **Excel** veya **CSV** olarak alın.

> CSV dosyaları BOM içeren UTF-8 formatındadır; böylece Türkçe karakterler
> Excel'de doğru açılır.

---

## 9. Güvenlik

- **Olayı hemen bildirin** — ramak kalaları da. Güvenlik → *Yeni olay*. Bugün
  kaydedilen bir ramak kala, gelecek ay bir yaralanmayı önler.
- **Cankurtaran çizelgesi** — hangi noktada kim, ne zaman görevli.
- **Acil durum kişileri** — yazdırılabilir; bir kopyasını telefonun yanında tutun.
- **Öğrenci kısıtları** — bir öğrenciye tanımlı sağlık veya beceri kısıtı,
  rezervasyon yapılmak istendiğinde otomatik olarak denetlenir.
- **Uyarılar** — yapay zekânın önerdiği bir uyarı açıkça işaretlenir ve bir
  personel onaylayana kadar **etkin sayılmaz**. Onay, kimin verdiğiyle kaydedilir.

---

## 10. Yapay zekâ asistanı

YZ → *AI Assistant*. Türkçe veya İngilizce sorun:

> "Bugünkü dersleri özetle."
> "Yarın başlangıç seviyesi için en uygun ders saati hangisi?"
> "Son 30 günde gelir ne kadar değişti?"
> "Hangi surfboardların bakıma ihtiyacı olabilir?"
> "Yarınki deniz şartları başlangıç öğrencileri için uygun mu?"

**Yalnızca veritabanındakini raporlar.** Her rakam gerçek bir sorgudan gelir. Veri
yoksa bunu söyler — makul görünen bir sayı uydurmaz. Ayrıca rolünüzün göremediği
hiçbir bilgiyi size gösteremez.

Her yanıtın altında hangi sağlayıcı ve modelin cevapladığını, ne kadar sürdüğünü
ve hangi sorguları çalıştırdığını görebilirsiniz.

Mesaj kutusundan modu seçin:

| Mod | Ne zaman |
|---|---|
| **Yalnızca yerel** | Müşteri verisi bilgisayardan çıkmamalıysa |
| **Otomatik** | Normal kullanım — kolay sorular yerelde, zorlar bulutta |
| **Yalnızca bulut** | En güçlü modeli istiyorsanız ve anahtarınız tanımlıysa |

**Bilgi tabanı** — YZ → Knowledge ile kendi kılavuzlarınızı, prosedürlerinizi ve
güvenlik talimatlarınızı ekleyebilirsiniz. Asistan bunlardan kaynak göstererek
alıntı yapar.

---

## 11. Yedekleme

Yedekleme ve Geri Yükleme → *Şimdi yedek al*. Alışılmadık bir işlemden önce
mutlaka alın: büyük bir fiyat değişikliği, toplu veri aktarımı, sürüm yükseltmesi.

Her yedeğin sağlama toplamı hesaplanır. *Doğrula* bunu yeniden kontrol eder —
hiç doğrulamadığınız bir yedek, bir plan değil bir temennidir.

**Geri yükleme mevcut veriyi değiştirir.** Sistem önce yedeği doğrular, ardından
mevcut durumun güvenlik yedeğini alır ve devam etmeden önce yedek kodunu
yazmanızı ister. Geri yükleme başarısız olursa her şeyi eski haline döndürür.

Günlük yedekleri zamanlayın (bkz. [BACKUP_RESTORE.md](BACKUP_RESTORE.md)) ve
bilgisayarın dışına kopyalayın — içinde her şey vardır.

---

## 12. Sistemi öğrenmek

- **Eğitim Merkezi** — gerçek işleri adım adım yaptıran kısa interaktif dersler:
  ilk öğrenciniz, ilk rezervasyonunuz, ödeme alma, rapor üretme. İlerlemeniz
  kaydedilir.
- **Yardım Merkezi** — her ekran için başvuru kaynağı, iki dilde.
- **Kurulum sihirbazı** — yeni bir kurulumda ilk açılış ayarları.

---

## 13. Hızlı başvuru

| Ne yapmak istiyorum | Nereye |
|---|---|
| Bugünü görmek | Ana pano |
| Ders rezerve etmek | Rezervasyonlar → Yeni rezervasyon |
| Müşteri eklemek | Müşteriler → Yeni müşteri |
| Öğrenci girişi yapmak | Dersi aç → Check-in |
| Ekipman kiralamak | Kiralama → Yeni kiralama |
| Ekipman teslim almak | Kiralama → İade |
| Hasar bildirmek | Bakım → Sorun bildir |
| Ödeme almak | Rezervasyon/kiralama → Ödeme kaydet |
| Ürün satmak | POS → Terminal |
| Rakamlara bakmak | Analitik |
| Belge üretmek | Raporlar |
| Denize bakmak | Deniz Koşulları |
| Olay bildirmek | Güvenlik → Yeni olay |
| Soru sormak | YZ → AI Assistant |
| Veriyi korumak | Yedekleme ve Geri Yükleme |
| Kim ne değiştirmiş görmek | Denetim Kaydı |

| Kısayol | İşlev |
|---|---|
| `/` | Genel arama |
| `Esc` | Pencereyi kapat |
| Barkod kutusunda `Enter` | Kalemi ekle |
