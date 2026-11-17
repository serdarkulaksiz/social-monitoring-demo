# Demo ve tanıtım (Windows)

Bu sayfa, projeyi GitHub'dan indiren birinin Windows bilgisayarda demoyu çalıştırıp tanıtım yapması için yazıldı. Demo **uydurma (sentetik) gönderilerle** çalışır; gerçek veri, internet hesabı ya da API anahtarı gerekmez.

Demo şunu gösterir:
- Gönderiler toplayıcıdan (`social-data-collector`) izleme uygulamasına (`social-incident-monitor`) gelir.
- Her gönderi otomatik sınıflanır: şikâyet mi, hangi hizmet, hangi sorun. Bunu kurallar ve makine öğrenmesi modeli birlikte yapar.
- Aynı sorundan çok kişi şikâyet edince **olay** açılır ve alarm üretilir. Örneğin 30 kişi "mobil uygulama açılmıyor" diyorsa KRİTİK alarm üretilir.
- Analist olayı dashboard'da görür, onaylar, not ekler ve yanlış sınıflanan gönderiyi düzeltir.

## 1. İlk kurulum (bir kez)

1. **Python 3.13'ü kurun:** https://www.python.org/downloads/ adresinden 3.13 (ya da daha yeni) sürümü indirin. Kurulumun ilk ekranında **"Add python.exe to PATH"** kutusunu işaretleyin. Yönetici yetkisi gerekmez.
2. **Repoya erişim:** Repolar özeldir (private). GitHub hesabınızın iki repoya da eklenmiş olması gerekir; davet e-postasını kabul edin.
3. **İki projeyi aynı klasöre indirin.** Projenin son hâli `main` dalındadır. İki yoldan biri yeterli:

**A) ZIP ile (Git gerekmez):**
- GitHub'a giriş yapın.
- https://github.com/serdarkulaksiz/social-incident-monitor adresinde yeşil **Code** düğmesi → **Download ZIP**.
- https://github.com/serdarkulaksiz/social-data-collector için de aynısını yapın.
- İki ZIP'i aynı klasöre (ör. `C:\social-monitoring`) çıkarın. Klasör adlarının sonundaki `-main` kalabilir.

