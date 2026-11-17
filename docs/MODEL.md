# Metin modeli ve etiketleme

Kural tabanlı sınıflandırıcı yazılı ifadeleri iyi yakalar. Yazım hatalarını ("acilmiyo"), kurala yazılmamış ifadeleri ("tarihi eser uygulamanızı geliştirin") ve ironiyi ise kaçırır. Metin modeli, analistlerin etiketlediği örneklerden öğrenir ve kuralların **yanında** çalışır. Kuralların yerini almaz.

## Neden saf Python?

- **Yeni bağımlılık yok.** Model yalnızca Python standart kütüphanesiyle yazılmıştır (`json`, `math`, `re`, `random`, `collections`). `requirements.txt` değişmez; scikit-learn, numpy, PyTorch ya da BERT kullanılmaz.
- **Denetlenebilir.** Model kodu `src/social_incident_monitor/classification/model/` altında birkaç yüz satırdır.
- **Model dosyası okunabilir.** Model bir JSON dosyasıdır: içinde kod yoktur, pickle kullanılmaz. Dosya kendi SHA-256 özetini taşır; içerik değiştirilmişse ya da bozulmuşsa model yüklenmez ve sistem yalnızca kurallarla çalışır.
- **Hızlı.** Gönderi başına tahmin milisaniyenin altındadır, eğitim birkaç bin gönderide saniyeler sürer. CPU yeterlidir.

## Model nasıl karar verir?

1. **Özellikler.** Gönderi şu parçalara ayrılır:
   - kelimeler ve kelime ikilileri ("sorun yok"),
   - 3–5 harflik parçalar: "acilmiyo" ile "açılmıyor" benzer görünür,
   - olumsuzluk işaretleri ("sorun yok", "çekemiyorum"),
   - emoji ve noktalama ipuçları (👏, "!!", "..."),
   - **kuralların bulduğu şeyler.** Model kuralların bilgisini kaybetmez, onların üstüne öğrenir.
   - **hashtag'ler ayrı tutulur** ("#çöktü" çoğunlukla bir şikâyet değil, gündemdeki siyasi bir etiket) ve spam ipuçları eklenir: çok sayıda hashtag, çok sayıda etiketlenen hesap, isim listesi.
   
   `@kullanıcı` adları ve linkler önce sabit bir kelimeye çevrilir. Kişi adları modele girmez.
2. **Dört karar ("baş").** Bankayla ilgili mi, şikâyet mi, hangi hizmet, hangi sorun. Örneği yetersiz olan bir karar öğrenilmez; o konuda kuralların cevabı geçerli kalır.
3. **İki algoritma.**
   - **Naive Bayes (`nb`):** Basit ve hızlıdır, olasılıkları abartma eğilimindedir.
   - **TF-IDF + lojistik regresyon (`logreg`):** Genelde daha dengelidir.
   
   İkisi de aynı test setinde ölçülür, iyi olan seçilir.
4. **Açıklama.** Her kararın yanında, kararı en çok etkileyen kelimeler gösterilir. Örnek: "belanızı, tarihi, geliştirin".

## Kuralların model için tuttuğu ek bilgiler (tr-1.3)

- **Hashtag içindeki sorun kelimeleri** ("#çöktü") sorun ifadesi sayılmaz. Bankanın adı hashtag'de geçerse ("#ziraatbankası") yine sayılır.
- **Başka bankaların adı** (Yapı Kredi, VakıfBank, Halkbank, Akbank…) kararı değiştirmez, ama `other_bank` olarak kaydedilir; model bundan gönderinin başka bir bankayla ilgili olabileceğini öğrenir.
- **Genel kesinti (tr-1.4):** Sorun bir kesinti ifadesiyse ("çöktü", "açılmıyor") ve gönderi hiç kanal adı vermiyorsa ("Ziraat çöktü"), birden çok kanal sayıyorsa ("mobil, ATM ve POS çalışmıyor") ya da "tüm sistem", "hiçbir işlem" diyorsa servis **Tüm kanallar / genel kesinti** olur. Böylece aynı kesinti tek bir olay olarak toplanır.
- **POS / üye işyeri (tr-1.4):** "pos cihazı", "poslar", "üye işyeri" ayrı bir servis; esnafın tahsilat sorunları buraya düşer.
- **Fon konusu (tr-1.4):** Fon tasfiyesi ve yatırım fonları (fon, tasfiye, PPF, TEFAS, PKV, Pardus, Tera, ara ödeme, portföy…) geçen gönderiler bankayla ilgili sayılır ama **asla şikâyet sayılmaz**, dolayısıyla alarm üretmez. Model de bu gönderilerde kararı değiştiremez. Dashboard'da "konu: fon / yatırım (alarm dışı)" olarak görünür.
- **Bankanın kendi hesapları** (@ZiraatDestek, @ZiraatBankasi…) bankayla ilgili sayılır ama asla şikâyet sayılmaz. Liste `rules_tr.json` içindeki `official_accounts`.

