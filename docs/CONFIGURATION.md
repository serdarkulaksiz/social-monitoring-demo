# Konfigürasyon

Ayarlar `MONITOR_` önekli ortam değişkenlerinden okunur. Çalışma dizinindeki `.env` dosyası (veya `--env-file` ile verilen dosya) başlangıçta yüklenir. Ortamda zaten tanımlı değişkenler `.env` dosyasındaki değerlere göre önceliklidir.

Tanınmayan `MONITOR_*` değişkenleri yazım hatası kabul edilir ve uygulama başlamaz (`unknown setting`). Konfigürasyonu kontrol etmek için:

```
python -m social_incident_monitor check-config
```

| Değişken | Varsayılan | Etki |
|---|---|---|
| `MONITOR_API_KEY` | — (zorunlu) | Collector'ın `X-API-Key` başlığında gönderdiği gizli anahtar. En az 24 karakter. |
| `MONITOR_HOST` | `127.0.0.1` | Dinleme adresi. Collector başka bir makinedeyse ağ arayüzü adresi verilir ve erişim güvenlik duvarıyla sınırlandırılır. |
| `MONITOR_PORT` | `8000` | Dinleme portu |
| `MONITOR_DATA_DIR` | `data` | JSONL dosyalarının bulunduğu klasör. Göreli yol, çalışma dizinine göre çözülür. |
| `MONITOR_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MONITOR_ACCEPT_SYNTHETIC` | `false` | `true` ise örnek yalnızca `synthetic` kaynaklı demo verisini kabul eder ve dashboard'da uyarı bandı gösterir. Ayrı bir veri klasörüyle kullanılmalıdır. |
| `MONITOR_CLASSIFIER` | `rule_based` (`.env.example`: `hybrid`) | `hybrid`: kurallar ve metin modeli birlikte karar verir ([MODEL.md](MODEL.md)). `rule_based`: yalnızca kurallar. Model dosyası yoksa ya da bozuksa uyarı loglanır ve yalnızca kurallar çalışır. `llm` seçilirse, bir sağlayıcı adaptörü olmadığı için uygulama başlamaz. |
| `MONITOR_CLASSIFICATION_RULES_PATH` | (paket içi `rules_tr.json`) | Özelleştirilmiş kural dosyası |
| `MONITOR_TEXT_MODEL_PATH` | `<data_dir>/models/active.json` | Kullanılan model dosyası. `promote-model` buraya yazar. Dosya yoksa kodla gelen hazır model kullanılır. |
| `MONITOR_BUNDLED_TEXT_MODEL` | `true` | `false`: hazır model kullanılmaz; devreye alınmış model yoksa yalnızca kurallar çalışır. |
| `MONITOR_TEXT_MODEL_MODE` | `active` | `active`: model kuralların kaçırdığı şikâyeti ekleyebilir, kuralların bulduğu şikâyeti (çok eminse) reddedebilir. `shadow` (gölge): model hesaplanır ve dashboard'da gösterilir, ama sonuç ve alarmlar kurallardan gelir. |
| `MONITOR_TEXT_MODEL_RESCUE_P` | `0.6` | Modelin, kuralların kaçırdığı bir gönderiyi şikâyet sayması için gereken en düşük olasılık |
| `MONITOR_TEXT_MODEL_VETO_P` | `0.7` | Modelin, kuralların şikâyet / bankayla ilgili dediği bir gönderiyi (açık sorun ifadesi içerse bile) reddetmesi için gereken en düşük olasılık |
| `MONITOR_DETECTION_WINDOWS_MINUTES` | `5,15,30` | Sayım pencereleri (dakika) |
| `MONITOR_DETECTION_THRESHOLDS` | `15:10:25` | `pencere:warning:critical` üçlüleri, virgülle ayrılır. Değerler tekil kullanıcı sayısıdır. Pencere, `DETECTION_WINDOWS_MINUTES` listesinde bulunmalıdır. |
| `MONITOR_INCIDENT_RESOLVE_AFTER_MINUTES` | `30` | Eşik altı kalınan bu süreden sonra incident otomatik kapanır. |
| `MONITOR_EVALUATION_INTERVAL_SECONDS` | `60` | Arka plan değerlendirme aralığı (en az 5) |
| `MONITOR_COLLECTOR_STALE_AFTER_MINUTES` | `15` | Bu süre boyunca başarılı collector çalışması yoksa `STALE` durumu ve `collector_stale` alarmı oluşur. `0` bu kontrolü kapatır. |
| `MONITOR_COLLECTOR_FAILURE_THRESHOLD` | `3` | Art arda bu kadar başarısız çalışmadan sonra `collector_failing` alarmı oluşur. |
| `MONITOR_MAX_CLOCK_SKEW_SECONDS` | `300` | Gelecek tarihli gönderiler için tolerans |
| `MONITOR_DASHBOARD_ENABLED` | `true` | `/dashboard` sayfalarını açar veya kapatır. |
| `MONITOR_DASHBOARD_ACTIONS` | `local` | Dashboard'dan onay/çözüm/not/etiket işlemlerine kimin izinli olduğu. `local`: yalnızca aynı bilgisayardan (127.0.0.1) gelen istekler; `api_key`: `X-API-Key` gerekir (dashboard'da anahtar alanı çıkar); `off`: dashboard salt okunur. |
| `MONITOR_ALLOWED_HOSTS` | (boş: `127.0.0.1`, `localhost`) | Sunucunun yanıt verdiği host adları, virgülle ayrılır. `MONITOR_HOST` loopback değilse (ör. `0.0.0.0`) zorunludur. |
| `MONITOR_BASELINE_ENABLED` | `true` | Normal seviye (baseline) kuralını açar/kapatır. |
| `MONITOR_BASELINE_HISTORY_DAYS` | `14` | Normal seviyenin öğrenildiği geçmiş (gün) |
| `MONITOR_BASELINE_MIN_HISTORY_DAYS` | `7` | Kuralın devreye girmesi için gereken en az geçmiş (gün). Daha azsa yalnızca sabit eşikler çalışır. |
| `MONITOR_BASELINE_WINDOWS_MINUTES` | `5,15` | Normal seviyeyle karşılaştırılan pencereler. En kısası "hızlı artış" kontrolüdür. |
| `MONITOR_BASELINE_MIN_USERS` | `4:8` | `uyarı:kritik` için gereken en az farklı kişi |
| `MONITOR_BASELINE_P_VALUES` | `0.001:0.00001` | `uyarı:kritik` için en yüksek tesadüf olasılığı |
| `MONITOR_BASELINE_MIN_EXPECTED_USERS` | `0.1` | Sessiz saatler için normal seviyenin alt sınırı (15 dakikada kişi) |
| `MONITOR_REPUTATION_ENABLED` | `true` | İtibar / mevduat riski sinyalini açar/kapatır. |
| `MONITOR_REPUTATION_WINDOW_MINUTES` | `60` | İtibar sinyali penceresi |
| `MONITOR_REPUTATION_MIN_USERS` | `3:10` | `uyarı:kritik` için farklı kişi sayısı |
| `MONITOR_REPUTATION_AMPLIFICATION` | `50:500` | `uyarı:kritik` için yayılım (her kişinin en yüksek yeniden paylaşım + alıntı sayısının toplamı) |
| `MONITOR_TOPICS_WINDOW_MINUTES` | `120` | Yükselen konular için incelenen son dönem |
| `MONITOR_TOPICS_HISTORY_DAYS` | `7` | Yükselen konular için karşılaştırılan geçmiş |
| `MONITOR_TOPICS_MIN_USERS` | `3` | Bir terimin konu sayılması için gereken en az farklı kişi |

