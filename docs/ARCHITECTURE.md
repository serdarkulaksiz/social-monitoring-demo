# Mimari

## 1. Sistem bağlamı

```
+-----------------------+   REST / JSON (contract v1)   +--------------------------+
| social-data-collector | ----------------------------> | social-incident-monitor  |
| (ayrı repo, ayrı venv)|   X-API-Key                   | FastAPI + JSONL          |
+-----------------------+                               +--------------------------+
                                                               |  /api/v1/*, /dashboard
                                                               v
                                                          Operatör / izleme
```

İki uygulama arasındaki tek bağ `contracts/collector-monitor.v1.schema.json` dosyasıdır. Bu dosyanın aynısı iki repoda da bulunur. Kopyaların farklılaşması, her iki repodaki sözleşme testinde sabitlenen SHA-256 değeri ile yakalanır.

Monitor, collector çalışmıyorken de kullanılabilir: API, dashboard, incident yönetimi ve sağlık durumu (UNKNOWN/STALE) çalışmaya devam eder.

## 2. Katmanlar ve bağımlılık yönü

```
api/ (FastAPI router, şema, hata eşleme)     dashboard/ (HTML üretimi)
        |                                         |
        v                                         v
application/ (use case servisleri, ports.py içindeki Protocol sözleşmeleri)
        |
        v
domain/ (entity ve enum'lar, dış bağımlılık yok)

infrastructure/ (JSONL repository, dosya kilidi, saat, notifier, logging)
classification/ (RuleBasedClassifier, LLMClassifier adaptörü)
   -> her ikisi de application/ports.py içindeki sözleşmeleri uygular

container.py  -> composition root: somut sınıfları yalnızca burada seçer ve birbirine bağlar
```

Kurallar:

- `domain` hiçbir katmanı import etmez.
- `application` yalnızca `domain` ve kendi `ports` modülüne bağlıdır. JSONL dosyalarını, HTTP'yi veya belirli bir sınıflandırıcıyı bilmez.
- `api` iş mantığı içermez. Girdiyi doğrular, application servisini çağırır ve yanıt modelini döndürür.
- Somut adaptörler yalnızca `container.py` içinde oluşturulur (Dependency Injection).

## 3. Modüller ve sorumluluklar

| Modül | Sorumluluk |
|---|---|
| `domain/enums.py` | Hizmet/problem kategorileri, alarm seviyeleri, incident ve alarm durumları |
| `domain/models.py` | `Post`, `EngagementSnapshot`, `AnalysisResult`, `Feedback`, `Incident`, `IncidentAuditEvent`, `Alert`, `CollectorRun` entity'leri; deterministik `post_id`; denetim kaydı hash'i |
| `application/ports.py` | Repository, `AuditLog`, `Classifier`, `TermSource`, `Notifier`, `Clock` sözleşmeleri |
| `application/ingestion_service.py` | Kabul kuralları (sentetik/gerçek ayrımı, saat sapması), mükerrer ayıklama, ilk etkileşim kaydı |
| `application/engagement_service.py` | Etkileşim güncellemelerini uygular (en yeni gözlem geçerli) |
| `application/analysis_service.py` | Sınıflandırma ve sonucun saklanması; çökme sonrası analiz edilmemiş gönderileri tamamlama |
| `application/feedback_service.py` | Analist etiketleri; `FeedbackAwareAnalyses` etiketleri sınıflandırıcı sonucunun yerine geçirir |
| `application/quality_service.py` | Sınıflandırma kalitesi ve inceleme kuyruğu |
| `application/detection/window_aggregator.py` | Kayan pencerelerde şikâyet ve tekil kullanıcı sayımı |
| `application/detection/rules.py` | `DetectionRule` sözleşmesi, `RuleContext`, `AbsoluteThresholdRule` |
| `application/detection/baseline.py` | Normal seviye profili (saat bazında) ve `BaselineRule` |
| `application/detection/reputation.py` | İtibar sinyali sayımı (kişi + yayılım) ve `ReputationRule` |
| `application/detection/engine.py` | Tespit hatlarını (operasyonel, itibar) çalıştırır, grup başına en yüksek seviyeyi seçer |
| `application/probability.py` | Poisson kuyruk olasılığı (baseline ve konu tespiti) |
| `application/incident_service.py` | Incident yaşam döngüsü, yükseltme, otomatik kapanış, işlemi yapan kişi |
| `application/audit_service.py` | Hash zincirli denetim kaydına yazma |
| `application/incident_views.py` | Zaman çizelgesi, kanıt gönderileri, süreler, rapor |
| `application/alert_service.py` | Alarm üretimi, tekrar önleme, notifier'lara dağıtım, collector sağlık alarmları |
| `application/collector_health_service.py` | Collector çalışma raporlarından sağlık durumu |
| `application/topics_service.py` | Yükselen konular |
| `application/monitoring_service.py` | Durum değiştiren tüm işlemleri tek kilit altında sıraya koyan koordinatör |
| `application/query_service.py` | API ve dashboard için salt okunur görünümler (zaman serileri, iller, itibar gönderileri) |
| `classification/rule_based.py` + `rules_tr.json` | Türkçe kural tabanlı sınıflandırıcı ve itibar sinyalleri; kurallar veri dosyasındadır |
| `classification/locations.py` + `locations_tr.json` | 81 il için konum ipucu |
| `classification/terms.py` + `stopwords_tr.json` | Konu terimleri (ilk 5 harf kökü) |
| `classification/llm.py` | Sağlayıcıdan bağımsız LLM adaptör sözleşmesi (varsayılan olarak devre dışı) |
| `infrastructure/jsonl/*` | JSONL dosya erişimi ve repository implementasyonları |
| `infrastructure/file_lock.py` | Veri klasörü için tek süreç kilidi (Windows: `msvcrt`, POSIX: `fcntl`) |
| `api/*` | FastAPI uygulaması, router'lar, API key ve dashboard işlem korumaları, güvenlik başlıkları, hata gövdeleri |
| `dashboard/*` | Türkçe Jinja2 şablonları, SVG grafikler, sayfa verileri, Markdown rapor, statik CSS/JS |