## Kurallar ve model birlikte (`MONITOR_CLASSIFIER=hybrid`)

Eşikler (%60 / %70), ilk etiketli X verisinde (16 Kasım 2026, 1.649 gönderi) eğitim verisinden ayrılan bir doğrulama parçasında seçildi. Kilitli test setinde (492 gönderi), kurallar `tr-1.4` ve yeni kategorilerle (etiketler v2):

| | Şikâyet F1 | Kesinlik | Yakalama | Yanlış alarm | Kaçan | Kategori doğruluğu |
|---|---|---|---|---|---|---|
| Kurallar | 0,594 | 0,866 | 0,452 | 11 | 86 | %61 |
| Yalnızca model | 0,868 | 0,877 | 0,860 | 19 | 22 | %59 |
| Kural + model | 0,861 | 0,897 | 0,828 | 15 | 27 | %55 |

Kategori sayıları küçük örneklemden geliyor (yakalanan ~100 şikâyet) ve etiketlerin bir kısmı yeni kategorilere kurallarla aktarıldı; insan kontrolünden sonra yeniden ölçülmeli. Etiketler henüz insan kontrolünden geçmedi; kontrol sonrası yeniden ölçülmeli.

| Durum | Kim karar verir |
|---|---|
| Kural açık bir sorun ifadesi buldu ("kartımı yuttu") | Kural; ancak model ≥ %70 "şikâyet değil" diyorsa **model** (`model_veto`). Örnek: dizi veya maç haberinde geçen "çöktü" |
| Gönderi bir itibar sinyali | Kural |
| Banka adı geçmiyor (ör. bankanın hesabına yazılmış bir yanıt) ve model %60'tan az emin ki gönderi bankayla ilgili | Kural |
| Kural şikâyet bulmadı, model ≥ %60 "şikâyet" diyor | **Model:** şikâyet sayılır (`model_rescue`) |
| Kural yalnızca genel kelimelerden ("rezalet", "sorun") şikâyet dedi, model ≥ %70 "değil" diyor | **Model:** şikâyet sayılmaz (`model_veto`) |
| Banka yalnızca marka kelimesiyle geçiyor (tam adı yazılmamış), model ≥ %70 "ilgisiz" diyor | **Model:** bankayla ilgisiz (`model_veto`) |
| Şikâyetin hizmeti ve sorunu (kural bulmuş olsa da) | Model ≥ %30 eminse **modelin** kategorisi, değilse kuralınki (`model_category`). Kurallar sabit bir sıraya göre ilk kelimeyi seçiyor ("mobil, ATM, POS çöktü" → ATM); model analistlerin seçimini öğreniyor. |
| Model dosyası yok ya da bozuk | Yalnızca kurallar |

Önem derecesi, itibar sinyalleri ve iller her zaman kurallardan gelir.

### Aktif mod (varsayılan) ve gölge mod

`MONITOR_TEXT_MODEL_MODE=active` (varsayılan) iken model yukarıdaki tabloya göre kuralların cevabını değiştirebilir: kuralların kaçırdığı şikâyeti ekler, çok eminse kuralın yanlış bulduğunu reddeder. Alarmlar bu son karara göre sayılır. Dashboard'da şunlar görünür:
- **Gönderi kartları:** Modelin şikâyet olasılığı, kararı etkileyen kelimeler ve "model kararı uygulandı" notu.
- **İnceleme → "Model ile kural ayrışanlar":** Model ile kuralların farklı karar verdiği gönderiler. Etiketlenmesi en değerli gönderiler bunlardır.
- **Kalite sayfası:** Son 7 günde modelin kaç şikâyeti kurtardığı, kaçını reddettiği.

`MONITOR_TEXT_MODEL_MODE=shadow` (gölge) iken model yine hesaplanıp gösterilir, ama gönderiler ve alarmlar kuralların cevabıyla kalır. Yeni bir modeli canlıda bir süre izlemek için kullanılabilir.

### Hazır model

Kodla birlikte eğitilmiş bir model gelir: `src/social_incident_monitor/classification/model/bundled/text-model.json`. 2026-11 tarihli, elle etiketlenmiş ~1.150 X gönderisiyle eğitildi. İnsan tarafından kontrol edilmiş test setinde şikâyet F1 değeri kural + model için 0,86, yalnızca kurallar için 0,60.