## Eşik değerleri hakkında

`15:10:25` başlangıç değeridir: 15 dakikada aynı hizmet ve problem için 10 farklı kullanıcı WARNING, 25 farklı kullanıcı CRITICAL üretir. Bu sayılar istatistiksel bir doğrulamaya dayanmaz. Yeterli geçmiş veri toplandıktan sonra gözden geçirilmelidir. 5 ve 30 dakikalık pencereler varsayılan olarak yalnızca yoğunluk göstergesi olarak hesaplanır; onlar için eşik tanımlanmamıştır. Gerekirse ör. `MONITOR_DETECTION_THRESHOLDS=5:5:12,15:10:25,30:15:40` ile eklenebilir.

## Normal seviye (baseline) kuralı

Kural, son `MONITOR_BASELINE_HISTORY_DAYS` gün içinde her (hizmet, sorun) ve günün her saati (UTC) için 15 dakikada normalde kaç farklı kişinin şikâyet ettiğini öğrenir. Geçmişteki büyük olaylar normal seviyeyi şişirmesin diye en yoğun %5'lik dilimler hesaba katılmaz. Şu anki sayı bu normal seviyeye göre Poisson dağılımıyla değerlendirilir: "Normal günde bu kadar kişinin tesadüfen şikâyet etme olasılığı" `MONITOR_BASELINE_P_VALUES` değerinin altındaysa ve kişi sayısı `MONITOR_BASELINE_MIN_USERS` değerine ulaşıyorsa alarm oluşur.

