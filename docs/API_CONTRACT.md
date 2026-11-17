# API Sözleşmesi

## Versiyonlama

- URL versiyonu: `/api/v1/...`
- Veri sözleşmesi: `contracts/collector-monitor.v1.schema.json` (JSON Schema 2020-12), sürüm **1.1**. Batch ve çalışma raporu nesnelerinde `schema_version` zorunludur; gönderide isteğe bağlıdır (yazılmazsa batch'in sürümü geçerlidir). Değeri `"1.0"` veya `"1.1"` olabilir. 1.1 yalnızca isteğe bağlı alanlar (`author_handle`, `author_display_name`, `engagement`) ve etkileşim uç noktası ekler; 1.0 yükleri geçerliliğini korur (ör. collector kuyruğunda bekleyen eski kayıtlar).
- Bu şema dosyasının aynısı `social-data-collector` reposunda da bulunur. Uyumlu olmayan her değişiklik yeni bir ana sürüm (`v2`) ve iki repoda koordineli güncelleme gerektirir. Her iki repodaki `tests/unit/test_contract.py` dosyası, şema dosyasının SHA-256 değerini sabitler.
- Örnek yükler: `contracts/examples/posts-batch.v1.json`, `contracts/examples/engagement-batch.v1.json`, `contracts/examples/collector-run.v1.json`.

## Kimlik doğrulama

`/api/v1/` altındaki tüm uç noktalar şu başlığı ister:

```
X-API-Key: <MONITOR_API_KEY>
```

Başlık eksik veya hatalıysa yanıt `401 unauthorized` olur. `GET /health` ve `/dashboard` sayfaları anahtar istemez. Dashboard işlemleri (`/dashboard/actions/...`) `MONITOR_DASHBOARD_ACTIONS` ayarına göre korunur (bkz. CONFIGURATION.md).

## Hata gövdesi

Tüm hatalar aynı biçimdedir:

```json
{"error": {"code": "validation_error", "message": "request validation failed", "details": [{"loc": ["body", "batch_id"], "msg": "Field required", "type": "missing"}]}}
```

| HTTP | `code` | Durum |
|---|---|---|
| 400 | (düz metin) | İstek bilinmeyen bir host adıyla geldi (`MONITOR_ALLOWED_HOSTS`) |
| 401 | `unauthorized` | API anahtarı yok veya hatalı |
| 403 | `forbidden` | Dashboard işlemi reddedildi (başka bilgisayar, eksik `X-Dashboard-Action` başlığı, farklı site) |
| 404 | `not_found` | Kaynak bulunamadı |
| 409 | `invalid_state_transition` | Örn. RESOLVED bir incident'ı onaylama |
| 422 | `validation_error` | İstek gövdesi veya parametreler geçersiz; bozuk JSON da bu koda düşer |
| 503 | `storage_unavailable` | Disk yazma/okuma hatası; istek tekrar denenebilir |
| 500 | `configuration_error` / `internal_error` | Beklenmeyen sunucu hatası |

## Uç noktalar

### `GET /health`

```json
{"status": "ok", "version": "0.1.0", "contract_version": "1.0"}
```

### `POST /api/v1/posts/batch`

İstek (en fazla 500 gönderi):

```json
{
  "schema_version": "1.0",
  "batch_id": "b-20260101-000001",
  "posts": [
    {
      "schema_version": "1.0",
      "source": "bluesky",
      "source_post_id": "at://did:plc:xxxx/app.bsky.feed.post/3kabc",
      "author_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
      "text": "Ziraat Mobil açılmıyor",
      "created_at": "2026-01-01T09:00:00Z",
      "collected_at": "2026-01-01T09:01:30Z",
      "url": "https://bsky.app/profile/did:plc:xxxx/post/3kabc"
    }
  ]
}
```

Gönderi alanları:

| Alan | Kural |
|---|---|
| `schema_version` | İsteğe bağlı; `"1.0"` veya `"1.1"`. Yazılmazsa batch'in `schema_version` değeri geçerli olur ve gönderi o değerle saklanır. Aynı batch'te farklı sürümde gönderiler varsa (ör. kuyrukta bekleyen eski gönderiler) gönderiye yazılmalıdır. |
| `source` | `^[a-z][a-z0-9_]{1,31}$` |
| `source_post_id` | 1–512 karakter; kaynak içinde benzersiz |
| `author_id` | 1–256 karakter; platformdaki kalıcı yazar kimliği (Bluesky: DID). Olduğu gibi saklanır; farklı kişi sayımı bu değere göre yapılır. |
| `author_handle` | İsteğe bağlı (1.1); kullanıcı adı, en fazla 253 karakter. Zamanla değişebilir. |
| `author_display_name` | İsteğe bağlı (1.1); görünen ad, en fazla 640 karakter |
| `text` | 1–10000 karakter |
| `created_at`, `collected_at` | Saat dilimi içeren ISO 8601 (saat dilimsiz değer reddedilir) |
| `url` | `null` veya en fazla 2048 karakter |
| `engagement` | İsteğe bağlı (1.1); `{like_count, repost_count, reply_count, quote_count}`, toplama anındaki sayılar. İlk etkileşim kaydı olarak saklanır. |
| `query_id` | İsteğe bağlı (1.1); gönderiyi bulan arama/filtrenin göndericideki kimliği (ör. `ziraat_mobil`), 1–64 karakter. Saklanır, API'de ve dashboard'da gösterilir; sınıflandırmada kullanılmaz. Filtre bazında kalite ve kapsama ölçümü içindir. |

Ek alanlar kabul edilmez.

Yanıt `200`:

```json
{"batch_id": "b-20260101-000001", "received": 3, "accepted": 1, "duplicates": 1,
 "rejected": [{"index": 2, "source_post_id": "x", "reason": "created_at: Input should have timezone info"}],
 "incidents_opened": 0, "alerts_created": 0}
```

Davranış:

- Zarf geçersizse (yanlış `schema_version`, boş veya 500'den uzun `posts`, eksik `batch_id`) yanıt `422` olur ve hiçbir gönderi işlenmez.
- Gönderiler tek tek doğrulanır. Geçersiz öğeler `rejected` listesinde `index` ile döner; diğerleri işlenir.
- Kabul kuralları:
  - `MONITOR_ACCEPT_SYNTHETIC=false` iken `source=synthetic` reddedilir.
  - Sentetik modda yalnızca `synthetic` kabul edilir.
  - `created_at` veya `collected_at`, `MONITOR_MAX_CLOCK_SKEW_SECONDS` değerinden daha ileri bir tarih ise öğe reddedilir.
- Mükerrer kontrolü `source + source_post_id` ile yapılır. Aynı batch'in tekrar gönderilmesi güvenlidir: öğeler `duplicates` olarak sayılır ve yeni kayıt oluşmaz.
- Kabul edilen gönderiler aynı istek içinde sınıflandırılır ve tespit değerlendirmesi çalışır.

### `POST /api/v1/posts/engagement`

Daha önce gönderilmiş gönderilerin yeni etkileşim sayıları (sözleşme 1.1):

```json
{"schema_version": "1.1", "batch_id": "e-…", "updates": [
  {"source": "bluesky", "source_post_id": "at://…", "observed_at": "2026-01-01T09:31:30Z",
   "engagement": {"like_count": 42, "repost_count": 17, "reply_count": 5, "quote_count": 2}}]}
```

Yanıt: `{"batch_id", "received", "applied", "stale", "unknown", "rejected"}`.

- `applied`: Gönderinin en yeni sayıları olarak kaydedildi.
- `stale`: Kayıtlı olandan daha yeni değil; yok sayıldı (tekrar gönderim güvenlidir).
- `unknown`: Monitor bu gönderiyi hiç almadı; yok sayıldı.
- `observed_at`, izin verilen saat sapmasından ileri bir tarihse öğe reddedilir.

Kabul edilen güncellemeden sonra değerlendirme yeniden çalışır; itibar sinyalinin yayılım ölçüsü bu sayılardan gelir.

### `GET /api/v1/posts`

Parametreler: `source`, `since` (saat dilimli ISO 8601, `created_at >= since`), `limit` (1–500, varsayılan 50), `offset`. Sonuçlar `created_at` alanına göre en yeniden başlayarak sıralanır.

```json
{"items": [{"post_id": "...", "source": "bluesky", "source_post_id": "...", "author_id": "did:plc:…",
  "author_handle": "kullanici.bsky.social", "author_display_name": "Ad Soyad", "text": "...",
  "created_at": "...", "collected_at": "...", "ingested_at": "...", "url": null,
  "engagement": {"observed_at": "...", "like_count": 3, "repost_count": 1, "reply_count": 0, "quote_count": 0}}],
 "limit": 50, "offset": 0, "count": 1}
```

### `GET /api/v1/analysis`

Parametreler: `is_complaint` (true/false), `service` (hizmet kategorisi), `limit`, `offset`.

```json
{"items": [{"post_id": "...", "is_bank_related": true, "is_complaint": true,
  "service_category": "mobile_banking", "issue_category": "application_unavailable",
  "severity": "medium", "confidence": 0.85, "confidence_method": "heuristic_rule_score",
  "classifier_type": "rule_based", "classifier_version": "tr-1.0",
  "matched_rules": ["brand", "service:mobile_banking", "issue:application_unavailable"],
  "analyzed_at": "...", "risk_signals": [], "locations": ["istanbul"], "label_source": "classifier"}],
 "limit": 50, "offset": 0, "count": 1}
```

- `risk_signals`: `solvency_rumor`, `deposit_flight`, `run_call` (itibar sinyalleri).
- `locations`: Metinde geçen il anahtarları (ipucu, doğrulanmış konum değildir).
- `label_source`: `feedback` ise analist etiketi sınıflandırıcı sonucunun yerine geçmiştir.

- `service_category` değerleri: `mobile_banking`, `internet_banking`, `fast_eft_transfer`, `atm`, `card`, `payments`, `customer_service`, `other`, `unknown` (incident'larda ayrıca `reputation`).
- `issue_category` değerleri: `none`, `login_failure`, `transfer_failure`, `application_unavailable`, `payment_failure`, `transaction_delay`, `atm_failure`, `card_failure`, `customer_service_unreachable`, `other` (itibar incident'larında `solvency_rumor`, `deposit_flight`, `run_call`).
- `severity` (tek şikâyetin tahmini etkisi): `none`, `low`, `medium`, `high`.
- `confidence`: `confidence_method=heuristic_rule_score` olduğunda sezgisel bir kural skorudur, olasılık değildir.

### `POST /api/v1/analysis/{post_id}/feedback`

Analist etiketi. Etiket, o gönderi için sınıflandırıcı sonucunun yerine geçer (sayımlar, tespit, normal seviye, itibar ve konular); sınıflandırıcının kendi sonucu değişmeden saklanır.

```json
{"verdict": "complaint", "service_category": "card", "issue_category": "card_failure",
 "risk_signals": null, "sample": "review_random", "actor": "Ayşe Yılmaz", "note": "…"}
```

- `verdict`: `confirm` (sınıflandırmanın **tüm** alanları doğru), `complaint` (şikâyet; `service_category` ve `issue_category` zorunlu), `not_complaint`, `not_bank_related`.
- `risk_signals`: `null` sınıflandırıcının sinyallerini korur; `[]` sinyalleri kaldırır.
- `sample`: Etiketin nerede verildiği: `review_random` (tarafsız örnek), `review_uncertain`, `review_incidents`, `incident_evidence`, `reputation`, `other`.
- Yanıt `201`: Etiket ve o anki sınıflandırıcı tahmini (`predicted_*`, `classifier_version`). Etkilenen aktif incident'ların zaman çizelgesine `FEEDBACK_APPLIED` yazılır ve değerlendirme hemen yeniden çalışır.

### `GET /api/v1/feedback`, `GET /api/v1/quality`, `GET /api/v1/review/queue`

- `/feedback`: Her gönderinin son etiketi, en yeniden başlayarak (`limit`, `offset`).
- `/quality?classifier_version=&sample=`: Karmaşıklık matrisi, precision, recall, accuracy, kategori doğruluğu, hizmet kırılımı, sürüm ve örneklem dağılımı. Alarm üzerinden verilen etiketler hatalara yoğunlaştığı için tarafsız ölçüm `sample=review_random` ile yapılmalıdır.
- `/review/queue?mode=incidents|uncertain|random&limit=`: Bankadan bahseden ve henüz etiketlenmemiş son 7 günün gönderileri.

### `GET /api/v1/incidents`, `GET /api/v1/incidents/{id}`

Parametreler: `status` (`OPEN`, `ACKNOWLEDGED`, `RESOLVED`), `limit`, `offset`.

```json
{"incident_id": "inc-…", "service_category": "mobile_banking", "issue_category": "login_failure",
 "status": "OPEN", "severity": "CRITICAL", "current_level": "CRITICAL",
 "opened_at": "...", "updated_at": "...", "last_triggered_at": "...", "acknowledged_at": null,
 "resolved_at": null, "resolution_reason": null, "window_minutes": 15,
 "complaints_count": 30, "unique_users_count": 30, "peak_unique_users_count": 30,
 "detection_reason": "30 unique users >= 25 (CRITICAL threshold) in the last 15 minutes; …", "version": 2,
 "detection_rule": "absolute_threshold", "detection_details": {"rule": "absolute_threshold", "window_minutes": 15, "unique_users": 30, "…": "…"},
 "first_complaint_at": "...", "peak_at": "...", "acknowledged_by": null, "resolved_by": null, "resolution_note": null}
```

- `severity`: incident aktifken ulaşılan en yüksek seviye; düşmez.
- `current_level`: son değerlendirmenin seviyesi.
- `detection_rule`: `absolute_threshold`, `baseline` veya `reputation`. `detection_details` gerekçeyi yapılandırılmış olarak verir (dashboard Türkçe metni bundan üretir).

### `POST /api/v1/incidents/{id}/acknowledge`, `POST /api/v1/incidents/{id}/resolve`

İsteğe bağlı gövde: `{"actor": "Ayşe Yılmaz", "note": "…"}` (gövdesiz çağrıda `actor` = `api`). Güncellenmiş incident'ı döndürür. Geçersiz durum geçişi `409`, bilinmeyen kimlik `404` döner. Onaylama ve kapatma, ilgili alarmların durumunu da günceller ve denetim kaydına yazılır.

### Olay denetim uç noktaları

| Uç nokta | Açıklama |
|---|---|
| `POST /api/v1/incidents/{id}/notes` | `{"actor", "text"}`; denetim kaydına `NOTE_ADDED` ekler (`201`). |
| `GET /api/v1/incidents/{id}/timeline` | Denetim kaydı: `OPENED`, `ESCALATED`, `LEVEL_CHANGED`, `ACKNOWLEDGED`, `RESOLVED`, `NOTE_ADDED`, `FEEDBACK_APPLIED`; her kayıt `previous_hash` ve `record_hash` taşır. |
| `GET /api/v1/incidents/{id}/evidence?limit=` | Olay dönemindeki eşleşen gönderiler (kullanıcı adı, metin, link, etkileşim ve sınıflandırma). |
| `GET /api/v1/incidents/{id}/report?format=json\|markdown` | Rapor: olay, süreler, zaman çizelgesi, kanıtlar, kayıt bütünlüğü ve uyarı notu. Markdown Türkçedir ve dosya olarak iner. |
| `GET /api/v1/incidents/metrics?days=30` | Olay sayısı ve ilk şikâyetten tespite / tespitten onaya / tespitten çözüme medyan süreler (saniye). |

`/metrics` yolu `{id}` yolundan önce tanımlıdır; bir incident kimliği `metrics` olamaz.

### `GET /api/v1/alerts`

Parametreler: `alert_type` (`SERVICE_INCIDENT`, `COLLECTOR_HEALTH`, `REPUTATION_SIGNAL`), `status` (`ACTIVE`, `ACKNOWLEDGED`, `RESOLVED`), `incident_id`, `limit`, `offset`.

```json
{"alarm_id": "alm-…", "alert_type": "SERVICE_INCIDENT", "incident_id": "inc-…",
 "service": "mobile_banking", "issue_category": "login_failure", "severity": "CRITICAL",
 "detected_at": "...", "complaints_count": 30, "unique_users_count": 30,
 "detection_reason": "...", "status": "ACTIVE", "updated_at": "..."}
```

Collector sağlık alarmlarında `incident_id` değeri `null`, `service` değeri `collector:<kaynak>` ve `issue_category` değeri `collector_stale` veya `collector_failing` olur.

### `GET /api/v1/stats`

Toplam/analiz edilmiş/bankacılıkla ilgili gönderi sayısı, şikâyet sayısı, hizmet ve problem dağılımı, her pencere için şikâyet ve tekil kullanıcı sayısı (grup kırılımıyla), açık incident ve aktif alarm sayısı, kaynak bazında collector sağlığı, atlanan bozuk satır sayıları, `synthetic_mode` ve normal seviye kuralının durumunu (`baseline`: `ready`, `history_days`, `min_history_days`; kural kapalıysa `null`) döndürür.

### `GET /api/v1/topics/emerging`, `GET /api/v1/locations`

- `/topics/emerging`: Yükselen konular: `label`, `terms`, `unique_users`, `posts`, `expected_users`, `p_value`, `first_seen`, `categories`, `unrecognised_share` (kurallarda karşılığı olmayan gönderi oranı), örnek gönderiler. Alarm üretmez; sonuç 60 saniye önbelleklenir.
- `/locations?hours=24&complaints_only=true`: Gönderilerde geçen iller, farklı kişi sayısına göre.

### `POST /api/v1/collector-runs`

İstek (`CollectorRunReport`):

```json
{"schema_version": "1.0", "run_id": "run-…", "source": "bluesky", "collector_version": "0.1.0",
 "started_at": "...", "finished_at": "...", "status": "partial",
 "queries": [{"query_id": "ziraat_atm", "status": "failed", "fetched_count": 0, "truncated": false, "error_message": "HTTP 503 from source"}],
 "fetched_count": 12, "new_count": 9, "sent_count": 9, "pending_count": 0, "error_message": "1 of 2 queries failed"}
```

Yanıt:

- Yeni kayıt: `201 {"run_id": "...", "stored": true}`
- Aynı `run_id` tekrar gönderilirse: `200 {"run_id": "...", "stored": false}`

`status=success` ve `fetched_count=0` geçerli bir sonuçtur; başarısızlık anlamına gelmez.

### `GET /api/v1/collector-runs`

Parametreler: `source`, `limit`, `offset`. Kayıtlar monitor'e ulaşma sırasına göre en yeniden başlayarak sıralanır.

### Dashboard

| Sayfa | İçerik |
|---|---|
| `/dashboard` | Genel bakış (30 sn'de yenilenir) |
| `/dashboard/incidents/{id}` | Olay detayı, grafik, zaman çizelgesi, kanıtlar, işlemler |
| `/dashboard/incidents/{id}/report`, `/report.md` | Yazdırılabilir rapor, Markdown indirme |
| `/dashboard/review?mode=` | İnceleme kuyruğu |
| `/dashboard/quality` | Sınıflandırma kalitesi |
| `/dashboard/topics` | Yükselen konular |
| `/dashboard/reputation` | İtibar sinyalleri (hassas) |

İşlem uç noktaları (`POST`, JSON gövde, `X-Dashboard-Action: 1` başlığı zorunlu): `/dashboard/actions/incidents/{id}/acknowledge`, `/resolve`, `/notes` ve `/dashboard/actions/posts/{post_id}/feedback`. Gövdeler API'deki karşılıklarıyla aynıdır.

`MONITOR_DASHBOARD_ENABLED=false` olduğunda dashboard yolları `404` döner. Dashboard yanıtları sıkı bir `Content-Security-Policy` (yalnızca aynı kaynaktan script/stil, satır içi kod yok), `X-Frame-Options: DENY` ve `Referrer-Policy: no-referrer` başlıklarıyla döner.