## 4. Veri akışları

### 4.1 Gönderi kabulü

```
POST /api/v1/posts/batch
  -> zarf doğrulaması (Pydantic, 422)
  -> her öğe için SocialPostV1 doğrulaması (geçersiz öğeler 'rejected' listesine)
  -> MonitoringService.process_batch  [tek kilit]
       -> IngestionService: kabul kuralları + add_if_absent (mükerrer => duplicates)
       -> AnalysisService: yeni gönderileri sınıflandır, analysis.jsonl'e yaz
       -> değerlendirme (4.2)
  -> {received, accepted, duplicates, rejected, incidents_opened, alerts_created}
```

### 4.2 Değerlendirme (her batch'ten, etkileşim güncellemesinden, analist etiketinden sonra ve periyodik olarak)

```
DetectionEngine.detect(now)
  -> operasyonel hat: ComplaintWindowAggregator (is_complaint=true, created_at'e göre pencereler)
       -> AbsoluteThresholdRule + BaselineRule
  -> itibar hattı: ReputationAggregator (risk_signals, kişi + yayılım) -> ReputationRule
  -> her grup için en yüksek seviye -> DetectionSignal
  (tüm hatlar FeedbackAwareAnalyses üzerinden okur: analist etiketi sınıflandırıcının yerine geçer)
IncidentService.reconcile(signals)
  -> OPENED / ESCALATED / UPDATED / RESOLVED olayları
AlertService.handle_incident_events
  -> OPENED, ESCALATED => yeni alarm (aynı incident + aynı seviye için en fazla bir alarm)
  -> ACKNOWLEDGED / RESOLVED => ilgili alarmların durumunu günceller
AuditService.record_incident_events
  -> OPENED / ESCALATED / LEVEL_CHANGED / RESOLVED -> incident_events.jsonl (hash zinciri)
CollectorHealthService.evaluate + AlertService.sync_collector_health
```

Periyodik değerlendirme, FastAPI lifespan içinde arka plan görevi olarak `MONITOR_EVALUATION_INTERVAL_SECONDS` aralığında çalışır. Yeni veri gelmese bile zamanla kapanan incident'lar ve bayatlayan collector durumu bu sayede güncellenir.

### 4.3 Collector çalışma raporu

`POST /api/v1/collector-runs` -> `CollectorRun` kaydı. `received_at` her zaman monitor saatiyle damgalanır. Aynı `run_id` tekrar gönderilirse kayıt eklenmez (idempotent) ve sağlık durumu yeniden değerlendirilir.