- `promote-model` ile bir model devreye alınana kadar bu model kullanılır. İlk `promote-model`, adayı bu modelle karşılaştırır.
- Dosya gönderi metni, kullanıcı adı ya da link içermez. Yalnızca kelime ve kelime parçası ağırlıkları vardır.
- Kapatmak için: `MONITOR_BUNDLED_TEXT_MODEL=false` (o zaman yalnızca devreye alınmış model ya da yalnızca kurallar çalışır).

## Eğitim döngüsü

Gerçek gönderiler içeren dosyalar **repoların dışında** tutulur. Önerilen klasör: `social-monitoring-workspace/datasets/`. Model dosyaları varsayılan olarak `<data_dir>/models/` altına yazılır. Bu klasör de veri klasörü gibi korunur.

```bash
# 1. Elle hazırlanmış CSV'yi etiket satırlarına çevir (ya da export-labels çıktısını kullan)
python -m social_incident_monitor import-dataset --file ../datasets/gonderiler.csv

# 2. Eğitim ve kilitli test setine ayır (%30 test). Test seti ile ASLA eğitim yapılmaz.
python -m social_incident_monitor split-dataset --labels ../datasets/gonderiler.labels.jsonl

# 3. Modeli eğit; kurallar, model ve birleşim test setinde yan yana ölçülür
python -m social_incident_monitor train-model --train ../datasets/split/train.jsonl --test ../datasets/split/test.jsonl --algorithm logreg
python -m social_incident_monitor train-model --train ../datasets/split/train.jsonl --test ../datasets/split/test.jsonl --algorithm nb

# 4. İstenirse tekrar ölç (kaynak bazında kırılımla)
python -m social_incident_monitor evaluate --labels ../datasets/split/test.jsonl --model data/models/model-logreg-....json

# 5. Kalite kapısı: model, mevcut modelden (yoksa yalnızca kurallardan) kötüyse devreye girmez
python -m social_incident_monitor promote-model --candidate data/models/model-logreg-....json --test ../datasets/split/test.jsonl

# Sorun çıkarsa bir önceki modele (ya da hazır modele) dön
python -m social_incident_monitor promote-model --rollback
```

`promote-model` ve `--rollback` komutlarından sonra servis yeniden başlatılır. Geçmiş gönderileri yeni modelle sınıflandırmak için servis durdurulup `reanalyze --since 14d` çalıştırılır.

### Kalite kapısı

`promote-model`, aday modeli ve mevcut durumu (kurallar + mevcut model) aynı test setinde ölçer.
- Şikâyet F1 değeri ya da yakalama oranı (recall) düşüyorsa model devreye girmez ve komut çıkış kodu 5 ile biter.
- Kapıyı geçen modelin dosyası `models/active.json` olur (bu dosya yoksa hazır model kullanılır). Eski model `models/archive/` altında saklanır, her işlem `models/history.jsonl` dosyasına yazılır.

### Ölçüler

| Ölçü | Anlamı |
|---|---|
| Kesinlik (P) | "Şikâyet" denenlerin ne kadarı gerçekten şikâyet. Düşükse **yanlış alarm** artar. |
| Yakalama (R) | Gerçek şikâyetlerin ne kadarı yakalandı. Düşükse **kaçan şikâyet** artar. |
| F1 | Kesinlik ile yakalamanın dengesi |
| yanlış alarm / kaçan | Test setindeki yanlış pozitif / yanlış negatif sayısı |
| kategori | Yakalanan şikâyetlerde hizmet ve sorunun ikisinin de doğru olma oranı |

Sonuçlar kaynak bazında da verilir (`sorgu1`, `genis`, `karisan`…). Böylece sorguların kaçırdıkları ayrıca görülür.

### Ne zaman yeniden eğitilir?

Model kendi kendine değişmez. Analistlerin dashboard'da verdiği her etiket bir sonraki eğitime girer, ama yeni model ancak bir kişi `train-model` ve `promote-model` çalıştırıp kalite kapısı geçilince devreye girer. Böylece hatalı birkaç etiket canlı sistemi bozamaz.

- **Başlangıçta:** Yeni etiketler test sonucunu iyileştirdikçe eğitime devam edilir (1.000 → 2.000 → 5.000…). Sonuç artık iyileşmiyorsa eğri düzleşmiştir.
- **Sonrasında:** Ayda bir, son dönemin gönderilerinden küçük bir rastgele örneklem etiketlenip ölçülür. Başarı düştüyse (yeni uygulama, yeni argo, yeni sorun türü) yeniden eğitilir.
- **Kural dosyası değiştiğinde de** yeniden eğitilir, çünkü kuralların sonuçları modelin girdileri arasındadır.