- Sessiz bir saatte normal seviye 0'a yakındır (en az 0,1). 15 dakikada 4 farklı kişi bu durumda UYARI üretir. Sabit eşik (10 kişi) aynı durumu yakalamaz.
- Kalabalık bir saatte aynı 4 kişi normal sayılır ve alarm oluşmaz.
- 5 dakikalık pencereden gelen alarm dashboard'da "Hızlı artış" olarak gösterilir.
- Geçmiş yetersizken dashboard'da kaç günlük veri olduğu gösterilir ve sabit eşikler çalışmaya devam eder. Türkiye'de yaz saati uygulaması olmadığından UTC saatleri günlük döngüyle birebir eşleşir.

## İtibar / mevduat riski sinyali

Sınıflandırma kuralları (`rules_tr.json` → `reputation_signals`) banka adının geçtiği gönderilerde üç sinyal arar:

- `solvency_rumor`: Batma/iflas söylentisi
- `deposit_flight`: Tüm parayı çekme, hesap kapatma, başka bankaya geçme
- `run_call`: Başkalarını para çekmeye çağırma

Seviye, pencere içindeki farklı kişi sayısı **veya** yayılım eşiği aşıldığında yükselir. Bu sinyaller hizmet arızası değildir: `REPUTATION_SIGNAL` türünde ayrı alarm üretir, dashboard'da ayrı "İtibar" sayfasında görünür ve hiçbir otomatik dış aksiyon tetiklemez. Yayılım sayıları collector'ın etkileşim yenilemelerinden gelir.

## Dashboard işlemleri ve erişim

Geliştirme aşamasında login yoktur. Güvenlik için:

- Sunucu varsayılan olarak `127.0.0.1` üzerinde dinler; sayfalar yalnızca o bilgisayardan açılabilir.
- `MONITOR_DASHBOARD_ACTIONS=local` iken işlemler yalnızca aynı bilgisayardan kabul edilir. İşlemi yapan kişinin adı dashboard'da bir kez girilir ve tarayıcıda saklanır; kayıtlara bu ad yazılır. Bu ad doğrulanmış bir kimlik değildir.
- İşlem istekleri `X-Dashboard-Action` başlığı ve Origin kontrolüyle başka web sitelerinden tetiklenemez (CSRF koruması). Bilinmeyen host adlarıyla gelen istekler reddedilir (DNS rebinding koruması).
- Monitor bir ters proxy arkasına veya ağa açılacaksa: `MONITOR_DASHBOARD_ACTIONS=api_key` (ya da `off`) kullanın ve `MONITOR_ALLOWED_HOSTS` değerini ayarlayın. Ters proxy aynı makinede çalışıyorsa tüm istekler 127.0.0.1'den geliyormuş gibi görüneceği için `local` modu bu kurulumda koruma sağlamaz.

## Ağ, proxy ve SSL

Monitor dışarıya HTTP isteği yapmaz; yalnızca gelen istekleri karşılar. Bu nedenle proxy ayarı gerekmez.

- Collector ile monitor aynı makinedeyse `127.0.0.1` kullanın. Collector tarafında `COLLECTOR_MONITOR_USE_PROXY=false` (varsayılan) olduğundan istekler kurumsal proxy'ye gitmez.
- TLS sonlandırma gerekiyorsa monitor'ü kurum standardındaki bir ters proxy (IIS ARR, nginx vb.) arkasında çalıştırın ve monitor'ü `127.0.0.1` adresinde bırakın.

## Çalışma zamanı dosyaları

`MONITOR_DATA_DIR` altında `posts.jsonl`, `analysis.jsonl`, `engagement.jsonl`, `feedback.jsonl`, `incidents.jsonl`, `incident_events.jsonl` (denetim kaydı), `alerts.jsonl`, `collection_runs.jsonl`, `monitor.lock` `export-labels` kullanıldıysa `exports/`, metin modeli kullanıldıysa `models/` (etkin model, arşiv, `history.jsonl`) oluşur. Model dosyaları gerçek gönderilerden öğrenilmiş kelimeler içerir; veri klasörü gibi korunur ve repoya eklenmez. Bu klasör `.gitignore` kapsamındadır ve repoya eklenmemelidir. Dosyalar gerçek kullanıcı adları ve gönderiler içerdiğinden erişimi kısıtlanmalıdır.