### 4.4 Etkileşim güncellemesi

`POST /api/v1/posts/engagement` -> `EngagementService.apply` -> `engagement.jsonl` (gönderi başına en yeni `observed_at` geçerli) -> değerlendirme. İtibar sinyalinin yayılım ölçüsü bu sayılardan hesaplanır.

### 4.5 Analist etiketi

`POST /api/v1/analysis/{post_id}/feedback` (veya dashboard işlemi) -> `FeedbackService.submit` (o anki sınıflandırıcı tahmini de saklanır) -> `feedback.jsonl` -> etkilenen aktif incident'lara `FEEDBACK_APPLIED` -> değerlendirme. Sınıflandırıcının kendi sonucu değişmez; kalite ölçümü tahmin ile etiketi karşılaştırır.

### 4.6 Onay, çözüm, not

Dashboard veya API -> `MonitoringService` (tek kilit) -> `IncidentService` (kim, ne zaman, not) -> `AuditService` -> `AlertService` (alarm durumları).

## 5. Interface'ler

| Sözleşme | Mevcut implementasyon | Gelecekteki olası implementasyon |
|---|---|---|
| `PostRepository`, `AnalysisRepository`, `EngagementRepository`, `FeedbackRepository`, `IncidentRepository`, `AlertRepository`, `CollectorRunRepository` | `Jsonl*Repository` | PostgreSQL adaptörü |
| `AuditLog` | `JsonlAuditLog` (hash zincirli) | Veritabanı tablosu + WORM arşiv |
| `Classifier` | `RuleBasedClassifier` | Yerel Türkçe model (ayrı servis), `LLMClassifier` + somut `LLMClient` |
| `DetectionRule` | `AbsoluteThresholdRule`, `BaselineRule`, `ReputationRule` | Sektör karşılaştırma kuralı |
| `TermSource` | `TermExtractor` | Gömme (embedding) tabanlı konu çıkarımı |
| `Notifier` | `LogNotifier` | E-posta, Microsoft Teams |
| `Clock` | `SystemClock` | Testlerde `FakeClock` |

Repository sözleşmeleri aggregate başına ayrı tutulmuştur (Interface Segregation). Örneğin incident servisi gönderi deposunu hiç görmez.

## 6. SOLID kararları

- **SRP:** Tespit (engine), yaşam döngüsü (incident service), bildirim (alert service) ve sağlık (collector health) ayrı sınıflardadır. Router'lar yalnızca HTTP ile ilgilenir.
- **OCP:** Yeni bir veri kaynağı yalnızca collector'ı etkiler. Yeni bir kural için `DetectionRule`, yeni bir bildirim kanalı için `Notifier`, yeni bir sınıflandırıcı için `Classifier` implementasyonu eklenir ve `container.py` içinde bağlanır; mevcut servisler değişmez. Kategori anahtar kelimeleri `rules_tr.json` dosyasındadır.
- **LSP:** Testler JSONL repository'leri ve sahte saat ile çalışır. Aynı sözleşmeyi uygulayan her bileşen servis koduna dokunmadan yerine konabilir.
- **ISP:** Protokoller küçük tutulmuştur; her servis yalnızca ihtiyaç duyduğu repository'yi alır.
- **DIP:** Application katmanı `ports.py` içindeki soyutlamalara bağlıdır. JSONL, FastAPI ve uvicorn yalnızca dış katmanlarda kullanılır.

Bilinçli olarak eklenmeyenler: generic repository taban sınıfı, event bus, plugin keşif mekanizması. Bunların POC'de somut bir ihtiyacı yoktur.

## 7. Depolama tasarımı (JSONL)

| Dosya | Strateji |
|---|---|
| `posts.jsonl` | Append-only; `post_id = sha256(source, source_post_id)` ile tekil |
| `analysis.jsonl` | Append-only; aynı gönderi için son sonuç geçerlidir |
| `incidents.jsonl` | Versiyonlu kayıt: her değişiklik tam snapshot + `version`. Yüklemede en yüksek sürüm geçerlidir |
| `alerts.jsonl` | Versiyonlu kayıt (durum değişiklikleri için) |
| `collection_runs.jsonl` | Append-only; `run_id` ile tekil |
| `engagement.jsonl` | Append-only; gönderi başına en yeni `observed_at` geçerlidir |
| `feedback.jsonl` | Append-only; gönderi başına son etiket geçerlidir, önceki etiketler dosyada kalır |
| `incident_events.jsonl` | Append-only ve hash zincirli denetim kaydı; `compact` bu dosyaya dokunmaz. Başlangıçta doğrulanır; bozulma loglanır ve `verify-audit` ile raporlanır |