## Veri hazırlama ve etiketleme kılavuzu

### CSV sütunları

| Sütun | Değerler | Zorunlu |
|---|---|---|
| `metin` | Gönderi metni | evet |
| `tarih` | `2026-11-14 09:30` ya da `14.11.2026 09:30` | hayır |
| `kaynak` | `sorgu1` … `sorgu4`, `genis`, `karisan` | önerilir |
| `banka_ilgili` | evet / hayır | etiket için |
| `sikayet` | evet / hayır | `banka_ilgili` evet ise |
| `servis` | mobil, internet, fast (eft/havale), atm, kart, pos (üye işyeri), ödeme, müşteri hizmetleri, tüm kanallar (genel kesinti), diğer, belirsiz | şikâyette |
| `sorun` | giriş, transfer, erişim, ödeme, gecikme, atm, kart, ulaşılamıyor, dolandırıcılık, diğer | şikâyette |
| `not` | Serbest not ("ücret", "tasarım" gibi; "diğer" seçildiğinde yararlı) | hayır |

- Ayraç virgül, noktalı virgül ya da sekme olabilir. Excel'den "CSV UTF-8" olarak kaydedilmesi yeterlidir.
- Etiketsiz satırlar atlanır; aynı metin bir kez alınır.
- Hatalı satırlar satır numarasıyla bildirilir.

### Veri karışımı

| Pay | Kaynak | Neden |
|---|---|---|
| ~%60 | 4 filtreli sorgu | Sistemin gerçekte göreceği akış |
| ~%25 | Geniş arama: yalnızca "Ziraat" geçen gönderiler | Sorguların kaçırdığı şikâyetler ve ilgisiz gönderiler |
| ~%15 | Karışan örnekler | Kupa, tarım, ironi, "artık sorun yok" |

### Ziraat'e özgü kararlar

| Gönderi | Etiket |
|---|---|
| Ziraat Türkiye Kupası, maç, voleybol | bankayla ilgisiz |
| Ziraat fakültesi, ziraat mühendisi, ziraat odası, tarım | bankayla ilgisiz |
| **Ziraat Katılım** (ayrı kurum) | **bankayla ilgisiz.** Kurallar da Ziraat Katılım'ı ayrı kurum sayar. |
| Bankkart, Ziraat Mobil (banka adı geçmese de) | bankayla ilgili |
| "Ziraat'ten X bankasına geçtim / hesabımı kapattım" | bankayla ilgili; itibar sinyali (para çekme / hesap kapatma) |
| "Hesabımdan haberim olmadan para çekilmiş", "kartım kopyalanmış" | şikâyet; sorun: **dolandırıcılık** |
| "Uygulama tarihi eser, menüyü bulamıyorum" (kesinti değil, kullanım zorluğu) | şikâyet; servis: mobil; sorun: diğer; not: "kullanılabilirlik" |
| Ücret, aidat, faiz şikâyeti | şikâyet; sorun: diğer; not: "ücret" |
| "Ziraat çöktü", "mobil, ATM ve POS çalışmıyor" | şikâyet; servis: **tüm kanallar**; sorun: erişim |
| "POS cihazı çalışmıyor", "POS'taki para hesaba geçmedi" | şikâyet; servis: **pos**; sorun: erişim / gecikme |
| Fon tasfiyesi, PKV/PPF/TP2, ara ödeme yazışmaları | bankayla ilgili, **şikâyet değil**; not: "fon" (alarm dışı konu) |
| Soru ("faiz oranı ne kadar?") | bankayla ilgili, şikâyet değil |
| İroni ("harika, yine para çekemedim 👏") | şikâyet |

"Diğer" kategorisine düşen gönderiler ilk 1.000 gönderiden sonra notlarına göre gözden geçirilecek. Sık çıkan konular (ücret, kullanılabilirlik, şube) ayrı kategori yapılabilir. Bu durumda yalnızca "diğer" etiketli gönderilerin yeniden etiketlenmesi gerekir.

### Test setinin kuralları

- Test seti eğitimde hiç kullanılmaz. `train-model`, eğitim verisinde test setinden bir satır görürse durur.
- Test setindeki etiketler insan tarafından kontrol edilmiş olmalıdır. Kural ya da başka bir model önerisi körü körüne onaylanmaz.
- Aynı test seti her ölçümde kullanılır. Böylece kurallar, eski model ve yeni model adil karşılaştırılır.