**B) Git ile** (https://git-scm.com/download/win kurulu ise; ilk komutta tarayıcıda GitHub girişi açılır). Başlat menüsünden "PowerShell" açıp şunları yazın:

```powershell
mkdir C:\social-monitoring
cd C:\social-monitoring
git clone https://github.com/serdarkulaksiz/social-incident-monitor.git
git clone https://github.com/serdarkulaksiz/social-data-collector.git
```

Sonuçta iki klasör yan yana olmalıdır:

```
C:\social-monitoring\
  social-incident-monitor\     (ZIP ile: social-incident-monitor-main)
  social-data-collector\       (ZIP ile: social-data-collector-main)
```

Klasörler yan yana değilse ya da toplayıcı hiç indirilmediyse demo yine çalışır. Bu durumda gönderiler toplayıcı yerine doğrudan izleme uygulamasına gönderilir.

## 2. Demoyu başlatma

`social-incident-monitor` klasöründeki **`demo-baslat.bat`** dosyasına çift tıklayın. Windows "bilgisayarınız korundu" uyarısı gösterirse **Ek bilgi → Yine de çalıştır** deyin.

- **İlk çalıştırmada** iki proje kurulur. Bu birkaç dakika sürer ve internet gerektirir. Kurumsal ağda `pip` hata verirse [INSTALLATION_WINDOWS.md](INSTALLATION_WINDOWS.md) dosyasındaki proxy adımlarına bakın.
- Sonra pencerede kısa bir özet görünür ve tarayıcıda dashboard açılır: http://127.0.0.1:8000/dashboard (8000 doluysa 8001, 8002… kullanılır; doğru adres pencerede yazar).

```
=== Demo hazır ===
Gönderi: 58   Şikâyet: 49   Kuralların kaçırıp modelin bulduğu şikâyet: 14
Olay: WARNING  atm / atm_failure (13 kişi)
Olay: CRITICAL mobile_banking / application_unavailable (30 kişi)
...
```

- **Durdurmak için** siyah pencerede `Ctrl+C` tuşlarına basın ya da pencereyi kapatın.
- Her başlatmada demo sıfırdan kurulur. Gönderilerin zamanı "şu an"a göre ayarlanır.
- Demo kendi veri klasörünü kullanır (`data\demo`). Asıl ayarlarınıza (`.env`) ve verinize dokunmaz.

Komut satırından da çalıştırılabilir:

```powershell
cd C:\social-monitoring\social-incident-monitor
.venv\Scripts\python scripts\demo.py               # toplayıcı ile
.venv\Scripts\python scripts\demo.py --direct      # toplayıcı olmadan, doğrudan API'ye
.venv\Scripts\python scripts\demo.py --port 8010   # belirli bir port
```

## 3. Güncelleme

Yeni sürümü almak için Git ile indirdiyseniz:

```powershell
cd C:\social-monitoring\social-incident-monitor
git pull
cd ..\social-data-collector
git pull
```

ZIP ile indirdiyseniz ZIP'leri yeniden indirip eski klasörlerin yerine çıkarın.

Bağımlılık listesi (`requirements.txt`) değiştiyse ilgili projenin `.venv` klasörünü silip `demo-baslat.bat` dosyasını yeniden çalıştırın.

## 4. Tanıtım akışı (yaklaşık 10 dakika)

| # | Nereye bakılır | Ne anlatılır |
|---|---|---|
| 1 | — | **Sorun:** Bir kesinti olduğunda müşteriler bunu önce sosyal medyada yazar. Banka bunu çağrı merkezi yoğunlaşmadan, dakikalar içinde görmek ister. |
| 2 | **Genel bakış** | İki olay açık. Mobil Bankacılık için **KRİTİK** (son 15 dakikada 30 farklı kişi), ATM için **UYARI** (13 kişi). Aynı kişinin 10 kez yazması tek kişi sayılır. Kesinti kişi sayısıyla ölçülür. |
| 3 | Genel bakış → **Veri toplama sağlığı** | Toplayıcının sağlıklı çalıştığı görünür. Toplayıcı durursa bu ayrı bir uyarı olarak çıkar; "şikâyet yok" ile "veri gelmiyor" birbirine karışmaz. |
| 4 | Mobil olayının **Detay** sayfası | Olayın grafiği, ne zaman başladığı, kanıt gönderileri. Gönderi kartlarında "model kararı uygulandı" notunu gösterin. Örnek: *"ziraat mobil uygulaması gitti yine, maaşımı göremiyorum"*. Kurallar bunu kaçırdı ("açılmıyor", "çöktü" geçmiyor), model şikâyet olduğunu anladı. **Model olmasa 18 kişi sayılıp yalnızca UYARI verilirdi. Model ile 30 kişi sayıldı ve KRİTİK oldu.** |
| 5 | Detay → **Onayla** / **Not ekle** | Üstteki "İşlemi yapan" kutusuna adınızı yazın, olayı onaylayın. Zaman çizelgesine kim, ne zaman yazıldı. Kayıt değiştirilemez (hash zinciri). Raporu Markdown ya da yazdırılabilir olarak indirin. |
| 6 | **İtibar** | "Banka batıyor, paranızı çekin" gibi söylentiler hizmet şikâyetlerinden ayrı izlenir. Kaç kişinin yazdığına ve ne kadar yayıldığına (paylaşım sayısı) bakılır. |
| 7 | **İnceleme** | Analist, emin olunamayan gönderileri "Doğru / Şikâyet değil / Kategori düzelt" ile işaretler. Sayılar anında düzelir. **"Model ile kural ayrışanlar"** listesi, etiketlenmesi en değerli gönderilerdir. |
| 8 | **Kalite** | Analist etiketlerine göre sistemin isabeti (kesinlik, yakalama) ve modelin kaç şikâyeti kurtardığı. |
| 9 | — | **Gerçek veriyle ölçüm** (aşağıdaki tablo) ve **sürekli öğrenme** (bölüm 5). |
| 10 | — | **Mimari:** Toplayıcı ve izleme uygulaması ayrı programlardır ve tek bir REST sözleşmesiyle konuşur ([API_CONTRACT.md](API_CONTRACT.md)). Başka bir ekip ya da firma aynı sözleşmeyle veri gönderebilir. |

**Gerçek X verisiyle ölçüm** (2026-11). Test seti 492 gönderiden oluşur ve etiketleri insan tarafından kontrol edildi. Model bu gönderileri eğitimde hiç görmedi.

| | Şikâyetleri doğru bulma (F1) | Yanlış alarm | Kaçan şikâyet |
|---|---|---|---|
| Yalnızca kurallar | 0,60 | 10 | 87 |
| Kurallar + model (kullanılan) | **0,86** | 14 | 28 |

## 5. Model nasıl öğrenmeye devam eder?

1. Analistler dashboard'da gönderileri etiketler ("şikâyet değil", "kategori düzelt"...).
2. Ayda bir (ya da yeterince yeni etiket birikince) bir kişi etiketleri dışa aktarır ve yeni model eğitir: `export-labels` → `train-model`.
3. `promote-model` yeni modeli sabit bir test setinde eskisiyle karşılaştırır. **Daha kötüyse devreye almaz.** Daha iyiyse devreye alır.
4. Sorun çıkarsa `promote-model --rollback` ile tek komutla bir önceki modele dönülür.

Model kendi kendine değişmez. Böylece birkaç hatalı etiket canlı sistemi bozamaz. Ayrıntılar: [MODEL.md](MODEL.md).

## 6. Tanıtımda açıkça söylenmesi gerekenler

- **Demo verisi uydurmadır.** Dashboard'daki kırmızı "SENTETİK VERİ MODU" bandı bunu belirtir. Ölçüm tablosu ise gerçek veriyle yapıldı.
- **Canlı toplama:** Toplayıcı şu an Bluesky'dan veri çekiyor. X (Twitter) için resmi API ücretlidir; X adaptörü hazır değildir. Modeli eğitmek için kullanılan X verisi elle toplandı.
- **Henüz olmayanlar:**
  - Veritabanı yok, veriler dosyada tutuluyor. Veritabanına yazan kod teslim aşamasında eklenecek.
  - Kullanıcı girişi (LDAP) yok.
  - E-posta/SMS bildirimi yok; bu kapsam dışı.
- **Model bilinen zayıflığı:** "Sorunsuz çalışıyor, teşekkürler" gibi olumlu ama sorun kelimesi geçen cümleleri bazen şikâyet sanıyor. Tek tük yanlışlar alarm üretmez, çünkü alarm için 15 dakikada en az 10 farklı kişi gerekir. Analist düzeltmeleriyle zamanla azalır.

## 7. Sorun giderme

| Belirti | Çözüm |
|---|---|
| "Python 3.13 bulunamadı" | Python'u kurarken "Add python.exe to PATH" işaretlenmemiş olabilir. Kurulumu "Modify" ile açıp işaretleyin ya da yeniden kurun. |
| `pip` bağlantı / SSL hatası | Kurumsal ağ: [INSTALLATION_WINDOWS.md](INSTALLATION_WINDOWS.md) içindeki proxy ve sertifika adımları. |
| "portu kullanılıyor" ya da "monitor exited early" | Açık kalmış eski demo pencerelerini kapatın ya da başka port verin: komut satırında `demo-baslat.bat --port 8010`. |
| Tarayıcı açılmadı | Siyah pencerede yazan "Dashboard:" adresini tarayıcıya elle yazın. |
| "Windows bilgisayarınızı korudu" | **Ek bilgi → Yine de çalıştır.** İnternetten indirilen .bat dosyalarında görünür. |
| "Gönderiler iletilemedi" | Toplayıcı kurulumu eksik olabilir. `--direct` ile toplayıcısız çalıştırın ya da `..\social-data-collector\.venv` klasörünü silip yeniden başlatın. |
| Kurulum yarıda kaldı | Projenin `.venv` klasörünü silip `demo-baslat.bat` dosyasını yeniden çalıştırın. |