Ele alınan senaryolar:

- **Tekrarlı kayıt:** Bellek içi indeks, yazmadan önce kontrol eder.
- **Yeniden başlatma:** Dosyalar açılışta akış halinde okunur ve indeksler yeniden kurulur. Analizi eksik kalmış gönderiler `recover()` ile tamamlanır.
- **Bozuk satır / yarım yazma:** Geçersiz JSON veya şema dışı kayıtlar atlanır, sayılır ve satır numarasıyla loglanır (içerik loglanmaz). Sayı `/api/v1/stats` ve dashboard'da görünür. Yarım kalan son satır, ilk yazmadan önce bir satır sonu eklenerek izole edilir.
- **Eş zamanlı erişim:** Süreç içinde tüm yazmalar `MonitoringService` kilidiyle sıralanır. Süreçler arasında veri klasöründeki `monitor.lock` dosya kilidi ikinci örneği engeller. `serve` her zaman `workers=1` ile başlar.
- **Dosya bulunamaması:** Boş depo olarak kabul edilir; dosyalar ilk yazmada oluşturulur.
- **Disk yazma hatası:** `OSError` -> `StorageError` -> HTTP 503. Bellek, yalnızca disk yazması başarılı olduktan sonra güncellenir.
- **Büyük dosyalar:** Okuma satır satır yapılır. Zaman penceresi sorguları `created_at` üzerinde sıralı indeks (bisect) kullanır. Tüm veri bellekte tutulduğu için bellek kullanımı veri hacmiyle doğrusal artar.

**Kısıt:** Bu depolama yalnızca tek yazıcı süreç ile güvenlidir. Çok süreçli veya yatay ölçeklenen bir kurulum için veritabanı adaptörü gerekir.

`fsync` her yazmada çağrılır. Güç kesintisinde son kayıt kaybolabilir veya yarım kalabilir; yarım kayıt okuma sırasında atlanır.

## 8. Güvenlik ve veri minimizasyonu

- `/api/v1/*` uç noktaları `X-API-Key` ister ve anahtarı `secrets.compare_digest` ile sabit zamanlı karşılaştırır. `/health` ve dashboard sayfaları anahtar istemez; varsayılan dinleme adresi `127.0.0.1`'dir.
- API anahtarı log formatter'ında maskelenir. Gönderi metinleri ve kullanıcı adları loglanmaz.
- **Kişisel veri:** Yazarın platform kimliği (Bluesky DID), kullanıcı adı ve görünen adı maskelenmeden saklanır ve API ile dashboard'da gösterilir. Avatar alınmaz. Erişim ve saklama süresi kurum politikasına göre belirlenmelidir.
- **Dashboard:** Jinja2 autoescape ile her metin kaçışlanır; yalnızca `http(s)://` linkler gösterilir. Sıkı CSP (satır içi script/stil yok), `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `nosniff`. Bilinmeyen host adları reddedilir (DNS rebinding). İşlemler `MONITOR_DASHBOARD_ACTIONS` ile korunur; CSRF'e karşı özel başlık, Origin ve `Sec-Fetch-Site` kontrolü yapılır.
- **Denetim kaydı:** Her kayıt bir öncekinin hash'ini içerir; bir kaydın değiştirilmesi, silinmesi veya sırasının bozulması `verify-audit` ile tespit edilir. Login olmadığından "işlemi yapan" alanı kişinin kendi yazdığı addır; kimlik doğrulama eklendiğinde bu alan dizin kimliğinden doldurulacaktır.
- İtibar sinyalleri hiçbir otomatik dış aksiyon tetiklemez; ayrı alarm türü ve ayrı sayfada gösterilir.
- Sentetik ve gerçek veri karışmaz: `MONITOR_ACCEPT_SYNTHETIC=true` olan örnek yalnızca `synthetic` kaynağını, diğer örnekler yalnızca gerçek kaynakları kabul eder. Sentetik modda dashboard'da uyarı bandı görünür.
- LLM sınıflandırıcı varsayılan olarak devre dışıdır ve harici bir servise veri göndermez.
